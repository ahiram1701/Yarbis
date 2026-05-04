import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from secrets_redaction import redact_secrets

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = WORKSPACE_ROOT / ".yarbis_runtime"
ACTIVITY_LOG_FILE = RUNTIME_DIR / "activity.log"
EVENTS_FILE = RUNTIME_DIR / "events.jsonl"

_ACTIVITY_LOCK = threading.RLock()


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _event_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_title(title: object) -> str:
    cleaned = " ".join(redact_secrets(title).strip().split())
    return cleaned or "Actividad"


def _clean_content(content: object) -> str:
    rendered = redact_secrets(content).replace("\r\n", "\n").replace("\r", "\n").strip()
    return rendered or "Sin salida adicional."


def _clean_event_value(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {
            str(key): _clean_event_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_clean_event_value(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_event_value(item) for item in value]
    return value


def new_operation_id(label: object = "op") -> str:
    prefix = "".join(
        char.lower()
        for char in str(label).strip()
        if char.isalnum()
    )[:18]
    if not prefix:
        prefix = "op"
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def format_activity_entry(title: object, content: object = "", timestamp: str | None = None) -> str:
    return f"[{timestamp or _timestamp()}] {_clean_title(title)}\n{_clean_content(content)}\n\n"


def append_activity(title: object, content: object = "", timestamp: str | None = None) -> str:
    rendered = format_activity_entry(title, content=content, timestamp=timestamp)
    with _ACTIVITY_LOCK:
        ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ACTIVITY_LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(rendered)
    return rendered


def emit_event(event_type: object, operation_id: str | None = None, **fields) -> dict:
    event = {
        "timestamp": _event_timestamp(),
        "type": str(event_type).strip() or "event",
    }
    if operation_id:
        event["operation_id"] = str(operation_id).strip()
    for key, value in fields.items():
        event[str(key)] = _clean_event_value(value)

    try:
        with _ACTIVITY_LOCK:
            EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(EVENTS_FILE, "a", encoding="utf-8") as events_file:
                events_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass
    return event


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
