import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

DEFAULT_CALENDAR_DIR = ".yarbis_runtime/calendar"


def _split_items(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[\n,;]+", str(value or ""))
        if item.strip()
    ]


def _runtime_path(path: str, workspace_root: Path, default_dir: str, default_name: str) -> Path:
    cleaned_path = str(path).strip()
    if cleaned_path:
        raw_path = Path(cleaned_path)
        candidate = raw_path if raw_path.is_absolute() else workspace_root / raw_path
    else:
        candidate = workspace_root / default_dir / default_name

    resolved = candidate.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _parse_iso_datetime(value: str, field_name: str) -> datetime:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{field_name} no puede quedar vacio.")
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} debe estar en formato ISO, por ejemplo 2026-05-06T15:00:00.") from exc


def _format_ics_datetime(value: datetime) -> str:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return value.strftime("%Y%m%dT%H%M%S")


def _escape_ics_text(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold_ics_line(line: str) -> list[str]:
    if len(line) <= 75:
        return [line]

    folded = []
    remaining = line
    while len(remaining) > 75:
        folded.append(remaining[:75])
        remaining = " " + remaining[75:]
    folded.append(remaining)
    return folded


def _render_ics(fields: list[str]) -> str:
    lines = []
    for field in fields:
        lines.extend(_fold_ics_line(field))
    return "\r\n".join(lines) + "\r\n"


def _open_default(target: str):
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606  # User-requested OS integration.
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def create_calendar_event_file(
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    attendees: str = "",
    output_path: str = "",
    open_file: bool = False,
    workspace_root: Path | None = None,
) -> str:
    workspace_root = workspace_root or Path.cwd()
    safe_title = str(title).strip()
    if not safe_title:
        raise ValueError("El titulo del evento no puede quedar vacio.")

    start_dt = _parse_iso_datetime(start, "start")
    end_dt = _parse_iso_datetime(end, "end")
    if end_dt <= start_dt:
        raise ValueError("end debe ser posterior a start.")

    event_uid = f"yarbis-{uuid4().hex}@local"
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    event_path = _runtime_path(
        output_path,
        workspace_root,
        DEFAULT_CALENDAR_DIR,
        f"yarbis-event-{uuid4().hex[:8]}.ics",
    )

    fields = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Yarbis//Local Agent//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{event_uid}",
        f"DTSTAMP:{now}",
        f"DTSTART:{_format_ics_datetime(start_dt)}",
        f"DTEND:{_format_ics_datetime(end_dt)}",
        f"SUMMARY:{_escape_ics_text(safe_title)}",
    ]
    if str(description).strip():
        fields.append(f"DESCRIPTION:{_escape_ics_text(description)}")
    if str(location).strip():
        fields.append(f"LOCATION:{_escape_ics_text(location)}")
    for attendee in _split_items(attendees):
        fields.append(f"ATTENDEE;CN={_escape_ics_text(attendee)}:MAILTO:{attendee}")
    fields.extend(["END:VEVENT", "END:VCALENDAR"])

    event_path.write_text(_render_ics(fields), encoding="utf-8", newline="")
    if open_file:
        _open_default(str(event_path))

    opened = " Abierto con la app predeterminada." if open_file else ""
    return f"Evento de calendario creado: {event_path}.{opened}".strip()


def _mailto_recipients(value: str) -> str:
    return ",".join(_split_items(value))


def compose_email_draft(
    to: str,
    subject: str = "",
    body: str = "",
    cc: str = "",
    bcc: str = "",
    open_client: bool = True,
) -> str:
    recipients = _mailto_recipients(to)
    if not recipients:
        raise ValueError("Debes indicar al menos un destinatario.")

    query = {}
    if str(subject).strip():
        query["subject"] = str(subject)
    if str(body).strip():
        query["body"] = str(body)
    cc_recipients = _mailto_recipients(cc)
    bcc_recipients = _mailto_recipients(bcc)
    if cc_recipients:
        query["cc"] = cc_recipients
    if bcc_recipients:
        query["bcc"] = bcc_recipients

    uri = "mailto:" + quote(recipients, safe="@.,")
    if query:
        uri += "?" + urlencode(query)

    if open_client:
        _open_default(uri)
        return f"Borrador de correo abierto.\nURI: {uri}"
    return f"Borrador de correo preparado.\nURI: {uri}"


def open_system_target(target: str, workspace_root: Path | None = None) -> str:
    workspace_root = workspace_root or Path.cwd()
    cleaned = str(target).strip()
    if not cleaned:
        raise ValueError("Debes indicar una ruta, URL o URI.")

    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", cleaned):
        resolved_target = cleaned
        display = cleaned
    else:
        raw_path = Path(cleaned)
        resolved = raw_path if raw_path.is_absolute() else workspace_root / raw_path
        resolved_target = str(resolved.resolve())
        display = resolved_target
        if not Path(resolved_target).exists():
            raise FileNotFoundError(f"No existe la ruta: {resolved_target}")

    _open_default(resolved_target)
    return f"Objetivo abierto con el manejador predeterminado: {display}"
