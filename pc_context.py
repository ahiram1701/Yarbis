import ctypes
import ctypes.wintypes as wintypes
import json
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from secrets_redaction import redact_secrets
from process_utils import no_window_creationflags
import yarbis_instance

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = yarbis_instance.runtime_dir()
SNAPSHOT_FILE = RUNTIME_DIR / "pc_context_latest.json"
EVENTS_FILE = RUNTIME_DIR / "pc_context_events.jsonl"

SNAPSHOT_SCHEMA_VERSION = 1
MAX_TEXT_CHARS = 180
MAX_EVENTS_FILE_BYTES = 512 * 1024
KEEP_EVENTS_FILE_BYTES = 256 * 1024
MAX_GIT_CHANGES = 12
MAX_RECENT_FILES = 12
RECENT_FILE_SECONDS = 60 * 60
IGNORED_WORKSPACE_DIRS = {
    ".git",
    ".venv",
    ".yarbis_runtime",
    ".yarbis_instances",
    ".yarbis_checkpoints",
    ".ruff_cache",
    "__pycache__",
    "tests_runtime",
}
IGNORED_WORKSPACE_FILES = {
    "state.json",
    "state.json.tmp",
}
DEFAULT_SETTINGS = {
    "enabled": True,
    "mode": "safe",
    "sample_interval_seconds": 30,
    "max_snapshot_age_seconds": 180,
    "include_window_title": False,
    "include_process_name": True,
    "include_workspace_changes": True,
    "include_system_health": True,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_timestamp() -> str:
    return _utc_now().isoformat()


def _clean_text(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    cleaned = " ".join(redact_secrets(str(value)).replace("\r", " ").replace("\n", " ").split())
    if len(cleaned) > limit:
        return cleaned[: max(0, limit - 3)].rstrip() + "..."
    return cleaned


def _format_bytes(value: int | float | None) -> str:
    if not value:
        return "desconocido"

    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024

    return f"{amount:.1f} TB"


def normalize_local_context_settings(settings: dict | None) -> dict:
    if not isinstance(settings, dict):
        settings = {}

    normalized = dict(DEFAULT_SETTINGS)
    normalized.update({
        key: settings.get(key, default)
        for key, default in DEFAULT_SETTINGS.items()
    })

    mode = str(normalized.get("mode", "safe")).strip().lower()
    if mode not in {"off", "safe", "detailed"}:
        mode = "safe"
    normalized["mode"] = mode
    normalized["enabled"] = bool(normalized.get("enabled")) and mode != "off"
    normalized["include_window_title"] = (
        mode == "detailed"
        and bool(normalized.get("include_window_title"))
    )
    normalized["include_process_name"] = bool(normalized.get("include_process_name"))
    normalized["include_workspace_changes"] = bool(normalized.get("include_workspace_changes"))
    normalized["include_system_health"] = bool(normalized.get("include_system_health"))

    try:
        normalized["sample_interval_seconds"] = max(
            5,
            min(24 * 60 * 60, int(normalized.get("sample_interval_seconds", 30))),
        )
    except (TypeError, ValueError):
        normalized["sample_interval_seconds"] = 30

    try:
        normalized["max_snapshot_age_seconds"] = max(
            15,
            min(24 * 60 * 60, int(normalized.get("max_snapshot_age_seconds", 180))),
        )
    except (TypeError, ValueError):
        normalized["max_snapshot_age_seconds"] = 180

    return normalized


def local_context_enabled(settings: dict | None) -> bool:
    normalized = normalize_local_context_settings(settings)
    return bool(normalized["enabled"]) and normalized["mode"] != "off"


def _windows_creationflags() -> int:
    return no_window_creationflags()


def _safe_run_command(args: list[str], timeout_seconds: float = 2.0) -> str:
    try:
        completed = subprocess.run(
            args,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            creationflags=_windows_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""

    if completed.returncode != 0:
        return ""

    return completed.stdout.strip()


def _linux_idle_seconds() -> int | None:
    """Segundos de inactividad en Linux (X11) via xprintidle (milisegundos)."""
    if not shutil.which("xprintidle"):
        return None
    try:
        out = subprocess.run(
            ["xprintidle"], capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return None
        return max(0, int(int(out.stdout.strip()) / 1000))
    except (OSError, ValueError):
        return None


def _macos_idle_seconds() -> int | None:
    """Segundos de inactividad en macOS via ioreg (HIDIdleTime en nanosegundos)."""
    if not shutil.which("ioreg"):
        return None
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return None
        best = None
        for line in out.stdout.splitlines():
            if "HIDIdleTime" in line and "=" in line:
                try:
                    value = int(line.split("=", 1)[1].strip())
                except ValueError:
                    continue
                best = value if best is None else min(best, value)
        if best is None:
            return None
        return max(0, int(best / 1_000_000_000))
    except (OSError, ValueError):
        return None


def _get_idle_seconds() -> int | None:
    system = platform.system().lower()
    if system == "linux":
        return _linux_idle_seconds()
    if system == "darwin":
        return _macos_idle_seconds()
    if system != "windows":
        return None

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("dwTime", wintypes.DWORD),
        ]

    try:
        last_input = LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):
            return None
        tick_count = ctypes.windll.kernel32.GetTickCount()
        return max(0, int((tick_count - last_input.dwTime) / 1000))
    except (AttributeError, OSError, ValueError):
        return None


def _presence_state(idle_seconds: int | None) -> str:
    if idle_seconds is None:
        return "unknown"
    if idle_seconds < 60:
        return "active"
    if idle_seconds < 10 * 60:
        return "idle"
    return "away"


def _query_process_image_name(pid: int) -> str:
    if platform.system().lower() != "windows" or pid <= 0:
        return ""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return str(Path(buffer.value).name)
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return ""


def _get_window_title(hwnd) -> str:
    try:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except (AttributeError, OSError, ValueError):
        return ""


def _run_capture(args: list[str], timeout: float = 3) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            return ""
        return out.stdout.strip()
    except (OSError, ValueError):
        return ""


def _linux_foreground(result: dict, settings: dict) -> dict:
    """Ventana activa en Linux (X11) via xdotool. Best-effort."""
    if not shutil.which("xdotool"):
        return result
    wid = _run_capture(["xdotool", "getactivewindow"])
    if not wid:
        return result
    result["available"] = True
    if settings["include_window_title"]:
        title = _run_capture(["xdotool", "getwindowname", wid])
        result["title"] = _clean_text(title, 160)
    if settings["include_process_name"]:
        pid = _run_capture(["xdotool", "getwindowpid", wid])
        name = ""
        if pid.isdigit():
            try:
                name = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                name = ""
        result["process_name"] = _clean_text(name, 80)
    return result


def _macos_foreground(result: dict, settings: dict) -> dict:
    """App en primer plano en macOS via osascript. Best-effort (el titulo de la
    ventana suele requerir permisos de accesibilidad, asi que puede quedar vacio)."""
    if not shutil.which("osascript"):
        return result
    app = _run_capture([
        "osascript", "-e",
        'tell application "System Events" to get name of first application process whose frontmost is true',
    ])
    if not app:
        return result
    result["available"] = True
    if settings["include_process_name"]:
        result["process_name"] = _clean_text(app, 80)
    return result


def _get_foreground_context(settings: dict) -> dict:
    result = {
        "available": False,
        "process_name": "",
        "title": "",
    }
    system = platform.system().lower()
    if system == "linux":
        return _linux_foreground(result, settings)
    if system == "darwin":
        return _macos_foreground(result, settings)
    if system != "windows":
        return result

    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return result

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result["available"] = True
        if settings["include_process_name"]:
            result["process_name"] = _clean_text(_query_process_image_name(int(pid.value)), 80)
        if settings["include_window_title"]:
            result["title"] = _clean_text(_get_window_title(hwnd), 160)
    except (AttributeError, OSError, ValueError):
        return result

    return result


def _get_power_status() -> dict:
    result = {
        "available": False,
        "ac_line_status": "unknown",
        "battery_percent": None,
        "battery_life_seconds": None,
    }
    if platform.system().lower() != "windows":
        return result

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("SystemStatusFlag", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    try:
        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return result
        ac_line_status = {
            0: "battery",
            1: "plugged",
            255: "unknown",
        }.get(int(status.ACLineStatus), "unknown")
        battery_percent = None if int(status.BatteryLifePercent) == 255 else int(status.BatteryLifePercent)
        battery_life_seconds = None if int(status.BatteryLifeTime) == 0xFFFFFFFF else int(status.BatteryLifeTime)
        result.update({
            "available": True,
            "ac_line_status": ac_line_status,
            "battery_percent": battery_percent,
            "battery_life_seconds": battery_life_seconds,
        })
    except (AttributeError, OSError, ValueError):
        return result

    return result


def _get_memory_status() -> dict:
    result = {
        "available": False,
        "load_percent": None,
        "total": "",
        "available_memory": "",
    }
    if platform.system().lower() != "windows":
        return result

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

        def __init__(self):
            super().__init__()
            self.dwLength = ctypes.sizeof(self)

    try:
        status = MEMORYSTATUSEX()
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return result
        result.update({
            "available": True,
            "load_percent": int(status.dwMemoryLoad),
            "total": _format_bytes(int(status.ullTotalPhys)),
            "available_memory": _format_bytes(int(status.ullAvailPhys)),
        })
    except (AttributeError, OSError, ValueError):
        return result

    return result


def _get_disk_status() -> dict:
    disk_target = Path(WORKSPACE_ROOT.anchor) if WORKSPACE_ROOT.anchor else WORKSPACE_ROOT
    try:
        usage = shutil.disk_usage(disk_target)
    except OSError:
        return {
            "available": False,
            "target": str(disk_target),
            "free": "",
            "total": "",
            "free_percent": None,
        }

    free_percent = round((usage.free / usage.total) * 100, 1) if usage.total else None
    return {
        "available": True,
        "target": str(disk_target),
        "free": _format_bytes(usage.free),
        "total": _format_bytes(usage.total),
        "free_percent": free_percent,
    }


def _get_system_health() -> dict:
    return {
        "power": _get_power_status(),
        "memory": _get_memory_status(),
        "disk": _get_disk_status(),
    }


def _workspace_path_is_ignored(path: Path) -> bool:
    try:
        relative = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return True
    if relative.as_posix() in IGNORED_WORKSPACE_FILES:
        return True
    return any(part in IGNORED_WORKSPACE_DIRS for part in relative.parts)


def _iter_workspace_files():
    stack = [WORKSPACE_ROOT]
    while stack:
        directory = stack.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue

        with entries:
            for entry in entries:
                path = Path(entry.path)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not _workspace_path_is_ignored(path):
                            stack.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue

                if not _workspace_path_is_ignored(path):
                    yield path


def _get_git_changes() -> dict:
    output = _safe_run_command(["git", "status", "--short", "--untracked-files=normal"], timeout_seconds=2)
    if not output:
        return {
            "available": False,
            "change_count": 0,
            "changes": [],
            "truncated": False,
        }

    changes = [
        _clean_text(line, 180)
        for line in output.splitlines()
        if line.strip()
    ]
    return {
        "available": True,
        "change_count": len(changes),
        "changes": changes[:MAX_GIT_CHANGES],
        "truncated": len(changes) > MAX_GIT_CHANGES,
    }


def _get_recent_workspace_files() -> list[dict]:
    cutoff = time.time() - RECENT_FILE_SECONDS
    candidates = []

    for path in _iter_workspace_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            continue
        try:
            relative = path.relative_to(WORKSPACE_ROOT).as_posix()
        except ValueError:
            continue
        candidates.append({
            "path": _clean_text(relative, 180),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "size": _format_bytes(stat.st_size),
        })

    return sorted(candidates, key=lambda item: item["modified_at"], reverse=True)[:MAX_RECENT_FILES]


def _get_workspace_context() -> dict:
    return {
        "git": _get_git_changes(),
        "recent_files": _get_recent_workspace_files(),
    }


def collect_snapshot(settings: dict | None = None, source: str = "manual") -> dict:
    normalized_settings = normalize_local_context_settings(settings)
    idle_seconds = _get_idle_seconds()
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "captured_at": _utc_timestamp(),
        "source": _clean_text(source, 80),
        "settings": {
            "mode": normalized_settings["mode"],
            "include_window_title": normalized_settings["include_window_title"],
            "include_process_name": normalized_settings["include_process_name"],
            "include_workspace_changes": normalized_settings["include_workspace_changes"],
            "include_system_health": normalized_settings["include_system_health"],
        },
        "user_presence": {
            "idle_seconds": idle_seconds,
            "state": _presence_state(idle_seconds),
        },
        "foreground": _get_foreground_context(normalized_settings),
        "system_health": {},
        "workspace": {},
        "limitations": [],
    }

    if normalized_settings["include_system_health"]:
        snapshot["system_health"] = _get_system_health()
    if normalized_settings["include_workspace_changes"]:
        snapshot["workspace"] = _get_workspace_context()
    if platform.system().lower() != "windows":
        snapshot["limitations"].append("Contexto interactivo limitado fuera de Windows.")
    if normalized_settings["mode"] != "detailed":
        snapshot["limitations"].append("Titulos de ventana desactivados por modo seguro.")

    return snapshot


def write_snapshot(snapshot: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = SNAPSHOT_FILE.with_name(f"{SNAPSHOT_FILE.name}.tmp")
    with open(tmp_file, "w", encoding="utf-8") as file:
        json.dump(snapshot, file, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_file.replace(SNAPSHOT_FILE)


def append_snapshot_event(snapshot: dict) -> None:
    event = {
        "timestamp": snapshot.get("captured_at", _utc_timestamp()),
        "type": "pc_context_snapshot",
        "presence": snapshot.get("user_presence", {}),
        "foreground": snapshot.get("foreground", {}),
    }
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        _trim_snapshot_events_file()
        with open(EVENTS_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def _trim_snapshot_events_file() -> None:
    try:
        if not EVENTS_FILE.exists() or EVENTS_FILE.stat().st_size <= MAX_EVENTS_FILE_BYTES:
            return
        with open(EVENTS_FILE, "rb") as file:
            file.seek(max(0, EVENTS_FILE.stat().st_size - KEEP_EVENTS_FILE_BYTES))
            data = file.read()
        if b"\n" in data:
            data = data.split(b"\n", 1)[1]
        EVENTS_FILE.write_bytes(data)
    except OSError:
        pass


def collect_and_write_snapshot(settings: dict | None = None, source: str = "pc_context_worker") -> dict:
    snapshot = collect_snapshot(settings=settings, source=source)
    write_snapshot(snapshot)
    append_snapshot_event(snapshot)
    return snapshot


def clear_snapshot() -> None:
    for path in (SNAPSHOT_FILE, SNAPSHOT_FILE.with_name(f"{SNAPSHOT_FILE.name}.tmp")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def snapshot_age_seconds(snapshot: dict) -> float | None:
    captured_at = _parse_timestamp(snapshot.get("captured_at", ""))
    if captured_at is None:
        return None
    return max(0.0, (_utc_now() - captured_at).total_seconds())


def load_latest_snapshot(max_age_seconds: int | None = None) -> dict | None:
    try:
        data = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if max_age_seconds is not None:
        age = snapshot_age_seconds(data)
        if age is None or age > max_age_seconds:
            return None
    return data


def render_snapshot_summary(snapshot: dict) -> str:
    if not isinstance(snapshot, dict):
        return "Contexto local: sin snapshot disponible."

    lines = []
    age = snapshot_age_seconds(snapshot)
    age_text = f"hace {int(age)}s" if age is not None else "edad desconocida"
    lines.append(f"Snapshot local capturado {age_text}.")

    presence = snapshot.get("user_presence", {})
    if isinstance(presence, dict):
        idle_seconds = presence.get("idle_seconds")
        state = str(presence.get("state", "unknown")).strip() or "unknown"
        if idle_seconds is None:
            lines.append(f"Presencia del usuario: {state}.")
        else:
            lines.append(f"Presencia del usuario: {state}, idle={idle_seconds}s.")

    foreground = snapshot.get("foreground", {})
    if isinstance(foreground, dict) and foreground.get("available"):
        parts = []
        process_name = str(foreground.get("process_name", "")).strip()
        title = str(foreground.get("title", "")).strip()
        if process_name:
            parts.append(f"proceso={process_name}")
        if title:
            parts.append(f"titulo={title}")
        if parts:
            lines.append("Ventana en primer plano: " + ", ".join(parts) + ".")
        else:
            lines.append("Ventana en primer plano detectada; detalles desactivados.")

    health = snapshot.get("system_health", {})
    if isinstance(health, dict):
        power = health.get("power", {})
        if isinstance(power, dict) and power.get("available"):
            battery = power.get("battery_percent")
            ac_line_status = power.get("ac_line_status", "unknown")
            battery_text = f", bateria={battery}%" if battery is not None else ""
            lines.append(f"Energia: {ac_line_status}{battery_text}.")

        memory = health.get("memory", {})
        if isinstance(memory, dict) and memory.get("available"):
            lines.append(
                "Memoria: "
                f"uso={memory.get('load_percent')}%, "
                f"libre={memory.get('available_memory')}, "
                f"total={memory.get('total')}."
            )

        disk = health.get("disk", {})
        if isinstance(disk, dict) and disk.get("available"):
            lines.append(
                "Disco del workspace: "
                f"libre={disk.get('free')} de {disk.get('total')} "
                f"({disk.get('free_percent')}%)."
            )

    workspace = snapshot.get("workspace", {})
    if isinstance(workspace, dict):
        git = workspace.get("git", {})
        if isinstance(git, dict) and git.get("available"):
            lines.append(f"Git workspace: {git.get('change_count', 0)} cambio(s) detectado(s).")
            changes = git.get("changes", [])
            if isinstance(changes, list) and changes:
                rendered = "; ".join(str(item) for item in changes[:5])
                if git.get("truncated"):
                    rendered += "; ..."
                lines.append("Cambios visibles: " + rendered)

        recent_files = workspace.get("recent_files", [])
        if isinstance(recent_files, list) and recent_files:
            rendered_files = []
            for item in recent_files[:5]:
                if isinstance(item, dict):
                    rendered_files.append(str(item.get("path", "")).strip())
            rendered_files = [item for item in rendered_files if item]
            if rendered_files:
                lines.append("Archivos tocados recientemente: " + "; ".join(rendered_files))

    limitations = snapshot.get("limitations", [])
    if isinstance(limitations, list) and limitations:
        lines.append("Limites del snapshot: " + " ".join(str(item).strip() for item in limitations if str(item).strip()))

    return "\n".join(lines)
