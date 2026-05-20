import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import activity
from memory import (
    DEFAULT_MODEL_PROVIDER,
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
    VALID_MODEL_PROVIDERS,
    load_state,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = WORKSPACE_ROOT / ".yarbis_runtime"
PID_FILE = RUNTIME_DIR / "service.pid"
STOP_FILE = RUNTIME_DIR / "service.stop"
LOG_FILE = RUNTIME_DIR / "service.log"
OPERATION_LOCK_FILE = RUNTIME_DIR / "session.lock"
SERVICE_SCRIPT = WORKSPACE_ROOT / "yarbis_service.py"
SERVICE_HOST_PROJECT = WORKSPACE_ROOT / "service_host" / "YarbisServiceHost.csproj"
SERVICE_HOST_OUTPUT_DIR = RUNTIME_DIR / "service_host"
SERVICE_HOST_EXE = SERVICE_HOST_OUTPUT_DIR / "YarbisServiceHost.exe"

SERVICE_NAME = "Yarbis"
SERVICE_DISPLAY_NAME = "Yarbis"
SERVICE_DESCRIPTION = "Yarbis local agent background service."
SERVICE_DEFAULT_ACCOUNT = "LocalSystem"
BUILTIN_SERVICE_ACCOUNTS = {
    "localsystem",
    "local system",
    r"nt authority\localsystem",
    r"nt authority\localservice",
    r"nt authority\networkservice",
    r"localservice",
    r"networkservice",
}
STARTUP_WAIT_SECONDS = 15.0
STOP_WAIT_SECONDS = 15.0
READINESS_CACHE_SECONDS = 15.0
SERVICE_STATUS_CACHE_SECONDS = 2.0
_READINESS_CACHE = {
    "created_at": 0.0,
    "status": None,
}
_SERVICE_STATUS_CACHE = {
    "created_at": 0.0,
    "status": None,
}
RUNTIME_DEPENDENCY_MODULES = ("ollama", "win11toast", "pystray", "PIL")


class ServiceLogonFailure(RuntimeError):
    pass


def _windows_only():
    if os.name != "nt":
        raise RuntimeError("Los servicios administrados por SCM solo estan disponibles en Windows.")


def _quote(value: str | Path) -> str:
    return f'"{value}"'


def _service_binary_path() -> str:
    return " ".join((_quote(SERVICE_HOST_EXE), _quote(WORKSPACE_ROOT)))


def _sc_exe() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(system_root) / "System32" / "sc.exe"
    if candidate.exists():
        return str(candidate)
    return "sc.exe"


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_sc(args: list[str], timeout_seconds: int = 30) -> subprocess.CompletedProcess:
    _windows_only()
    return subprocess.run(
        [_sc_exe(), *args],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        creationflags=_creationflags(),
    )


def _completed_output(completed: subprocess.CompletedProcess) -> str:
    return "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if str(part).strip()
    )


def _is_logon_failure_output(output: str) -> bool:
    normalized = str(output).lower()
    return (
        "1069" in normalized
        or "logon failure" in normalized
        or "error en el inicio de sesi" in normalized
        or "error de inicio de sesi" in normalized
        or "no se puede iniciar el servicio debido a un error" in normalized
    )


def _ensure_success(completed: subprocess.CompletedProcess, action: str):
    if completed.returncode == 0:
        return

    output = _completed_output(completed)
    if "Access is denied" in output or "Acceso denegado" in output:
        raise PermissionError(
            f"No tengo permisos para {action}. Abre Yarbis como administrador e intenta de nuevo."
        )

    if _is_logon_failure_output(output):
        raise ServiceLogonFailure(
            f"No pude {action} porque Windows rechazo la cuenta configurada del servicio (SCM 1069).\n"
            f"{output or f'exit={completed.returncode}'}"
        )

    raise RuntimeError(f"No pude {action}.\n{output or f'exit={completed.returncode}'}")


def _parse_sc_value(output: str, *keys: str) -> str:
    normalized_keys = {key.strip().upper() for key in keys}
    for line in str(output).splitlines():
        if ":" not in line:
            continue
        left, right = line.split(":", 1)
        if left.strip().upper() in normalized_keys:
            return right.strip()
    return ""


_SC_STATE_BY_CODE = {
    "1": "stopped",
    "2": "start_pending",
    "3": "stop_pending",
    "4": "running",
    "5": "continue_pending",
    "6": "pause_pending",
    "7": "paused",
}

_SC_START_TYPE_BY_CODE = {
    "0": "boot_start",
    "1": "system_start",
    "2": "auto_start",
    "3": "demand_start",
    "4": "disabled",
}


def _parse_state(output: str) -> tuple[str, bool]:
    raw_state = _parse_sc_value(output, "STATE", "ESTADO")
    parts = raw_state.split()
    state_name = _SC_STATE_BY_CODE.get(
        parts[0],
        parts[1].lower() if len(parts) >= 2 else "unknown",
    ) if parts else "unknown"
    return state_name, state_name == "running"


def _parse_pid(output: str) -> int | None:
    raw_pid = _parse_sc_value(output, "PID")
    try:
        pid = int(raw_pid)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _parse_start_type(output: str) -> str:
    raw_start_type = _parse_sc_value(output, "START_TYPE", "TIPO_INICIO")
    parts = raw_start_type.split()
    return _SC_START_TYPE_BY_CODE.get(
        parts[0],
        parts[1].lower() if len(parts) >= 2 else "unknown",
    ) if parts else "unknown"


def _parse_service_account(output: str) -> str:
    account = _parse_sc_value(
        output,
        "SERVICE_START_NAME",
        "NOMBRE_INICIO_SERVICIO",
        "NOMBRE_CUENTA",
    )
    return account or "unknown"


def _service_missing(completed: subprocess.CompletedProcess) -> bool:
    output = _completed_output(completed)
    return completed.returncode != 0 and (
        "1060" in output
        or "does not exist" in output.lower()
        or "no existe" in output.lower()
        or "no se ha instalado" in output.lower()
    )


def _dotnet_exe() -> str:
    return "dotnet"


def _service_host_sources() -> list[Path]:
    return [
        SERVICE_HOST_PROJECT,
        WORKSPACE_ROOT / "service_host" / "Program.cs",
    ]


def _service_host_is_current() -> bool:
    if not SERVICE_HOST_EXE.exists():
        return False

    try:
        exe_mtime = SERVICE_HOST_EXE.stat().st_mtime
        return all(
            source.exists() and source.stat().st_mtime <= exe_mtime
            for source in _service_host_sources()
        )
    except OSError:
        return False


def _ensure_service_host_built():
    if _service_host_is_current():
        return

    if not SERVICE_HOST_PROJECT.exists():
        raise RuntimeError("No encontre el proyecto service_host/YarbisServiceHost.csproj.")

    SERVICE_HOST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        build = subprocess.run(
            [
                _dotnet_exe(),
                "publish",
                str(SERVICE_HOST_PROJECT),
                "-c",
                "Release",
                "-o",
                str(SERVICE_HOST_OUTPUT_DIR),
                "--nologo",
            ],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=_creationflags(),
        )
    except FileNotFoundError as exc:
        raise RuntimeError("No encontre dotnet. Instala .NET SDK 8 para compilar el host del servicio.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("dotnet publish excedio el tiempo limite al compilar el host del servicio.") from exc
    _ensure_success(build, "compilar el host nativo del servicio de Yarbis")

    if not SERVICE_HOST_EXE.exists():
        raise RuntimeError("dotnet publish termino, pero no encontre YarbisServiceHost.exe.")


def _clear_service_status_cache() -> None:
    _SERVICE_STATUS_CACHE["created_at"] = 0.0
    _SERVICE_STATUS_CACHE["status"] = None


def _cached_service_status(now: float) -> dict | None:
    cached = _SERVICE_STATUS_CACHE.get("status")
    if cached is None:
        return None
    if now - float(_SERVICE_STATUS_CACHE.get("created_at", 0.0)) >= SERVICE_STATUS_CACHE_SECONDS:
        return None
    return dict(cached)


def _store_service_status(status: dict) -> dict:
    stored = dict(status)
    _SERVICE_STATUS_CACHE["created_at"] = time.monotonic()
    _SERVICE_STATUS_CACHE["status"] = stored
    return dict(stored)


def get_service_status(force: bool = False) -> dict:
    now = time.monotonic()
    if not force:
        cached = _cached_service_status(now)
        if cached is not None:
            return cached

    base = {
        "service_name": SERVICE_NAME,
        "display_name": SERVICE_DISPLAY_NAME,
        "installed": False,
        "running": False,
        "state": "not_installed",
        "pid": None,
        "autostart_enabled": False,
        "start_type": "not_installed",
        "account_name": "",
        "log_file": str(LOG_FILE),
        "service_binary": _service_binary_path(),
    }

    if os.name != "nt":
        return _store_service_status(base)

    query = _run_sc(["queryex", SERVICE_NAME])
    if _service_missing(query):
        return _store_service_status(base)
    _ensure_success(query, "consultar el servicio de Yarbis")

    config = _run_sc(["qc", SERVICE_NAME])
    _ensure_success(config, "consultar la configuracion del servicio de Yarbis")

    state, running = _parse_state(query.stdout)
    start_type = _parse_start_type(config.stdout)

    base.update({
        "installed": True,
        "running": running,
        "state": state,
        "pid": _parse_pid(query.stdout) if running else None,
        "autostart_enabled": start_type == "auto_start",
        "start_type": start_type,
        "account_name": _parse_service_account(config.stdout),
    })
    return _store_service_status(base)


def _safe_service_status(force: bool = False) -> dict:
    try:
        return get_service_status(force=force)
    except Exception as exc:
        return {
            "service_name": SERVICE_NAME,
            "display_name": SERVICE_DISPLAY_NAME,
            "installed": False,
            "running": False,
            "state": "unknown",
            "pid": None,
            "autostart_enabled": False,
            "start_type": "unknown",
            "account_name": "unknown",
            "log_file": str(LOG_FILE),
            "service_binary": _service_binary_path(),
            "error": str(exc),
        }


def health_status(force_service: bool = False) -> dict:
    try:
        state = load_state()
    except Exception as exc:
        state = {}
        state_error = str(exc)
    else:
        state_error = ""

    notifications = state.get("notifications", {}) if isinstance(state, dict) else {}
    if not isinstance(notifications, dict):
        notifications = {}
    channels = notifications.get("channels", [])
    telegram = notifications.get("telegram", {})
    if not isinstance(telegram, dict):
        telegram = {}

    service = state.get("service", {}) if isinstance(state, dict) else {}
    if not isinstance(service, dict):
        service = {}
    proactive = service.get("proactive", {})
    if not isinstance(proactive, dict):
        proactive = {}

    runtime = state.get("runtime", {}) if isinstance(state, dict) else {}
    if not isinstance(runtime, dict):
        runtime = {}
    thinking = runtime.get("thinking", {})
    if not isinstance(thinking, dict):
        thinking = {}

    provider, active_model, ollama, openrouter = _model_provider_settings(state)

    service_status = _safe_service_status(force=force_service)
    telegram_enabled = bool(notifications.get("enabled", True) and "telegram" in channels)
    telegram_configured = bool(str(telegram.get("bot_token", "")).strip())
    telegram_linked = bool(str(telegram.get("chat_id", "")).strip())
    operation_active = bool(thinking.get("active") and str(thinking.get("label", "")).strip())

    return {
        "service": service_status,
        "telegram": {
            "enabled": telegram_enabled,
            "configured": telegram_configured,
            "linked": telegram_linked,
        },
        "proactive": {
            "enabled": bool(proactive.get("enabled")),
            "interval_seconds": proactive.get("interval_seconds"),
            "cycles": proactive.get("cycles"),
            "model": str(proactive.get("model", "")).strip(),
            "last_pulse_at": str(proactive.get("last_pulse_at", "")).strip(),
        },
        "operation": {
            "active": operation_active,
            "label": str(thinking.get("label", "")).strip() if operation_active else "",
            "operation_id": str(thinking.get("operation_id", "")).strip() if operation_active else "",
            "started_at": str(thinking.get("started_at", "")).strip() if operation_active else "",
            "lock_file": str(OPERATION_LOCK_FILE),
            "lock_file_present": OPERATION_LOCK_FILE.exists(),
        },
        "ollama": {
            "model": str(ollama.get("model", DEFAULT_OLLAMA_MODEL)).strip() or DEFAULT_OLLAMA_MODEL,
            "fallback_models": ollama.get("fallback_models", []),
            "host": str(ollama.get("host", DEFAULT_OLLAMA_HOST)).strip(),
            "api_key_env_var": (
                str(ollama.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR)).strip()
                or DEFAULT_OLLAMA_API_KEY_ENV_VAR
            ),
            "timeout_seconds": ollama.get("timeout_seconds", DEFAULT_OLLAMA_TIMEOUT_SECONDS),
        },
        "openrouter": {
            "model": str(openrouter.get("model", DEFAULT_OPENROUTER_MODEL)).strip(),
            "fallback_models": openrouter.get("fallback_models", []),
            "host": str(openrouter.get("host", DEFAULT_OPENROUTER_HOST)).strip() or DEFAULT_OPENROUTER_HOST,
            "api_key_env_var": (
                str(openrouter.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR)).strip()
                or DEFAULT_OPENROUTER_API_KEY_ENV_VAR
            ),
            "timeout_seconds": openrouter.get("timeout_seconds", DEFAULT_OPENROUTER_TIMEOUT_SECONDS),
        },
        "model_provider": {
            "default": provider,
            "label": _provider_label(provider),
            "model": str(active_model.get("model", "")).strip()
            or (DEFAULT_OLLAMA_MODEL if provider == MODEL_PROVIDER_OLLAMA else ""),
            "fallback_models": active_model.get("fallback_models", []),
            "host": str(active_model.get(
                "host",
                DEFAULT_OPENROUTER_HOST if provider == MODEL_PROVIDER_OPENROUTER else DEFAULT_OLLAMA_HOST,
            )).strip(),
            "api_key_env_var": str(active_model.get(
                "api_key_env_var",
                DEFAULT_OPENROUTER_API_KEY_ENV_VAR
                if provider == MODEL_PROVIDER_OPENROUTER
                else DEFAULT_OLLAMA_API_KEY_ENV_VAR,
            )).strip(),
            "timeout_seconds": active_model.get(
                "timeout_seconds",
                DEFAULT_OPENROUTER_TIMEOUT_SECONDS
                if provider == MODEL_PROVIDER_OPENROUTER
                else DEFAULT_OLLAMA_TIMEOUT_SECONDS,
            ),
        },
        "events_file": str(activity.EVENTS_FILE),
        "state_error": state_error,
    }


def format_health_status(status: dict | None = None) -> str:
    status = status or health_status()
    service = status.get("service", {})
    telegram = status.get("telegram", {})
    proactive = status.get("proactive", {})
    operation = status.get("operation", {})
    ollama = status.get("ollama", {})
    model_provider = status.get("model_provider", {})

    if service.get("running"):
        account_text = f", cuenta {service.get('account_name')}" if service.get("account_name") else ""
        service_text = f"servicio activo (PID {service.get('pid') or '-'}{account_text})"
    elif service.get("installed"):
        account_text = f", cuenta {service.get('account_name')}" if service.get("account_name") else ""
        service_text = f"servicio detenido ({service.get('state', 'unknown')}{account_text})"
    else:
        service_text = "servicio no instalado"

    if telegram.get("enabled"):
        telegram_text = "Telegram vinculado" if telegram.get("linked") else "Telegram pendiente de vincular"
    else:
        telegram_text = "Telegram desactivado"

    pulse_text = "pulso activo" if proactive.get("enabled") else "pulso desactivado"
    pulse_model = str(proactive.get("model", "")).strip()
    pulse_text += f", modelo {pulse_model or 'principal'}"
    if proactive.get("last_pulse_at"):
        pulse_text += f", ultimo {proactive['last_pulse_at']}"

    operation_text = (
        f"operacion activa: {operation.get('label')}"
        if operation.get("active")
        else "sin operacion activa"
    )
    model_details = model_provider if isinstance(model_provider, dict) and model_provider else ollama
    provider_label = model_details.get("label") or "Ollama"
    fallback_models = (
        model_details.get("fallback_models")
        if isinstance(model_details.get("fallback_models"), list)
        else []
    )
    fallback_text = f" + {len(fallback_models)} fallback(s)" if fallback_models else ""
    host = model_details.get("host", "")
    host_text = _ollama_host_label(host) if provider_label == "Ollama" else (host or DEFAULT_OPENROUTER_HOST)
    model_name = model_details.get("model") or ("sin modelo" if provider_label == "OpenRouter" else DEFAULT_OLLAMA_MODEL)
    model_text = (
        f"{provider_label} {model_name}{fallback_text} "
        f"@ {host_text} "
        f"({model_details.get('timeout_seconds')}s)"
    )

    return " | ".join((service_text, telegram_text, pulse_text, operation_text, model_text))


def _readiness_item(key: str, label: str, status: str, detail: str = "", action: str = "") -> dict:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": str(detail).strip(),
        "action": str(action).strip(),
    }


def _dependency_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _ollama_host_label(host: str) -> str:
    return str(host).strip() or "local"


def _provider_label(provider: str) -> str:
    return "OpenRouter" if provider == MODEL_PROVIDER_OPENROUTER else "Ollama"


def _model_provider_settings(state: dict) -> tuple[str, dict, dict, dict]:
    model_provider = state.get("model_provider", {}) if isinstance(state, dict) else {}
    if not isinstance(model_provider, dict):
        model_provider = {}
    provider = str(model_provider.get("default", DEFAULT_MODEL_PROVIDER)).strip().lower()
    if provider not in VALID_MODEL_PROVIDERS:
        provider = DEFAULT_MODEL_PROVIDER

    ollama = model_provider.get("ollama", state.get("ollama", {}))
    if not isinstance(ollama, dict):
        ollama = {}
    openrouter = model_provider.get("openrouter", {})
    if not isinstance(openrouter, dict):
        openrouter = {}
    active = openrouter if provider == MODEL_PROVIDER_OPENROUTER else ollama
    return provider, active, ollama, openrouter


def _openrouter_api_key_present(api_key_env_var: str) -> tuple[bool, str]:
    if os.getenv("YARBIS_OPENROUTER_API_KEY", "").strip():
        return True, "YARBIS_OPENROUTER_API_KEY"
    cleaned_env_var = str(api_key_env_var).strip() or DEFAULT_OPENROUTER_API_KEY_ENV_VAR
    return bool(os.getenv(cleaned_env_var, "").strip()), cleaned_env_var


def _host_uses_ollama_cloud(host: str) -> bool:
    hostname = urlparse(str(host).strip()).hostname or ""
    return hostname.lower().endswith("ollama.com")


def _ollama_model_names(timeout_seconds: int = 5) -> tuple[list[str], str]:
    ollama_exe = shutil.which("ollama")
    if not ollama_exe:
        return [], "No encontre ollama en PATH."

    try:
        completed = subprocess.run(
            [ollama_exe, "list"],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            creationflags=_creationflags(),
        )
    except subprocess.TimeoutExpired:
        return [], "ollama list excedio el tiempo limite."
    except OSError as exc:
        return [], f"No pude ejecutar ollama list: {exc}"

    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if str(part).strip()
    )
    if completed.returncode != 0:
        return [], output or f"ollama list fallo con exit={completed.returncode}."

    model_names = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("name"):
            continue
        model_names.append(stripped.split()[0])
    return model_names, ""


def readiness_status(force: bool = False) -> dict:
    now = time.monotonic()
    if (
        not force
        and _READINESS_CACHE["status"] is not None
        and now - float(_READINESS_CACHE["created_at"]) < READINESS_CACHE_SECONDS
    ):
        return _READINESS_CACHE["status"]

    try:
        state = load_state()
    except Exception as exc:
        state = {}
        state_error = str(exc)
    else:
        state_error = ""

    items = []
    version_ok = sys.version_info >= (3, 11)
    items.append(_readiness_item(
        "python",
        "Python",
        "ok" if version_ok else "missing",
        f"{sys.version.split()[0]} en {sys.executable}",
        "Instala Python 3.11 o superior." if not version_ok else "",
    ))

    python_path = WORKSPACE_ROOT / ".venv" / "Scripts" / "python.exe"
    venv_ok = python_path.exists()
    items.append(_readiness_item(
        "venv",
        "Entorno virtual",
        "ok" if venv_ok else "missing",
        str(python_path) if venv_ok else "No existe .venv\\Scripts\\python.exe.",
        ".\\scripts\\setup.ps1 puede crearlo." if not venv_ok else "",
    ))

    missing_modules = [module_name for module_name in RUNTIME_DEPENDENCY_MODULES if not _dependency_available(module_name)]
    items.append(_readiness_item(
        "dependencies",
        "Dependencias Python",
        "ok" if not missing_modules else "missing",
        "Dependencias Python disponibles." if not missing_modules else "Faltan: " + ", ".join(missing_modules),
        ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt" if missing_modules else "",
    ))

    provider, active_model, ollama, openrouter = _model_provider_settings(state)
    if provider == MODEL_PROVIDER_OPENROUTER:
        model = str(openrouter.get("model", DEFAULT_OPENROUTER_MODEL)).strip()
        host = str(openrouter.get("host", DEFAULT_OPENROUTER_HOST)).strip() or DEFAULT_OPENROUTER_HOST
        api_key_env_var = (
            str(openrouter.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR)).strip()
            or DEFAULT_OPENROUTER_API_KEY_ENV_VAR
        )
        api_key_present, key_source = _openrouter_api_key_present(api_key_env_var)
        if not model:
            items.append(_readiness_item(
                "model_provider",
                "Modelo",
                "missing",
                f"OpenRouter activo @ {host}, pero falta elegir modelo.",
                "Configura /openrouter MODELO o usa Modelo y timeout.",
            ))
        else:
            items.append(_readiness_item(
                "model_provider",
                "Modelo",
                "ok" if api_key_present else "missing",
                f"OpenRouter activo: {model} @ {host}",
                f"Define `{key_source}` con tu API key de OpenRouter." if not api_key_present else "",
            ))
    else:
        model = str(ollama.get("model", DEFAULT_OLLAMA_MODEL)).strip() or DEFAULT_OLLAMA_MODEL
        host = str(ollama.get("host", DEFAULT_OLLAMA_HOST)).strip()
        api_key_env_var = (
            str(ollama.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR)).strip()
            or DEFAULT_OLLAMA_API_KEY_ENV_VAR
        )
        if _host_uses_ollama_cloud(host):
            api_key_present = bool(os.getenv(api_key_env_var, "").strip())
            items.append(_readiness_item(
                "model_provider",
                "Modelo",
                "ok" if api_key_present else "missing",
                f"Ollama Cloud directo: {model} @ {host}",
                f"Define `{api_key_env_var}` con tu API key de Ollama Cloud." if not api_key_present else "",
            ))
        elif host:
            items.append(_readiness_item(
                "model_provider",
                "Modelo",
                "warning",
                f"Ollama remoto configurado: {model} @ {host}. No verifico modelos remotos aqui.",
                "",
            ))
        else:
            model_names, ollama_error = _ollama_model_names()
            if ollama_error:
                items.append(_readiness_item(
                    "model_provider",
                    "Modelo",
                    "missing",
                    ollama_error,
                    "Instala/inicia Ollama y ejecuta `ollama list`, o configura host cloud.",
                ))
            else:
                model_ok = model in model_names
                items.append(_readiness_item(
                    "model_provider",
                    "Modelo",
                    "ok" if model_ok else "missing",
                    f"Ollama activo: {model}" if model_ok else f"No encontre el modelo configurado: {model}",
                    f"Ejecuta `ollama pull {model}` o cambia el modelo en Yarbis." if not model_ok else "",
                ))

    goal = str(state.get("goal", "")).strip() if isinstance(state, dict) else ""
    items.append(_readiness_item(
        "goal",
        "Objetivo",
        "ok" if goal else "missing",
        goal if goal else "Todavia no hay objetivo principal.",
        "Define un objetivo desde la app para ejecutar el primer ciclo." if not goal else "",
    ))

    service_status = _safe_service_status(force=force)
    items.append(_readiness_item(
        "service",
        "Servicio",
        "ok" if service_status.get("running") else "warning",
        (
            f"Activo (PID {service_status.get('pid') or '-'})"
            if service_status.get("running")
            else (
                f"Instalado, estado {service_status.get('state', 'unknown')}"
                if service_status.get("installed")
                else "No instalado. La app igual puede usarse."
            )
        ),
        "Instalalo desde la app si quieres continuidad 24/7." if not service_status.get("running") else "",
    ))

    notifications = state.get("notifications", {}) if isinstance(state, dict) else {}
    if not isinstance(notifications, dict):
        notifications = {}
    channels = notifications.get("channels", [])
    telegram = notifications.get("telegram", {})
    if not isinstance(telegram, dict):
        telegram = {}
    telegram_enabled = bool(notifications.get("enabled", True) and "telegram" in channels)
    if telegram_enabled and telegram.get("bot_token") and telegram.get("chat_id"):
        telegram_status = "ok"
        telegram_detail = "Vinculado."
        telegram_action = ""
    elif telegram_enabled and telegram.get("bot_token"):
        telegram_status = "warning"
        telegram_detail = "Configurado, falta vincular chat."
        telegram_action = "Envia /start al bot."
    else:
        telegram_status = "warning"
        telegram_detail = "Desactivado. La app local igual funciona."
        telegram_action = "Configuralo solo si quieres control remoto."
    items.append(_readiness_item("telegram", "Telegram", telegram_status, telegram_detail, telegram_action))

    dotnet_ok = shutil.which("dotnet") is not None
    items.append(_readiness_item(
        "dotnet",
        ".NET SDK",
        "ok" if dotnet_ok else "warning",
        "Disponible para compilar el host del servicio." if dotnet_ok else "No disponible; solo afecta el servicio SCM.",
        "Instala .NET SDK 8 para usar el servicio." if not dotnet_ok else "",
    ))

    if state_error:
        items.append(_readiness_item("state", "Estado", "missing", state_error, "Revisa state.json."))

    blocking = [item for item in items if item["status"] == "missing"]
    warnings = [item for item in items if item["status"] == "warning"]
    ready = not blocking
    status = {
        "ready": ready,
        "summary": "Listo para usar." if ready else f"Faltan {len(blocking)} punto(s) para el primer uso.",
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "items": items,
    }
    _READINESS_CACHE["created_at"] = now
    _READINESS_CACHE["status"] = status
    return status


def format_readiness_status(status: dict | None = None, compact: bool = True) -> str:
    status = status or readiness_status()
    items = status.get("items", [])
    missing = [item for item in items if item.get("status") == "missing"]
    warnings = [item for item in items if item.get("status") == "warning"]
    if compact:
        if missing:
            return "Falta configurar: " + ", ".join(item["label"] for item in missing)
        if warnings:
            return "Usable ahora. Opcional: " + ", ".join(item["label"] for item in warnings)
        return "Listo para usar."

    lines = [str(status.get("summary") or "Preparacion")]
    for item in items:
        marker = "OK" if item.get("status") == "ok" else ("FALTA" if item.get("status") == "missing" else "OPCIONAL")
        line = f"- {marker}: {item.get('label')}"
        if item.get("detail"):
            line += f" - {item['detail']}"
        if item.get("action"):
            line += f" ({item['action']})"
        lines.append(line)
    return "\n".join(lines)


def is_service_running() -> bool:
    return bool(get_service_status()["running"])


def _service_account_args(account_name: str = "", password: str = "") -> list[str]:
    cleaned_account = str(account_name).strip()
    if not cleaned_account:
        return []

    args = ["obj=", cleaned_account]
    cleaned_password = str(password)
    if cleaned_password:
        args.extend(["password=", cleaned_password])
    return args


def service_account_requires_password(account_name: str) -> bool:
    cleaned_account = str(account_name).strip()
    if not cleaned_account:
        return False
    normalized = cleaned_account.lower()
    if normalized in BUILTIN_SERVICE_ACCOUNTS:
        return False
    return not cleaned_account.endswith("$")


def _service_account_result_text(account_name: str = "") -> str:
    cleaned_account = str(account_name).strip()
    if cleaned_account:
        return f" Cuenta: {cleaned_account}."
    return f" Cuenta: {SERVICE_DEFAULT_ACCOUNT} (predeterminada de SCM)."


def install_service(start_auto: bool = True, account_name: str = "", password: str = "") -> str:
    _windows_only()
    _ensure_service_host_built()
    if not SERVICE_SCRIPT.exists():
        raise RuntimeError("No encontre yarbis_service.py para instalar el servicio.")

    status = get_service_status(force=True)
    start_value = "auto" if start_auto else "demand"
    if service_account_requires_password(account_name) and not str(password):
        raise ValueError(
            "La cuenta indicada para el servicio requiere password. "
            "Indica password o deja cuenta y password vacios para usar LocalSystem."
        )
    account_args = _service_account_args(account_name, password)

    if status["installed"]:
        config_args = [
            "config",
            SERVICE_NAME,
            "binPath=",
            _service_binary_path(),
            "start=",
            start_value,
            "DisplayName=",
            SERVICE_DISPLAY_NAME,
        ]
        config_args.extend(account_args)
        config = _run_sc(config_args)
        _ensure_success(config, "actualizar el servicio de Yarbis")
        _clear_service_status_cache()
        account_text = _service_account_result_text(account_name) if account_args else " Cuenta sin cambios."
        return (
            "Servicio de Yarbis ya instalado en SCM. Configuracion actualizada."
            + account_text
        )

    create_args = [
        "create",
        SERVICE_NAME,
        "binPath=",
        _service_binary_path(),
        "start=",
        start_value,
        "DisplayName=",
        SERVICE_DISPLAY_NAME,
    ]
    create_args.extend(account_args)
    create = _run_sc(create_args)
    _ensure_success(create, "instalar el servicio de Yarbis")

    description = _run_sc(["description", SERVICE_NAME, SERVICE_DESCRIPTION])
    _ensure_success(description, "guardar la descripcion del servicio de Yarbis")
    _clear_service_status_cache()

    return "Servicio de Yarbis instalado en SCM." + _service_account_result_text(account_name)


def remove_service() -> str:
    _windows_only()
    status = get_service_status(force=True)
    if not status["installed"]:
        return "El servicio de Yarbis no esta instalado en SCM."

    if status["running"]:
        stop_service()

    delete = _run_sc(["delete", SERVICE_NAME])
    _ensure_success(delete, "quitar el servicio de Yarbis")
    _clear_service_status_cache()
    return "Servicio de Yarbis quitado de SCM."


def start_service(account_name: str = "", password: str = "") -> str:
    _windows_only()
    status = get_service_status(force=True)
    if not status["installed"]:
        if str(account_name).strip() or str(password):
            install_service(start_auto=False, account_name=account_name, password=password)
        else:
            install_service(start_auto=False)
        status = get_service_status(force=True)
    else:
        _ensure_service_host_built()

    if status["running"]:
        return f"El servicio de Yarbis ya esta activo en SCM (PID {status['pid']})."

    def start_once() -> str:
        start = _run_sc(["start", SERVICE_NAME], timeout_seconds=45)
        _ensure_success(start, "iniciar el servicio de Yarbis")

        deadline = time.monotonic() + STARTUP_WAIT_SECONDS
        while time.monotonic() < deadline:
            refreshed = get_service_status(force=True)
            if refreshed["running"]:
                return f"Servicio de Yarbis iniciado por SCM (PID {refreshed['pid']})."
            time.sleep(0.5)

        return "SCM recibio la orden de inicio, pero no pude confirmar que Yarbis quedara activo."

    try:
        return start_once()
    except ServiceLogonFailure:
        if str(account_name).strip() or str(password):
            raise

        repair_result = install_service(
            start_auto=bool(status.get("autostart_enabled")),
            account_name=SERVICE_DEFAULT_ACCOUNT,
        )
        started_result = start_once()
        return (
            f"{repair_result}\n"
            "Cuenta del servicio restablecida a LocalSystem tras el error SCM 1069.\n"
            f"{started_result}"
        )


def stop_service(timeout_seconds: float = STOP_WAIT_SECONDS) -> str:
    _windows_only()
    status = get_service_status(force=True)
    if not status["installed"]:
        return "El servicio de Yarbis no esta instalado en SCM."
    if not status["running"]:
        return "El servicio de Yarbis ya estaba detenido."

    stop = _run_sc(["stop", SERVICE_NAME], timeout_seconds=45)
    _ensure_success(stop, "detener el servicio de Yarbis")

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        refreshed = get_service_status(force=True)
        if not refreshed["running"]:
            return "Servicio de Yarbis detenido por SCM."
        time.sleep(0.5)

    return "SCM recibio la orden de parada, pero Yarbis aun aparece activo."


def set_autostart_enabled(enabled: bool) -> str:
    _windows_only()
    status = get_service_status(force=True)
    if not status["installed"]:
        install_service(start_auto=bool(enabled))
        return (
            "Servicio de Yarbis instalado en SCM con arranque automatico."
            if enabled
            else "Servicio de Yarbis instalado en SCM con arranque manual."
        )

    start_value = "auto" if enabled else "demand"
    config = _run_sc(["config", SERVICE_NAME, "start=", start_value])
    _ensure_success(config, "cambiar el tipo de arranque del servicio de Yarbis")
    _clear_service_status_cache()
    if enabled:
        return "Yarbis quedo configurado para iniciar automaticamente con Windows desde SCM."
    return "Yarbis quedo configurado con inicio manual desde SCM."
