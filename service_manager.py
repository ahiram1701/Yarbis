import ctypes
import os
import signal
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

AUTOSTART_VALUE_NAME = "Yarbis Service"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_WAIT_SECONDS = 3.0
STOP_WAIT_SECONDS = 10.0


def _runtime_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_pid() -> int | None:
    try:
        raw_pid = PID_FILE.read_text(encoding="utf-8").strip()
        return int(raw_pid)
    except (OSError, TypeError, ValueError):
        return None


def _write_pid(pid: int):
    _runtime_path(PID_FILE).write_text(str(int(pid)), encoding="utf-8")


def _remove_file(path: Path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _is_windows_process_alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259

    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        int(pid),
    )
    if not handle:
        return False

    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _is_process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False

    if os.name == "nt":
        return _is_windows_process_alive(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _preferred_pythonw() -> Path:
    venv_pythonw = WORKSPACE_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return venv_pythonw

    current_python = Path(sys.executable)
    sibling_pythonw = current_python.with_name("pythonw.exe")
    if sibling_pythonw.exists():
        return sibling_pythonw

    return current_python


def _launch_command() -> list[str]:
    return [str(_preferred_pythonw()), str(SERVICE_SCRIPT)]


def _autostart_command() -> str:
    return " ".join(f'"{part}"' for part in _launch_command())


def _startupinfo():
    if os.name != "nt":
        return None

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def _creationflags() -> int:
    if os.name != "nt":
        return 0

    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS


def _open_log_for_append():
    _runtime_path(LOG_FILE)
    return open(LOG_FILE, "a", encoding="utf-8")


def is_service_running() -> bool:
    return _is_process_alive(_read_pid())


def get_service_status() -> dict:
    pid = _read_pid()
    running = _is_process_alive(pid)
    if pid and not running:
        _remove_file(PID_FILE)

    return {
        "running": running,
        "pid": pid if running else None,
        "autostart_enabled": is_autostart_enabled(),
        "log_file": str(LOG_FILE),
        "launch_command": _autostart_command(),
    }


def start_service() -> str:
    status = get_service_status()
    if status["running"]:
        return f"El servicio de Yarbis ya esta activo (PID {status['pid']})."

    if not SERVICE_SCRIPT.exists():
        raise RuntimeError("No encontre yarbis_service.py para iniciar el servicio.")

    _remove_file(STOP_FILE)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    with _open_log_for_append() as log_file:
        process = subprocess.Popen(
            _launch_command(),
            cwd=WORKSPACE_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            startupinfo=_startupinfo(),
            creationflags=_creationflags(),
        )

    _write_pid(process.pid)
    deadline = time.monotonic() + STARTUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _is_process_alive(process.pid):
            return f"Servicio de Yarbis iniciado (PID {process.pid})."
        if process.poll() is not None:
            break
        time.sleep(0.2)

    _remove_file(PID_FILE)
    return "No pude confirmar el arranque del servicio. Revisa .yarbis_runtime/service.log."


def _request_stop():
    _runtime_path(STOP_FILE).write_text("stop", encoding="utf-8")


def _terminate_process(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def stop_service(timeout_seconds: float = STOP_WAIT_SECONDS) -> str:
    pid = _read_pid()
    if not _is_process_alive(pid):
        _remove_file(PID_FILE)
        _remove_file(STOP_FILE)
        return "El servicio de Yarbis ya estaba inactivo."

    _request_stop()
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if not _is_process_alive(pid):
            _remove_file(PID_FILE)
            _remove_file(STOP_FILE)
            return "Servicio de Yarbis detenido."
        time.sleep(0.25)

    if _terminate_process(pid):
        time.sleep(0.5)

    _remove_file(PID_FILE)
    _remove_file(STOP_FILE)
    if _is_process_alive(pid):
        return "Solicite detener el servicio, pero Windows aun reporta el proceso activo."

    return "Servicio de Yarbis detenido forzadamente."


def _open_run_key(write: bool = False):
    if os.name != "nt":
        raise RuntimeError("El arranque con Windows solo esta disponible en Windows.")

    import winreg

    access = winreg.KEY_READ
    if write:
        access |= winreg.KEY_SET_VALUE
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)

    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)


def is_autostart_enabled() -> bool:
    if os.name != "nt":
        return False

    import winreg

    try:
        with _open_run_key(write=False) as key:
            value, _value_type = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
    except FileNotFoundError:
        return False
    except OSError:
        return False

    return bool(str(value).strip())


def set_autostart_enabled(enabled: bool) -> str:
    if os.name != "nt":
        raise RuntimeError("El arranque con Windows solo esta disponible en Windows.")

    import winreg

    with _open_run_key(write=True) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                AUTOSTART_VALUE_NAME,
                0,
                winreg.REG_SZ,
                _autostart_command(),
            )
            return "Yarbis se abrira como servicio al iniciar Windows."

        try:
            winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
        except FileNotFoundError:
            pass
        return "Yarbis ya no se abrira automaticamente al iniciar Windows."
