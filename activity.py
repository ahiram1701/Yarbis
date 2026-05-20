import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from secrets_redaction import build_secret_redactor

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = WORKSPACE_ROOT / ".yarbis_runtime"
ACTIVITY_LOG_FILE = RUNTIME_DIR / "activity.log"
EVENTS_FILE = RUNTIME_DIR / "events.jsonl"
DEFAULT_ACTIVITY_MAX_BYTES = 256 * 1024
MAX_EVENTS_FILE_BYTES = 512 * 1024
KEEP_EVENTS_FILE_BYTES = 256 * 1024

_ACTIVITY_LOCK = threading.RLock()


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _event_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_title(title: object, redactor=None) -> str:
    redactor = redactor or build_secret_redactor()
    cleaned = " ".join(redactor(title).strip().split())
    return cleaned or "Actividad"


def _clean_content(content: object, redactor=None) -> str:
    redactor = redactor or build_secret_redactor()
    rendered = redactor(content).replace("\r\n", "\n").replace("\r", "\n").strip()
    return rendered or "Sin salida adicional."


def _clean_event_value(value, redactor=None):
    redactor = redactor or build_secret_redactor()
    if isinstance(value, str):
        return redactor(value)
    if isinstance(value, dict):
        return {
            str(key): _clean_event_value(item, redactor=redactor)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_clean_event_value(item, redactor=redactor) for item in value]
    if isinstance(value, tuple):
        return [_clean_event_value(item, redactor=redactor) for item in value]
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


def format_activity_entry(title: object, content: object = "", timestamp: str | None = None, redactor=None) -> str:
    redactor = redactor or build_secret_redactor()
    return (
        f"[{timestamp or _timestamp()}] {_clean_title(title, redactor=redactor)}\n"
        f"{_clean_content(content, redactor=redactor)}\n\n"
    )


def append_activity(title: object, content: object = "", timestamp: str | None = None) -> str:
    redactor = build_secret_redactor()
    rendered = format_activity_entry(title, content=content, timestamp=timestamp, redactor=redactor)
    with _ACTIVITY_LOCK:
        ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ACTIVITY_LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(rendered)
    return rendered


def emit_event(event_type: object, operation_id: str | None = None, **fields) -> dict:
    redactor = build_secret_redactor()
    event = {
        "timestamp": _event_timestamp(),
        "type": str(event_type).strip() or "event",
    }
    if operation_id:
        event["operation_id"] = str(operation_id).strip()
    for key, value in fields.items():
        event[str(key)] = _clean_event_value(value, redactor=redactor)

    try:
        with _ACTIVITY_LOCK:
            EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(EVENTS_FILE, "a", encoding="utf-8") as events_file:
                events_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            _trim_events_file_unlocked()
    except OSError:
        pass
    return event


def _read_tail_bytes(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with open(path, "rb") as file:
        file.seek(max(0, size - max_bytes))
        data = file.read()

    text = data.decode("utf-8", errors="replace")
    if size > max_bytes and "\n" in text:
        text = text.split("\n", 1)[1]
    return text


def _trim_events_file_unlocked() -> None:
    try:
        if not EVENTS_FILE.exists() or EVENTS_FILE.stat().st_size <= MAX_EVENTS_FILE_BYTES:
            return
        text = _read_tail_bytes(EVENTS_FILE, KEEP_EVENTS_FILE_BYTES)
        EVENTS_FILE.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _parse_event_timestamp(value: object) -> str:
    text = str(value).strip()
    if not text:
        return _timestamp()
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _event_title(event: dict) -> str:
    event_type = str(event.get("type", "event")).strip() or "event"
    label = str(event.get("label", "")).strip()
    if label:
        if event_type == "operation_started":
            return f"{label} iniciado"
        if event_type == "operation_finished":
            return f"{label} finalizado"
        if event_type == "remote_job_started":
            return f"{label} remoto iniciado"
        if event_type == "remote_job_finished":
            return f"{label} remoto finalizado"
        if event_type == "remote_job_failed":
            return f"{label} remoto con error"
        return label
    return event_type.replace("_", " ").capitalize()


def _event_content(event: dict) -> str:
    preferred_keys = ("content", "status_text", "reason")
    for key in preferred_keys:
        value = str(event.get(key, "")).strip()
        if value:
            return value

    details = []
    for key in ("operation_id", "source", "last_pulse_at", "recovered_user_message"):
        if key in event and str(event.get(key)).strip():
            details.append(f"{key}: {event[key]}")
    return "\n".join(details) if details else "Sin salida adicional."


def read_recent_events(limit: int = 80) -> list[dict]:
    try:
        normalized_limit = max(1, min(500, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 80

    with _ACTIVITY_LOCK:
        try:
            if EVENTS_FILE.stat().st_size > KEEP_EVENTS_FILE_BYTES:
                lines = _read_tail_bytes(EVENTS_FILE, KEEP_EVENTS_FILE_BYTES).splitlines()
            else:
                lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError:
            return []

    events = []
    for line in lines[-normalized_limit:]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def render_recent_events(limit: int = 80) -> str:
    rendered = []
    redactor = build_secret_redactor()
    for event in read_recent_events(limit=limit):
        rendered.append(format_activity_entry(
            _event_title(event),
            _event_content(event),
            timestamp=_parse_event_timestamp(event.get("timestamp", "")),
            redactor=redactor,
        ))
    return "".join(rendered)


def _read_text_tail(path: Path, max_bytes: int | None = None) -> str:
    if max_bytes is None:
        return path.read_text(encoding="utf-8")

    try:
        normalized_max = max(1, int(max_bytes))
    except (TypeError, ValueError):
        normalized_max = DEFAULT_ACTIVITY_MAX_BYTES

    size = path.stat().st_size
    if size <= normalized_max:
        return path.read_text(encoding="utf-8")

    with open(path, "rb") as file:
        file.seek(max(0, size - normalized_max))
        data = file.read()

    text = data.decode("utf-8", errors="replace")
    if "\n" in text:
        text = text.split("\n", 1)[1]
    return "[historial anterior omitido]\n" + text


def read_activity_log(max_bytes: int | None = None) -> str:
    with _ACTIVITY_LOCK:
        try:
            return _read_text_tail(ACTIVITY_LOG_FILE, max_bytes=max_bytes)
        except FileNotFoundError:
            return ""
        except OSError as exc:
            return format_activity_entry(
                "Actividad",
                f"No pude leer el historial de actividad: {exc}",
            )


def read_activity_history(event_limit: int = 80, activity_max_bytes: int = DEFAULT_ACTIVITY_MAX_BYTES) -> str:
    human_log = read_activity_log(max_bytes=activity_max_bytes)
    event_log = render_recent_events(limit=event_limit)
    if human_log and event_log:
        return human_log + "Eventos recientes\n\n" + event_log
    return human_log or event_log


def activity_history_signature() -> tuple[tuple[int, int], tuple[int, int]]:
    def file_signature(path: Path) -> tuple[int, int]:
        try:
            stat = path.stat()
        except OSError:
            return (0, 0)
        return (int(stat.st_size), int(stat.st_mtime_ns))

    with _ACTIVITY_LOCK:
        return (
            file_signature(ACTIVITY_LOG_FILE),
            file_signature(EVENTS_FILE),
        )


def clear_activity_log():
    with _ACTIVITY_LOCK:
        ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVITY_LOG_FILE.write_text("", encoding="utf-8")


def clear_activity_history():
    clear_activity_log()
    with _ACTIVITY_LOCK:
        try:
            EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            EVENTS_FILE.write_text("", encoding="utf-8")
        except OSError:
            pass
