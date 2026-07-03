import ctypes
import ctypes.wintypes
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pc_context import local_context_enabled, normalize_local_context_settings
from memory import load_state
from process_utils import no_window_creationflags
import yarbis_instance

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = yarbis_instance.runtime_dir()
HELPER_SCRIPT = WORKSPACE_ROOT / "pc_context_tray.py"
DESKTOP_SCRIPT = WORKSPACE_ROOT / "yarbis_desktop.py"
HELPER_PID_FILE = RUNTIME_DIR / "pc_context_helper.pid"
HELPER_STOP_FILE = RUNTIME_DIR / "pc_context_helper.stop"
HELPER_STATUS_FILE = RUNTIME_DIR / "pc_context_helper.status.json"
TASK_NAME = yarbis_instance.pc_context_task_name()
HELPER_MUTEX_NAME = yarbis_instance.pc_context_mutex_name()
HELPER_STOP_WAIT_SECONDS = 6.0

_HELPER_MUTEX_HANDLE = None
_ERROR_ALREADY_EXISTS = 183
_WINDOWS_STILL_ACTIVE = 259


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _creationflags() -> int:
    return no_window_creationflags()


def _windows_only() -> None:
    if os.name != "nt":
        raise RuntimeError("El helper de contexto local persistente solo esta disponible en Windows.")


def _quote(value: str | Path) -> str:
    return f'"{value}"'


def _pythonw_path() -> Path:
    candidate = WORKSPACE_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate

    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return pythonw
    return executable


def _helper_command() -> str:
    return f"{_quote(_pythonw_path())} {_quote(HELPER_SCRIPT)} --instance {_quote(yarbis_instance.current_instance_id())}"


def _schtasks_exe() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(system_root) / "System32" / "schtasks.exe"
    if candidate.exists():
        return str(candidate)
    return "schtasks.exe"


def _task_create_args() -> list[str]:
    return [
        _schtasks_exe(),
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        _helper_command(),
        "/SC",
        "ONLOGON",
        "/RL",
        "LIMITED",
        "/F",
    ]


def _task_query_args() -> list[str]:
    return [_schtasks_exe(), "/Query", "/TN", TASK_NAME]


def _task_delete_args() -> list[str]:
    return [_schtasks_exe(), "/Delete", "/TN", TASK_NAME, "/F"]


def _run_schtasks(args: list[str], timeout_seconds: int = 30) -> subprocess.CompletedProcess:
    _windows_only()
    return subprocess.run(
        args,
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


def _task_missing(completed: subprocess.CompletedProcess) -> bool:
    output = _completed_output(completed).lower()
    return completed.returncode != 0 and (
        "cannot find" in output
        or "no se encuentra" in output
        or "no se puede encontrar" in output
        or "does not exist" in output
        or "no existe" in output
    )


def task_exists() -> bool:
    if os.name != "nt":
        return False
    completed = _run_schtasks(_task_query_args())
    return completed.returncode == 0


def ensure_context_task() -> str:
    if os.name != "nt":
        return "Tarea de contexto local omitida: solo esta disponible en Windows."

    if not HELPER_SCRIPT.exists():
        raise RuntimeError("No encontre pc_context_tray.py para registrar el helper de contexto local.")

    completed = _run_schtasks(_task_create_args())
    if completed.returncode != 0:
        raise RuntimeError(
            f"No pude crear/actualizar la tarea programada {TASK_NAME}.\n"
            + (_completed_output(completed) or f"exit={completed.returncode}")
        )
    return f"Tarea programada {TASK_NAME} lista para iniciar con tu sesion de Windows."


def remove_context_task() -> str:
    if os.name != "nt":
        return "Tarea de contexto local omitida: solo esta disponible en Windows."

    completed = _run_schtasks(_task_delete_args())
    if completed.returncode == 0:
        return f"Tarea programada {TASK_NAME} eliminada."
    if _task_missing(completed):
        return f"La tarea programada {TASK_NAME} no estaba registrada."
    raise RuntimeError(
        f"No pude eliminar la tarea programada {TASK_NAME}.\n"
        + (_completed_output(completed) or f"exit={completed.returncode}")
    )


def _read_pid() -> int | None:
    try:
        value = int(HELPER_PID_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None
    return value if value > 0 else None


def write_helper_pid(pid: int | None = None) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    HELPER_PID_FILE.write_text(str(pid or os.getpid()), encoding="utf-8")


def clear_helper_pid(pid: int | None = None) -> None:
    current_pid = _read_pid()
    if pid is not None and current_pid not in {None, int(pid)}:
        return
    try:
        HELPER_PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _pid_is_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False

    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
            kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                ctypes.wintypes.HANDLE,
                ctypes.POINTER(ctypes.wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess.restype = ctypes.wintypes.BOOL
            kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
            kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                exit_code = ctypes.wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return int(exit_code.value) == _WINDOWS_STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def helper_is_running() -> bool:
    pid = _read_pid()
    running = _pid_is_running(pid)
    if pid and not running:
        clear_helper_pid(pid)
    return running


def clear_helper_stop_request() -> None:
    try:
        HELPER_STOP_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def request_helper_stop() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    HELPER_STOP_FILE.write_text(_utc_timestamp(), encoding="utf-8")


def helper_stop_requested() -> bool:
    return HELPER_STOP_FILE.exists()


def start_context_helper() -> str:
    if helper_is_running():
        return f"Helper de contexto local ya activo (PID {_read_pid()})."

    if not HELPER_SCRIPT.exists():
        raise RuntimeError("No encontre pc_context_tray.py para iniciar el helper de contexto local.")

    clear_helper_stop_request()
    pythonw = _pythonw_path()
    subprocess.Popen(
        [str(pythonw), str(HELPER_SCRIPT), "--instance", yarbis_instance.current_instance_id()],
        cwd=str(WORKSPACE_ROOT),
        env=yarbis_instance.with_instance_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creationflags(),
    )
    return "Helper de contexto local iniciado en la sesion interactiva."


def stop_context_helper(timeout_seconds: float = HELPER_STOP_WAIT_SECONDS) -> str:
    pid = _read_pid()
    if not _pid_is_running(pid):
        clear_helper_pid(pid)
        write_helper_status({"state": "sin helper", "detail": "Helper detenido."})
        return "El helper de contexto local no estaba activo."

    request_helper_stop()
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            clear_helper_pid(pid)
            write_helper_status({"state": "sin helper", "detail": "Helper detenido."})
            return "Helper de contexto local detenido."
        time.sleep(0.25)

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                cwd=str(WORKSPACE_ROOT),
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=_creationflags(),
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass

    if not _pid_is_running(pid):
        clear_helper_pid(pid)
        write_helper_status({"state": "sin helper", "detail": "Helper detenido."})
        return "Helper de contexto local detenido."
    return "Solicitud enviada, pero el helper de contexto local aun aparece activo."


def write_helper_status(status: dict) -> None:
    payload = dict(status) if isinstance(status, dict) else {"state": "error", "detail": str(status)}
    payload.setdefault("pid", os.getpid())
    payload["updated_at"] = _utc_timestamp()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = HELPER_STATUS_FILE.with_name(f"{HELPER_STATUS_FILE.name}.tmp")
    tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_file.replace(HELPER_STATUS_FILE)


def read_helper_status() -> dict:
    try:
        data = json.loads(HELPER_STATUS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def get_context_helper_status(include_task: bool = False) -> dict:
    pid = _read_pid()
    running = _pid_is_running(pid)
    if pid and not running:
        clear_helper_pid(pid)

    try:
        settings = normalize_local_context_settings(load_state().get("local_context", {}))
    except Exception:
        settings = normalize_local_context_settings({})
    context_enabled = local_context_enabled(settings)

    helper_status = read_helper_status()
    state = "sin helper"
    detail = "Helper no iniciado."
    if not context_enabled:
        state = "desactivado"
        detail = "Contexto local desactivado por configuracion."
    elif running:
        state = str(helper_status.get("state", "pausado")).strip() or "pausado"
        detail = str(helper_status.get("detail", "")).strip()

    return {
        "task_name": TASK_NAME,
        "task_installed": task_exists() if include_task and os.name == "nt" else False,
        "running": bool(running),
        "pid": pid if running else None,
        "state": state,
        "detail": detail,
        "updated_at": helper_status.get("updated_at", ""),
        "captured_at": helper_status.get("captured_at", ""),
    }


def format_context_helper_status(status: dict | None = None) -> str:
    status = status or get_context_helper_status()
    state = str(status.get("state", "sin helper")).strip() or "sin helper"
    pid = status.get("pid")
    pid_text = f", PID {pid}" if pid else ""
    detail = str(status.get("detail", "")).strip()
    detail_text = f": {detail}" if detail else ""
    return f"contexto {state}{pid_text}{detail_text}"


def acquire_helper_instance_lock() -> bool:
    global _HELPER_MUTEX_HANDLE

    if os.name != "nt":
        return True
    if _HELPER_MUTEX_HANDLE:
        return True

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.wintypes.LPVOID,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

        handle = kernel32.CreateMutexW(None, False, HELPER_MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _HELPER_MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def release_helper_instance_lock() -> None:
    global _HELPER_MUTEX_HANDLE

    if os.name != "nt" or not _HELPER_MUTEX_HANDLE:
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        kernel32.CloseHandle(_HELPER_MUTEX_HANDLE)
    except Exception:
        pass
    finally:
        _HELPER_MUTEX_HANDLE = None


def open_desktop_app() -> str:
    pythonw = _pythonw_path()
    subprocess.Popen(
        [str(pythonw), str(DESKTOP_SCRIPT), "--instance", yarbis_instance.current_instance_id()],
        cwd=str(WORKSPACE_ROOT),
        env=yarbis_instance.with_instance_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creationflags(),
    )
    return "Abriendo Yarbis."
