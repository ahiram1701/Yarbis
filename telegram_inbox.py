import threading
import time
from typing import Any, Callable

from memory import load_state, save_state
from notifications import get_telegram_settings, send_telegram_message, telegram_api_request
from session import (
    get_status_text,
    run_auto_with_output,
    run_cycle_with_output,
    submit_user_reply,
    update_goal,
)

_POLL_IDLE_SECONDS = 3
_poller_thread = None
_poller_stop_event = threading.Event()
_poller_callback = None
_poller_lock = threading.Lock()


def _emit_event(message: Any):
    callback = _poller_callback
    if not callable(callback):
        return

    if isinstance(message, dict):
        callback(message)
        return

    rendered = str(message).strip()
    if rendered:
        callback(rendered)


def _emit_job_started(label: str):
    job_label = str(label).strip() or "Telegram"
    _emit_event({
        "type": "remote_job_started",
        "label": job_label,
        "status_text": f"Ejecutando: {job_label}...",
    })


def _emit_job_finished(label: str, content: str):
    job_label = str(label).strip() or "Telegram"
    _emit_event({
        "type": "remote_job_finished",
        "label": job_label,
        "content": str(content).strip(),
    })


def _emit_job_failed(label: str, content: str):
    job_label = str(label).strip() or "Telegram"
    _emit_event({
        "type": "remote_job_failed",
        "label": job_label,
        "content": str(content).strip(),
    })


def _update_telegram_state(**changes):
    state = load_state()
    notifications_state = state.setdefault("notifications", {})
    telegram_state = notifications_state.setdefault("telegram", {})
    telegram_state.update(changes)
    save_state(state)


def _telegram_state_from_memory() -> dict:
    state = load_state()
    notifications_state = state.get("notifications", {})
    if not isinstance(notifications_state, dict):
        notifications_state = {}
    telegram_state = notifications_state.get("telegram", {})
    if not isinstance(telegram_state, dict):
        telegram_state = {}
    return telegram_state


def _help_text() -> str:
    return (
        "Yarbis por Telegram listo.\n\n"
        "Comandos disponibles:\n"
        "/status - ver el estado actual\n"
        "/goal TEXTO - cambiar el objetivo\n"
        "/objetivo TEXTO - cambiar el objetivo\n"
        "/run - ejecutar un ciclo\n"
        "/auto - ejecutar el modo autonomo con los ciclos por defecto\n"
        "/auto N - ejecutar N ciclos\n"
        "/help - ver esta ayuda\n\n"
        "Tambien puedes responder con texto libre cuando Yarbis te pida algo."
    )


def _bind_chat_if_needed(message: dict) -> str:
    chat = message.get("chat", {})
    incoming_chat_id = str(chat.get("id", "")).strip()
    configured_chat_id = str(get_telegram_settings().get("chat_id", "")).strip()
    if configured_chat_id or not incoming_chat_id:
        return ""

    if str(chat.get("type", "")).strip().lower() != "private":
        return ""

    _update_telegram_state(chat_id=incoming_chat_id)
    return (
        "Este chat quedo vinculado con Yarbis. "
        "A partir de ahora te respondere por aqui."
    )


def _is_allowed_chat(message: dict) -> bool:
    chat = message.get("chat", {})
    incoming_chat_id = str(chat.get("id", "")).strip()
    if not incoming_chat_id:
        return False

    configured_chat_id = str(get_telegram_settings().get("chat_id", "")).strip()
    if configured_chat_id:
        return incoming_chat_id == configured_chat_id

    return str(chat.get("type", "")).strip().lower() == "private"


def _trim_for_activity(text: str, limit: int = 180) -> str:
    compact = " ".join(str(text).strip().split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}..."


def _incoming_activity_text(text: str) -> str:
    trimmed_text = _trim_for_activity(text)
    return f"Telegram: recibido '{trimmed_text}'. Procesando..."


def _job_label_for_message(text: str) -> str:
    cleaned_text = str(text).strip()
    if not cleaned_text:
        return ""

    if not cleaned_text.startswith("/"):
        return "Respuesta"

    command = cleaned_text.split()[0].split("@")[0].lower()
    if command == "/run":
        return "Ciclo"
    if command == "/auto":
        return "Modo autonomo"
    if command in {"/goal", "/objetivo"}:
        return "Objetivo"

    return ""


def _dispatch_command(command_text: str) -> str:
    cleaned_text = str(command_text).strip()
    parts = cleaned_text.split()
    command_parts = cleaned_text.split(maxsplit=1)
    command = parts[0].split("@")[0].lower() if parts else ""
    argument_text = command_parts[1].strip() if len(command_parts) > 1 else ""

    if command in {"/start", "/help"}:
        return _help_text()

    if command == "/status":
        return get_status_text()

    if command in {"/goal", "/objetivo"}:
        if not argument_text:
            return "Uso: /goal nuevo objetivo"
        return update_goal(argument_text)

    if command == "/run":
        return run_cycle_with_output(emit_notifications=False)

    if command == "/auto":
        if len(parts) > 1:
            try:
                cycles = int(parts[1])
            except ValueError:
                return "Uso: /auto o /auto 3"
            if cycles <= 0:
                return "El numero de ciclos debe ser mayor que cero."
        else:
            cycles = None

        return run_auto_with_output(cycles=cycles, emit_notifications=False)

    return "Comando no reconocido.\n\n" + _help_text()


def process_telegram_update(update: dict) -> str:
    message = update.get("message")
    if not isinstance(message, dict):
        return ""

    if not _is_allowed_chat(message):
        return ""

    binding_notice = _bind_chat_if_needed(message)
    chat_id = str(message.get("chat", {}).get("id", "")).strip()
    text = str(message.get("text", "")).strip()

    if not text:
        reply = "Por ahora solo puedo procesar mensajes de texto."
        if binding_notice:
            reply = f"{binding_notice}\n\n{reply}"
        send_telegram_message(reply, chat_id=chat_id)
        return "Telegram: mensaje no textual ignorado."

    _emit_event(_incoming_activity_text(text))
    job_label = _job_label_for_message(text)

    if job_label:
        _emit_job_started(job_label)

    try:
        if text.startswith("/"):
            reply = _dispatch_command(text)
        else:
            reply = submit_user_reply(text, emit_notifications=False)
    except Exception as exc:
        if job_label:
            _emit_job_failed(job_label, str(exc))
        raise

    if job_label:
        _emit_job_finished(job_label, reply)

    if binding_notice:
        reply = f"{binding_notice}\n\n{reply}"

    send_telegram_message(reply, chat_id=chat_id)
    return f"Telegram: procesado '{_trim_for_activity(text)}'."


def _poll_updates_once():
    settings = load_state().get("notifications", {})
    config = get_telegram_settings(settings)
    if not config["enabled"] or not config["bot_token"]:
        return False

    telegram_state = _telegram_state_from_memory()
    offset = int(telegram_state.get("last_update_id", 0)) + 1
    response = telegram_api_request(
        "getUpdates",
        {
            "offset": offset,
            "timeout": config["poll_timeout_seconds"],
            "allowed_updates": ["message"],
        },
        settings=settings,
        timeout=config["poll_timeout_seconds"] + config["timeout_seconds"],
    )
    updates = response.get("result", [])
    if not isinstance(updates, list) or not updates:
        return False

    for update in updates:
        summary = process_telegram_update(update)
        try:
            update_id = int(update.get("update_id", 0))
        except (TypeError, ValueError):
            update_id = 0

        if update_id > 0:
            _update_telegram_state(last_update_id=update_id)

        if summary:
            _emit_event(summary)

    return True


def _poll_forever():
    while not _poller_stop_event.is_set():
        try:
            did_work = _poll_updates_once()
        except Exception:
            did_work = False

        if not did_work:
            _poller_stop_event.wait(_POLL_IDLE_SECONDS)


def start_telegram_polling(event_callback: Callable[[Any], None] | None = None) -> bool:
    global _poller_thread
    global _poller_callback

    with _poller_lock:
        _poller_callback = event_callback
        if _poller_thread is not None and _poller_thread.is_alive():
            return False

        _poller_stop_event.clear()
        _poller_thread = threading.Thread(
            target=_poll_forever,
            name="yarbis-telegram-poller",
            daemon=True,
        )
        _poller_thread.start()
        return True


def stop_telegram_polling(timeout: float = 1.0):
    global _poller_thread

    with _poller_lock:
        _poller_stop_event.set()
        if _poller_thread is not None and _poller_thread.is_alive():
            _poller_thread.join(timeout=timeout)
        _poller_thread = None
        _poller_stop_event.clear()
        time.sleep(0)
