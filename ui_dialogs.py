import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from memory import DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_TIMEOUT_SECONDS, load_state
from session import delete_note_text, save_note_text
from ui_theme import THEMES, style_listbox_widget, style_scrollbar_widget, style_text_widget


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


class SocialOAuthDialog(ThemedDialog):
    def __init__(self, parent):
        super().__init__(parent, "Conectar red social")

    def body(self, master):
        self._prepare_body(master)
        master.columnconfigure(1, weight=1)

        self.provider_var = tk.StringVar(value="meta")
        self.open_browser_var = tk.BooleanVar(value=True)

        ttk.Label(master, text="Proveedor").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.provider_combo = ttk.Combobox(
            master,
            textvariable=self.provider_var,
            values=("meta", "linkedin"),
            state="readonly",
            width=18,
        )
        self.provider_combo.grid(row=0, column=1, sticky="w", padx=6, pady=(6, 2))

        ttk.Label(master, text="Client/App ID").grid(row=1, column=0, sticky="w", padx=6, pady=(8, 2))
        self.client_id_entry = ttk.Entry(master, width=62)
        self.client_id_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 2))

        ttk.Label(master, text="Client/App Secret").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.client_secret_entry = ttk.Entry(master, width=62, show="*")
        self.client_secret_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=(8, 2))

        ttk.Label(master, text="Redirect URI opcional").grid(row=3, column=0, sticky="w", padx=6, pady=(8, 2))
        self.redirect_entry = ttk.Entry(master, width=62)
        self.redirect_entry.grid(row=3, column=1, sticky="ew", padx=6, pady=(8, 2))

        ttk.Label(master, text="Scopes opcionales").grid(row=4, column=0, sticky="w", padx=6, pady=(8, 2))
        self.scopes_entry = ttk.Entry(master, width=62)
        self.scopes_entry.grid(row=4, column=1, sticky="ew", padx=6, pady=(8, 2))

        ttk.Label(master, text="Callback pegado").grid(row=5, column=0, sticky="nw", padx=6, pady=(8, 2))
        self.callback_text = tk.Text(master, width=62, height=3, wrap="word")
        self.callback_text.grid(row=5, column=1, sticky="ew", padx=6, pady=(8, 2))
        self._style_text_widget(self.callback_text)

        ttk.Checkbutton(
            master,
            text="Abrir navegador para autorizar",
            variable=self.open_browser_var,
        ).grid(row=6, column=1, sticky="w", padx=6, pady=(8, 8))

        return self.client_id_entry

    def apply(self):
        self.result = {
            "provider": self.provider_var.get().strip(),
            "client_id": self.client_id_entry.get().strip(),
            "client_secret": self.client_secret_entry.get().strip(),
            "redirect_uri": self.redirect_entry.get().strip(),
            "scopes": self.scopes_entry.get().strip(),
            "authorization_response_url": self.callback_text.get("1.0", "end-1c").strip(),
            "open_browser": self.open_browser_var.get(),
        }


class FirstRunDialog(ThemedDialog):
    GOAL_TEMPLATES = (
        "Organizar mis tareas y avanzar el siguiente paso importante",
        "Mejorar este proyecto y dejarlo listo para usar",
        "Investigar informacion publica y resumir decisiones accionables",
        "Crear un sistema de notas y seguimiento por Telegram",
    )

    def __init__(self, parent, initial_state: dict):
        self.initial_state = initial_state
        super().__init__(parent, "Primer uso de Yarbis")

    def body(self, master):
        self._prepare_body(master)
        master.columnconfigure(0, weight=1)

        state_goal = str(self.initial_state.get("goal", "")).strip()
        profile = self.initial_state.get("profile", {})
        if not isinstance(profile, dict):
            profile = {}
        ollama = self.initial_state.get("ollama", {})
        if not isinstance(ollama, dict):
            ollama = {}

        ttk.Label(
            master,
            text="Objetivo principal",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))

        self.goal_text = tk.Text(master, width=68, height=4, wrap="word")
        self.goal_text.grid(row=1, column=0, sticky="ew", padx=6)
        self._style_text_widget(self.goal_text)
        self.goal_text.insert("1.0", state_goal)

        ttk.Label(master, text="Plantillas rapidas").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.template_combo = ttk.Combobox(
            master,
            values=self.GOAL_TEMPLATES,
            state="readonly",
            width=64,
        )
        self.template_combo.grid(row=3, column=0, sticky="ew", padx=6)
        self.template_combo.bind("<<ComboboxSelected>>", self._apply_template)

        identity = ttk.LabelFrame(master, text="Contexto minimo")
        identity.grid(row=4, column=0, sticky="ew", padx=6, pady=(10, 0))
        identity.columnconfigure(0, weight=1)

        ttk.Label(identity, text="Nombre opcional").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.name_entry = ttk.Entry(identity, width=54)
        self.name_entry.grid(row=1, column=0, sticky="ew", padx=8)
        self.name_entry.insert(0, profile.get("name", ""))

        ttk.Label(identity, text="Rol o contexto opcional").grid(row=2, column=0, sticky="w", padx=8, pady=(8, 2))
        self.role_entry = ttk.Entry(identity, width=54)
        self.role_entry.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.role_entry.insert(0, profile.get("role", ""))

        model_frame = ttk.LabelFrame(master, text="Modelo Ollama")
        model_frame.grid(row=5, column=0, sticky="ew", padx=6, pady=(10, 0))
        model_frame.columnconfigure(0, weight=1)

        ttk.Label(model_frame, text="Modelo Ollama").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.model_entry = ttk.Entry(model_frame, width=42)
        self.model_entry.grid(row=1, column=0, sticky="ew", padx=8)
        self.model_entry.insert(0, str(ollama.get("model", DEFAULT_OLLAMA_MODEL)).strip() or DEFAULT_OLLAMA_MODEL)

        ttk.Label(model_frame, text="Timeout en segundos").grid(row=0, column=1, sticky="w", padx=8, pady=(8, 2))
        self.timeout_entry = ttk.Entry(model_frame, width=12)
        self.timeout_entry.grid(row=1, column=1, sticky="w", padx=8, pady=(0, 8))
        self.timeout_entry.insert(0, str(ollama.get("timeout_seconds", DEFAULT_OLLAMA_TIMEOUT_SECONDS)))

        ttk.Label(model_frame, text="Host opcional").grid(row=2, column=0, sticky="w", padx=8, pady=(4, 2))
        self.host_entry = ttk.Entry(model_frame, width=42)
        self.host_entry.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        self.host_entry.insert(0, str(ollama.get("host", DEFAULT_OLLAMA_HOST)).strip())

        options = ttk.LabelFrame(master, text="Al guardar")
        options.grid(row=6, column=0, sticky="ew", padx=6, pady=(10, 6))
        self.run_first_cycle_var = tk.BooleanVar(value=True)
        self.open_notifications_var = tk.BooleanVar(value=False)
        self.install_service_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="Ejecutar el primer ciclo",
            variable=self.run_first_cycle_var,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        ttk.Checkbutton(
            options,
            text="Abrir configuracion de Telegram",
            variable=self.open_notifications_var,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(
            options,
            text="Instalar/iniciar servicio de fondo",
            variable=self.install_service_var,
        ).grid(row=2, column=0, sticky="w", padx=8, pady=(2, 8))

        return self.goal_text

    def _apply_template(self, _event=None):
        selected = self.template_combo.get().strip()
        if not selected:
            return
        self.goal_text.delete("1.0", "end")
        self.goal_text.insert("1.0", selected)

    def validate(self):
        if not self.goal_text.get("1.0", "end-1c").strip():
            messagebox.showwarning("Yarbis", "Define un objetivo para empezar.", parent=self)
            return False
        if not self.model_entry.get().strip():
            messagebox.showwarning("Yarbis", "El modelo Ollama no puede quedar vacio.", parent=self)
            return False
        try:
            int(self.timeout_entry.get().strip())
        except ValueError:
            messagebox.showwarning("Yarbis", "El timeout debe ser un numero de segundos.", parent=self)
            return False
        return True

    def apply(self):
        self.result = {
            "goal": self.goal_text.get("1.0", "end-1c").strip(),
            "name": self.name_entry.get().strip(),
            "role": self.role_entry.get().strip(),
            "model": self.model_entry.get().strip(),
            "timeout_seconds": self.timeout_entry.get().strip(),
            "host": self.host_entry.get().strip(),
            "run_first_cycle": self.run_first_cycle_var.get(),
            "open_notifications": self.open_notifications_var.get(),
            "install_service": self.install_service_var.get(),
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


class MemoryImportModeDialog(ThemedDialog):
    MODE_LABELS = {
        "Reemplazar memoria": "replace",
        "Fusionar recuerdos": "merge",
    }

    def __init__(self, parent, backup_summary: str):
        self.backup_summary = backup_summary
        super().__init__(parent, "Trasplantar memoria")

    def body(self, master):
        self._prepare_body(master)
        master.columnconfigure(0, weight=1)

        ttk.Label(master, text="Respaldo seleccionado").grid(
            row=0,
            column=0,
            sticky="w",
            padx=6,
            pady=(6, 2),
        )
        self.summary_text = tk.Text(master, width=72, height=9, wrap="word")
        self.summary_text.grid(row=1, column=0, sticky="ew", padx=6)
        self._style_text_widget(self.summary_text)
        self.summary_text.insert("1.0", self.backup_summary)
        self.summary_text.configure(state="disabled")

        ttk.Label(master, text="Modo de trasplante").grid(
            row=2,
            column=0,
            sticky="w",
            padx=6,
            pady=(10, 2),
        )
        self.mode_combo = ttk.Combobox(
            master,
            values=tuple(self.MODE_LABELS.keys()),
            state="readonly",
            width=28,
        )
        self.mode_combo.grid(row=3, column=0, sticky="w", padx=6, pady=(0, 6))
        self.mode_combo.set("Reemplazar memoria")

        ttk.Label(
            master,
            text=(
                "Reemplazar crea un respaldo local previo y sustituye state.json. "
                "Fusionar conserva configuracion local y combina perfil, notas, tareas, mensajes y plan."
            ),
            wraplength=520,
        ).grid(row=4, column=0, sticky="ew", padx=6, pady=(0, 6))
        return self.mode_combo

    def apply(self):
        self.result = self.MODE_LABELS.get(self.mode_combo.get(), "replace")


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
