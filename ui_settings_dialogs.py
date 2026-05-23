import os
import tkinter as tk
from tkinter import filedialog, ttk

from memory import (
    DEFAULT_OLLAMA_API_KEY_ENV_VAR,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_OPENROUTER_API_KEY_ENV_VAR,
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
    DEFAULT_SERVICE_PROACTIVE_MODEL,
    MAX_OLLAMA_TIMEOUT_SECONDS,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    MIN_OLLAMA_TIMEOUT_SECONDS,
    format_cycle_count,
    normalize_cycle_count,
)
from ui_dialogs import ThemedDialog


class LocalContextDialog(ThemedDialog):
    def __init__(self, parent, initial_settings: dict):
        self.initial_settings = initial_settings
        super().__init__(parent, "Contexto local")

    def body(self, master):
        self._prepare_body(master)
        initial_mode = str(self.initial_settings.get("mode", "safe") or "safe").strip().lower()
        initial_enabled = bool(self.initial_settings.get("enabled", True))
        if initial_mode == "off":
            initial_enabled = False
            initial_mode = "safe"
        self.enabled_var = tk.BooleanVar(value=initial_enabled)
        self.mode_var = tk.StringVar(value=initial_mode)
        self.sample_interval_var = tk.StringVar(
            value=str(self.initial_settings.get("sample_interval_seconds", 30))
        )
        self.max_age_var = tk.StringVar(
            value=str(self.initial_settings.get("max_snapshot_age_seconds", 180))
        )
        self.process_var = tk.BooleanVar(value=bool(self.initial_settings.get("include_process_name", True)))
        self.title_var = tk.BooleanVar(value=bool(self.initial_settings.get("include_window_title", False)))
        self.workspace_var = tk.BooleanVar(value=bool(self.initial_settings.get("include_workspace_changes", True)))
        self.system_var = tk.BooleanVar(value=bool(self.initial_settings.get("include_system_health", True)))
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
            text="Contexto local",
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

        mode_frame = ttk.LabelFrame(container, text="Privacidad")
        mode_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        mode_frame.columnconfigure(1, weight=1)

        ttk.Label(mode_frame, text="Modo").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))
        self.mode_combo = ttk.Combobox(
            mode_frame,
            textvariable=self.mode_var,
            values=("safe", "detailed"),
            state="readonly",
            width=14,
        )
        self.mode_combo.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=(10, 6))
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_enabled_state())

        self.process_check = ttk.Checkbutton(
            mode_frame,
            text="Incluir proceso en primer plano",
            variable=self.process_var,
            command=self._refresh_preview,
        )
        self.process_check.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=2)

        self.title_check = ttk.Checkbutton(
            mode_frame,
            text="Incluir titulo de ventana",
            variable=self.title_var,
            command=self._refresh_preview,
        )
        self.title_check.grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 10))

        cadence = ttk.LabelFrame(container, text="Cadencia")
        cadence.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        cadence.columnconfigure(0, weight=1)
        cadence.columnconfigure(1, weight=1)

        ttk.Label(cadence, text="Muestra cada").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 2))
        sample_row = ttk.Frame(cadence)
        sample_row.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))
        self.sample_spin = ttk.Spinbox(
            sample_row,
            from_=5,
            to=86400,
            increment=5,
            width=8,
            textvariable=self.sample_interval_var,
            style="Yarbis.TSpinbox",
        )
        self.sample_spin.pack(side="left")
        ttk.Label(sample_row, text="s").pack(side="left", padx=(6, 0))

        ttk.Label(cadence, text="Snapshot vigente").grid(row=0, column=1, sticky="w", padx=10, pady=(10, 2))
        age_row = ttk.Frame(cadence)
        age_row.grid(row=1, column=1, sticky="w", padx=10, pady=(0, 10))
        self.max_age_spin = ttk.Spinbox(
            age_row,
            from_=15,
            to=86400,
            increment=15,
            width=8,
            textvariable=self.max_age_var,
            style="Yarbis.TSpinbox",
        )
        self.max_age_spin.pack(side="left")
        ttk.Label(age_row, text="s").pack(side="left", padx=(6, 0))

        signals = ttk.LabelFrame(container, text="Senales")
        signals.grid(row=3, column=0, sticky="ew")

        self.workspace_check = ttk.Checkbutton(
            signals,
            text="Cambios del workspace",
            variable=self.workspace_var,
            command=self._refresh_preview,
        )
        self.workspace_check.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 2))

        self.system_check = ttk.Checkbutton(
            signals,
            text="Salud del sistema",
            variable=self.system_var,
            command=self._refresh_preview,
        )
        self.system_check.grid(row=1, column=0, sticky="w", padx=10, pady=(2, 10))

        self._controlled_widgets = [
            self.mode_combo,
            self.sample_spin,
            self.max_age_spin,
            self.process_check,
            self.title_check,
            self.workspace_check,
            self.system_check,
        ]
        for variable in (self.sample_interval_var, self.max_age_var):
            variable.trace_add("write", lambda *_args: self._refresh_preview())

        self._sync_enabled_state()
        return self.mode_combo

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _refresh_preview(self):
        status = "Activo" if self.enabled_var.get() else "Desactivado"
        mode = str(self.mode_var.get() or "safe").strip().lower()
        sample = max(5, self._safe_int(self.sample_interval_var.get(), 30))
        max_age = max(15, self._safe_int(self.max_age_var.get(), 180))
        title_text = "titulos si" if self.title_var.get() and mode == "detailed" else "titulos no"
        self.preview_var.set(
            f"{status} | modo {mode} | cada {sample}s | vigente {max_age}s | {title_text}"
        )

    def _sync_enabled_state(self):
        widget_state = "normal" if self.enabled_var.get() else "disabled"
        for widget in getattr(self, "_controlled_widgets", []):
            widget.configure(state=widget_state)

        if self.enabled_var.get():
            self.mode_combo.configure(state="readonly")
            if str(self.mode_var.get()).strip().lower() != "detailed":
                self.title_var.set(False)
                self.title_check.configure(state="disabled")
        self._refresh_preview()

    def apply(self):
        self.result = {
            "enabled": self.enabled_var.get(),
            "mode": self.mode_var.get().strip().lower() or "safe",
            "sample_interval_seconds": self.sample_interval_var.get().strip(),
            "max_snapshot_age_seconds": self.max_age_var.get().strip(),
            "include_window_title": self.title_var.get(),
            "include_process_name": self.process_var.get(),
            "include_workspace_changes": self.workspace_var.get(),
            "include_system_health": self.system_var.get(),
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
        initial_cycles = self.initial_settings.get("cycles")
        self.cycles_var = tk.StringVar(value="" if initial_cycles is None else str(initial_cycles))
        self.start_delay_var = tk.StringVar(value=str(self.initial_settings.get("start_delay_seconds", 60)))
        self.model_var = tk.StringVar(
            value=str(self.initial_settings.get("model", DEFAULT_SERVICE_PROACTIVE_MODEL) or "").strip()
        )
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
        self.cycles_entry = ttk.Entry(
            rhythm,
            width=8,
            textvariable=self.cycles_var,
        )
        self.cycles_entry.grid(row=1, column=1, sticky="w", padx=10)
        ttk.Label(
            rhythm,
            text="Vacio = hasta terminar",
            foreground=self.theme_palette["muted"],
        ).grid(row=2, column=1, sticky="w", padx=10, pady=(8, 10))

        startup = ttk.LabelFrame(container, text="Arranque")
        startup.grid(row=2, column=0, sticky="ew", pady=(0, 10))
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

        model_frame = ttk.LabelFrame(container, text="Modelo")
        model_frame.grid(row=3, column=0, sticky="ew")
        model_frame.columnconfigure(0, weight=1)

        ttk.Label(model_frame, text="Modelo del pulso").grid(
            row=0,
            column=0,
            sticky="w",
            padx=10,
            pady=(10, 2),
        )
        self.model_entry = ttk.Entry(model_frame, textvariable=self.model_var, width=44)
        self.model_entry.grid(row=1, column=0, sticky="ew", padx=10)
        ttk.Label(
            model_frame,
            text="Vacio = usa el modelo principal de Ollama.",
            foreground=self.theme_palette["muted"],
            wraplength=430,
        ).grid(row=2, column=0, sticky="ew", padx=10, pady=(3, 10))

        self._controlled_widgets = [
            self.interval_spin,
            self.cycles_entry,
            self.start_delay_spin,
            self.model_entry,
            *self.preset_buttons,
        ]
        for variable in (self.interval_minutes_var, self.cycles_var, self.start_delay_var, self.model_var):
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
        cycles = normalize_cycle_count(self.cycles_var.get(), default=0)
        cycles_text = "valor invalido" if cycles == 0 else format_cycle_count(cycles)
        start_delay = max(0, self._safe_int(self.start_delay_var.get(), 60))
        status = "Activo" if self.enabled_var.get() else "Desactivado"
        model_text = str(self.model_var.get() or "").strip() or "modelo principal"
        self.preview_var.set(
            f"{status} | {cycles_text} cada "
            f"{self._format_duration(interval_seconds)} | "
            f"arranque {self._format_duration(start_delay)} | "
            f"{model_text}"
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
            "model": self.model_var.get().strip(),
        }


class ServiceInstallDialog(ThemedDialog):
    def __init__(self, parent, initial_autostart: bool = False):
        self.initial_autostart = initial_autostart
        super().__init__(parent, "Instalar servicio")

    @staticmethod
    def _current_windows_account() -> str:
        domain = os.getenv("USERDOMAIN", "").strip()
        user = os.getenv("USERNAME", "").strip()
        if domain and user:
            return f"{domain}\\{user}"
        return user

    def body(self, master):
        self._prepare_body(master)
        master.columnconfigure(0, weight=1)

        self.autostart_var = tk.BooleanVar(value=bool(self.initial_autostart))
        default_account = self._current_windows_account()

        ttk.Checkbutton(
            master,
            text="Iniciar con Windows",
            variable=self.autostart_var,
        ).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 8))

        ttk.Label(master, text="Cuenta del servicio").grid(row=1, column=0, sticky="w", padx=6, pady=(2, 2))
        self.account_entry = ttk.Entry(master, width=44)
        self.account_entry.grid(row=2, column=0, sticky="ew", padx=6)
        self.account_entry.insert(0, default_account)

        ttk.Label(master, text="Password").grid(row=3, column=0, sticky="w", padx=6, pady=(10, 2))
        self.password_entry = ttk.Entry(master, width=44, show="*")
        self.password_entry.grid(row=4, column=0, sticky="ew", padx=6)

        ttk.Label(
            master,
            text=(
                "Usa DOMINIO\\usuario o .\\usuario. Si dejas cuenta y password vacios, "
                "SCM usara LocalSystem."
            ),
            foreground=self.theme_palette["muted"],
            wraplength=390,
        ).grid(row=5, column=0, sticky="ew", padx=6, pady=(8, 6))

        return self.password_entry if default_account else self.account_entry

    def apply(self):
        self.result = {
            "start_auto": self.autostart_var.get(),
            "account_name": self.account_entry.get().strip(),
            "password": self.password_entry.get(),
        }


class OllamaSettingsDialog(ThemedDialog):
    def __init__(self, parent, initial_settings: dict):
        self.initial_settings = initial_settings
        self._loaded_provider = ""
        super().__init__(parent, "Modelo y timeout")

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

        if isinstance(self.initial_settings, dict) and (
            "default" in self.initial_settings or provider in self.initial_settings
        ):
            source = self.initial_settings.get(provider, {})
        else:
            source = self.initial_settings if provider == MODEL_PROVIDER_OLLAMA else {}
        if not isinstance(source, dict):
            source = {}
        settings = dict(defaults)
        settings.update(source)
        return settings

    def _fallback_text(self, fallback_models) -> str:
        if isinstance(fallback_models, list):
            return ", ".join(str(item).strip() for item in fallback_models if str(item).strip())
        return str(fallback_models).strip()

    def _current_form_settings(self) -> dict:
        return {
            "model": self.model_entry.get().strip(),
            "fallback_models": self.fallback_entry.get().strip(),
            "host": self.host_entry.get().strip(),
            "api_key": self.api_key_entry.get().strip(),
            "api_key_env_var": self.api_key_env_entry.get().strip(),
            "timeout_seconds": self.timeout_spin.get().strip(),
        }

    def _load_provider_fields(self, provider: str):
        settings = self._provider_settings[provider]
        model_default = DEFAULT_OPENROUTER_MODEL if provider == MODEL_PROVIDER_OPENROUTER else DEFAULT_OLLAMA_MODEL
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

        self.model_entry.delete(0, "end")
        self.model_entry.insert(0, str(settings.get("model", model_default)).strip())
        self.fallback_entry.delete(0, "end")
        self.fallback_entry.insert(0, self._fallback_text(settings.get("fallback_models", [])))
        self.host_entry.delete(0, "end")
        self.host_entry.insert(0, str(settings.get("host", "")).strip())
        self.api_key_env_entry.delete(0, "end")
        self.api_key_env_entry.insert(
            0,
            str(settings.get("api_key_env_var", api_default)).strip() or api_default,
        )
        self.api_key_entry.configure(state="normal")
        self.api_key_entry.delete(0, "end")
        self.api_key_entry.insert(0, str(settings.get("api_key", "")).strip())
        self.timeout_spin.delete(0, "end")
        self.timeout_spin.insert(0, str(settings.get("timeout_seconds", timeout_default)))
        self.host_help_var.set(
            "Vacio = local. Usa https://ollama.com para Cloud directo."
            if provider == MODEL_PROVIDER_OLLAMA
            else "Base compatible con OpenAI. Normalmente https://openrouter.ai/api/v1."
        )
        self.api_help_var.set(
            (
                "La API key directa se guarda localmente; env sigue siendo util para overrides."
                if provider == MODEL_PROVIDER_OPENROUTER
                else "Se usa con Ollama Cloud; para Ollama local puedes dejarla vacia."
            )
        )
        self.api_key_entry.configure(
            state="normal"
        )
        self._loaded_provider = provider

    def _provider_changed(self, _event=None):
        if self._loaded_provider:
            self._provider_settings[self._loaded_provider] = self._current_form_settings()
        provider = self.provider_var.get().strip().lower()
        if provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
            provider = MODEL_PROVIDER_OLLAMA
            self.provider_var.set(provider)
        self._load_provider_fields(provider)

    def body(self, master):
        self._prepare_body(master)
        initial_provider = str(
            self.initial_settings.get("default", MODEL_PROVIDER_OLLAMA)
            if isinstance(self.initial_settings, dict)
            else MODEL_PROVIDER_OLLAMA
        ).strip().lower()
        if initial_provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
            initial_provider = MODEL_PROVIDER_OLLAMA
        self._provider_settings = {
            MODEL_PROVIDER_OLLAMA: self._initial_provider_settings(MODEL_PROVIDER_OLLAMA),
            MODEL_PROVIDER_OPENROUTER: self._initial_provider_settings(MODEL_PROVIDER_OPENROUTER),
        }
        self.provider_var = tk.StringVar(value=initial_provider)
        self.host_help_var = tk.StringVar()
        self.api_help_var = tk.StringVar()

        ttk.Label(master, text="Proveedor por defecto").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.provider_combo = ttk.Combobox(
            master,
            textvariable=self.provider_var,
            values=(MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER),
            state="readonly",
            width=20,
        )
        self.provider_combo.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6)
        self.provider_combo.bind("<<ComboboxSelected>>", self._provider_changed)

        ttk.Label(master, text="Modelo").grid(row=2, column=0, sticky="w", padx=6, pady=(10, 2))
        self.model_entry = ttk.Entry(master, width=44)
        self.model_entry.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6)

        ttk.Label(master, text="Fallbacks").grid(row=4, column=0, sticky="w", padx=6, pady=(10, 2))
        self.fallback_entry = ttk.Entry(master, width=44)
        self.fallback_entry.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6)

        ttk.Label(master, text="Host").grid(row=6, column=0, sticky="w", padx=6, pady=(10, 2))
        self.host_entry = ttk.Entry(master, width=44)
        self.host_entry.grid(row=7, column=0, columnspan=2, sticky="ew", padx=6)
        ttk.Label(
            master,
            textvariable=self.host_help_var,
            foreground=self.theme_palette["muted"],
            wraplength=360,
        ).grid(row=8, column=0, columnspan=2, sticky="w", padx=6, pady=(3, 0))

        ttk.Label(master, text="API key env").grid(row=9, column=0, sticky="w", padx=6, pady=(10, 2))
        self.api_key_env_entry = ttk.Entry(master, width=44)
        self.api_key_env_entry.grid(row=10, column=0, columnspan=2, sticky="ew", padx=6)

        ttk.Label(master, text="API key directa").grid(row=11, column=0, sticky="w", padx=6, pady=(10, 2))
        self.api_key_entry = ttk.Entry(master, width=44, show="*")
        self.api_key_entry.grid(row=12, column=0, columnspan=2, sticky="ew", padx=6)
        ttk.Label(
            master,
            textvariable=self.api_help_var,
            foreground=self.theme_palette["muted"],
            wraplength=360,
        ).grid(row=13, column=0, columnspan=2, sticky="w", padx=6, pady=(3, 0))

        ttk.Label(master, text="Timeout").grid(row=14, column=0, sticky="w", padx=6, pady=(10, 2))
        timeout_row = ttk.Frame(master)
        timeout_row.grid(row=15, column=0, sticky="w", padx=6, pady=(0, 6))
        self.timeout_spin = ttk.Spinbox(
            timeout_row,
            from_=MIN_OLLAMA_TIMEOUT_SECONDS,
            to=MAX_OLLAMA_TIMEOUT_SECONDS,
            increment=30,
            width=10,
            style="Yarbis.TSpinbox",
        )
        self.timeout_spin.pack(side="left")
        ttk.Label(timeout_row, text="s").pack(side="left", padx=(6, 0))

        if (
            os.getenv("YARBIS_MODEL")
            or os.getenv("YARBIS_MODEL_PROVIDER")
            or os.getenv("YARBIS_OLLAMA_TIMEOUT_SECONDS")
            or os.getenv("YARBIS_OLLAMA_HOST")
            or os.getenv("YARBIS_OLLAMA_FALLBACK_MODELS")
            or os.getenv("YARBIS_OLLAMA_API_KEY")
            or os.getenv("YARBIS_OLLAMA_API_KEY_ENV_VAR")
            or os.getenv("YARBIS_OPENROUTER_API_KEY")
            or os.getenv("YARBIS_OPENROUTER_HOST")
            or os.getenv("YARBIS_OPENROUTER_TIMEOUT_SECONDS")
        ):
            ttk.Label(
                master,
                text="Hay variables de entorno YARBIS_* activas; esas pueden tener prioridad.",
                foreground=self.theme_palette["muted"],
                wraplength=360,
            ).grid(row=16, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 6))

        master.columnconfigure(0, weight=1)
        self._load_provider_fields(initial_provider)
        return self.model_entry

    def apply(self):
        provider = self.provider_var.get().strip().lower()
        if provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
            provider = MODEL_PROVIDER_OLLAMA
        self.result = {
            "provider": provider,
            "model": self.model_entry.get().strip(),
            "fallback_models": self.fallback_entry.get().strip(),
            "host": self.host_entry.get().strip(),
            "api_key": self.api_key_entry.get().strip(),
            "api_key_env_var": self.api_key_env_entry.get().strip(),
            "timeout_seconds": self.timeout_spin.get().strip(),
        }


class MemoryProtectionDialog(ThemedDialog):
    def __init__(self, parent, initial_settings: dict, status_text: str = ""):
        self.initial_settings = initial_settings if isinstance(initial_settings, dict) else {}
        self.status_text = status_text
        super().__init__(parent, "Proteccion de memoria")

    def body(self, master):
        self._prepare_body(master)
        retention = self.initial_settings.get("retention", {})
        if not isinstance(retention, dict):
            retention = {}

        self.enabled_var = tk.BooleanVar(value=bool(self.initial_settings.get("enabled", True)))
        self.backup_each_change_var = tk.BooleanVar(
            value=bool(self.initial_settings.get("backup_on_every_change", True))
        )
        self.verify_var = tk.BooleanVar(value=bool(self.initial_settings.get("verify_after_write", True)))
        self.restore_var = tk.BooleanVar(value=bool(self.initial_settings.get("auto_restore", True)))
        self.mirror_var = tk.StringVar(value=str(self.initial_settings.get("mirror_dir", "")).strip())
        self.max_auto_var = tk.StringVar(value=str(retention.get("max_auto_backups", 250)))
        self.keep_daily_var = tk.StringVar(value=str(retention.get("keep_daily_days", 90)))

        container = ttk.Frame(master)
        container.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        container.columnconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)

        ttk.Checkbutton(
            container,
            text="Activar proteccion de memoria",
            variable=self.enabled_var,
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        ttk.Checkbutton(
            container,
            text="Respaldar cada cambio de memoria",
            variable=self.backup_each_change_var,
        ).grid(row=1, column=0, sticky="w", pady=2)

        ttk.Checkbutton(
            container,
            text="Verificar JSON despues de escribir",
            variable=self.verify_var,
        ).grid(row=2, column=0, sticky="w", pady=2)

        ttk.Checkbutton(
            container,
            text="Restaurar automaticamente si state.json falla",
            variable=self.restore_var,
        ).grid(row=3, column=0, sticky="w", pady=(2, 10))

        mirror_frame = ttk.LabelFrame(container, text="Espejo externo")
        mirror_frame.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        mirror_frame.columnconfigure(0, weight=1)

        self.mirror_entry = ttk.Entry(mirror_frame, textvariable=self.mirror_var, width=64)
        self.mirror_entry.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        ttk.Button(
            mirror_frame,
            text="Elegir carpeta",
            command=self._choose_mirror_dir,
        ).grid(row=0, column=1, sticky="e", padx=(0, 10), pady=10)

        retention_frame = ttk.LabelFrame(container, text="Retencion")
        retention_frame.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        retention_frame.columnconfigure(0, weight=1)
        retention_frame.columnconfigure(1, weight=1)

        ttk.Label(retention_frame, text="Automaticos recientes").grid(
            row=0,
            column=0,
            sticky="w",
            padx=10,
            pady=(10, 2),
        )
        self.max_auto_spin = ttk.Spinbox(
            retention_frame,
            from_=1,
            to=5000,
            increment=10,
            width=8,
            textvariable=self.max_auto_var,
            style="Yarbis.TSpinbox",
        )
        self.max_auto_spin.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))

        ttk.Label(retention_frame, text="Dias diarios").grid(
            row=0,
            column=1,
            sticky="w",
            padx=10,
            pady=(10, 2),
        )
        self.keep_daily_spin = ttk.Spinbox(
            retention_frame,
            from_=0,
            to=3650,
            increment=1,
            width=8,
            textvariable=self.keep_daily_var,
            style="Yarbis.TSpinbox",
        )
        self.keep_daily_spin.grid(row=1, column=1, sticky="w", padx=10, pady=(0, 10))

        if self.status_text:
            ttk.Label(
                container,
                text=self.status_text,
                foreground=self.theme_palette["muted"],
                wraplength=560,
                justify="left",
            ).grid(row=6, column=0, sticky="ew")

        return self.mirror_entry

    def _choose_mirror_dir(self):
        selected = filedialog.askdirectory(
            parent=self,
            title="Seleccionar espejo externo",
            initialdir=self.mirror_var.get().strip() or os.getcwd(),
        )
        if selected:
            self.mirror_var.set(selected)

    def apply(self):
        self.result = {
            "enabled": self.enabled_var.get(),
            "backup_on_every_change": self.backup_each_change_var.get(),
            "mirror_dir": self.mirror_var.get().strip(),
            "max_auto_backups": self.max_auto_spin.get().strip(),
            "keep_daily_days": self.keep_daily_spin.get().strip(),
            "verify_after_write": self.verify_var.get(),
            "auto_restore": self.restore_var.get(),
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
