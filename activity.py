import threading
from datetime import datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = WORKSPACE_ROOT / ".yarbis_runtime"
ACTIVITY_LOG_FILE = RUNTIME_DIR / "activity.log"

_ACTIVITY_LOCK = threading.RLock()


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_title(title: object) -> str:
    cleaned = " ".join(str(title).strip().split())
    return cleaned or "Actividad"


def _clean_content(content: object) -> str:
    rendered = str(content).replace("\r\n", "\n").replace("\r", "\n").strip()
    return rendered or "Sin salida adicional."


def format_activity_entry(title: object, content: object = "", timestamp: str | None = None) -> str:
    return f"[{timestamp or _timestamp()}] {_clean_title(title)}\n{_clean_content(content)}\n\n"


def append_activity(title: object, content: object = "", timestamp: str | None = None) -> str:
    rendered = format_activity_entry(title, content=content, timestamp=timestamp)
    with _ACTIVITY_LOCK:
        ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ACTIVITY_LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(rendered)
    return rendered


def read_activity_log() -> str:
    with _ACTIVITY_LOCK:
        try:
            return ACTIVITY_LOG_FILE.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError as exc:
            return format_activity_entry(
                "Actividad",
                f"No pude leer el historial de actividad: {exc}",
            )


def clear_activity_log():
    with _ACTIVITY_LOCK:
        ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVITY_LOG_FILE.write_text("", encoding="utf-8")
