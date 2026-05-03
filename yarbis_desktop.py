import ctypes
import os
import sys
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk

import activity
from memory import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    MAX_OLLAMA_TIMEOUT_SECONDS,
    MIN_OLLAMA_TIMEOUT_SECONDS,
    load_state,
    render_state_summary,
)
from session import (
    add_task_text,
    delete_note_text,
    get_notification_settings,
    get_ollama_settings,
    get_service_proactive_settings,
    get_status_text,
    get_ui_theme,
    has_pending_user_question,
    run_auto_with_output,
    run_cycle_with_output,
    run_startup_self_analysis,
    save_note_text,
    send_test_notification,
    submit_user_reply,
    update_notification_settings,
    update_goal,
    update_ollama_settings,
    update_profile_text,
    update_service_proactive_settings,
    update_ui_theme,
)
from service_manager import (
    get_service_status,
    remove_service,
    set_autostart_enabled,
    start_service,
    stop_service,
)
from telegram_inbox import start_telegram_polling, stop_telegram_polling

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


THEMES = {
    "dark": {
        "bg": "#000000",
        "panel": "#05080c",
        "panel_alt": "#08131a",
        "border": "#123746",
        "fg": "#f4fbff",
        "muted": "#8fb8c8",
        "field_bg": "#030609",
        "field_fg": "#f4fbff",
        "button_bg": "#071016",
        "button_active": "#0b1d27",
        "disabled_bg": "#05080c",
        "disabled_fg": "#51616a",
        "accent": "#00d9ff",
        "accent_hover": "#54e8ff",
        "accent_fg": "#001116",
        "secondary": "#00ff88",
        "secondary_hover": "#5dffb2",
        "secondary_fg": "#00150b",
        "danger": "#ff3b5c",
        "danger_hover": "#ff6b82",
        "danger_fg": "#190006",
        "select_bg": "#004f63",
        "select_fg": "#f4fbff",
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
        "secondary": "#177b4d",
        "secondary_hover": "#20915d",
        "secondary_fg": "#fffdfa",
        "danger": "#c7364d",
        "danger_hover": "#d64c61",
        "danger_fg": "#fffdfa",
        "select_bg": "#d97a34",
        "select_fg": "#fff8f1",
    },
}

_STATE_SYNC_INTERVAL_MS = 1000


def style_scrollbar_widget(scrollbar, palette: dict):
    try:
        scrollbar.configure(style="Yarbis.Vertical.TScrollbar")
    except tk.TclError:
        try:
            scrollbar.configure(
                bg=palette["button_bg"],
                activebackground=palette["button_active"],
                troughcolor=palette["panel"],
                highlightbackground=palette["panel"],
                bd=0,
                relief="flat",
                width=14,
            )
        except tk.TclError:
            pass


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
        style_scrollbar_widget(vbar, palette)


def style_listbox_widget(widget, palette: dict):
    widget.configure(
        bg=palette["field_bg"],
        fg=palette["field_fg"],
        selectbackground=palette["select_bg"],
        selectforeground=palette["select_fg"],
        highlightthickness=1,
        highlightbackground=palette["border"],
        highlightcolor=palette["accent"],
        relief="flat",
        bd=0,
    )


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


class NotesDialog(ThemedDialog):
    def __init__(self, parent):
        self.notes = []
        self.activity_messages = []
        super().__init__(parent, "Notas")

    def body(self, master):
        self._prepare_body(master)
        master.columnconfigure(0, weight=0)
        master.columnconfigure(1, weight=1)
        master.rowconfigure(0, weight=1)

        list_frame = tk.Frame(master, bd=0, highlightthickness=0)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=(6, 4), pady=6)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        list_frame.configure(bg=self.theme_palette["bg"])

        self.notes_list = tk.Listbox(
            list_frame,
            width=38,
            height=18,
            activestyle="dotbox",
            exportselection=False,
        )
        self.notes_list.grid(row=0, column=0, sticky="nsew")
        style_listbox_widget(self.notes_list, self.theme_palette)
        self.notes_list.bind("<<ListboxSelect>>", self._show_selected_note)

        self.notes_scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.notes_list.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        self.notes_scrollbar.grid(row=0, column=1, sticky="ns")
        self.notes_list.configure(yscrollcommand=self.notes_scrollbar.set)
        style_scrollbar_widget(self.notes_scrollbar, self.theme_palette)

        detail_frame = tk.Frame(master, bd=0, highlightthickness=0)
        detail_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 6), pady=6)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.configure(bg=self.theme_palette["bg"])

        self.detail_text = tk.Text(detail_frame, width=58, height=18, wrap="word")
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        self._style_text_widget(self.detail_text)
        self.detail_text.configure(state="disabled")

        self.detail_scrollbar = ttk.Scrollbar(
            detail_frame,
            orient="vertical",
            command=self.detail_text.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        self.detail_scrollbar.grid(row=0, column=1, sticky="ns")
        self.detail_text.configure(yscrollcommand=self.detail_scrollbar.set)
        style_scrollbar_widget(self.detail_scrollbar, self.theme_palette)

        self._refresh_notes()
        return self.notes_list

    def buttonbox(self):
        box = ttk.Frame(self)
        self.new_button = ttk.Button(
            box,
            text="Nueva nota",
            command=self._new_note,
            style="Accent.TButton",
        )
        self.new_button.pack(side="left", padx=(0, 8))
        self.delete_button = ttk.Button(
            box,
            text="Eliminar",
            command=self._delete_selected_note,
            style="Danger.TButton",
        )
        self.delete_button.pack(side="left", padx=(0, 8))
        ttk.Button(box, text="Refrescar", command=self._refresh_notes).pack(side="left", padx=(0, 8))
        ttk.Button(box, text="Cerrar", command=self.ok).pack(side="left")

        self.bind("<Escape>", self.cancel)
        box.pack(padx=10, pady=(0, 10), anchor="e")
        self._sync_delete_button()

    def _set_detail_text(self, content: str):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", content)
        self.detail_text.configure(state="disabled")

    def _render_note(self, note: dict) -> str:
        content = note.get("content", "").strip() or "Sin contenido."
        return (
            f"[{note.get('id', '')}] {note.get('title', 'Nota sin titulo')}\n"
            f"Categoria: {note.get('category', 'general')}\n\n"
            f"{content}"
        )

    def _selected_note(self) -> dict | None:
        selection = self.notes_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self.notes):
            return None
        return self.notes[index]

    def _sync_delete_button(self):
        if hasattr(self, "delete_button"):
            state = "normal" if self._selected_note() else "disabled"
            self.delete_button.configure(state=state)

    def _show_selected_note(self, _event=None):
        note = self._selected_note()
        if not note:
            self._set_detail_text("Selecciona una nota para verla completa.")
            self._sync_delete_button()
            return

        self._set_detail_text(self._render_note(note))
        self._sync_delete_button()

    def _refresh_notes(self):
        selected_id = ""
        selected_note = self._selected_note() if hasattr(self, "notes_list") else None
        if selected_note:
            selected_id = selected_note.get("id", "")

        state = load_state()
        self.notes = list(reversed(state.get("notes", [])))
        self.notes_list.delete(0, "end")
        for note in self.notes:
            self.notes_list.insert(
                "end",
                f"[{note.get('id', '')}] {note.get('title', 'Nota sin titulo')} ({note.get('category', 'general')})",
            )

        if not self.notes:
            self._set_detail_text("No hay notas guardadas.")
            self._sync_delete_button()
            return

        next_index = 0
        if selected_id:
            for index, note in enumerate(self.notes):
                if note.get("id", "") == selected_id:
                    next_index = index
                    break

        self.notes_list.selection_clear(0, "end")
        self.notes_list.selection_set(next_index)
        self.notes_list.activate(next_index)
        self.notes_list.see(next_index)
        self._show_selected_note()

    def _new_note(self):
        dialog = NoteDialog(self, "Guardar nota")
        if dialog.result is None:
            return

        result = save_note_text(**dialog.result)
        self.activity_messages.append(result)
        self._refresh_notes()

    def _delete_selected_note(self):
        note = self._selected_note()
        if not note:
            return

        should_delete = messagebox.askyesno(
            "Eliminar nota",
            f"Quieres eliminar la nota '{note.get('title', 'Nota sin titulo')}'?",
            parent=self,
        )
        if not should_delete:
            return

        result = delete_note_text(note.get("id", ""))
        self.activity_messages.append(result)
        self._refresh_notes()

    def apply(self):
        self.result = "\n".join(self.activity_messages).strip()


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


class ServicePulseDialog(ThemedDialog):
    def __init__(self, parent, initial_settings: dict):
        self.initial_settings = initial_settings
        super().__init__(parent, "Pulso proactivo")

    def body(self, master):
        self._prepare_body(master)
        self.enabled_var = tk.BooleanVar(value=bool(self.initial_settings.get("enabled", True)))
        interval_seconds = self._safe_int(
            self.initial_settings.get("interval_seconds", 1800),
            1800,
        )
        interval_minutes = max(1, round(interval_seconds / 60))
        self.interval_minutes_var = tk.StringVar(value=str(interval_minutes))
        self.cycles_var = tk.StringVar(value=str(self.initial_settings.get("cycles", 1)))
        self.start_delay_var = tk.StringVar(value=str(self.initial_settings.get("start_delay_seconds", 60)))
        self.preview_var = tk.StringVar()
        self._controlled_widgets = []

        container = ttk.Frame(master)
        container.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        container.columnconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="Pulso proactivo",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self.enabled_check = ttk.Checkbutton(
            header,
            text="Activo",
            variable=self.enabled_var,
            command=self._sync_enabled_state,
        )
        self.enabled_check.grid(row=0, column=1, sticky="e", padx=(12, 0))

        ttk.Label(
            header,
            textvariable=self.preview_var,
            foreground=self.theme_palette["muted"],
            wraplength=430,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        rhythm = ttk.LabelFrame(container, text="Ritmo")
        rhythm.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        rhythm.columnconfigure(0, weight=1)
        rhythm.columnconfigure(1, weight=1)

        ttk.Label(rhythm, text="Intervalo").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 2))
        interval_row = ttk.Frame(rhythm)
        interval_row.grid(row=1, column=0, sticky="w", padx=10)
        self.interval_spin = ttk.Spinbox(
            interval_row,
            from_=1,
            to=1440,
            increment=5,
            width=8,
            textvariable=self.interval_minutes_var,
            style="Yarbis.TSpinbox",
        )
        self.interval_spin.pack(side="left")
        ttk.Label(interval_row, text="min").pack(side="left", padx=(6, 0))

        presets = ttk.Frame(rhythm)
        presets.grid(row=2, column=0, sticky="w", padx=10, pady=(8, 10))
        self.preset_buttons = []
        for label, minutes in (("15 min", 15), ("30 min", 30), ("1 h", 60), ("4 h", 240)):
            button = ttk.Button(
                presets,
                text=label,
                width=7,
                command=lambda value=minutes: self._set_interval_minutes(value),
            )
            button.pack(side="left", padx=(0, 5))
            self.preset_buttons.append(button)

        ttk.Label(rhythm, text="Ciclos por pulso").grid(row=0, column=1, sticky="w", padx=10, pady=(10, 2))
        self.cycles_spin = ttk.Spinbox(
            rhythm,
            from_=1,
            to=5,
            increment=1,
            width=8,
            textvariable=self.cycles_var,
            style="Yarbis.TSpinbox",
        )
        self.cycles_spin.grid(row=1, column=1, sticky="w", padx=10)

        startup = ttk.LabelFrame(container, text="Arranque")
        startup.grid(row=2, column=0, sticky="ew")
        startup.columnconfigure(0, weight=1)

        ttk.Label(startup, text="Espera inicial").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 2))
        delay_row = ttk.Frame(startup)
        delay_row.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))
        self.start_delay_spin = ttk.Spinbox(
            delay_row,
            from_=0,
            to=86400,
            increment=30,
            width=8,
            textvariable=self.start_delay_var,
            style="Yarbis.TSpinbox",
        )
        self.start_delay_spin.pack(side="left")
        ttk.Label(delay_row, text="s").pack(side="left", padx=(6, 0))

        self._controlled_widgets = [
            self.interval_spin,
            self.cycles_spin,
            self.start_delay_spin,
            *self.preset_buttons,
        ]
        for variable in (self.interval_minutes_var, self.cycles_var, self.start_delay_var):
            variable.trace_add("write", lambda *_args: self._refresh_preview())

        self._sync_enabled_state()
        return self.interval_spin

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds <= 0:
            return "sin espera"
        if seconds % 3600 == 0:
            return f"{seconds // 3600} h"
        if seconds % 60 == 0:
            return f"{seconds // 60} min"
        return f"{seconds}s"

    def _set_interval_minutes(self, minutes: int):
        self.interval_minutes_var.set(str(minutes))

    def _refresh_preview(self):
        interval_minutes = max(1, self._safe_int(self.interval_minutes_var.get(), 30))
        interval_seconds = interval_minutes * 60
        cycles = max(1, min(5, self._safe_int(self.cycles_var.get(), 1)))
        start_delay = max(0, self._safe_int(self.start_delay_var.get(), 60))
        status = "Activo" if self.enabled_var.get() else "Desactivado"
        self.preview_var.set(
            f"{status} | {cycles} ciclo(s) cada "
            f"{self._format_duration(interval_seconds)} | "
            f"arranque {self._format_duration(start_delay)}"
        )

    def _sync_enabled_state(self):
        widget_state = "normal" if self.enabled_var.get() else "disabled"
        for widget in getattr(self, "_controlled_widgets", []):
            widget.configure(state=widget_state)
        self._refresh_preview()

    def apply(self):
        interval_minutes = self._safe_int(self.interval_minutes_var.get().strip(), 30)
        self.result = {
            "enabled": self.enabled_var.get(),
            "interval_seconds": str(max(1, interval_minutes) * 60),
            "cycles": self.cycles_var.get().strip(),
            "start_delay_seconds": self.start_delay_var.get().strip(),
        }


class OllamaSettingsDialog(ThemedDialog):
    def __init__(self, parent, initial_settings: dict):
        self.initial_settings = initial_settings
        super().__init__(parent, "Modelo y timeout")

    def body(self, master):
        self._prepare_body(master)
        model = str(self.initial_settings.get("model", DEFAULT_OLLAMA_MODEL)).strip()
        timeout_seconds = str(
            self.initial_settings.get(
                "timeout_seconds",
                DEFAULT_OLLAMA_TIMEOUT_SECONDS,
            )
        )

        ttk.Label(master, text="Modelo").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.model_entry = ttk.Entry(master, width=44)
        self.model_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6)
        self.model_entry.insert(0, model or DEFAULT_OLLAMA_MODEL)

        ttk.Label(master, text="Timeout").grid(row=2, column=0, sticky="w", padx=6, pady=(10, 2))
        timeout_row = ttk.Frame(master)
        timeout_row.grid(row=3, column=0, sticky="w", padx=6, pady=(0, 6))
        self.timeout_spin = ttk.Spinbox(
            timeout_row,
            from_=MIN_OLLAMA_TIMEOUT_SECONDS,
            to=MAX_OLLAMA_TIMEOUT_SECONDS,
            increment=30,
            width=10,
            style="Yarbis.TSpinbox",
        )
        self.timeout_spin.pack(side="left")
        self.timeout_spin.delete(0, "end")
        self.timeout_spin.insert(0, timeout_seconds)
        ttk.Label(timeout_row, text="s").pack(side="left", padx=(6, 0))

        if os.getenv("YARBIS_MODEL") or os.getenv("YARBIS_OLLAMA_TIMEOUT_SECONDS"):
            ttk.Label(
                master,
                text="Hay variables de entorno YARBIS_* activas; esas pueden tener prioridad.",
                foreground=self.theme_palette["muted"],
                wraplength=360,
            ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 6))

        master.columnconfigure(0, weight=1)
        return self.model_entry

    def apply(self):
        self.result = {
            "model": self.model_entry.get().strip(),
            "timeout_seconds": self.timeout_spin.get().strip(),
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
        self.thinking_var = tk.StringVar(value="No.")
        self.theme_var = tk.StringVar()
        self.ollama_var = tk.StringVar()
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
        self._local_telegram_polling = False

        self._build_ui()
        self._apply_theme(self.current_theme_name)
        self.refresh_state_view()
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

        ttk.Label(summary, text="Ollama").grid(row=3, column=0, sticky="w", padx=10, pady=4)
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

        ttk.Label(summary, text="Pensando").grid(row=5, column=0, sticky="nw", padx=10, pady=4)
        ttk.Label(summary, textvariable=self.thinking_var, wraplength=780).grid(
            row=5,
            column=1,
            sticky="nw",
            padx=(0, 10),
            pady=4,
        )

        ttk.Label(summary, text="Pendiente").grid(row=6, column=0, sticky="nw", padx=10, pady=(4, 10))
        ttk.Label(summary, textvariable=self.pending_var, wraplength=780).grid(
            row=6,
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
            "Comunicacion",
            (
                {"text": "Notificaciones", "command": self._edit_notifications},
                {"text": "Probar notificacion", "command": self._send_test_notification},
            ),
        )
        self._build_service_group(actions)
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

        status_bar = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))

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
            self._pack_action_button(ttk.Button(group, **button_options))

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

        self.remove_service_button = ttk.Button(
            group,
            text="Quitar de SCM",
            command=self._remove_service,
            style="Danger.TButton",
        )
        self._pack_action_button(self.remove_service_button)

    def _pack_action_button(self, button):
        button.pack(fill="x", padx=8, pady=3)
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
        content = activity.read_activity_log()
        if content == self._last_activity_text:
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

    def refresh_state_view(self):
        state = load_state()
        self.goal_var.set(state["goal"])
        self.cycles_var.set(str(state["cycle_count"]))
        ollama_settings = state.get("ollama", {})
        self.ollama_var.set(
            f"{ollama_settings.get('model', DEFAULT_OLLAMA_MODEL)} "
            f"({ollama_settings.get('timeout_seconds', DEFAULT_OLLAMA_TIMEOUT_SECONDS)}s)"
        )

        service_status = get_service_status()
        proactive_settings = state["service"]["proactive"]
        pulse_status = "activo" if proactive_settings["enabled"] else "desactivado"
        pulse_text = (
            f"Pulso {pulse_status}: {proactive_settings['cycles']} ciclo(s) cada "
            f"{proactive_settings['interval_seconds']}s."
        )
        if not service_status["installed"]:
            self.service_var.set(f"No instalado en SCM. {pulse_text}")
            self.service_button_text.set("Instalar e iniciar")
        elif service_status["running"]:
            self.service_var.set(
                f"Activo en SCM (PID {service_status['pid']}, arranque={service_status['start_type']}). "
                f"{pulse_text}"
            )
            self.service_button_text.set("Detener servicio")
        else:
            self.service_var.set(
                f"Instalado en SCM, detenido (arranque={service_status['start_type']}). "
                f"{pulse_text}"
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

    def _sync_state_view(self):
        self.refresh_state_view()
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
        dialog = OllamaSettingsDialog(self, initial_settings=get_ollama_settings())
        if dialog.result is None:
            return

        try:
            result = update_ollama_settings(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        self._append_activity("Ollama", result)
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

    def _toggle_service(self):
        service_status = get_service_status()
        if service_status["running"]:
            self._start_background_job("Servicio", stop_service)
            return

        if self._local_telegram_polling:
            stop_telegram_polling()
            self._local_telegram_polling = False
        self._start_background_job("Servicio", start_service)

    def _toggle_service_autostart(self):
        service_status = get_service_status()
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
        service_status = get_service_status()
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

        self._start_background_job("Quitar servicio", remove_service)

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

    def _manage_notes(self):
        dialog = NotesDialog(self)
        if dialog.result:
            self._append_activity("Notas", dialog.result)
        self.refresh_state_view()

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
        activity.clear_activity_log()
        self._last_activity_text = ""
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
    if not _acquire_single_instance_lock():
        _show_already_running_message()
        return

    try:
        startup_message = run_startup_self_analysis()
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
