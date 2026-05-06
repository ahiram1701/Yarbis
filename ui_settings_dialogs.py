import os
import tkinter as tk
from tkinter import ttk

from memory import (
    DEFAULT_OLLAMA_API_KEY_ENV_VAR,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    MAX_OLLAMA_TIMEOUT_SECONDS,
    MIN_OLLAMA_TIMEOUT_SECONDS,
)
from ui_dialogs import ThemedDialog


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
        super().__init__(parent, "Modelo y timeout")

    def body(self, master):
        self._prepare_body(master)
        model = str(self.initial_settings.get("model", DEFAULT_OLLAMA_MODEL)).strip()
        fallback_models = self.initial_settings.get("fallback_models", [])
        if isinstance(fallback_models, list):
            fallback_models = ", ".join(str(item).strip() for item in fallback_models if str(item).strip())
        else:
            fallback_models = str(fallback_models).strip()
        host = str(self.initial_settings.get("host", DEFAULT_OLLAMA_HOST)).strip()
        api_key_env_var = (
            str(self.initial_settings.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR)).strip()
            or DEFAULT_OLLAMA_API_KEY_ENV_VAR
        )
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

        ttk.Label(master, text="Fallbacks").grid(row=2, column=0, sticky="w", padx=6, pady=(10, 2))
        self.fallback_entry = ttk.Entry(master, width=44)
        self.fallback_entry.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6)
        self.fallback_entry.insert(0, fallback_models)

        ttk.Label(master, text="Host").grid(row=4, column=0, sticky="w", padx=6, pady=(10, 2))
        self.host_entry = ttk.Entry(master, width=44)
        self.host_entry.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6)
        self.host_entry.insert(0, host)
        ttk.Label(
            master,
            text="Vacio = local. Usa https://ollama.com para Cloud directo.",
            foreground=self.theme_palette["muted"],
            wraplength=360,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=(3, 0))

        ttk.Label(master, text="API key env").grid(row=7, column=0, sticky="w", padx=6, pady=(10, 2))
        self.api_key_env_entry = ttk.Entry(master, width=44)
        self.api_key_env_entry.grid(row=8, column=0, columnspan=2, sticky="ew", padx=6)
        self.api_key_env_entry.insert(0, api_key_env_var)

        ttk.Label(master, text="Timeout").grid(row=9, column=0, sticky="w", padx=6, pady=(10, 2))
        timeout_row = ttk.Frame(master)
        timeout_row.grid(row=10, column=0, sticky="w", padx=6, pady=(0, 6))
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

        if (
            os.getenv("YARBIS_MODEL")
            or os.getenv("YARBIS_OLLAMA_TIMEOUT_SECONDS")
            or os.getenv("YARBIS_OLLAMA_HOST")
            or os.getenv("YARBIS_OLLAMA_FALLBACK_MODELS")
            or os.getenv("YARBIS_OLLAMA_API_KEY_ENV_VAR")
        ):
            ttk.Label(
                master,
                text="Hay variables de entorno YARBIS_* activas; esas pueden tener prioridad.",
                foreground=self.theme_palette["muted"],
                wraplength=360,
            ).grid(row=11, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 6))

        master.columnconfigure(0, weight=1)
        return self.model_entry

    def apply(self):
        self.result = {
            "model": self.model_entry.get().strip(),
            "fallback_models": self.fallback_entry.get().strip(),
            "host": self.host_entry.get().strip(),
            "api_key_env_var": self.api_key_env_entry.get().strip(),
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
