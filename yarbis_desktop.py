import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from memory import load_state, render_state_summary
from session import (
    add_task_text,
    get_notification_settings,
    get_status_text,
    get_ui_theme,
    has_pending_user_question,
    run_auto_with_output,
    run_cycle_with_output,
    save_note_text,
    send_test_notification,
    submit_user_reply,
    update_notification_settings,
    update_goal,
    update_profile_text,
    update_ui_theme,
)
from telegram_inbox import start_telegram_polling, stop_telegram_polling

THEMES = {
    "dark": {
        "bg": "#101318",
        "panel": "#171c23",
        "panel_alt": "#1d2430",
        "border": "#313947",
        "fg": "#f2ede3",
        "muted": "#bfae93",
        "field_bg": "#0f141b",
        "field_fg": "#f2ede3",
        "button_bg": "#232b36",
        "button_active": "#2b3441",
        "disabled_bg": "#171c23",
        "disabled_fg": "#6f7883",
        "accent": "#f0a35b",
        "accent_hover": "#f6b372",
        "accent_fg": "#23160b",
        "select_bg": "#42546b",
        "select_fg": "#f8f4ee",
    },
    "light": {
        "bg": "#f3ede2",
        "panel": "#fff9f1",
        "panel_alt": "#efe5d6",
        "border": "#d5c8b8",
        "fg": "#1d232b",
        "muted": "#6f6559",
        "field_bg": "#fffdfa",
        "field_fg": "#1d232b",
        "button_bg": "#ebe1d2",
        "button_active": "#dfd2c0",
        "disabled_bg": "#f0e8dd",
        "disabled_fg": "#9d958a",
        "accent": "#d97a34",
        "accent_hover": "#e38a49",
        "accent_fg": "#fff8f1",
        "select_bg": "#d97a34",
        "select_fg": "#fff8f1",
    },
}

_STATE_SYNC_INTERVAL_MS = 1000


def style_text_widget(widget, palette: dict):
    widget.configure(
        bg=palette["field_bg"],
        fg=palette["field_fg"],
        insertbackground=palette["field_fg"],
        selectbackground=palette["select_bg"],
        selectforeground=palette["select_fg"],
        inactiveselectbackground=palette["select_bg"],
        highlightthickness=1,
        highlightbackground=palette["border"],
        highlightcolor=palette["accent"],
        relief="flat",
        bd=0,
        padx=10,
        pady=10,
    )

    try:
        widget.configure(disabledforeground=palette["field_fg"])
    except tk.TclError:
        pass

    frame = getattr(widget, "frame", None)
    if frame is not None:
        frame.configure(bg=palette["panel"], bd=0, highlightthickness=0)

    vbar = getattr(widget, "vbar", None)
    if vbar is not None:
        try:
            vbar.configure(
                bg=palette["button_bg"],
                activebackground=palette["button_active"],
                troughcolor=palette["panel"],
                highlightbackground=palette["panel"],
                bd=0,
            )
        except tk.TclError:
            pass


class ThemedDialog(simpledialog.Dialog):
    def __init__(self, parent, title: str):
        self.theme_palette = getattr(parent, "theme_palette", THEMES["dark"])
        super().__init__(parent, title)

    def _prepare_body(self, master):
        self.configure(bg=self.theme_palette["bg"])
        master.configure(bg=self.theme_palette["bg"])

    def _style_text_widget(self, widget):
        style_text_widget(widget, self.theme_palette)

    def buttonbox(self):
        box = ttk.Frame(self)
        ok_button = ttk.Button(box, text="Aceptar", command=self.ok, style="Accent.TButton")
        ok_button.pack(side="left", padx=(0, 8))
        ttk.Button(box, text="Cancelar", command=self.cancel).pack(side="left")

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.pack(padx=10, pady=(0, 10), anchor="e")


class MultilineTextDialog(ThemedDialog):
    def __init__(self, parent, title: str, label: str, initial_value: str = "", height: int = 6):
        self.label = label
        self.initial_value = initial_value
        self.height = height
        super().__init__(parent, title)

    def body(self, master):
        self._prepare_body(master)
        ttk.Label(master, text=self.label, anchor="w").grid(
            row=0,
            column=0,
            sticky="w",
            padx=6,
            pady=(6, 4),
        )
        self.text = tk.Text(master, width=72, height=self.height, wrap="word")
        self.text.grid(row=1, column=0, padx=6, pady=(0, 6))
        self._style_text_widget(self.text)
        self.text.insert("1.0", self.initial_value)
        return self.text

    def apply(self):
        self.result = self.text.get("1.0", "end-1c").strip()


class ProfileDialog(ThemedDialog):
    def __init__(self, parent, initial_profile: dict):
        self.initial_profile = initial_profile
        super().__init__(parent, "Editar perfil")

    def body(self, master):
        self._prepare_body(master)
        ttk.Label(master, text="Nombre").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.name_entry = ttk.Entry(master, width=56)
        self.name_entry.grid(row=1, column=0, sticky="ew", padx=6)
        self.name_entry.insert(0, self.initial_profile.get("name", ""))

        ttk.Label(master, text="Rol o contexto").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.role_entry = ttk.Entry(master, width=56)
        self.role_entry.grid(row=3, column=0, sticky="ew", padx=6)
        self.role_entry.insert(0, self.initial_profile.get("role", ""))

        ttk.Label(master, text="Preferencias (coma o salto de linea)").grid(
            row=4,
            column=0,
            sticky="w",
            padx=6,
            pady=(8, 2),
        )
        self.preferences_text = tk.Text(master, width=56, height=4, wrap="word")
        self.preferences_text.grid(row=5, column=0, padx=6)
        self._style_text_widget(self.preferences_text)
        self.preferences_text.insert(
            "1.0",
            "\n".join(self.initial_profile.get("preferences", [])),
        )

        ttk.Label(master, text="Restricciones (coma o salto de linea)").grid(
            row=6,
            column=0,
            sticky="w",
            padx=6,
            pady=(8, 2),
        )
        self.constraints_text = tk.Text(master, width=56, height=4, wrap="word")
        self.constraints_text.grid(row=7, column=0, padx=6, pady=(0, 6))
        self._style_text_widget(self.constraints_text)
        self.constraints_text.insert(
            "1.0",
            "\n".join(self.initial_profile.get("constraints", [])),
        )

        return self.name_entry

    def apply(self):
        self.result = {
            "name": self.name_entry.get().strip(),
            "role": self.role_entry.get().strip(),
            "preferences": self.preferences_text.get("1.0", "end-1c").strip(),
            "constraints": self.constraints_text.get("1.0", "end-1c").strip(),
        }


class NoteDialog(ThemedDialog):
    def body(self, master):
        self._prepare_body(master)
        ttk.Label(master, text="Titulo").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.title_entry = ttk.Entry(master, width=56)
        self.title_entry.grid(row=1, column=0, sticky="ew", padx=6)

        ttk.Label(master, text="Contenido").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.content_text = tk.Text(master, width=56, height=6, wrap="word")
        self.content_text.grid(row=3, column=0, padx=6)
        self._style_text_widget(self.content_text)

        ttk.Label(master, text="Categoria").grid(row=4, column=0, sticky="w", padx=6, pady=(8, 2))
        self.category_entry = ttk.Entry(master, width=56)
        self.category_entry.grid(row=5, column=0, sticky="ew", padx=6, pady=(0, 6))
        self.category_entry.insert(0, "general")
        return self.title_entry

    def apply(self):
        self.result = {
            "title": self.title_entry.get().strip(),
            "content": self.content_text.get("1.0", "end-1c").strip(),
            "category": self.category_entry.get().strip() or "general",
        }


class TaskDialog(ThemedDialog):
    def body(self, master):
        self._prepare_body(master)
        ttk.Label(master, text="Titulo").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.title_entry = ttk.Entry(master, width=56)
        self.title_entry.grid(row=1, column=0, sticky="ew", padx=6)

        ttk.Label(master, text="Detalles").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.details_text = tk.Text(master, width=56, height=5, wrap="word")
        self.details_text.grid(row=3, column=0, padx=6)
        self._style_text_widget(self.details_text)

        ttk.Label(master, text="Prioridad").grid(row=4, column=0, sticky="w", padx=6, pady=(8, 2))
        self.priority_combo = ttk.Combobox(
            master,
            values=("alta", "media", "baja"),
            state="readonly",
            width=20,
        )
        self.priority_combo.grid(row=5, column=0, sticky="w", padx=6, pady=(0, 6))
        self.priority_combo.set("media")
        return self.title_entry

    def apply(self):
        self.result = {
            "title": self.title_entry.get().strip(),
            "details": self.details_text.get("1.0", "end-1c").strip(),
            "priority": self.priority_combo.get().strip() or "media",
        }


class NotificationsDialog(ThemedDialog):
    def __init__(self, parent, initial_settings: dict):
        self.initial_settings = initial_settings
        super().__init__(parent, "Notificaciones")

    def body(self, master):
        self._prepare_body(master)
        ntfy = self.initial_settings.get("ntfy", {})
        if not isinstance(ntfy, dict):
            ntfy = {}

        channels = set(self.initial_settings.get("channels", []))
        self.enabled_var = tk.BooleanVar(value=bool(self.initial_settings.get("enabled", True)))
        self.windows_var = tk.BooleanVar(value="windows" in channels)
        self.ntfy_var = tk.BooleanVar(value="ntfy" in channels)
        self.telegram_var = tk.BooleanVar(value="telegram" in channels)

        ttk.Checkbutton(
            master,
            text="Activar notificaciones",
            variable=self.enabled_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 4))

        ttk.Checkbutton(
            master,
            text="Windows",
            variable=self.windows_var,
        ).grid(row=1, column=0, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(
            master,
            text="iPhone via ntfy",
            variable=self.ntfy_var,
        ).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(
            master,
            text="Telegram",
            variable=self.telegram_var,
        ).grid(row=1, column=2, sticky="w", padx=6, pady=4)

        ttk.Label(master, text="Servidor ntfy").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.server_entry = ttk.Entry(master, width=56)
        self.server_entry.grid(row=3, column=0, columnspan=3, sticky="ew", padx=6)
        self.server_entry.insert(0, ntfy.get("server", "https://ntfy.sh"))

        ttk.Label(master, text="Topic").grid(row=4, column=0, sticky="w", padx=6, pady=(8, 2))
        self.topic_entry = ttk.Entry(master, width=56)
        self.topic_entry.grid(row=5, column=0, columnspan=3, sticky="ew", padx=6)
        self.topic_entry.insert(0, ntfy.get("topic", ""))

        ttk.Label(master, text="Token").grid(row=6, column=0, sticky="w", padx=6, pady=(8, 2))
        self.token_entry = ttk.Entry(master, width=56, show="*")
        self.token_entry.grid(row=7, column=0, columnspan=3, sticky="ew", padx=6)
        self.token_entry.insert(0, ntfy.get("token", ""))

        ttk.Label(master, text="Prioridad").grid(row=8, column=0, sticky="w", padx=6, pady=(8, 2))
        self.priority_combo = ttk.Combobox(
            master,
            values=("", "min", "low", "default", "high", "urgent"),
            state="readonly",
            width=20,
        )
        self.priority_combo.grid(row=9, column=0, sticky="w", padx=6)
        self.priority_combo.set(ntfy.get("priority", ""))

        ttk.Label(master, text="Tags").grid(row=8, column=1, sticky="w", padx=6, pady=(8, 2))
        self.tags_entry = ttk.Entry(master, width=28)
        self.tags_entry.grid(row=9, column=1, sticky="ew", padx=6)
        self.tags_entry.insert(0, ntfy.get("tags", ""))

        telegram = self.initial_settings.get("telegram", {})
        if not isinstance(telegram, dict):
            telegram = {}

        ttk.Label(master, text="Bot token de Telegram").grid(
            row=10,
            column=0,
            sticky="w",
            padx=6,
            pady=(12, 2),
        )
        self.telegram_bot_token_entry = ttk.Entry(master, width=56, show="*")
        self.telegram_bot_token_entry.grid(row=11, column=0, columnspan=3, sticky="ew", padx=6)
        self.telegram_bot_token_entry.insert(0, telegram.get("bot_token", ""))

        ttk.Label(master, text="Chat ID (opcional)").grid(
            row=12,
            column=0,
            sticky="w",
            padx=6,
            pady=(8, 2),
        )
        self.telegram_chat_id_entry = ttk.Entry(master, width=56)
        self.telegram_chat_id_entry.grid(row=13, column=0, columnspan=3, sticky="ew", padx=6)
        self.telegram_chat_id_entry.insert(0, telegram.get("chat_id", ""))

        ttk.Label(
            master,
            text="Si dejas el Chat ID vacio, Yarbis vinculara el primer chat privado que escriba al bot.",
            anchor="w",
        ).grid(row=14, column=0, columnspan=3, sticky="w", padx=6, pady=(4, 6))

        master.columnconfigure(0, weight=1)
        master.columnconfigure(1, weight=1)
        master.columnconfigure(2, weight=1)
        return self.topic_entry

    def apply(self):
        self.result = {
            "enabled": self.enabled_var.get(),
            "windows_enabled": self.windows_var.get(),
            "ntfy_enabled": self.ntfy_var.get(),
            "telegram_enabled": self.telegram_var.get(),
            "ntfy_server": self.server_entry.get().strip(),
            "ntfy_topic": self.topic_entry.get().strip(),
            "ntfy_token": self.token_entry.get().strip(),
            "ntfy_priority": self.priority_combo.get().strip(),
            "ntfy_tags": self.tags_entry.get().strip(),
            "telegram_bot_token": self.telegram_bot_token_entry.get().strip(),
            "telegram_chat_id": self.telegram_chat_id_entry.get().strip(),
        }


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
        self.theme_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Listo.")
        self.theme_button_text = tk.StringVar()

        self._result_queue = queue.Queue()
        self._worker_thread = None
        self._busy = False
        self._busy_sources = set()
        self._action_buttons = []
        self._view_has_pending_question = False
        self._last_summary_text = ""

        self._build_ui()
        self._apply_theme(self.current_theme_name)
        self.refresh_state_view()
        start_telegram_polling(event_callback=self._handle_telegram_event)
        self.after(150, self._poll_worker_queue)
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

        ttk.Label(summary, text="Pendiente").grid(row=3, column=0, sticky="nw", padx=10, pady=(4, 10))
        ttk.Label(summary, textvariable=self.pending_var, wraplength=780).grid(
            row=3,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=(4, 10),
        )

        actions = ttk.LabelFrame(self, text="Acciones")
        actions.grid(row=1, column=0, sticky="ns", padx=(12, 6), pady=6)

        self._pack_action_button(
            ttk.Button(actions, text="Ejecutar ciclo", command=self._run_cycle, style="Accent.TButton"),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Modo autonomo", command=self._run_auto),
        )
        self._pack_action_button(
            ttk.Button(actions, textvariable=self.theme_button_text, command=self._toggle_theme),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Notificaciones", command=self._edit_notifications),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Probar notificacion", command=self._send_test_notification),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Cambiar objetivo", command=self._change_goal),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Editar perfil", command=self._edit_profile),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Guardar nota", command=self._save_note),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Crear tarea", command=self._create_task),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Refrescar estado", command=self.refresh_state_view),
        )
        self._pack_action_button(
            ttk.Button(actions, text="Limpiar actividad", command=self._clear_activity),
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
        self.summary_text = ScrolledText(summary_frame, wrap="word", height=16)
        self.summary_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.summary_text.configure(state="disabled")

        activity_frame = ttk.LabelFrame(main_panel, text="Actividad")
        activity_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        activity_frame.columnconfigure(0, weight=1)
        activity_frame.rowconfigure(0, weight=1)
        self.activity_text = ScrolledText(activity_frame, wrap="word", height=18)
        self.activity_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
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

        status_bar = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _pack_action_button(self, button):
        button.pack(fill="x", padx=10, pady=6)
        self._action_buttons.append(button)

    def _apply_theme(self, theme_name: str):
        self.current_theme_name = theme_name if theme_name in THEMES else "dark"
        self.theme_palette = THEMES[self.current_theme_name]
        palette = self.theme_palette

        self.style.theme_use("clam")
        self.configure(bg=palette["bg"])

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
        self.style.configure(
            "TScrollbar",
            background=palette["button_bg"],
            troughcolor=palette["panel"],
            bordercolor=palette["panel"],
            arrowcolor=palette["fg"],
        )

        self.option_add("*TCombobox*Listbox*Background", palette["field_bg"])
        self.option_add("*TCombobox*Listbox*Foreground", palette["field_fg"])
        self.option_add("*TCombobox*Listbox*selectBackground", palette["select_bg"])
        self.option_add("*TCombobox*Listbox*selectForeground", palette["select_fg"])

        style_text_widget(self.summary_text, palette)
        style_text_widget(self.activity_text, palette)
        style_text_widget(self.reply_text, palette)

        self.theme_var.set("Oscuro" if self.current_theme_name == "dark" else "Claro")
        self.theme_button_text.set(
            "Usar modo claro" if self.current_theme_name == "dark" else "Usar modo oscuro"
        )

    def _set_text(self, widget, content: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _append_activity(self, title: str, content: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        rendered = f"[{timestamp}] {title}\n{content.strip() or 'Sin salida adicional.'}\n\n"
        self.activity_text.configure(state="normal")
        self.activity_text.insert("end", rendered)
        self.activity_text.see("end")
        self.activity_text.configure(state="disabled")

    def refresh_state_view(self):
        state = load_state()
        self.goal_var.set(state["goal"])
        self.cycles_var.set(str(state["cycle_count"]))

        pending_question = state["awaiting_user_input"].get("question", "").strip()
        self._view_has_pending_question = has_pending_user_question(state)
        if self._view_has_pending_question:
            self.pending_var.set(pending_question)
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

    def _sync_state_view(self):
        self.refresh_state_view()
        self.after(_STATE_SYNC_INTERVAL_MS, self._sync_state_view)

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
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.")
            return

        self._set_busy(True, f"Ejecutando: {label}...")

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

    def _create_task(self):
        dialog = TaskDialog(self, "Crear tarea")
        if dialog.result is None:
            return

        result = add_task_text(**dialog.result)
        self._append_activity("Tarea", result)
        self.refresh_state_view()

    def _send_reply(self):
        reply_text = self.reply_text.get("1.0", "end-1c").strip()
        if not reply_text:
            messagebox.showinfo("Yarbis", "Escribe una respuesta o contexto antes de enviarlo.")
            return

        displayed_pending = self._view_has_pending_question
        actual_pending = has_pending_user_question(load_state())
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
        self._set_text(self.activity_text, "")

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
        stop_telegram_polling()
        self.destroy()


def main():
    app = YarbisDesktop()
    app._append_activity(
        "Interfaz lista",
        (
            "Ya puedes usar Yarbis sin abrir terminal. "
            "Ejecuta un ciclo, deja un objetivo o responde desde esta ventana."
        ),
    )
    app._append_activity("Estado inicial", get_status_text())
    app.mainloop()


if __name__ == "__main__":
    main()
