import ctypes
import sys
import queue
import subprocess
import threading
import tkinter as tk
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import activity
from memory import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    load_state,
    render_state_summary,
)
from pc_context import local_context_enabled
from pc_context_runtime import (
    ensure_context_task,
    format_context_helper_status,
    get_context_helper_status,
    remove_context_task,
    start_context_helper,
    stop_context_helper,
)
from session import (
    add_task_text,
    clear_activity_for_first_run_if_needed,
    coding_apply_proposal_text,
    coding_discard_proposal_text,
    coding_get_proposal_text,
    coding_list_proposals_text,
    coding_set_workspace_text,
    create_memory_backup_text,
    get_local_context_settings,
    get_model_provider_settings,
    get_notification_settings,
    get_ollama_settings,
    get_service_proactive_settings,
    get_status_text,
    get_ui_theme,
    has_pending_user_question,
    factory_reset_yarbis,
    import_memory_backup_text,
    inspect_memory_backup_text,
    list_social_publications_text,
    memory_protection_status_text,
    open_assisted_social_post_text,
    request_stop_current_operation,
    run_auto_with_output,
    run_cycle_with_output,
    run_startup_self_analysis,
    save_note_text,
    send_test_notification,
    submit_user_reply,
    social_accounts_overview_text,
    start_social_oauth_text,
    update_local_context_settings,
    update_notification_settings,
    update_goal,
    update_model_provider,
    update_ollama_settings,
    update_openrouter_settings,
    update_memory_protection_settings_text,
    update_profile_text,
    update_service_proactive_settings,
    update_ui_theme,
    verify_memory_backups_text,
)
from service_manager import (
    format_health_status,
    format_readiness_status,
    get_service_status,
    health_status,
    install_service,
    readiness_status,
    remove_service,
    service_account_requires_password,
    set_autostart_enabled,
    start_service,
    stop_service,
)
from telegram_inbox import start_telegram_polling, stop_telegram_polling
from ui_dialogs import (
    FirstRunDialog,
    MemoryImportModeDialog,
    MultilineTextDialog,
    NoteDialog,
    NotesDialog,
    ProfileDialog,
    CodingProposalsDialog,
    SocialOAuthDialog,
    TaskDialog,
)
from ui_settings_dialogs import (
    LocalContextDialog,
    MemoryProtectionDialog,
    NotificationsDialog,
    OllamaSettingsDialog,
    ServiceInstallDialog,
    ServicePulseDialog,
)
from ui_theme import THEMES, style_scrollbar_widget, style_text_widget

_SINGLE_INSTANCE_MUTEX_NAME = "Local\\YarbisDesktopSingleInstance"
_SINGLE_INSTANCE_MUTEX_HANDLE = None
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_lock() -> bool:
    global _SINGLE_INSTANCE_MUTEX_HANDLE

    if sys.platform != "win32":
        return True

    if _SINGLE_INSTANCE_MUTEX_HANDLE:
        return True

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            return True

        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False

        _SINGLE_INSTANCE_MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def _release_single_instance_lock():
    global _SINGLE_INSTANCE_MUTEX_HANDLE

    if sys.platform != "win32" or not _SINGLE_INSTANCE_MUTEX_HANDLE:
        return

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        kernel32.CloseHandle(_SINGLE_INSTANCE_MUTEX_HANDLE)
    except Exception:
        pass
    finally:
        _SINGLE_INSTANCE_MUTEX_HANDLE = None


def _show_already_running_message():
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "Yarbis",
        "Yarbis ya esta abierto. Usa la ventana existente para evitar duplicar Telegram.",
        parent=root,
    )
    root.destroy()


_STATE_SYNC_INTERVAL_MS = 1000
_HEAVY_STATE_SYNC_INTERVAL_MS = 5000
_CONTEXT_HELPER_SYNC_MS = 10000
_WORKSPACE_ROOT = Path(__file__).resolve().parent
_UPDATE_SCRIPT = _WORKSPACE_ROOT / "scripts" / "update.ps1"


def _powershell_single_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _launch_update_process(needs_admin: bool) -> None:
    if not _UPDATE_SCRIPT.exists():
        raise RuntimeError("No encontre scripts/update.ps1.")

    update_args = [
        "-NoExit",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(_UPDATE_SCRIPT),
        "-RestartDesktop",
    ]
    argument_list = "@(" + ", ".join(_powershell_single_quote(arg) for arg in update_args) + ")"
    command = (
        "Start-Process -FilePath 'powershell.exe' "
        f"-ArgumentList {argument_list} "
        f"-WorkingDirectory {_powershell_single_quote(_WORKSPACE_ROOT)}"
    )
    if needs_admin:
        command += " -Verb RunAs"

    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=str(_WORKSPACE_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class YarbisDesktop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Yarbis")
        self.geometry("1120x760")
        self.minsize(900, 620)

        self.current_theme_name = get_ui_theme()
        self.theme_palette = THEMES.get(self.current_theme_name, THEMES["dark"])
        self.style = ttk.Style(self)

        self.goal_var = tk.StringVar()
        self.cycles_var = tk.StringVar()
        self.pending_var = tk.StringVar()
        self.thinking_var = tk.StringVar(value="No.")
        self.theme_var = tk.StringVar()
        self.ollama_var = tk.StringVar()
        self.coding_var = tk.StringVar()
        self.health_var = tk.StringVar()
        self.readiness_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Listo.")
        self.theme_button_text = tk.StringVar()
        self.service_var = tk.StringVar()
        self.service_button_text = tk.StringVar()
        self.service_autostart_var = tk.BooleanVar()
        self.service_autostart_text = tk.StringVar()

        self._result_queue = queue.Queue()
        self._worker_thread = None
        self._busy = False
        self._busy_sources = set()
        self._action_buttons = []
        self._view_has_pending_question = False
        self._last_summary_text = ""
        self._last_activity_text = ""
        self._last_activity_signature = None
        self._last_heavy_state_refresh_at = 0.0
        self._cached_health_status = None
        self._cached_readiness_status = None
        self._cached_context_helper_status = None
        self._local_telegram_polling = False
        self._closing = False
        self._first_run_checked = False

        self._build_ui()
        self._apply_theme(self.current_theme_name)
        self.refresh_state_view()
        self.after(250, self._maybe_show_first_run)
        self.after(150, self._poll_worker_queue)
        self.after(750, self._ensure_context_helper)
        self.after(_STATE_SYNC_INTERVAL_MS, self._sync_state_view)

    def _build_ui(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        summary = ttk.LabelFrame(self, text="Resumen rapido")
        summary.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=12, pady=(12, 6))
        summary.columnconfigure(1, weight=1)

        ttk.Label(summary, text="Objetivo").grid(row=0, column=0, sticky="nw", padx=10, pady=(10, 4))
        ttk.Label(summary, textvariable=self.goal_var, wraplength=780).grid(
            row=0,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=(10, 4),
        )

        ttk.Label(summary, text="Ciclos").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.cycles_var).grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(summary, text="Tema").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.theme_var).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(summary, text="Modelo").grid(row=3, column=0, sticky="w", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.ollama_var, wraplength=780).grid(
            row=3,
            column=1,
            sticky="w",
            padx=(0, 10),
            pady=4,
        )

        ttk.Label(summary, text="Servicio").grid(row=4, column=0, sticky="nw", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.service_var, wraplength=780).grid(
            row=4,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=4,
        )

        ttk.Label(summary, text="Coding").grid(row=5, column=0, sticky="nw", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.coding_var, wraplength=780).grid(
            row=5,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=4,
        )

        ttk.Label(summary, text="Pensando").grid(row=6, column=0, sticky="nw", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.thinking_var, wraplength=780).grid(
            row=6,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=4,
        )

        ttk.Label(summary, text="Salud").grid(row=7, column=0, sticky="nw", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.health_var, wraplength=780).grid(
            row=7,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=4,
        )

        ttk.Label(summary, text="Preparacion").grid(row=8, column=0, sticky="nw", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.readiness_var, wraplength=780).grid(
            row=8,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=4,
        )

        ttk.Label(summary, text="Pendiente").grid(row=9, column=0, sticky="nw", padx=10, pady=(4, 10))
        ttk.Label(summary, textvariable=self.pending_var, wraplength=780).grid(
            row=9,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=(4, 10),
        )

        actions_shell = ttk.Frame(self)
        actions_shell.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=6)
        actions_shell.columnconfigure(0, weight=1)
        actions_shell.rowconfigure(0, weight=1)

        self.actions_canvas = tk.Canvas(
            actions_shell,
            width=220,
            height=1,
            highlightthickness=0,
            bd=0,
        )
        self.actions_canvas.grid(row=0, column=0, sticky="nsew")
        self.actions_scrollbar = ttk.Scrollbar(
            actions_shell,
            orient="vertical",
            command=self.actions_canvas.yview,
        )
        self.actions_scrollbar.grid(row=0, column=1, sticky="ns")
        self.actions_canvas.configure(yscrollcommand=self.actions_scrollbar.set)

        actions = ttk.Frame(self.actions_canvas)
        self.actions_window = self.actions_canvas.create_window((0, 0), window=actions, anchor="nw")
        actions.bind("<Configure>", self._on_actions_content_configure)
        self.actions_canvas.bind("<Configure>", self._on_actions_canvas_configure)
        self.actions_canvas.bind("<Enter>", self._bind_action_mousewheel)
        self.actions_canvas.bind("<Leave>", self._unbind_action_mousewheel)
        actions.columnconfigure(0, weight=1)

        self._build_action_group(
            actions,
            "Ejecucion",
            (
                {"text": "Ejecutar ciclo", "command": self._run_cycle, "style": "Accent.TButton"},
                {"text": "Modo autonomo", "command": self._run_auto, "style": "Secondary.TButton"},
                {
                    "text": "Detener pensando",
                    "command": self._stop_current_operation,
                    "style": "Danger.TButton",
                    "disable_when_busy": False,
                },
            ),
        )
        self._build_action_group(
            actions,
            "Modelo",
            (
                {"text": "Modelo y timeout", "command": self._edit_ollama_settings},
            ),
        )
        self._build_action_group(
            actions,
            "Contexto",
            (
                {"text": "Cambiar objetivo", "command": self._change_goal},
                {"text": "Editar perfil", "command": self._edit_profile},
                {"text": "Ver notas", "command": self._manage_notes},
                {"text": "Crear tarea", "command": self._create_task},
            ),
        )
        self._build_action_group(
            actions,
            "Coding",
            (
                {"text": "Workspace de codigo", "command": self._choose_coding_workspace},
                {"text": "Propuestas", "command": self._manage_coding_proposals},
            ),
        )
        self._build_action_group(
            actions,
            "Memoria",
            (
                {"text": "Proteccion", "command": self._edit_memory_protection},
                {"text": "Respaldar memoria", "command": self._backup_memory},
                {"text": "Trasplantar memoria", "command": self._import_memory},
                {"text": "Verificar respaldos", "command": self._verify_memory_backups},
            ),
        )
        self._build_action_group(
            actions,
            "Comunicacion",
            (
                {"text": "Notificaciones", "command": self._edit_notifications},
                {"text": "Probar notificacion", "command": self._send_test_notification},
            ),
        )
        self._build_action_group(
            actions,
            "Redes sociales",
            (
                {"text": "Conectar cuenta", "command": self._connect_social_account},
                {"text": "Ver cuentas", "command": self._show_social_accounts},
                {"text": "Ver pendientes", "command": self._show_social_publications},
                {"text": "Copiar confirmacion", "command": self._copy_social_confirmation},
                {"text": "Abrir asistido", "command": self._open_assisted_social_post},
            ),
        )
        self._build_service_group(actions)
        self._build_action_group(
            actions,
            "Mantenimiento",
            (
                {"text": "Actualizar Yarbis", "command": self._update_yarbis, "style": "Secondary.TButton"},
            ),
        )
        self._build_action_group(
            actions,
            "Vista",
            (
                {"textvariable": self.theme_button_text, "command": self._toggle_theme},
                {"text": "Refrescar estado", "command": self.refresh_state_view},
                {"text": "Limpiar actividad", "command": self._clear_activity, "style": "Danger.TButton"},
            ),
        )

        main_panel = ttk.Frame(self)
        main_panel.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)
        main_panel.columnconfigure(0, weight=1)
        main_panel.columnconfigure(1, weight=1)
        main_panel.rowconfigure(0, weight=1)
        main_panel.rowconfigure(1, weight=0)

        summary_frame = ttk.LabelFrame(main_panel, text="Estado actual")
        summary_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)
        self.summary_text = self._create_scrolled_text(summary_frame, wrap="word", height=16)
        self.summary_text.frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.summary_text.configure(state="disabled")

        activity_frame = ttk.LabelFrame(main_panel, text="Actividad")
        activity_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        activity_frame.columnconfigure(0, weight=1)
        activity_frame.rowconfigure(0, weight=1)
        self.activity_text = self._create_scrolled_text(activity_frame, wrap="word", height=18)
        self.activity_text.frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.activity_text.configure(state="disabled")

        composer = ttk.LabelFrame(main_panel, text="Respuesta o contexto libre")
        composer.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        composer.columnconfigure(0, weight=1)
        self.reply_text = tk.Text(composer, height=5, wrap="word")
        self.reply_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.send_button = ttk.Button(
            composer,
            text="Enviar y ejecutar",
            command=self._send_reply,
            style="Accent.TButton",
        )
        self.send_button.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self._action_buttons.append(self.send_button)

        status_shell = ttk.Frame(self)
        status_shell.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        status_shell.columnconfigure(0, weight=1)

        status_bar = ttk.Label(status_shell, textvariable=self.status_var, anchor="w")
        status_bar.grid(row=0, column=0, sticky="ew")

        self.factory_reset_button = ttk.Button(
            status_shell,
            text="",
            width=2,
            command=self._factory_reset,
            style="Hidden.TButton",
        )
        self.factory_reset_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._action_buttons.append(self.factory_reset_button)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _create_scrolled_text(self, parent, **text_options):
        frame = tk.Frame(parent, bd=0, highlightthickness=0)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text = tk.Text(frame, **text_options)
        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=text.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        text.configure(yscrollcommand=scrollbar.set)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        text.frame = frame
        text.vbar = scrollbar
        return text

    def _build_action_group(self, parent, title: str, button_specs):
        group = ttk.LabelFrame(parent, text=title)
        group.pack(fill="x", pady=(0, 8))

        for spec in button_specs:
            button_options = {
                "command": spec["command"],
                "style": spec.get("style", "TButton"),
            }
            if "textvariable" in spec:
                button_options["textvariable"] = spec["textvariable"]
            else:
                button_options["text"] = spec["text"]
            self._pack_action_button(
                ttk.Button(group, **button_options),
                disable_when_busy=spec.get("disable_when_busy", True),
            )

    def _build_service_group(self, parent):
        group = ttk.LabelFrame(parent, text="Servicio")
        group.pack(fill="x", pady=(0, 8))

        self.service_toggle_button = ttk.Button(
            group,
            textvariable=self.service_button_text,
            command=self._toggle_service,
            style="Secondary.TButton",
        )
        self._pack_action_button(self.service_toggle_button)

        self.service_autostart_check = tk.Checkbutton(
            group,
            textvariable=self.service_autostart_text,
            variable=self.service_autostart_var,
            command=self._toggle_service_autostart,
            indicatoron=False,
            anchor="center",
            padx=10,
            pady=7,
            bd=0,
            relief="flat",
            highlightthickness=1,
        )
        self.service_autostart_check.pack(fill="x", padx=8, pady=(5, 8))
        self._action_buttons.append(self.service_autostart_check)

        self.service_pulse_button = ttk.Button(
            group,
            text="Configurar pulso",
            command=self._edit_service_pulse,
        )
        self._pack_action_button(self.service_pulse_button)

        self.local_context_button = ttk.Button(
            group,
            text="Contexto local",
            command=self._edit_local_context,
        )
        self._pack_action_button(self.local_context_button)

        self.remove_service_button = ttk.Button(
            group,
            text="Quitar de SCM",
            command=self._remove_service,
            style="Danger.TButton",
        )
        self._pack_action_button(self.remove_service_button)

    def _pack_action_button(self, button, disable_when_busy: bool = True):
        button.pack(fill="x", padx=8, pady=3)
        if disable_when_busy:
            self._action_buttons.append(button)

    def _on_actions_content_configure(self, _event=None):
        self.actions_canvas.configure(scrollregion=self.actions_canvas.bbox("all"))

    def _on_actions_canvas_configure(self, event):
        self.actions_canvas.itemconfigure(self.actions_window, width=event.width)

    def _bind_action_mousewheel(self, _event=None):
        self.actions_canvas.bind_all("<MouseWheel>", self._on_action_mousewheel)

    def _unbind_action_mousewheel(self, _event=None):
        self.actions_canvas.unbind_all("<MouseWheel>")

    def _on_action_mousewheel(self, event):
        self.actions_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _apply_theme(self, theme_name: str):
        self.current_theme_name = theme_name if theme_name in THEMES else "dark"
        self.theme_palette = THEMES[self.current_theme_name]
        palette = self.theme_palette

        self.style.theme_use("clam")
        self.configure(bg=palette["bg"])
        if hasattr(self, "actions_canvas"):
            self.actions_canvas.configure(bg=palette["bg"])
        if hasattr(self, "actions_scrollbar"):
            style_scrollbar_widget(self.actions_scrollbar, palette)

        self.style.configure(".", background=palette["bg"], foreground=palette["fg"])
        self.style.configure("TFrame", background=palette["bg"])
        self.style.configure(
            "TLabelframe",
            background=palette["bg"],
            bordercolor=palette["border"],
            relief="solid",
        )
        self.style.configure(
            "TLabelframe.Label",
            background=palette["bg"],
            foreground=palette["muted"],
        )
        self.style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
        self.style.configure(
            "TCheckbutton",
            background=palette["bg"],
            foreground=palette["fg"],
            padding=(6, 4),
        )
        self.style.map(
            "TCheckbutton",
            background=[
                ("active", palette["button_active"]),
                ("disabled", palette["bg"]),
            ],
            foreground=[
                ("disabled", palette["disabled_fg"]),
            ],
        )
        self.style.configure(
            "TButton",
            background=palette["button_bg"],
            foreground=palette["fg"],
            bordercolor=palette["border"],
            relief="flat",
            padding=(10, 7),
        )
        self.style.map(
            "TButton",
            background=[
                ("active", palette["button_active"]),
                ("disabled", palette["disabled_bg"]),
            ],
            foreground=[
                ("disabled", palette["disabled_fg"]),
            ],
        )
        self.style.configure(
            "Accent.TButton",
            background=palette["accent"],
            foreground=palette["accent_fg"],
            bordercolor=palette["accent"],
        )
        self.style.map(
            "Accent.TButton",
            background=[
                ("active", palette["accent_hover"]),
                ("disabled", palette["disabled_bg"]),
            ],
            foreground=[
                ("disabled", palette["disabled_fg"]),
            ],
        )
        self.style.configure(
            "Secondary.TButton",
            background=palette["secondary"],
            foreground=palette["secondary_fg"],
            bordercolor=palette["secondary"],
        )
        self.style.map(
            "Secondary.TButton",
            background=[
                ("active", palette["secondary_hover"]),
                ("disabled", palette["disabled_bg"]),
            ],
            foreground=[
                ("disabled", palette["disabled_fg"]),
            ],
        )
        self.style.configure(
            "Danger.TButton",
            background=palette["danger"],
            foreground=palette["danger_fg"],
            bordercolor=palette["danger"],
        )
        self.style.map(
            "Danger.TButton",
            background=[
                ("active", palette["danger_hover"]),
                ("disabled", palette["disabled_bg"]),
            ],
            foreground=[
                ("disabled", palette["disabled_fg"]),
            ],
        )
        self.style.configure(
            "Hidden.TButton",
            background=palette["bg"],
            foreground=palette["bg"],
            bordercolor=palette["bg"],
            lightcolor=palette["bg"],
            darkcolor=palette["bg"],
            relief="flat",
            padding=(0, 0),
        )
        self.style.map(
            "Hidden.TButton",
            background=[
                ("active", palette["button_active"]),
                ("pressed", palette["button_active"]),
                ("disabled", palette["bg"]),
            ],
            foreground=[
                ("active", palette["button_active"]),
                ("pressed", palette["button_active"]),
                ("disabled", palette["bg"]),
            ],
            bordercolor=[
                ("active", palette["button_active"]),
                ("pressed", palette["button_active"]),
                ("disabled", palette["bg"]),
            ],
        )
        self.style.configure(
            "TEntry",
            fieldbackground=palette["field_bg"],
            foreground=palette["field_fg"],
            insertcolor=palette["field_fg"],
            bordercolor=palette["border"],
            padding=6,
        )
        self.style.map(
            "TEntry",
            fieldbackground=[
                ("disabled", palette["disabled_bg"]),
            ],
            foreground=[
                ("disabled", palette["disabled_fg"]),
            ],
        )
        for spinbox_style in ("TSpinbox", "Yarbis.TSpinbox"):
            self.style.configure(
                spinbox_style,
                fieldbackground=palette["field_bg"],
                background=palette["button_bg"],
                foreground=palette["field_fg"],
                insertcolor=palette["field_fg"],
                arrowcolor=palette["field_fg"],
                bordercolor=palette["border"],
                lightcolor=palette["border"],
                darkcolor=palette["border"],
                padding=6,
            )
            self.style.map(
                spinbox_style,
                fieldbackground=[
                    ("readonly", palette["field_bg"]),
                    ("disabled", palette["disabled_bg"]),
                ],
                background=[
                    ("active", palette["button_active"]),
                    ("disabled", palette["disabled_bg"]),
                ],
                foreground=[
                    ("readonly", palette["field_fg"]),
                    ("disabled", palette["disabled_fg"]),
                ],
                arrowcolor=[
                    ("active", palette["accent"]),
                    ("disabled", palette["disabled_fg"]),
                ],
                selectbackground=[
                    ("focus", palette["select_bg"]),
                ],
                selectforeground=[
                    ("focus", palette["select_fg"]),
                ],
            )
        self.style.configure(
            "TCombobox",
            fieldbackground=palette["field_bg"],
            background=palette["field_bg"],
            foreground=palette["field_fg"],
            arrowcolor=palette["field_fg"],
            bordercolor=palette["border"],
            padding=6,
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", palette["field_bg"]),
                ("disabled", palette["disabled_bg"]),
            ],
            foreground=[
                ("readonly", palette["field_fg"]),
                ("disabled", palette["disabled_fg"]),
            ],
            selectbackground=[
                ("readonly", palette["select_bg"]),
            ],
            selectforeground=[
                ("readonly", palette["select_fg"]),
            ],
            arrowcolor=[
                ("disabled", palette["disabled_fg"]),
            ],
        )
        for scrollbar_style in ("TScrollbar", "Yarbis.Vertical.TScrollbar"):
            self.style.configure(
                scrollbar_style,
                background=palette["button_bg"],
                troughcolor=palette["panel"],
                bordercolor=palette["border"],
                darkcolor=palette["button_bg"],
                lightcolor=palette["button_bg"],
                arrowcolor=palette["fg"],
                relief="flat",
                borderwidth=0,
                arrowsize=12,
                width=14,
            )
            self.style.map(
                scrollbar_style,
                background=[
                    ("active", palette["button_active"]),
                    ("pressed", palette["accent"]),
                    ("disabled", palette["disabled_bg"]),
                ],
                arrowcolor=[
                    ("pressed", palette["accent_fg"]),
                    ("disabled", palette["disabled_fg"]),
                ],
            )

        self.option_add("*TCombobox*Listbox*Background", palette["field_bg"])
        self.option_add("*TCombobox*Listbox*Foreground", palette["field_fg"])
        self.option_add("*TCombobox*Listbox*selectBackground", palette["select_bg"])
        self.option_add("*TCombobox*Listbox*selectForeground", palette["select_fg"])

        style_text_widget(self.summary_text, palette)
        style_text_widget(self.activity_text, palette)
        style_text_widget(self.reply_text, palette)
        self._style_service_autostart_toggle()

        self.theme_var.set("Oscuro" if self.current_theme_name == "dark" else "Claro")
        self.theme_button_text.set(
            "Usar modo claro" if self.current_theme_name == "dark" else "Usar modo oscuro"
        )

    def _style_service_autostart_toggle(self):
        if not hasattr(self, "service_autostart_check"):
            return

        palette = self.theme_palette
        enabled = bool(self.service_autostart_var.get())
        state = str(self.service_autostart_check.cget("state"))
        disabled = state == "disabled"

        if disabled:
            bg = palette["disabled_bg"]
            fg = palette["disabled_fg"]
            active_bg = palette["disabled_bg"]
        elif enabled:
            bg = palette["secondary"]
            fg = palette["secondary_fg"]
            active_bg = palette["secondary_hover"]
        else:
            bg = palette["button_bg"]
            fg = palette["fg"]
            active_bg = palette["button_active"]

        self.service_autostart_check.configure(
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=fg,
            selectcolor=bg,
            disabledforeground=palette["disabled_fg"],
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
        )

    def _set_text(self, widget, content: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _activity_should_follow_end(self) -> bool:
        try:
            return self.activity_text.yview()[1] >= 0.98
        except tk.TclError:
            return True

    def _refresh_activity_view(self, force_scroll: bool = False):
        signature = activity.activity_history_signature()
        if not force_scroll and signature == self._last_activity_signature:
            return

        content = activity.read_activity_history()
        if content == self._last_activity_text:
            self._last_activity_signature = signature
            return

        should_scroll = force_scroll or self._activity_should_follow_end()
        try:
            previous_view = self.activity_text.yview()[0]
        except tk.TclError:
            previous_view = 1.0

        self.activity_text.configure(state="normal")
        self.activity_text.delete("1.0", "end")
        self.activity_text.insert("1.0", content)
        if should_scroll:
            self.activity_text.see("end")
        else:
            self.activity_text.yview_moveto(previous_view)
        self.activity_text.configure(state="disabled")
        self._last_activity_text = content
        self._last_activity_signature = signature

    def _append_activity_fallback(self, title: str, content: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rendered = f"[{timestamp}] {title}\n{content.strip() or 'Sin salida adicional.'}\n\n"
        self.activity_text.configure(state="normal")
        self.activity_text.insert("end", rendered)
        self.activity_text.see("end")
        self.activity_text.configure(state="disabled")
        self._last_activity_text += rendered

    def _append_activity(self, title: str, content: str):
        try:
            activity.append_activity(title, content)
            self._refresh_activity_view(force_scroll=True)
        except Exception:  # pragma: no cover - respaldo visual si falla el disco
            self._append_activity_fallback(title, str(content))

    @staticmethod
    def _thinking_status_text(label: str) -> str:
        safe_label = str(label).strip() or "Operacion"
        return f"Estoy pensando: {safe_label}..."

    @staticmethod
    def _format_thinking_started_at(value: str) -> str:
        started_at = str(value).strip()
        if not started_at:
            return ""
        try:
            parsed = datetime.fromisoformat(started_at)
            return parsed.astimezone().strftime("%H:%M:%S")
        except ValueError:
            return started_at

    @staticmethod
    def _runtime_thinking_from_state(state: dict) -> tuple[bool, str, str]:
        thinking = state.get("runtime", {}).get("thinking", {})
        thinking_label = str(thinking.get("label", "")).strip() or "Operacion"
        thinking_active = bool(thinking.get("active")) and bool(thinking_label)
        started_at = str(thinking.get("started_at", "")).strip()
        return thinking_active, thinking_label, started_at

    def _sync_runtime_thinking(self, state: dict | None = None) -> bool:
        state = state or load_state()
        thinking_active, thinking_label, started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            started_display = self._format_thinking_started_at(started_at)
            started_text = f" desde {started_display}" if started_display else ""
            self.thinking_var.set(f"SI - {thinking_label}{started_text}")
            self._set_busy(True, self._thinking_status_text(thinking_label), source="runtime")
            return True

        self.thinking_var.set("No.")
        if "runtime" in self._busy_sources:
            self._set_busy(False, source="runtime")
        return False

    def _show_thinking_blocked_message(self, label: str):
        messagebox.showinfo(
            "Yarbis",
            (
                f"{self._thinking_status_text(label)}\n\n"
                "La entrada queda bloqueada hasta que termine para no mezclar dos operaciones."
            ),
            parent=self,
        )

    def refresh_state_view(self, force_heavy: bool = True):
        state = load_state()
        self.goal_var.set(state["goal"])
        self.cycles_var.set(str(state["cycle_count"]))
        model_provider = state.get("model_provider", {})
        if not isinstance(model_provider, dict):
            model_provider = {}
        active_provider = str(model_provider.get("default", MODEL_PROVIDER_OLLAMA)).strip().lower()
        if active_provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
            active_provider = MODEL_PROVIDER_OLLAMA
        model_settings = model_provider.get(active_provider, state.get("ollama", {}))
        if not isinstance(model_settings, dict):
            model_settings = {}
        fallback_models = model_settings.get("fallback_models", [])
        fallback_text = f", +{len(fallback_models)} fallback(s)" if fallback_models else ""
        host_text = model_settings.get("host") or "local"
        provider_label = "OpenRouter" if active_provider == MODEL_PROVIDER_OPENROUTER else "Ollama"
        model_name = model_settings.get("model") or ("sin modelo" if active_provider == MODEL_PROVIDER_OPENROUTER else DEFAULT_OLLAMA_MODEL)
        self.ollama_var.set(
            f"{provider_label}: {model_name}{fallback_text} "
            f"@ {host_text} "
            f"({model_settings.get('timeout_seconds', DEFAULT_OLLAMA_TIMEOUT_SECONDS)}s)"
        )
        coding_settings = state.get("coding", {})
        coding_workspace = str(coding_settings.get("workspace_path", "")).strip()
        coding_pending = coding_settings.get("pending_proposal_ids", [])
        if coding_workspace:
            self.coding_var.set(
                f"{coding_workspace} "
                f"(modo={coding_settings.get('mode', 'propose_first')}, "
                f"propuestas={len(coding_pending)})"
            )
        else:
            self.coding_var.set("Sin workspace de codigo.")

        now = time.monotonic()
        refresh_heavy = (
            force_heavy
            or self._cached_health_status is None
            or now - self._last_heavy_state_refresh_at >= (_HEAVY_STATE_SYNC_INTERVAL_MS / 1000)
        )
        if refresh_heavy:
            health = health_status()
            readiness = readiness_status()
            helper_status = get_context_helper_status()
            self._cached_health_status = health
            self._cached_readiness_status = readiness
            self._cached_context_helper_status = helper_status
            self._last_heavy_state_refresh_at = now
        else:
            health = self._cached_health_status
            readiness = self._cached_readiness_status
            helper_status = self._cached_context_helper_status

        self.health_var.set(format_health_status(health))
        self.readiness_var.set(format_readiness_status(readiness))
        service_status = health["service"]
        proactive_settings = state["service"]["proactive"]
        pulse_status = "activo" if proactive_settings["enabled"] else "desactivado"
        pulse_model = str(proactive_settings.get("model", "")).strip()
        pulse_model_text = f", modelo {pulse_model}" if pulse_model else ", modelo principal"
        pulse_text = (
            f"Pulso {pulse_status}: {proactive_settings['cycles']} ciclo(s) cada "
            f"{proactive_settings['interval_seconds']}s{pulse_model_text}."
        )
        local_context_settings = state.get("local_context", {})
        local_context_status = "activo" if local_context_enabled(local_context_settings) else "desactivado"
        helper_text = format_context_helper_status(helper_status)
        local_context_text = (
            f"Contexto local {local_context_status} "
            f"(modo={local_context_settings.get('mode', 'safe')}, {helper_text})."
        )
        if not service_status["installed"]:
            self.service_var.set(f"No instalado en SCM. {pulse_text} {local_context_text}")
            self.service_button_text.set("Instalar e iniciar")
        elif service_status["running"]:
            account_text = (
                f", cuenta={service_status['account_name']}"
                if service_status.get("account_name")
                else ""
            )
            self.service_var.set(
                f"Activo en SCM (PID {service_status['pid']}, "
                f"arranque={service_status['start_type']}{account_text}). "
                f"{pulse_text} {local_context_text}"
            )
            self.service_button_text.set("Detener servicio")
        else:
            account_text = (
                f", cuenta={service_status['account_name']}"
                if service_status.get("account_name")
                else ""
            )
            self.service_var.set(
                f"Instalado en SCM, detenido "
                f"(arranque={service_status['start_type']}{account_text}). "
                f"{pulse_text} {local_context_text}"
            )
            self.service_button_text.set("Iniciar servicio")
        autostart_enabled = bool(service_status["autostart_enabled"])
        self.service_autostart_var.set(autostart_enabled)
        self.service_autostart_text.set(
            "Iniciar con Windows: SI" if autostart_enabled else "Iniciar con Windows: NO"
        )
        if service_status["installed"]:
            self.service_autostart_check.configure(state="normal")
        else:
            self.service_autostart_check.configure(state="disabled")
        self._style_service_autostart_toggle()
        if refresh_heavy:
            self._ensure_telegram_polling_matches_service(service_status["running"])

        self._sync_runtime_thinking(state)

        pending_question = state["awaiting_user_input"].get("question", "").strip()
        self._view_has_pending_question = has_pending_user_question(state)
        if self._view_has_pending_question:
            self.pending_var.set(pending_question)
            if not self._busy:
                self.status_var.set("Esperando respuesta del usuario.")
            self.send_button.configure(text="Responder y continuar")
        else:
            self.pending_var.set("Sin preguntas pendientes.")
            if not self._busy:
                self.status_var.set("Listo.")
            self.send_button.configure(text="Enviar y ejecutar")

        summary_text = render_state_summary(state)
        if summary_text != self._last_summary_text:
            self._set_text(self.summary_text, summary_text)
            self._last_summary_text = summary_text
        self._refresh_activity_view()

    def _maybe_show_first_run(self):
        if self._first_run_checked:
            return

        state = load_state()
        if str(state.get("goal", "")).strip():
            self._first_run_checked = True
            return
        if self._busy:
            self.after(1000, self._maybe_show_first_run)
            return

        self._first_run_checked = True
        dialog = FirstRunDialog(self, initial_state=state)
        if dialog.result is None:
            self.status_var.set("Define un objetivo para empezar.")
            return

        self._apply_first_run_setup(dialog.result)

    def _apply_first_run_setup(self, settings: dict):
        activity_lines = []
        try:
            activity_lines.append(update_goal(settings["goal"]))
            activity_lines.append(update_ollama_settings(
                settings["model"],
                settings["timeout_seconds"],
                host=settings.get("host", ""),
            ))
            if settings.get("name") or settings.get("role"):
                activity_lines.append(update_profile_text(
                    name=settings.get("name", ""),
                    role=settings.get("role", ""),
                ))
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            self.refresh_state_view()
            return

        self._append_activity("Primer uso", "\n\n".join(line for line in activity_lines if line))
        readiness_status(force=True)
        self.refresh_state_view()

        if settings.get("open_notifications"):
            self._edit_notifications()

        if settings.get("install_service"):
            self._toggle_service()
            return

        if settings.get("run_first_cycle"):
            self._start_background_job("Primer ciclo", run_cycle_with_output)

    def _sync_state_view(self):
        self.refresh_state_view(force_heavy=False)
        self.after(_STATE_SYNC_INTERVAL_MS, self._sync_state_view)

    def _ensure_telegram_polling_matches_service(self, service_running: bool | None = None):
        if service_running is None:
            service_running = get_service_status()["running"]

        if service_running:
            if self._local_telegram_polling:
                stop_telegram_polling()
                self._local_telegram_polling = False
            return

        if not self._local_telegram_polling:
            start_telegram_polling(event_callback=self._handle_telegram_event)
            self._local_telegram_polling = True

    def _sync_context_helper_once(self, ensure_task: bool = False):
        try:
            settings = get_local_context_settings()
            service_status = get_service_status()
        except Exception:
            return

        if not (service_status.get("running") and local_context_enabled(settings)):
            return

        try:
            messages = []
            if ensure_task:
                messages.append(ensure_context_task())
            if not get_context_helper_status().get("running"):
                messages.append(start_context_helper())
            rendered = "\n".join(message for message in messages if str(message).strip())
            if rendered:
                self._append_activity("Contexto local", rendered)
        except Exception as exc:
            self._append_activity("Contexto local", f"No pude iniciar el helper local: {exc}")

    def _ensure_context_helper(self):
        if self._closing:
            return

        self._sync_context_helper_once()
        self.after(_CONTEXT_HELPER_SYNC_MS, self._ensure_context_helper)

    def _set_busy(self, busy: bool, status_text: str = "", source: str = "local"):
        if busy:
            self._busy_sources.add(source)
        else:
            self._busy_sources.discard(source)

        self._busy = bool(self._busy_sources)
        state = "disabled" if self._busy else "normal"
        for button in self._action_buttons:
            button.configure(state=state)
        self.reply_text.configure(state=state)
        if status_text:
            self.status_var.set(status_text)
        elif not self._busy:
            self.status_var.set("Listo.")

    def _start_background_job(self, label: str, func, *args, **kwargs):
        state = load_state()
        thinking_active, thinking_label, _started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            self._sync_runtime_thinking(state)
            self._show_thinking_blocked_message(thinking_label)
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.")
            return

        self._set_busy(True, self._thinking_status_text(label))
        self._append_activity(f"{label} iniciado", "Operacion en curso.")

        def worker():
            try:
                result = func(*args, **kwargs)
                self._result_queue.put(("success", label, result))
            except Exception as exc:  # pragma: no cover - respaldo visual
                self._result_queue.put(("error", label, str(exc)))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _poll_worker_queue(self):
        try:
            while True:
                kind, label, payload = self._result_queue.get_nowait()
                if kind == "success":
                    self._set_busy(False, source="local")
                    self._append_activity(label, str(payload))
                    self.refresh_state_view()
                elif kind == "remote_start":
                    self._set_busy(True, str(payload), source="remote")
                    self._append_activity(f"{label} iniciado", str(payload))
                elif kind == "remote_success":
                    self._set_busy(False, source="remote")
                    self._append_activity(label, str(payload))
                    self.refresh_state_view()
                elif kind == "remote_error":
                    self._set_busy(False, source="remote")
                    self._append_activity(f"{label} (error)", str(payload))
                    self.status_var.set("La accion termino con error.")
                elif kind == "event":
                    self._append_activity(label, str(payload))
                    self.refresh_state_view()
                else:
                    self._set_busy(False, source="local")
                    self._append_activity(f"{label} (error)", str(payload))
                    self.status_var.set("La accion termino con error.")
        except queue.Empty:
            pass

        self.after(150, self._poll_worker_queue)

    def _run_cycle(self):
        if self._show_pending_user_question():
            return
        self._start_background_job("Ciclo", run_cycle_with_output)

    def _run_auto(self):
        if self._show_pending_user_question():
            return
        default_cycles = load_state()["autonomy"]["auto_cycles_default"]
        cycles = simpledialog.askinteger(
            "Modo autonomo",
            "Cuantos ciclos quieres ejecutar?",
            initialvalue=default_cycles,
            minvalue=1,
            maxvalue=20,
            parent=self,
        )
        if cycles is None:
            return
        self._start_background_job("Modo autonomo", run_auto_with_output, cycles)

    def _stop_current_operation(self):
        result = request_stop_current_operation(source="desktop")
        self._append_activity("Detener", result)
        self.status_var.set(result)
        self.refresh_state_view()

    def _show_pending_user_question(self) -> bool:
        state = load_state()
        if not has_pending_user_question(state):
            return False

        pending_question = state["awaiting_user_input"].get("question", "").strip()
        self.refresh_state_view()
        self.reply_text.focus_set()
        messagebox.showinfo(
            "Yarbis",
            (
                "Yarbis necesita tu respuesta antes de continuar.\n"
                "Cuando respondas, retomara el modo autonomo.\n\n"
                f"{pending_question}"
            ).strip(),
            parent=self,
        )
        return True

    def _toggle_theme(self):
        next_theme = "light" if self.current_theme_name == "dark" else "dark"
        result = update_ui_theme(next_theme)
        self._apply_theme(next_theme)
        self._append_activity("Tema", result)
        self.status_var.set("Listo.")

    def _edit_ollama_settings(self):
        dialog = OllamaSettingsDialog(self, initial_settings=get_model_provider_settings())
        if dialog.result is None:
            return

        try:
            provider = dialog.result.pop("provider", MODEL_PROVIDER_OLLAMA)
            if provider == MODEL_PROVIDER_OPENROUTER:
                result = update_openrouter_settings(**dialog.result)
            else:
                dialog.result.pop("api_key", None)
                result = update_ollama_settings(**dialog.result)
            provider_result = update_model_provider(provider)
            result = f"{provider_result}\n{result}"
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        self._append_activity("Modelo", result)
        readiness_status(force=True)
        self.refresh_state_view()

    def _edit_notifications(self):
        dialog = NotificationsDialog(self, initial_settings=get_notification_settings())
        if dialog.result is None:
            return

        try:
            result = update_notification_settings(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc))
            return

        self._append_activity("Notificaciones", result)
        readiness_status(force=True)
        self.refresh_state_view()
        if (
            dialog.result.get("telegram_enabled")
            and dialog.result.get("telegram_bot_token")
            and not dialog.result.get("telegram_chat_id")
        ):
            messagebox.showinfo(
                "Yarbis",
                (
                    "Telegram ya quedo configurado, pero falta vincular el chat.\n\n"
                    "Abre tu bot en Telegram y envia /start. Despues podras usar "
                    "'Probar notificacion' para confirmar que ya quedo enlazado."
                ),
                parent=self,
            )

    def _send_test_notification(self):
        self._start_background_job("Prueba de notificacion", send_test_notification)

    def _connect_social_account(self):
        dialog = SocialOAuthDialog(self)
        if dialog.result is None:
            return

        missing = [
            label
            for label, value in (
                ("Client/App ID", dialog.result.get("client_id")),
                ("Client/App Secret", dialog.result.get("client_secret")),
            )
            if not str(value or "").strip()
        ]
        if missing:
            messagebox.showwarning("Yarbis", "Faltan datos: " + ", ".join(missing), parent=self)
            return

        self._start_background_job(
            "Conectar red social",
            start_social_oauth_text,
            **dialog.result,
        )

    def _show_social_accounts(self):
        self._append_activity("Redes sociales", social_accounts_overview_text())
        self.refresh_state_view()

    def _show_social_publications(self):
        self._append_activity("Publicaciones sociales", list_social_publications_text(status="all", limit=15))
        self.refresh_state_view()

    def _copy_social_confirmation(self):
        publication_id = simpledialog.askstring(
            "Copiar confirmacion",
            "Id de publicacion pendiente:",
            parent=self,
        )
        if publication_id is None:
            return
        cleaned_id = publication_id.strip().lower()
        if not cleaned_id:
            messagebox.showinfo("Yarbis", "Indica un id de publicacion pendiente.", parent=self)
            return

        state = load_state()
        matches = [
            publication
            for publication in state.get("social", {}).get("pending_publications", [])
            if publication.get("id", "").lower() == cleaned_id
            or publication.get("id", "").lower().startswith(cleaned_id)
        ]
        if len(matches) != 1:
            messagebox.showwarning(
                "Yarbis",
                "No encontre una publicacion pendiente unica con ese id.",
                parent=self,
            )
            return

        phrase = matches[0].get("confirmation_phrase", f"PUBLICAR {matches[0]['id']}")
        self.clipboard_clear()
        self.clipboard_append(phrase)
        self.update()
        self._append_activity("Redes sociales", f"Confirmacion copiada: {phrase}")

    def _open_assisted_social_post(self):
        publication_id = simpledialog.askstring(
            "Abrir asistido",
            "Id de publicacion pendiente o draft:",
            parent=self,
        )
        if publication_id is None:
            return
        cleaned_id = publication_id.strip()
        if not cleaned_id:
            messagebox.showinfo("Yarbis", "Indica un id de publicacion o draft.", parent=self)
            return
        self._start_background_job(
            "Facebook asistido",
            open_assisted_social_post_text,
            publication_id="" if cleaned_id.lower().startswith("draft") else cleaned_id,
            draft_id=cleaned_id if cleaned_id.lower().startswith("draft") else "",
        )

    @staticmethod
    def _install_and_start_service(start_auto: bool, account_name: str = "", password: str = "") -> str:
        install_result = install_service(
            start_auto=start_auto,
            account_name=account_name,
            password=password,
        )
        start_result = start_service()
        task_result = ensure_context_task()
        helper_result = start_context_helper()
        return f"{install_result}\n{start_result}\n{task_result}\n{helper_result}"

    @staticmethod
    def _start_service_with_context_helper() -> str:
        start_result = start_service()
        task_result = ensure_context_task()
        helper_result = start_context_helper()
        return f"{start_result}\n{task_result}\n{helper_result}"

    @staticmethod
    def _remove_service_with_context_helper() -> str:
        service_result = remove_service()
        helper_result = stop_context_helper()
        task_result = remove_context_task()
        return f"{service_result}\n{helper_result}\n{task_result}"

    def _edit_service_pulse(self):
        dialog = ServicePulseDialog(self, initial_settings=get_service_proactive_settings())
        if dialog.result is None:
            return

        try:
            result = update_service_proactive_settings(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        self._append_activity("Pulso proactivo", result)
        self.refresh_state_view()

    def _edit_local_context(self):
        dialog = LocalContextDialog(self, initial_settings=get_local_context_settings())
        if dialog.result is None:
            return

        try:
            result = update_local_context_settings(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        self._append_activity("Contexto local", result)
        self._sync_context_helper_once(ensure_task=True)
        self.refresh_state_view()

    def _toggle_service(self):
        service_status = get_service_status(force=True)
        if service_status["running"]:
            self._start_background_job("Servicio", stop_service)
            return

        if self._local_telegram_polling:
            stop_telegram_polling()
            self._local_telegram_polling = False

        if not service_status["installed"]:
            dialog = ServiceInstallDialog(
                self,
                initial_autostart=bool(self.service_autostart_var.get()),
            )
            if dialog.result is None:
                self.refresh_state_view()
                return
            account_name = dialog.result.get("account_name", "")
            password = dialog.result.get("password", "")
            if service_account_requires_password(account_name) and not password:
                messagebox.showwarning(
                    "Yarbis",
                    (
                        "La cuenta indicada necesita password para registrarse en SCM.\n\n"
                        "Indica password o deja cuenta y password vacios para usar LocalSystem."
                    ),
                    parent=self,
                )
                self.refresh_state_view()
                return

            self._start_background_job(
                "Servicio",
                self._install_and_start_service,
                bool(dialog.result.get("start_auto")),
                account_name,
                password,
            )
            return

        self._start_background_job("Servicio", self._start_service_with_context_helper)

    def _toggle_service_autostart(self):
        service_status = get_service_status(force=True)
        if not service_status["installed"]:
            messagebox.showinfo(
                "Yarbis",
                "Instala el servicio en SCM antes de cambiar su arranque con Windows.",
                parent=self,
            )
            self.refresh_state_view()
            return

        enabled = self.service_autostart_var.get()
        self.service_autostart_text.set(
            "Iniciar con Windows: SI" if enabled else "Iniciar con Windows: NO"
        )
        self._style_service_autostart_toggle()
        try:
            result = set_autostart_enabled(enabled)
        except Exception as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            self.refresh_state_view()
            return

        self._append_activity("Servicio", result)
        self.refresh_state_view()

    def _remove_service(self):
        service_status = get_service_status(force=True)
        if not service_status["installed"]:
            self._append_activity("Servicio", "El servicio de Yarbis no esta instalado en SCM.")
            self.refresh_state_view()
            return

        should_remove = messagebox.askyesno(
            "Quitar servicio",
            (
                "Esto detendra Yarbis si esta activo y quitara el registro del servicio en SCM.\n\n"
                "Quieres continuar?"
            ),
            parent=self,
        )
        if not should_remove:
            return

        self._start_background_job("Quitar servicio", self._remove_service_with_context_helper)

    def _update_yarbis(self):
        state = load_state()
        thinking_active, thinking_label, _started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            self._sync_runtime_thinking(state)
            self._show_thinking_blocked_message(thinking_label)
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        try:
            service_status = get_service_status(force=True)
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude revisar el servicio: {exc}", parent=self)
            return

        needs_admin = bool(service_status.get("installed"))
        admin_text = (
            "\n\nComo el servicio SCM esta instalado, Windows pedira permisos de administrador."
            if needs_admin
            else ""
        )
        should_update = messagebox.askyesno(
            "Actualizar Yarbis",
            (
                "Voy a abrir una ventana externa de PowerShell para actualizar Yarbis. "
                "Esta ventana se cerrara para que el actualizador pueda cambiar codigo y dependencias.\n\n"
                "Si hay cambios locales, el actualizador los guardara con git stash y los reaplicara despues."
                f"{admin_text}\n\nQuieres continuar?"
            ),
            parent=self,
        )
        if not should_update:
            return

        try:
            _launch_update_process(needs_admin=needs_admin)
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude abrir el actualizador: {exc}", parent=self)
            return

        self.status_var.set("Actualizador iniciado en ventana externa.")
        self._append_activity(
            "Actualizacion",
            "Actualizador externo iniciado. Esta ventana se cerrara y Yarbis se reabrira si termina bien.",
        )
        self._closing = True
        stop_telegram_polling()
        self.destroy()

    def _change_goal(self):
        dialog = MultilineTextDialog(
            self,
            title="Cambiar objetivo",
            label="Nuevo objetivo para Yarbis",
            initial_value=load_state()["goal"],
            height=6,
        )
        if dialog.result is None:
            return

        try:
            result = update_goal(dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc))
            return

        self._append_activity("Objetivo actualizado", result)
        readiness_status(force=True)
        self.refresh_state_view()

    def _choose_coding_workspace(self):
        state = load_state()
        initial_dir = str(state.get("coding", {}).get("workspace_path", "")).strip() or str(_WORKSPACE_ROOT)
        selected_path = filedialog.askdirectory(
            title="Selecciona el repositorio de codigo",
            initialdir=initial_dir,
            parent=self,
        )
        if not selected_path:
            return

        result = coding_set_workspace_text(selected_path)
        self._append_activity("Workspace de codigo", result)
        self.refresh_state_view()

    def _manage_coding_proposals(self):
        dialog = CodingProposalsDialog(
            self,
            apply_callback=coding_apply_proposal_text,
            discard_callback=coding_discard_proposal_text,
            detail_callback=coding_get_proposal_text,
            list_callback=coding_list_proposals_text,
        )
        if dialog.result:
            self._append_activity("Propuestas de coding", dialog.result)
        self.refresh_state_view()

    def _edit_profile(self):
        dialog = ProfileDialog(self, initial_profile=load_state()["profile"])
        if dialog.result is None:
            return

        result = update_profile_text(**dialog.result)
        self._append_activity("Perfil", result)
        self.refresh_state_view()

    def _save_note(self):
        dialog = NoteDialog(self, "Guardar nota")
        if dialog.result is None:
            return

        result = save_note_text(**dialog.result)
        self._append_activity("Nota", result)
        self.refresh_state_view()

    def _manage_notes(self):
        dialog = NotesDialog(self)
        if dialog.result:
            self._append_activity("Notas", dialog.result)
        self.refresh_state_view()

    def _memory_backups_dir(self) -> Path:
        return Path(__file__).resolve().parent / ".yarbis_memory_backups"

    def _edit_memory_protection(self):
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        state = load_state()
        dialog = MemoryProtectionDialog(
            self,
            initial_settings=state.get("memory_protection", {}),
            status_text=memory_protection_status_text(),
        )
        if dialog.result is None:
            return

        result = update_memory_protection_settings_text(**dialog.result)
        self._append_activity("Proteccion de memoria", result)
        self.refresh_state_view()
        messagebox.showinfo("Yarbis", result, parent=self)

    def _verify_memory_backups(self):
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        result = verify_memory_backups_text()
        self._append_activity("Verificacion de memoria", result)
        self.refresh_state_view()
        messagebox.showinfo("Yarbis", result, parent=self)

    def _backup_memory(self):
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        backups_dir = self._memory_backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)
        target_path = filedialog.asksaveasfilename(
            parent=self,
            title="Guardar respaldo de memoria",
            initialdir=str(backups_dir),
            defaultextension=".json",
            filetypes=(
                ("Respaldos de memoria Yarbis", "*.json"),
                ("Todos los archivos", "*.*"),
            ),
        )
        if not target_path:
            return

        include_secrets = messagebox.askyesno(
            "Secretos en respaldo",
            (
                "Quieres incluir tokens persistidos de ntfy y Telegram?\n\n"
                "Elige No para crear un respaldo portable con secretos redactados."
            ),
            parent=self,
        )
        result = create_memory_backup_text(
            path=target_path,
            include_secrets=include_secrets,
        )
        self._append_activity("Respaldo de memoria", result)
        self.refresh_state_view()
        messagebox.showinfo("Yarbis", result, parent=self)

    def _import_memory(self):
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        backups_dir = self._memory_backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)
        source_path = filedialog.askopenfilename(
            parent=self,
            title="Seleccionar respaldo de memoria",
            initialdir=str(backups_dir),
            filetypes=(
                ("Respaldos de memoria Yarbis", "*.json"),
                ("Todos los archivos", "*.*"),
            ),
        )
        if not source_path:
            return

        summary = inspect_memory_backup_text(source_path)
        if not summary.startswith("Respaldo de memoria."):
            messagebox.showwarning("Yarbis", summary, parent=self)
            return

        dialog = MemoryImportModeDialog(self, backup_summary=summary)
        if dialog.result is None:
            return

        mode = dialog.result
        if mode == "replace":
            should_import = messagebox.askyesno(
                "Confirmar trasplante",
                (
                    "Esto reemplazara la memoria actual despues de crear un respaldo local previo. "
                    "Quieres continuar?"
                ),
                parent=self,
            )
            if not should_import:
                return

        result = import_memory_backup_text(source_path, mode=mode)
        self._append_activity("Trasplante de memoria", result)
        self.refresh_state_view()
        messagebox.showinfo("Yarbis", result, parent=self)

    def _create_task(self):
        dialog = TaskDialog(self, "Crear tarea")
        if dialog.result is None:
            return

        result = add_task_text(**dialog.result)
        self._append_activity("Tarea", result)
        self.refresh_state_view()

    def _send_reply(self):
        state = load_state()
        thinking_active, thinking_label, _started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            self._sync_runtime_thinking(state)
            self._show_thinking_blocked_message(thinking_label)
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        reply_text = self.reply_text.get("1.0", "end-1c").strip()
        if not reply_text:
            messagebox.showinfo("Yarbis", "Escribe una respuesta o contexto antes de enviarlo.")
            return

        displayed_pending = self._view_has_pending_question
        actual_pending = has_pending_user_question(state)
        if actual_pending != displayed_pending:
            self.refresh_state_view()
            if displayed_pending and not actual_pending:
                messagebox.showinfo(
                    "Yarbis",
                    (
                        "La pregunta pendiente ya se respondio desde otro canal.\n\n"
                        "Revise el estado actualizado. Si aun quieres mandar contexto "
                        "adicional, vuelve a presionar Enviar."
                    ),
                    parent=self,
                )
                return

        self.reply_text.delete("1.0", "end")
        self._start_background_job("Respuesta", submit_user_reply, reply_text)

    def _clear_activity(self):
        activity.clear_activity_history()
        self._last_activity_text = ""
        self._set_text(self.activity_text, "")

    def _factory_reset(self):
        state = load_state()
        thinking_active, thinking_label, _started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            self._sync_runtime_thinking(state)
            self._show_thinking_blocked_message(thinking_label)
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        should_reset = messagebox.askyesno(
            "Reiniciar de fabrica",
            (
                "Esto borrara objetivo, perfil, notas, tareas, conversacion, Telegram, "
                "configuracion local y actividad.\n\n"
                "No borra el codigo ni desinstala el servicio de Windows.\n\n"
                "Quieres continuar?"
            ),
            parent=self,
        )
        if not should_reset:
            return

        confirmation = simpledialog.askstring(
            "Confirmar reinicio",
            "Escribe REINICIAR para dejar Yarbis de fabrica.",
            parent=self,
        )
        if confirmation != "REINICIAR":
            self.status_var.set("Reinicio de fabrica cancelado.")
            return

        if self._local_telegram_polling:
            stop_telegram_polling()
            self._local_telegram_polling = False

        result = factory_reset_yarbis()
        self._last_activity_text = ""
        self._last_summary_text = ""
        self._view_has_pending_question = False
        self._first_run_checked = False
        self.reply_text.delete("1.0", "end")
        self._set_text(self.activity_text, "")
        self._apply_theme(get_ui_theme())
        readiness_status(force=True)
        self.refresh_state_view()
        self.status_var.set("Yarbis reiniciado de fabrica.")
        messagebox.showinfo("Yarbis", result, parent=self)
        self.after(250, self._maybe_show_first_run)

    def _handle_telegram_event(self, message):
        if isinstance(message, dict):
            event_type = str(message.get("type", "")).strip().lower()
            label = str(message.get("label", "")).strip() or "Telegram"
            content = str(message.get("content", "")).strip()
            status_text = str(message.get("status_text", "")).strip()

            if event_type == "remote_job_started":
                self._result_queue.put(("remote_start", label, status_text or f"Ejecutando: {label}..."))
                return

            if event_type == "remote_job_finished":
                self._result_queue.put(("remote_success", label, content))
                return

            if event_type == "remote_job_failed":
                self._result_queue.put(("remote_error", label, content or "Error desconocido."))
                return

        rendered = str(message).strip()
        if not rendered:
            return
        if rendered.startswith("Telegram: procesado "):
            return

        self._result_queue.put(("event", "Telegram", rendered))

    def _on_close(self):
        if self._busy:
            should_close = messagebox.askyesno(
                "Cerrar Yarbis",
                "Hay una accion en curso. Quieres cerrar la ventana de todos modos?",
                parent=self,
            )
            if not should_close:
                return
        self._closing = True
        stop_telegram_polling()
        self.destroy()


def main():
    if not _acquire_single_instance_lock():
        _show_already_running_message()
        return

    try:
        clear_activity_for_first_run_if_needed()
        startup_message = run_startup_self_analysis(force=False, background=True)
        app = YarbisDesktop()
        app._append_activity("Autoanalisis inicial", startup_message)
        app._append_activity(
            "Interfaz lista",
            (
                "Ya puedes usar Yarbis sin abrir terminal. "
                "Ejecuta un ciclo, deja un objetivo o responde desde esta ventana."
            ),
        )
        app._append_activity("Estado inicial", get_status_text())
        app.mainloop()
    finally:
        _release_single_instance_lock()


if __name__ == "__main__":
    main()
