import os
import subprocess
import sys
import time
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = WORKSPACE_ROOT / ".yarbis_runtime"
PID_FILE = RUNTIME_DIR / "service.pid"
STOP_FILE = RUNTIME_DIR / "service.stop"
LOG_FILE = RUNTIME_DIR / "service.log"
SERVICE_SCRIPT = WORKSPACE_ROOT / "yarbis_service.py"
SERVICE_HOST_PROJECT = WORKSPACE_ROOT / "service_host" / "YarbisServiceHost.csproj"
SERVICE_HOST_OUTPUT_DIR = RUNTIME_DIR / "service_host"
SERVICE_HOST_EXE = SERVICE_HOST_OUTPUT_DIR / "YarbisServiceHost.exe"

SERVICE_NAME = "Yarbis"
SERVICE_DISPLAY_NAME = "Yarbis"
SERVICE_DESCRIPTION = "Yarbis local agent background service."
STARTUP_WAIT_SECONDS = 15.0
STOP_WAIT_SECONDS = 15.0


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


def _ensure_success(completed: subprocess.CompletedProcess, action: str):
    if completed.returncode == 0:
        return

    output = _completed_output(completed)
    if "Access is denied" in output or "Acceso denegado" in output:
        raise PermissionError(
            f"No tengo permisos para {action}. Abre Yarbis como administrador e intenta de nuevo."
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


def get_service_status() -> dict:
    base = {
        "service_name": SERVICE_NAME,
        "display_name": SERVICE_DISPLAY_NAME,
        "installed": False,
        "running": False,
        "state": "not_installed",
        "pid": None,
        "autostart_enabled": False,
        "start_type": "not_installed",
        "log_file": str(LOG_FILE),
        "service_binary": _service_binary_path(),
    }

    if os.name != "nt":
        return base

    query = _run_sc(["queryex", SERVICE_NAME])
    if _service_missing(query):
        return base
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
    })
    return base


def is_service_running() -> bool:
    return bool(get_service_status()["running"])


def install_service(start_auto: bool = True) -> str:
    _windows_only()
    _ensure_service_host_built()
    if not SERVICE_SCRIPT.exists():
        raise RuntimeError("No encontre yarbis_service.py para instalar el servicio.")

    status = get_service_status()
    start_value = "auto" if start_auto else "demand"

    if status["installed"]:
        config = _run_sc([
            "config",
            SERVICE_NAME,
            "binPath=",
            _service_binary_path(),
            "start=",
            start_value,
            "DisplayName=",
            SERVICE_DISPLAY_NAME,
        ])
        _ensure_success(config, "actualizar el servicio de Yarbis")
        return "Servicio de Yarbis ya instalado en SCM. Configuracion actualizada."

    create = _run_sc([
        "create",
        SERVICE_NAME,
        "binPath=",
        _service_binary_path(),
        "start=",
        start_value,
        "DisplayName=",
        SERVICE_DISPLAY_NAME,
    ])
    _ensure_success(create, "instalar el servicio de Yarbis")

    description = _run_sc(["description", SERVICE_NAME, SERVICE_DESCRIPTION])
    _ensure_success(description, "guardar la descripcion del servicio de Yarbis")

    return "Servicio de Yarbis instalado en SCM."


def remove_service() -> str:
    _windows_only()
    status = get_service_status()
    if not status["installed"]:
        return "El servicio de Yarbis no esta instalado en SCM."

    if status["running"]:
        stop_service()

    delete = _run_sc(["delete", SERVICE_NAME])
    _ensure_success(delete, "quitar el servicio de Yarbis")
    return "Servicio de Yarbis quitado de SCM."


def start_service() -> str:
    _windows_only()
    status = get_service_status()
    if not status["installed"]:
        install_service(start_auto=False)
        status = get_service_status()
    else:
        _ensure_service_host_built()

    if status["running"]:
        return f"El servicio de Yarbis ya esta activo en SCM (PID {status['pid']})."

    start = _run_sc(["start", SERVICE_NAME], timeout_seconds=45)
    _ensure_success(start, "iniciar el servicio de Yarbis")

    deadline = time.monotonic() + STARTUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        refreshed = get_service_status()
        if refreshed["running"]:
            return f"Servicio de Yarbis iniciado por SCM (PID {refreshed['pid']})."
        time.sleep(0.5)

    return "SCM recibio la orden de inicio, pero no pude confirmar que Yarbis quedara activo."


def stop_service(timeout_seconds: float = STOP_WAIT_SECONDS) -> str:
    _windows_only()
    status = get_service_status()
    if not status["installed"]:
        return "El servicio de Yarbis no esta instalado en SCM."
    if not status["running"]:
        return "El servicio de Yarbis ya estaba detenido."

    stop = _run_sc(["stop", SERVICE_NAME], timeout_seconds=45)
    _ensure_success(stop, "detener el servicio de Yarbis")

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        refreshed = get_service_status()
        if not refreshed["running"]:
            return "Servicio de Yarbis detenido por SCM."
        time.sleep(0.5)

    return "SCM recibio la orden de parada, pero Yarbis aun aparece activo."


def set_autostart_enabled(enabled: bool) -> str:
    _windows_only()
    status = get_service_status()
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
    if enabled:
        return "Yarbis quedo configurado para iniciar automaticamente con Windows desde SCM."
    return "Yarbis quedo configurado con inicio manual desde SCM."
