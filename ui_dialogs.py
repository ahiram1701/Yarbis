import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from urllib.parse import urlparse

from memory import (
    DEFAULT_OLLAMA_API_KEY_ENV_VAR,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_OPENROUTER_API_KEY_ENV_VAR,
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    load_state,
)
from session import (
    create_idea_project_text,
    delete_note_text,
    promote_idea_project_to_work_text,
    save_note_text,
    update_idea_project_text,
)
from ui_theme import THEMES, style_listbox_widget, style_scrollbar_widget, style_text_widget


def _host_uses_ollama_cloud(host: str) -> bool:
    hostname = urlparse(str(host).strip()).hostname or ""
    return hostname.lower().endswith("ollama.com")


class ThemedDialog(simpledialog.Dialog):
    def __init__(self, parent, title: str):
        self.theme_palette = getattr(parent, "theme_palette", THEMES["dark"])
        super().__init__(parent, title)

    def _prepare_body(self, master):
        self.configure(bg=self.theme_palette["bg"])
        master.configure(bg=self.theme_palette["bg"])
        try:
            self.resizable(True, True)
        except tk.TclError:
            pass

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

        ttk.Label(master, text="Preferencias (coma o salto de línea)").grid(
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

        ttk.Label(master, text="Restricciones (coma o salto de línea)").grid(
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
        "Explorar y planificar ideas de producto o negocio hasta convertirlas en proximos pasos",
        "Organizar mis proyectos personales con claridad, prioridades y seguimiento",
        "Convertir ideas borrosas en briefs, alternativas creativas, decisiones y tareas",
        "Mejorar este proyecto y dejarlo listo para usar",
    )

    def __init__(self, parent, initial_state: dict):
        self.initial_state = initial_state
        self._loaded_provider = ""
        super().__init__(parent, "Primer uso de Yarbis")

    def _initial_provider_settings(self, provider: str) -> dict:
        if provider == MODEL_PROVIDER_OPENROUTER:
            defaults = {
                "model": DEFAULT_OPENROUTER_MODEL,
                "fallback_models": [],
                "host": DEFAULT_OPENROUTER_HOST,
                "api_key": "",
                "api_key_env_var": DEFAULT_OPENROUTER_API_KEY_ENV_VAR,
                "timeout_seconds": DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
            }
        else:
            defaults = {
                "model": DEFAULT_OLLAMA_MODEL,
                "fallback_models": [],
                "host": DEFAULT_OLLAMA_HOST,
                "api_key": "",
                "api_key_env_var": DEFAULT_OLLAMA_API_KEY_ENV_VAR,
                "timeout_seconds": DEFAULT_OLLAMA_TIMEOUT_SECONDS,
            }

        model_provider = self.initial_state.get("model_provider", {})
        if not isinstance(model_provider, dict):
            model_provider = {}
        if provider in model_provider and isinstance(model_provider.get(provider), dict):
            source = model_provider[provider]
        elif provider == MODEL_PROVIDER_OLLAMA and isinstance(self.initial_state.get("ollama"), dict):
            source = self.initial_state["ollama"]
        else:
            source = {}

        settings = dict(defaults)
        settings.update(source)
        return settings

    def _fallback_text(self, fallback_models) -> str:
        if isinstance(fallback_models, list):
            return ", ".join(str(item).strip() for item in fallback_models if str(item).strip())
        return str(fallback_models).strip()

    def _current_provider_form_settings(self) -> dict:
        return {
            "model": self.model_entry.get().strip(),
            "fallback_models": self.fallback_entry.get().strip(),
            "host": self.host_entry.get().strip(),
            "api_key": self.api_key_entry.get().strip(),
            "api_key_env_var": self.api_key_env_entry.get().strip(),
            "timeout_seconds": self.timeout_entry.get().strip(),
        }

    def _load_provider_fields(self, provider: str):
        settings = self._provider_settings[provider]
        model_default = DEFAULT_OPENROUTER_MODEL if provider == MODEL_PROVIDER_OPENROUTER else DEFAULT_OLLAMA_MODEL
        host_default = DEFAULT_OPENROUTER_HOST if provider == MODEL_PROVIDER_OPENROUTER else DEFAULT_OLLAMA_HOST
        api_default = (
            DEFAULT_OPENROUTER_API_KEY_ENV_VAR
            if provider == MODEL_PROVIDER_OPENROUTER
            else DEFAULT_OLLAMA_API_KEY_ENV_VAR
        )
        timeout_default = (
            DEFAULT_OPENROUTER_TIMEOUT_SECONDS
            if provider == MODEL_PROVIDER_OPENROUTER
            else DEFAULT_OLLAMA_TIMEOUT_SECONDS
        )

        self.model_label_var.set("Modelo OpenRouter" if provider == MODEL_PROVIDER_OPENROUTER else "Modelo Ollama")
        self.model_entry.delete(0, "end")
        self.model_entry.insert(0, str(settings.get("model", model_default)).strip())
        self.fallback_entry.delete(0, "end")
        self.fallback_entry.insert(0, self._fallback_text(settings.get("fallback_models", [])))
        self.timeout_entry.delete(0, "end")
        self.timeout_entry.insert(0, str(settings.get("timeout_seconds", timeout_default)).strip())
        self.host_entry.delete(0, "end")
        self.host_entry.insert(0, str(settings.get("host", host_default)).strip())
        self.api_key_env_entry.delete(0, "end")
        self.api_key_env_entry.insert(0, str(settings.get("api_key_env_var", api_default)).strip() or api_default)
        self.api_key_entry.configure(state="normal")
        self.api_key_entry.delete(0, "end")
        self.api_key_entry.insert(0, str(settings.get("api_key", "")).strip())
        if provider == MODEL_PROVIDER_OPENROUTER:
            self.api_key_entry.configure(state="normal")
            self.api_help_var.set("OpenRouter puede usar la API key directa guardada localmente.")
        else:
            self.api_key_entry.configure(state="normal")
            self.api_help_var.set("Se usa con Ollama Cloud; en local puedes dejarla vacía.")
        self._loaded_provider = provider

    def _provider_changed(self, _event=None):
        if self._loaded_provider:
            self._provider_settings[self._loaded_provider] = self._current_provider_form_settings()
        provider = self.provider_var.get().strip().lower()
        if provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
            provider = MODEL_PROVIDER_OLLAMA
            self.provider_var.set(provider)
        self._load_provider_fields(provider)

    def _create_scrollable_body(self, master):
        screen_width = max(760, self.winfo_screenwidth())
        screen_height = max(560, self.winfo_screenheight())
        body_width = max(640, min(720, screen_width - 120))
        body_height = max(360, min(520, screen_height - 210))

        try:
            self.minsize(720, min(560, screen_height - 80))
        except tk.TclError:
            pass

        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        container = ttk.Frame(master)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            container,
            width=body_width,
            height=body_height,
            bd=0,
            highlightthickness=0,
            bg=self.theme_palette["bg"],
        )
        canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            container,
            orient="vertical",
            command=canvas.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        style_scrollbar_widget(scrollbar, self.theme_palette)

        content = ttk.Frame(canvas)
        content.columnconfigure(0, weight=1)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def sync_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_content_width(event):
            canvas.itemconfigure(content_window, width=event.width)

        def scroll_body(event):
            if event.delta:
                canvas.yview_scroll(int(-event.delta / 120), "units")

        content.bind("<Configure>", sync_scroll_region)
        canvas.bind("<Configure>", sync_content_width)
        canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", scroll_body))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))

        self.first_run_canvas = canvas
        self.first_run_scrollbar = scrollbar
        return content

    def body(self, master):
        self._prepare_body(master)
        form = self._create_scrollable_body(master)
        form.columnconfigure(0, weight=1)

        state_goal = str(self.initial_state.get("goal", "")).strip()
        profile = self.initial_state.get("profile", {})
        if not isinstance(profile, dict):
            profile = {}
        model_provider = self.initial_state.get("model_provider", {})
        if not isinstance(model_provider, dict):
            model_provider = {}
        initial_provider = str(model_provider.get("default", MODEL_PROVIDER_OLLAMA)).strip().lower()
        if initial_provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
            initial_provider = MODEL_PROVIDER_OLLAMA
        self._provider_settings = {
            MODEL_PROVIDER_OLLAMA: self._initial_provider_settings(MODEL_PROVIDER_OLLAMA),
            MODEL_PROVIDER_OPENROUTER: self._initial_provider_settings(MODEL_PROVIDER_OPENROUTER),
        }
        self.provider_var = tk.StringVar(value=initial_provider)
        self.model_label_var = tk.StringVar()
        self.api_help_var = tk.StringVar()

        ttk.Label(
            form,
            text="Objetivo principal",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))

        self.goal_text = tk.Text(form, width=68, height=4, wrap="word")
        self.goal_text.grid(row=1, column=0, sticky="ew", padx=6)
        self._style_text_widget(self.goal_text)
        self.goal_text.insert("1.0", state_goal)

        ttk.Label(form, text="Plantillas rápidas").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.template_combo = ttk.Combobox(
            form,
            values=self.GOAL_TEMPLATES,
            state="readonly",
            width=64,
        )
        self.template_combo.grid(row=3, column=0, sticky="ew", padx=6)
        self.template_combo.bind("<<ComboboxSelected>>", self._apply_template)

        identity = ttk.LabelFrame(form, text="Contexto mínimo")
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

        model_frame = ttk.LabelFrame(form, text="Modelo")
        model_frame.grid(row=5, column=0, sticky="ew", padx=6, pady=(10, 0))
        model_frame.columnconfigure(0, weight=1)
        model_frame.columnconfigure(1, weight=0)

        ttk.Label(model_frame, text="Proveedor por defecto").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.provider_combo = ttk.Combobox(
            model_frame,
            textvariable=self.provider_var,
            values=(MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER),
            state="readonly",
            width=18,
        )
        self.provider_combo.grid(row=1, column=0, sticky="ew", padx=8)
        self.provider_combo.bind("<<ComboboxSelected>>", self._provider_changed)

        ttk.Label(model_frame, textvariable=self.model_label_var).grid(row=2, column=0, sticky="w", padx=8, pady=(8, 2))
        self.model_entry = ttk.Entry(model_frame, width=42)
        self.model_entry.grid(row=3, column=0, sticky="ew", padx=8)

        ttk.Label(model_frame, text="Timeout en segundos").grid(row=2, column=1, sticky="w", padx=8, pady=(8, 2))
        self.timeout_entry = ttk.Entry(model_frame, width=12)
        self.timeout_entry.grid(row=3, column=1, sticky="w", padx=8)

        ttk.Label(model_frame, text="Fallbacks opcionales").grid(row=4, column=0, sticky="w", padx=8, pady=(8, 2))
        self.fallback_entry = ttk.Entry(model_frame, width=42)
        self.fallback_entry.grid(row=5, column=0, columnspan=2, sticky="ew", padx=8)

        ttk.Label(model_frame, text="Host").grid(row=6, column=0, sticky="w", padx=8, pady=(8, 2))
        self.host_entry = ttk.Entry(model_frame, width=42)
        self.host_entry.grid(row=7, column=0, columnspan=2, sticky="ew", padx=8)

        ttk.Label(model_frame, text="API key env").grid(row=8, column=0, sticky="w", padx=8, pady=(8, 2))
        self.api_key_env_entry = ttk.Entry(model_frame, width=42)
        self.api_key_env_entry.grid(row=9, column=0, sticky="ew", padx=8, pady=(0, 8))

        ttk.Label(model_frame, text="API key directa").grid(row=8, column=1, sticky="w", padx=8, pady=(8, 2))
        self.api_key_entry = ttk.Entry(model_frame, width=24, show="*")
        self.api_key_entry.grid(row=9, column=1, sticky="ew", padx=8, pady=(0, 8))
        ttk.Label(
            model_frame,
            textvariable=self.api_help_var,
            foreground=self.theme_palette["muted"],
            wraplength=420,
        ).grid(row=10, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))

        options = ttk.LabelFrame(form, text="Al guardar")
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
            text="Abrir configuración de Telegram",
            variable=self.open_notifications_var,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(
            options,
            text="Instalar/iniciar servicio de fondo",
            variable=self.install_service_var,
        ).grid(row=2, column=0, sticky="w", padx=8, pady=(2, 8))

        self._load_provider_fields(initial_provider)
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

        provider = self.provider_var.get().strip().lower()
        if provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
            messagebox.showwarning("Yarbis", "Proveedor inválido.", parent=self)
            return False
        self._provider_settings[provider] = self._current_provider_form_settings()
        settings = self._provider_settings[provider]

        if not settings["model"]:
            label = "OpenRouter" if provider == MODEL_PROVIDER_OPENROUTER else "Ollama"
            messagebox.showwarning("Yarbis", f"El modelo {label} no puede quedar vacío.", parent=self)
            return False
        try:
            int(settings["timeout_seconds"])
        except ValueError:
            messagebox.showwarning("Yarbis", "El timeout debe ser un número de segundos.", parent=self)
            return False
        if provider == MODEL_PROVIDER_OPENROUTER:
            api_key_env_var = settings["api_key_env_var"] or DEFAULT_OPENROUTER_API_KEY_ENV_VAR
            if not settings["api_key"] and not os.getenv(api_key_env_var, "").strip():
                messagebox.showwarning(
                    "Yarbis",
                    f"Pega una API key directa o define {api_key_env_var} para usar OpenRouter.",
                    parent=self,
                )
                return False
        elif _host_uses_ollama_cloud(settings["host"]):
            api_key_env_var = settings["api_key_env_var"] or DEFAULT_OLLAMA_API_KEY_ENV_VAR
            if not settings["api_key"] and not os.getenv(api_key_env_var, "").strip():
                messagebox.showwarning(
                    "Yarbis",
                    f"Pega una API key directa o define {api_key_env_var} para usar Ollama Cloud.",
                    parent=self,
                )
                return False
        return True

    def apply(self):
        provider = self.provider_var.get().strip().lower()
        self._provider_settings[provider] = self._current_provider_form_settings()
        settings = self._provider_settings[provider]
        self.result = {
            "goal": self.goal_text.get("1.0", "end-1c").strip(),
            "name": self.name_entry.get().strip(),
            "role": self.role_entry.get().strip(),
            "provider": provider,
            "model": settings["model"],
            "fallback_models": settings["fallback_models"],
            "timeout_seconds": settings["timeout_seconds"],
            "host": settings["host"],
            "api_key": settings["api_key"],
            "api_key_env_var": settings["api_key_env_var"],
            "run_first_cycle": self.run_first_cycle_var.get(),
            "open_notifications": self.open_notifications_var.get(),
            "install_service": self.install_service_var.get(),
        }


class NoteDialog(ThemedDialog):
    def body(self, master):
        self._prepare_body(master)
        ttk.Label(master, text="Título").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.title_entry = ttk.Entry(master, width=56)
        self.title_entry.grid(row=1, column=0, sticky="ew", padx=6)

        ttk.Label(master, text="Contenido").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.content_text = tk.Text(master, width=56, height=6, wrap="word")
        self.content_text.grid(row=3, column=0, padx=6)
        self._style_text_widget(self.content_text)

        ttk.Label(master, text="Categoría").grid(row=4, column=0, sticky="w", padx=6, pady=(8, 2))
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
            f"[{note.get('id', '')}] {note.get('title', 'Nota sin título')}\n"
            f"Categoría: {note.get('category', 'general')}\n\n"
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
                f"[{note.get('id', '')}] {note.get('title', 'Nota sin título')} ({note.get('category', 'general')})",
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
            f"¿Quieres eliminar la nota '{note.get('title', 'Nota sin título')}'?",
            parent=self,
        )
        if not should_delete:
            return

        result = delete_note_text(note.get("id", ""))
        self.activity_messages.append(result)
        self._refresh_notes()

    def apply(self):
        self.result = "\n".join(self.activity_messages).strip()


class IdeaProjectEditDialog(ThemedDialog):
    KIND_OPTIONS = ("producto_negocio", "vida_proyecto", "mixto", "otro")
    STATUS_OPTIONS = ("exploring", "planned", "active", "paused", "done", "archived")
    TEXT_FIELDS = (
        ("summary", "Resumen", 3),
        ("audience", "Audiencia", 2),
        ("desired_outcome", "Resultado deseado", 2),
        ("problem", "Problema u oportunidad", 2),
        ("creative_directions", "Direcciones creativas", 4),
        ("selected_direction", "Direccion elegida", 2),
        ("success_criteria", "Criterios de exito", 3),
        ("constraints", "Restricciones", 2),
        ("risks", "Riesgos", 2),
        ("open_questions", "Preguntas abiertas", 3),
        ("next_steps", "Proximos pasos", 3),
    )
    LIST_FIELDS = {
        "creative_directions",
        "success_criteria",
        "constraints",
        "risks",
        "open_questions",
        "next_steps",
    }

    def __init__(self, parent, initial_project: dict | None = None):
        self.initial_project = initial_project or {}
        self.text_widgets = {}
        title = "Editar proyecto" if initial_project else "Nuevo proyecto"
        super().__init__(parent, title)

    def body(self, master):
        self._prepare_body(master)
        master.columnconfigure(0, weight=1)
        master.columnconfigure(1, weight=1)

        ttk.Label(master, text="Titulo").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.title_entry = ttk.Entry(master, width=56)
        self.title_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6)
        self.title_entry.insert(0, self.initial_project.get("title", ""))

        ttk.Label(master, text="Tipo").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.kind_combo = ttk.Combobox(master, values=self.KIND_OPTIONS, state="readonly", width=24)
        self.kind_combo.grid(row=3, column=0, sticky="ew", padx=6)
        self.kind_combo.set(self.initial_project.get("kind", "mixto") or "mixto")

        ttk.Label(master, text="Estado").grid(row=2, column=1, sticky="w", padx=6, pady=(8, 2))
        self.status_combo = ttk.Combobox(master, values=self.STATUS_OPTIONS, state="readonly", width=24)
        self.status_combo.grid(row=3, column=1, sticky="ew", padx=6)
        self.status_combo.set(self.initial_project.get("status", "exploring") or "exploring")

        row = 4
        for field_name, label, height in self.TEXT_FIELDS:
            column = 0 if (row - 4) % 2 == 0 else 1
            ttk.Label(master, text=label).grid(row=row, column=column, sticky="w", padx=6, pady=(8, 2))
            text_widget = tk.Text(master, width=38, height=height, wrap="word")
            text_widget.grid(row=row + 1, column=column, sticky="ew", padx=6)
            self._style_text_widget(text_widget)
            value = self.initial_project.get(field_name, "")
            if isinstance(value, list):
                value = "\n".join(str(item) for item in value)
            text_widget.insert("1.0", str(value))
            self.text_widgets[field_name] = text_widget
            if column == 1:
                row += 2

        return self.title_entry

    def validate(self):
        if not self.title_entry.get().strip():
            messagebox.showwarning("Yarbis", "El proyecto necesita titulo.", parent=self)
            return False
        return True

    def _field_value(self, field_name: str):
        text = self.text_widgets[field_name].get("1.0", "end-1c").strip()
        if text:
            return text
        if not self.initial_project:
            return ""
        original = self.initial_project.get(field_name, "")
        if isinstance(original, list):
            return "[clear]" if original else ""
        return "[clear]" if str(original).strip() else ""

    def apply(self):
        self.result = {
            "title": self.title_entry.get().strip(),
            "kind": self.kind_combo.get().strip() or "mixto",
            "status": self.status_combo.get().strip() or "exploring",
        }
        for field_name, _label, _height in self.TEXT_FIELDS:
            self.result[field_name] = self._field_value(field_name)


class IdeaProjectsDialog(ThemedDialog):
    def __init__(self, parent, visual_callback=None):
        self.projects = []
        self.activity_messages = []
        self.visual_callback = visual_callback
        super().__init__(parent, "Ideas/proyectos")

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

        self.projects_list = tk.Listbox(
            list_frame,
            width=42,
            height=20,
            activestyle="dotbox",
            exportselection=False,
        )
        self.projects_list.grid(row=0, column=0, sticky="nsew")
        style_listbox_widget(self.projects_list, self.theme_palette)
        self.projects_list.bind("<<ListboxSelect>>", self._show_selected_project)

        projects_scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.projects_list.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        projects_scrollbar.grid(row=0, column=1, sticky="ns")
        self.projects_list.configure(yscrollcommand=projects_scrollbar.set)
        style_scrollbar_widget(projects_scrollbar, self.theme_palette)

        detail_frame = tk.Frame(master, bd=0, highlightthickness=0)
        detail_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 6), pady=6)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.configure(bg=self.theme_palette["bg"])

        self.detail_text = tk.Text(detail_frame, width=64, height=20, wrap="word")
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        self._style_text_widget(self.detail_text)
        self.detail_text.configure(state="disabled")

        detail_scrollbar = ttk.Scrollbar(
            detail_frame,
            orient="vertical",
            command=self.detail_text.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        detail_scrollbar.grid(row=0, column=1, sticky="ns")
        self.detail_text.configure(yscrollcommand=detail_scrollbar.set)
        style_scrollbar_widget(detail_scrollbar, self.theme_palette)

        self._refresh_projects()
        return self.projects_list

    def buttonbox(self):
        box = ttk.Frame(self)
        ttk.Button(box, text="Nuevo", command=self._new_project, style="Accent.TButton").pack(side="left", padx=(0, 8))
        self.edit_button = ttk.Button(box, text="Editar", command=self._edit_selected_project)
        self.edit_button.pack(side="left", padx=(0, 8))
        self.activate_button = ttk.Button(box, text="Activar", command=self._activate_selected_project)
        self.activate_button.pack(side="left", padx=(0, 8))
        self.visual_button = ttk.Button(box, text="Visual web", command=self._open_visual_workspace)
        self.visual_button.pack(side="left", padx=(0, 8))
        self.archive_button = ttk.Button(
            box,
            text="Archivar",
            command=self._archive_selected_project,
            style="Danger.TButton",
        )
        self.archive_button.pack(side="left", padx=(0, 8))
        ttk.Button(box, text="Refrescar", command=self._refresh_projects).pack(side="left", padx=(0, 8))
        ttk.Button(box, text="Cerrar", command=self.ok).pack(side="left")

        self.bind("<Escape>", self.cancel)
        box.pack(padx=10, pady=(0, 10), anchor="e")
        self._sync_action_buttons()

    def _set_detail_text(self, content: str):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", content)
        self.detail_text.configure(state="disabled")

    def _selected_project(self) -> dict | None:
        selection = self.projects_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self.projects):
            return None
        return self.projects[index]

    def _sync_action_buttons(self):
        project = self._selected_project() if hasattr(self, "projects_list") else None
        enabled = "normal" if project else "disabled"
        for button_name in ("edit_button", "activate_button", "archive_button"):
            if hasattr(self, button_name):
                getattr(self, button_name).configure(state=enabled)
        if hasattr(self, "visual_button"):
            self.visual_button.configure(state="normal" if self.visual_callback else "disabled")

    def _render_project(self, project: dict) -> str:
        lines = [
            f"[{project.get('id', '')}] {project.get('title', 'Proyecto sin titulo')}",
            f"Tipo: {project.get('kind', '-')}",
            f"Estado: {project.get('status', '-')}",
        ]
        for label, key in (
            ("Resumen", "summary"),
            ("Audiencia", "audience"),
            ("Resultado deseado", "desired_outcome"),
            ("Problema", "problem"),
            ("Direccion elegida", "selected_direction"),
        ):
            if project.get(key):
                lines.append(f"{label}: {project[key]}")

        for label, key in (
            ("Direcciones creativas", "creative_directions"),
            ("Criterios de exito", "success_criteria"),
            ("Restricciones", "constraints"),
            ("Riesgos", "risks"),
            ("Preguntas abiertas", "open_questions"),
            ("Proximos pasos", "next_steps"),
        ):
            values = project.get(key) or []
            if values:
                lines.append(label + ":")
                lines.extend(f"- {value}" for value in values)
        return "\n".join(lines)

    def _show_selected_project(self, _event=None):
        project = self._selected_project()
        if not project:
            self._set_detail_text("Selecciona un proyecto para verlo completo.")
            self._sync_action_buttons()
            return
        self._set_detail_text(self._render_project(project))
        self._sync_action_buttons()

    def _refresh_projects(self):
        selected_id = ""
        selected_project = self._selected_project() if hasattr(self, "projects_list") else None
        if selected_project:
            selected_id = selected_project.get("id", "")

        state = load_state()
        self.projects = list(reversed(state.get("idea_projects", [])))
        self.projects_list.delete(0, "end")
        for project in self.projects:
            self.projects_list.insert(
                "end",
                f"[{project.get('id', '')}] {project.get('title', 'Proyecto sin titulo')} "
                f"({project.get('status', '-')})",
            )

        if not self.projects:
            self._set_detail_text("No hay proyectos de ideas guardados.")
            self._sync_action_buttons()
            return

        next_index = 0
        if selected_id:
            for index, project in enumerate(self.projects):
                if project.get("id", "") == selected_id:
                    next_index = index
                    break

        self.projects_list.selection_clear(0, "end")
        self.projects_list.selection_set(next_index)
        self.projects_list.activate(next_index)
        self.projects_list.see(next_index)
        self._show_selected_project()

    def _new_project(self):
        dialog = IdeaProjectEditDialog(self)
        if dialog.result is None:
            return
        result = create_idea_project_text(**dialog.result)
        self.activity_messages.append(result)
        self._refresh_projects()

    def _edit_selected_project(self):
        project = self._selected_project()
        if not project:
            return
        dialog = IdeaProjectEditDialog(self, initial_project=project)
        if dialog.result is None:
            return
        result = update_idea_project_text(project_id=project.get("id", ""), **dialog.result)
        self.activity_messages.append(result)
        self._refresh_projects()

    def _activate_selected_project(self):
        project = self._selected_project()
        if not project:
            return
        result = promote_idea_project_to_work_text(project.get("id", ""))
        self.activity_messages.append(result)
        self._refresh_projects()

    def _open_visual_workspace(self):
        if callable(self.visual_callback):
            self.visual_callback()

    def _archive_selected_project(self):
        project = self._selected_project()
        if not project:
            return
        should_archive = messagebox.askyesno(
            "Archivar proyecto",
            f"Quieres archivar '{project.get('title', 'Proyecto sin titulo')}'?",
            parent=self,
        )
        if not should_archive:
            return
        result = update_idea_project_text(project_id=project.get("id", ""), status="archived")
        self.activity_messages.append(result)
        self._refresh_projects()

    def apply(self):
        self.result = "\n".join(self.activity_messages).strip()


class CodingProposalsDialog(ThemedDialog):
    def __init__(self, parent, apply_callback, discard_callback, detail_callback, list_callback, validate_callback):
        self.apply_callback = apply_callback
        self.discard_callback = discard_callback
        self.detail_callback = detail_callback
        self.list_callback = list_callback
        self.validate_callback = validate_callback
        self.proposal_lines = []
        self.activity_messages = []
        super().__init__(parent, "Propuestas de coding")

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

        self.proposals_list = tk.Listbox(
            list_frame,
            width=46,
            height=18,
            activestyle="dotbox",
            exportselection=False,
        )
        self.proposals_list.grid(row=0, column=0, sticky="nsew")
        style_listbox_widget(self.proposals_list, self.theme_palette)
        self.proposals_list.bind("<<ListboxSelect>>", self._show_selected_proposal)

        proposals_scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.proposals_list.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        proposals_scrollbar.grid(row=0, column=1, sticky="ns")
        self.proposals_list.configure(yscrollcommand=proposals_scrollbar.set)
        style_scrollbar_widget(proposals_scrollbar, self.theme_palette)

        detail_frame = tk.Frame(master, bd=0, highlightthickness=0)
        detail_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 6), pady=6)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.configure(bg=self.theme_palette["bg"])

        self.detail_text = tk.Text(detail_frame, width=58, height=18, wrap="word")
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        self._style_text_widget(self.detail_text)
        self.detail_text.configure(state="disabled")

        detail_scrollbar = ttk.Scrollbar(
            detail_frame,
            orient="vertical",
            command=self.detail_text.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        detail_scrollbar.grid(row=0, column=1, sticky="ns")
        self.detail_text.configure(yscrollcommand=detail_scrollbar.set)
        style_scrollbar_widget(detail_scrollbar, self.theme_palette)

        self._refresh_proposals()
        return self.proposals_list

    def buttonbox(self):
        box = ttk.Frame(self)
        self.apply_button = ttk.Button(
            box,
            text="Aplicar",
            command=self._apply_selected_proposal,
            style="Accent.TButton",
        )
        self.apply_button.pack(side="left", padx=(0, 8))
        self.discard_button = ttk.Button(
            box,
            text="Descartar",
            command=self._discard_selected_proposal,
            style="Danger.TButton",
        )
        self.discard_button.pack(side="left", padx=(0, 8))
        self.validate_button = ttk.Button(
            box,
            text="Validar",
            command=self._validate_selected_proposal,
        )
        self.validate_button.pack(side="left", padx=(0, 8))
        ttk.Button(box, text="Refrescar", command=self._refresh_proposals).pack(side="left", padx=(0, 8))
        ttk.Button(box, text="Cerrar", command=self.ok).pack(side="left")

        self.bind("<Escape>", self.cancel)
        box.pack(padx=10, pady=(0, 10), anchor="e")
        self._sync_action_buttons()

    def _set_detail_text(self, content: str):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", content)
        self.detail_text.configure(state="disabled")

    def _selected_line(self) -> str:
        selection = self.proposals_list.curselection()
        if not selection:
            return ""
        index = int(selection[0])
        if index < 0 or index >= len(self.proposal_lines):
            return ""
        return self.proposal_lines[index]

    @staticmethod
    def _proposal_id_from_line(line: str) -> str:
        cleaned = str(line).strip()
        if not cleaned.startswith("[") or "]" not in cleaned:
            return ""
        return cleaned.split("]", 1)[0].strip("[")

    def _sync_action_buttons(self):
        enabled = "normal" if self._proposal_id_from_line(self._selected_line()) else "disabled"
        if hasattr(self, "apply_button"):
            self.apply_button.configure(state=enabled)
        if hasattr(self, "discard_button"):
            self.discard_button.configure(state=enabled)
        if hasattr(self, "validate_button"):
            self.validate_button.configure(state=enabled)

    def _show_selected_proposal(self, _event=None):
        line = self._selected_line()
        proposal_id = self._proposal_id_from_line(line)
        detail = self.detail_callback(proposal_id) if proposal_id else "Selecciona una propuesta pendiente."
        self._set_detail_text(detail)
        self._sync_action_buttons()

    def _refresh_proposals(self):
        selected_id = self._proposal_id_from_line(self._selected_line()) if hasattr(self, "proposals_list") else ""
        rendered = self.list_callback(status="pending", limit=50)
        if rendered.startswith("No hay propuestas") or rendered.startswith("No hay workspace"):
            self.proposal_lines = []
        else:
            self.proposal_lines = [line for line in rendered.splitlines() if line.strip().startswith("[")]

        self.proposals_list.delete(0, "end")
        for line in self.proposal_lines:
            self.proposals_list.insert("end", line)

        if not self.proposal_lines:
            self._set_detail_text(rendered)
            self._sync_action_buttons()
            return

        next_index = 0
        if selected_id:
            for index, line in enumerate(self.proposal_lines):
                if self._proposal_id_from_line(line) == selected_id:
                    next_index = index
                    break

        self.proposals_list.selection_clear(0, "end")
        self.proposals_list.selection_set(next_index)
        self.proposals_list.activate(next_index)
        self.proposals_list.see(next_index)
        self._show_selected_proposal()

    def _apply_selected_proposal(self):
        proposal_id = self._proposal_id_from_line(self._selected_line())
        if not proposal_id:
            return
        should_apply = messagebox.askyesno(
            "Aplicar propuesta",
            f"¿Quieres aplicar la propuesta {proposal_id}?",
            parent=self,
        )
        if not should_apply:
            return
        result = self.apply_callback(proposal_id)
        self.activity_messages.append(result)
        self._refresh_proposals()

    def _discard_selected_proposal(self):
        proposal_id = self._proposal_id_from_line(self._selected_line())
        if not proposal_id:
            return
        should_discard = messagebox.askyesno(
            "Descartar propuesta",
            f"¿Quieres descartar la propuesta {proposal_id}?",
            parent=self,
        )
        if not should_discard:
            return
        result = self.discard_callback(proposal_id)
        self.activity_messages.append(result)
        self._refresh_proposals()

    def _validate_selected_proposal(self):
        proposal_id = self._proposal_id_from_line(self._selected_line())
        if not proposal_id:
            return
        result = self.validate_callback(proposal_id=proposal_id)
        self.activity_messages.append(result)
        self._refresh_proposals()

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
                "Fusionar conserva configuración local y combina perfil, notas, tareas, mensajes y plan."
            ),
            wraplength=520,
        ).grid(row=4, column=0, sticky="ew", padx=6, pady=(0, 6))
        return self.mode_combo

    def apply(self):
        self.result = self.MODE_LABELS.get(self.mode_combo.get(), "replace")


class TaskDialog(ThemedDialog):
    def body(self, master):
        self._prepare_body(master)
        ttk.Label(master, text="Título").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
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
