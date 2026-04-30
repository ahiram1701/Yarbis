import threading
import time
import re
import unicodedata
from typing import Any, Callable

from memory import load_state, save_state
from notifications import get_telegram_settings, send_telegram_message, telegram_api_request
from power import (
    DEFAULT_SHUTDOWN_DELAY_SECONDS,
    cancel_system_shutdown,
    request_system_restart,
    request_system_shutdown,
)
from session import (
    get_status_text,
    handle_note_text_request,
    note_request_label,
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


def _update_id_from_update(update: dict) -> int:
    try:
        return int(update.get("update_id", 0))
    except (TypeError, ValueError):
        return 0


def _claim_telegram_update(update_id: int) -> bool:
    if update_id <= 0:
        return True

    telegram_state = _telegram_state_from_memory()
    try:
        last_update_id = int(telegram_state.get("last_update_id", 0))
    except (TypeError, ValueError):
        last_update_id = 0

    if last_update_id >= update_id:
        return False

    _update_telegram_state(last_update_id=update_id)
    return True


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
        "/notas - listar notas\n"
        "/nota crear Titulo | contenido | categoria - guardar una nota\n"
        "/nota ID - ver una nota\n"
        "/nota borrar ID - eliminar una nota\n"
        "/apagar - apagar esta PC en 60 segundos\n"
        "/apagar ahora - apagar esta PC inmediatamente\n"
        "/reiniciar - reiniciar esta PC en 60 segundos\n"
        "/reiniciar ahora - reiniciar esta PC inmediatamente\n"
        "/cancelar_apagado - cancelar un apagado programado\n"
        "/cancelar_reinicio - cancelar un reinicio programado\n"
        "/help - ver esta ayuda\n\n"
        "Tambien puedes decir 'guarda una nota: ...', 'apaga la pc', 'reinicia pc' "
        "o responder con texto libre cuando Yarbis te pida algo."
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


def _normalize_intent_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text).strip().lower())
    without_accents = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    without_punctuation = re.sub(r"[^\w\s]", " ", without_accents)
    return " ".join(without_punctuation.replace("_", " ").split())


def _strip_yarbis_prefix(text: str) -> str:
    normalized = _normalize_intent_text(text)
    for prefix in ("yarbis ", "oye yarbis ", "hey yarbis "):
        if normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return normalized


def _natural_power_intent(text: str) -> str:
    normalized = _strip_yarbis_prefix(text)
    if not normalized:
        return ""

    shutdown_phrases = {
        "apaga la pc",
        "apaga pc",
        "apaga mi pc",
        "apaga el pc",
        "apaga la computadora",
        "apaga mi computadora",
        "apaga el equipo",
        "apaga la compu",
        "apaga windows",
        "apague la pc",
        "apaque la pc",
        "apagar pc",
        "apagar la pc",
        "shutdown pc",
    }
    restart_phrases = {
        "reinicia la pc",
        "reinicia pc",
        "reinicia mi pc",
        "reinicia el pc",
        "reinicia la computadora",
        "reinicia mi computadora",
        "reinicia el equipo",
        "reinicia la compu",
        "reinicia windows",
        "reinicie la pc",
        "reiniciar pc",
        "reiniciar la pc",
        "restart pc",
    }
    immediate_suffixes = (" ahora", " ya")
    delayed_prefixes = (
        "apaga la pc en ",
        "apaga pc en ",
        "apaga mi pc en ",
        "apaga la computadora en ",
        "apaga el equipo en ",
    )
    restart_delayed_prefixes = (
        "reinicia la pc en ",
        "reinicia pc en ",
        "reinicia mi pc en ",
        "reinicia la computadora en ",
        "reinicia el equipo en ",
    )
    cancel_phrases = {
        "cancela el apagado",
        "cancelar apagado",
        "cancela apagado",
        "cancela apagar la pc",
        "cancela el reinicio",
        "cancelar reinicio",
        "cancela reinicio",
        "cancela reiniciar la pc",
        "no apagues la pc",
        "no reinicies la pc",
        "deten el apagado",
        "deten el reinicio",
        "aborta el apagado",
        "aborta el reinicio",
    }

    if normalized in cancel_phrases:
        return "cancel_power_action"
    if normalized in shutdown_phrases:
        return "shutdown"
    if normalized in restart_phrases:
        return "restart"
    if any(normalized == phrase + suffix for phrase in shutdown_phrases for suffix in immediate_suffixes):
        return "shutdown"
    if any(normalized == phrase + suffix for phrase in restart_phrases for suffix in immediate_suffixes):
        return "restart"
    if any(normalized.startswith(prefix) for prefix in delayed_prefixes):
        return "shutdown"
    if any(normalized.startswith(prefix) for prefix in restart_delayed_prefixes):
        return "restart"
    return ""


def _power_intent_for_message(text: str) -> str:
    cleaned_text = str(text).strip()
    if not cleaned_text:
        return ""

    if not cleaned_text.startswith("/"):
        return _natural_power_intent(cleaned_text)

    command = cleaned_text.split()[0].split("@")[0].lower()
    if command in {"/apagar", "/apagar_pc", "/shutdown"}:
        return "shutdown"
    if command in {"/reiniciar", "/reiniciar_pc", "/restart", "/reboot"}:
        return "restart"
    if command in {
        "/cancelar_apagado",
        "/cancelarapagado",
        "/abortar_apagado",
        "/cancelar_reinicio",
        "/cancelarreinicio",
        "/abortar_reinicio",
    }:
        return "cancel_power_action"
    return ""


def _parse_shutdown_delay_seconds(argument_text: str) -> tuple[int | None, str | None]:
    cleaned = _normalize_intent_text(argument_text)
    if not cleaned:
        return DEFAULT_SHUTDOWN_DELAY_SECONDS, None
    if cleaned in {"ahora", "ya", "now", "inmediato", "inmediatamente"}:
        return 0, None

    match = re.search(r"\b(\d{1,4})\s*(segundo|segundos|second|seconds|sec|s)\b", cleaned)
    if match:
        seconds = int(match.group(1))
        if seconds > 3600:
            return None, "El maximo permitido es 3600 segundos."
        return seconds, None

    match = re.search(r"\b(\d{1,3})\s*(minuto|minutos|minute|minutes|min|m)\b", cleaned)
    if match:
        seconds = int(match.group(1)) * 60
        if seconds > 3600:
            return None, "El maximo permitido es 60 minutos."
        return seconds, None

    match = re.fullmatch(r"\d{1,3}", cleaned)
    if match:
        seconds = int(cleaned) * 60
        if seconds > 3600:
            return None, "El maximo permitido es 60 minutos."
        return seconds, None

    return None, "Uso: /apagar, /apagar ahora, /apagar 30s o /apagar 5m"


def _argument_after_natural_delay_prefix(text: str) -> str:
    normalized = _strip_yarbis_prefix(text)
    for prefix in (
        "apaga la pc en ",
        "apaga pc en ",
        "apaga mi pc en ",
        "apaga la computadora en ",
        "apaga el equipo en ",
        "reinicia la pc en ",
        "reinicia pc en ",
        "reinicia mi pc en ",
        "reinicia la computadora en ",
        "reinicia el equipo en ",
    ):
        if normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    if normalized.endswith(" ahora"):
        return "ahora"
    if normalized.endswith(" ya"):
        return "ya"
    return ""


def _dispatch_shutdown(argument_text: str = "") -> str:
    delay_seconds, error = _parse_shutdown_delay_seconds(argument_text)
    if error:
        return error
    return request_system_shutdown(delay_seconds=delay_seconds)


def _dispatch_restart(argument_text: str = "") -> str:
    delay_seconds, error = _parse_shutdown_delay_seconds(argument_text)
    if error:
        return error.replace("/apagar", "/reiniciar")
    return request_system_restart(delay_seconds=delay_seconds)


def _is_restart_cancel_command(command: str) -> bool:
    return command in {"/cancelar_reinicio", "/cancelarreinicio", "/abortar_reinicio"}


def _is_shutdown_cancel_command(command: str) -> bool:
    return command in {"/cancelar_apagado", "/cancelarapagado", "/abortar_apagado"}


def _cancel_label_for_natural_text(text: str) -> str:
    normalized = _strip_yarbis_prefix(text)
    if "reinicio" in normalized or "reiniciar" in normalized or "reinicies" in normalized:
        return "reinicio"
    return "apagado"


def _job_label_for_message(text: str) -> str:
    cleaned_text = str(text).strip()
    if not cleaned_text:
        return ""

    note_label = note_request_label(cleaned_text)
    if note_label:
        return note_label

    if not cleaned_text.startswith("/"):
        natural_intent = _power_intent_for_message(cleaned_text)
        if natural_intent == "shutdown":
            return "Apagado"
        if natural_intent == "restart":
            return "Reinicio"
        if natural_intent == "cancel_power_action":
            return "Cancelar apagado/reinicio"
        return "Respuesta"

    command = cleaned_text.split()[0].split("@")[0].lower()
    if command == "/run":
        return "Ciclo"
    if command == "/auto":
        return "Modo autonomo"
    if command in {"/goal", "/objetivo"}:
        return "Objetivo"
    if command in {"/notas", "/nota", "/crear_nota", "/guardar_nota", "/borrar_nota", "/eliminar_nota", "/ver_nota"}:
        return note_request_label(cleaned_text) or "Notas"
    if command in {"/apagar", "/apagar_pc", "/shutdown"}:
        return "Apagado"
    if command in {"/reiniciar", "/reiniciar_pc", "/restart", "/reboot"}:
        return "Reinicio"
    if _is_shutdown_cancel_command(command) or _is_restart_cancel_command(command):
        return "Cancelar apagado/reinicio"

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

    note_reply = handle_note_text_request(cleaned_text)
    if note_reply is not None:
        return note_reply

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

    if command in {"/apagar", "/apagar_pc", "/shutdown"}:
        return _dispatch_shutdown(argument_text)

    if command in {"/reiniciar", "/reiniciar_pc", "/restart", "/reboot"}:
        return _dispatch_restart(argument_text)

    if _is_restart_cancel_command(command):
        return cancel_system_shutdown(action_label="reinicio")

    if _is_shutdown_cancel_command(command):
        return cancel_system_shutdown(action_label="apagado")

    return "Comando no reconocido.\n\n" + _help_text()


def process_telegram_update(update: dict) -> str:
    message = update.get("message")
    if not isinstance(message, dict):
        return ""

    update_id = _update_id_from_update(update)
    if not _claim_telegram_update(update_id):
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

    power_intent = _power_intent_for_message(text)
    if binding_notice and power_intent:
        reply = (
            f"{binding_notice}\n\n"
            "Por seguridad, vuelve a enviar el comando de apagado o reinicio desde este chat ya vinculado."
        )
        send_telegram_message(reply, chat_id=chat_id)
        return f"Telegram: vinculo chat para '{_trim_for_activity(text)}'."

    _emit_event(_incoming_activity_text(text))
    job_label = _job_label_for_message(text)

    if job_label:
        _emit_job_started(job_label)

    try:
        if text.startswith("/"):
            reply = _dispatch_command(text)
        elif power_intent == "shutdown":
            reply = _dispatch_shutdown(_argument_after_natural_delay_prefix(text))
        elif power_intent == "restart":
            reply = _dispatch_restart(_argument_after_natural_delay_prefix(text))
        elif power_intent == "cancel_power_action":
            reply = cancel_system_shutdown(action_label=_cancel_label_for_natural_text(text))
        else:
            note_reply = handle_note_text_request(text)
            if note_reply is not None:
                reply = note_reply
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
        update_id = _update_id_from_update(update)

        summary = ""
        try:
            summary = process_telegram_update(update)
        except Exception as exc:
            summary = f"Telegram: error procesando update {update_id or '?'}: {exc}"
        finally:
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
