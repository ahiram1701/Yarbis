import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import activity
import yarbis_instance
from memory import state_transaction


SCHEMA_VERSION = 1
KIND_DIRECT = "direct"
KIND_DIRECT_REPLY = "direct_reply"
STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_READ = "read"
MAX_MESSAGE_CHARS = 12_000
DEFAULT_PROCESS_LIMIT = 3


class YarbisBusError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_message_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"yarbis-msg-{timestamp}-{uuid4().hex[:8]}"


def _clean_content(value: object) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS].rstrip()
    return text


def _message_path(message_id: str) -> Path:
    cleaned = str(message_id).strip()
    if not cleaned or any(char in cleaned for char in "\\/:*?\"<>|"):
        raise YarbisBusError("Id de mensaje invalido.")
    return yarbis_instance.bus_dir() / f"{cleaned}.json"


def _write_message(message: dict) -> dict:
    yarbis_instance.bus_dir().mkdir(parents=True, exist_ok=True)
    message_id = str(message.get("id", "")).strip()
    if not message_id:
        raise YarbisBusError("Mensaje sin id.")
    path = _message_path(message_id)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    tmp_path.write_text(json.dumps(message, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
    return dict(message)


def _read_message(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _message_files() -> list[Path]:
    try:
        return sorted(yarbis_instance.bus_dir().glob("*.json"))
    except OSError:
        return []


def _base_message(
    *,
    from_instance: str,
    to_instance: str,
    kind: str,
    content: str,
    conversation_id: str = "",
    reply_to: str = "",
    status: str = STATUS_QUEUED,
) -> dict:
    message_id = _new_message_id()
    return {
        "schema_version": SCHEMA_VERSION,
        "id": message_id,
        "conversation_id": str(conversation_id).strip() or message_id,
        "from_instance": yarbis_instance.normalize_instance_id(from_instance),
        "to_instance": yarbis_instance.normalize_instance_id(to_instance),
        "kind": str(kind).strip() or KIND_DIRECT,
        "content": _clean_content(content),
        "reply_to": str(reply_to).strip(),
        "created_at": _utc_now(),
        "status": str(status).strip() or STATUS_QUEUED,
        "processed_at": "",
        "response": "",
        "error": "",
    }


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            still_active = 259
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == still_active
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return 0


def instance_is_active(instance_id: object) -> bool:
    normalized = yarbis_instance.normalize_instance_id(instance_id)
    runtime_dir = yarbis_instance.runtime_dir(normalized)
    for name in ("service.pid", "desktop.pid"):
        pid = _read_pid(runtime_dir / name)
        if pid and _pid_is_running(pid):
            return True
    return False


def send_message(
    target_instance: object,
    message: object,
    *,
    wait_for_reply: bool = True,
    timeout_seconds: float = 120,
) -> dict:
    target = yarbis_instance.normalize_instance_id(target_instance)
    content = _clean_content(message)
    if not content:
        raise YarbisBusError("El mensaje no puede quedar vacio.")
    sender = yarbis_instance.current_instance_id()
    yarbis_instance.ensure_instance_registered(sender)

    payload = _base_message(
        from_instance=sender,
        to_instance=target,
        kind=KIND_DIRECT,
        content=content,
    )
    _write_message(payload)
    activity.emit_event("yarbis_message_sent", target_instance=target, message_id=payload["id"])

    if not wait_for_reply:
        return dict(payload)
    if not instance_is_active(target):
        queued = dict(payload)
        queued["status_text"] = "queued"
        return queued
    return wait_for_response(payload["id"], timeout_seconds=timeout_seconds)


def wait_for_response(message_id: str, timeout_seconds: float = 120) -> dict:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    path = _message_path(message_id)
    while time.monotonic() < deadline:
        payload = _read_message(path)
        if payload and payload.get("status") in {STATUS_DONE, STATUS_ERROR}:
            return payload
        time.sleep(0.2)
    payload = _read_message(path) or {"id": message_id}
    payload["status_text"] = "timeout"
    return payload


def list_messages(
    *,
    target_instance: object | None = None,
    unread_only: bool = True,
    limit: int = 20,
    mark_read: bool = False,
) -> list[dict]:
    target = yarbis_instance.normalize_instance_id(
        yarbis_instance.current_instance_id() if target_instance is None else target_instance
    )
    try:
        normalized_limit = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 20

    items = []
    for path in _message_files():
        message = _read_message(path)
        if not message:
            continue
        if yarbis_instance.normalize_instance_id(message.get("to_instance", "")) != target:
            continue
        if unread_only and message.get("status") not in {STATUS_QUEUED, STATUS_DONE}:
            continue
        items.append(message)

    items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    selected = items[:normalized_limit]
    if mark_read:
        for message in selected:
            if message.get("kind") == KIND_DIRECT_REPLY and message.get("status") == STATUS_QUEUED:
                message["status"] = STATUS_READ
                message["processed_at"] = message.get("processed_at") or _utc_now()
                _write_message(message)
    return selected


def list_instance_messages(
    *,
    instance_id: object | None = None,
    limit: int = 50,
    include_read: bool = True,
) -> list[dict]:
    target = yarbis_instance.normalize_instance_id(
        yarbis_instance.current_instance_id() if instance_id is None else instance_id
    )
    try:
        normalized_limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 50

    items = []
    for path in _message_files():
        message = _read_message(path)
        if not message:
            continue
        sender = yarbis_instance.normalize_instance_id(message.get("from_instance", ""))
        receiver = yarbis_instance.normalize_instance_id(message.get("to_instance", ""))
        if target not in {sender, receiver}:
            continue
        if not include_read and message.get("status") == STATUS_READ:
            continue
        items.append(message)

    items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return items[:normalized_limit]


def message_counts(instance_id: object | None = None) -> dict:
    target = yarbis_instance.normalize_instance_id(
        yarbis_instance.current_instance_id() if instance_id is None else instance_id
    )
    pending_direct = 0
    unread_replies = 0
    for path in _message_files():
        message = _read_message(path)
        if not message:
            continue
        receiver = yarbis_instance.normalize_instance_id(message.get("to_instance", ""))
        if receiver != target:
            continue
        if message.get("kind") == KIND_DIRECT and message.get("status") == STATUS_QUEUED:
            pending_direct += 1
        elif message.get("kind") == KIND_DIRECT_REPLY and message.get("status") in {STATUS_QUEUED, STATUS_DONE}:
            unread_replies += 1
    return {
        "pending_direct": pending_direct,
        "unread_replies": unread_replies,
        "total_unread": pending_direct + unread_replies,
    }


def mark_messages_read(message_ids: list[object], instance_id: object | None = None) -> int:
    target = yarbis_instance.normalize_instance_id(
        yarbis_instance.current_instance_id() if instance_id is None else instance_id
    )
    normalized_ids = {str(message_id or "").strip() for message_id in message_ids}
    normalized_ids.discard("")
    if not normalized_ids:
        return 0

    updated = 0
    for message_id in normalized_ids:
        message = _read_message(_message_path(message_id))
        if not message:
            continue
        receiver = yarbis_instance.normalize_instance_id(message.get("to_instance", ""))
        if receiver != target:
            continue
        if message.get("kind") != KIND_DIRECT_REPLY:
            continue
        if message.get("status") == STATUS_READ:
            continue
        message["status"] = STATUS_READ
        message["processed_at"] = message.get("processed_at") or _utc_now()
        _write_message(message)
        updated += 1
    return updated


def pending_direct_messages(target_instance: object | None = None) -> list[dict]:
    target = yarbis_instance.normalize_instance_id(
        yarbis_instance.current_instance_id() if target_instance is None else target_instance
    )
    pending = []
    for path in _message_files():
        message = _read_message(path)
        if not message:
            continue
        if message.get("kind") != KIND_DIRECT:
            continue
        if message.get("status") != STATUS_QUEUED:
            continue
        if yarbis_instance.normalize_instance_id(message.get("to_instance", "")) != target:
            continue
        pending.append(message)
    pending.sort(key=lambda item: str(item.get("created_at", "")))
    return pending


def _claim_message(message: dict) -> dict | None:
    current = _read_message(_message_path(str(message.get("id", ""))))
    if not current or current.get("status") != STATUS_QUEUED:
        return None
    current["status"] = STATUS_PROCESSING
    current["processed_at"] = _utc_now()
    current["error"] = ""
    return _write_message(current)


def _requeue_message(message: dict) -> None:
    message["status"] = STATUS_QUEUED
    message["processed_at"] = ""
    _write_message(message)


def _write_reply_for(message: dict, response: str, status: str = STATUS_QUEUED) -> dict:
    reply = _base_message(
        from_instance=message.get("to_instance", ""),
        to_instance=message.get("from_instance", ""),
        kind=KIND_DIRECT_REPLY,
        content=response,
        conversation_id=str(message.get("conversation_id", "")),
        reply_to=str(message.get("id", "")),
        status=status,
    )
    reply["response"] = response
    reply["processed_at"] = _utc_now()
    return _write_message(reply)


def _complete_message(message: dict, response: str) -> dict:
    message["status"] = STATUS_DONE
    message["processed_at"] = _utc_now()
    message["response"] = str(response).strip()
    message["error"] = ""
    stored = _write_message(message)
    _write_reply_for(stored, stored["response"])
    activity.emit_event(
        "yarbis_message_processed",
        source_instance=stored.get("from_instance", ""),
        message_id=stored.get("id", ""),
    )
    return stored


def _fail_message(message: dict, error: object) -> dict:
    message["status"] = STATUS_ERROR
    message["processed_at"] = _utc_now()
    message["error"] = str(error).strip() or "Error desconocido."
    stored = _write_message(message)
    _write_reply_for(stored, stored["error"], status=STATUS_ERROR)
    return stored


def _run_message_cycle(message: dict) -> str:
    from session import _capture_operation_output, run_one_cycle, session_operation_lock

    prompt = (
        f"Mensaje directo de la instancia Yarbis `{message.get('from_instance', '')}`:\n\n"
        f"{message.get('content', '')}\n\n"
        "Responde a esa instancia de forma concreta. Si necesitas delegar de vuelta, usa mensajes directos "
        "solo cuando aporte valor claro."
    )
    with session_operation_lock("Mensaje Yarbis", blocking=False):
        state_transaction(
            "yarbis_direct_message_received",
            lambda state: state["messages"].append({"role": "user", "content": prompt}),
        )
        output, _ = _capture_operation_output(run_one_cycle)
    return output or "Mensaje procesado sin salida visible."


def process_pending_messages(limit: int = DEFAULT_PROCESS_LIMIT, runner=None) -> int:
    from session import SessionOperationBusy

    processed = 0
    for message in pending_direct_messages()[: max(1, int(limit or DEFAULT_PROCESS_LIMIT))]:
        claimed = _claim_message(message)
        if not claimed:
            continue
        try:
            response = runner(claimed) if callable(runner) else _run_message_cycle(claimed)
        except SessionOperationBusy:
            _requeue_message(claimed)
            continue
        except Exception as exc:
            _fail_message(claimed, exc)
            processed += 1
            continue
        _complete_message(claimed, response)
        processed += 1
    return processed
