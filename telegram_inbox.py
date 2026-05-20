import contextlib
import json
import threading
import time
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import activity
from memory import (
    DEFAULT_OLLAMA_CLOUD_HOST,
    DEFAULT_OPENROUTER_API_KEY_ENV_VAR,
    DEFAULT_OPENROUTER_HOST,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    load_state,
    state_transaction,
)
from secrets_redaction import redact_secrets
from intent_text import (
    normalize_intent_text as _normalize_intent_text,
    strip_yarbis_prefix as _strip_yarbis_prefix,
)
from notifications import (
    get_telegram_settings,
    send_telegram_chat_action,
    send_telegram_message,
    telegram_api_request,
)
from telegram_format import format_telegram_operation_reply
from power import (
    DEFAULT_SHUTDOWN_DELAY_SECONDS,
    cancel_system_shutdown,
    request_system_restart,
    request_system_shutdown,
)
from session import (
    SessionOperationBusy,
    get_default_model_provider,
    get_ollama_settings,
    get_openrouter_settings,
    get_status_text,
    has_pending_user_question,
    handle_note_text_request,
    note_request_label,
    request_stop_current_operation,
    run_auto_with_output,
    run_cycle_with_output,
    submit_user_reply,
    update_model_provider,
    update_goal,
    update_ollama_settings,
    update_openrouter_settings,
)

_POLL_IDLE_SECONDS = 3
_TELEGRAM_THINKING_PULSE_SECONDS = 4.0
DEFERRED_TELEGRAM_REPLIES_FILE = (
    Path(__file__).resolve().parent / ".yarbis_runtime" / "telegram_deferred_replies.json"
)
MAX_DEFERRED_TELEGRAM_REPLIES = 20
TELEGRAM_OPERATION_LABELS = {
    "Ciclo",
    "Detener",
    "Modelo",
    "Proveedor",
    "Modo autonomo",
    "Pulso proactivo",
    "Respuesta",
    "Respuesta diferida",
    "Timeout",
}
_poller_thread = None
_poller_stop_event = threading.Event()
_poller_callback = None
_poller_lock = threading.Lock()
_deferred_replies_lock = threading.Lock()
POWER_CONFIRMATION_TTL_SECONDS = 10 * 60


def _emit_event(message: Any):
    callback = _poller_callback

    if isinstance(message, dict):
        redacted_message = {
            key: redact_secrets(value) if isinstance(value, str) else value
            for key, value in message.items()
        }
        event_type = str(redacted_message.get("type", "telegram_event")).strip() or "telegram_event"
        event_fields = {
            key: value
            for key, value in redacted_message.items()
            if key not in {"type", "operation_id"}
        }
        activity.emit_event(
            event_type,
            operation_id=redacted_message.get("operation_id"),
            **event_fields,
        )
        if callable(callback):
            callback(redacted_message)
        return

    rendered = redact_secrets(message).strip()
    if rendered:
        activity.emit_event("telegram_event", content=rendered)
    if rendered and callable(callback):
        callback(rendered)


def _emit_job_started(label: str) -> str:
    job_label = str(label).strip() or "Telegram"
    operation_id = activity.new_operation_id(f"telegram-{job_label}")
    _emit_event({
        "type": "remote_job_started",
        "operation_id": operation_id,
        "label": job_label,
        "status_text": f"Estoy pensando: {job_label}...",
    })
    return operation_id


def _emit_job_finished(label: str, content: str, operation_id: str = ""):
    job_label = str(label).strip() or "Telegram"
    _emit_event({
        "type": "remote_job_finished",
        "operation_id": operation_id,
        "label": job_label,
        "content": str(content).strip(),
    })


def _emit_job_failed(label: str, content: str, operation_id: str = ""):
    job_label = str(label).strip() or "Telegram"
    _emit_event({
        "type": "remote_job_failed",
        "operation_id": operation_id,
        "label": job_label,
        "content": redact_secrets(content).strip(),
    })


def _update_telegram_state(**changes):
    def mutate(state):
        notifications_state = state.setdefault("notifications", {})
        telegram_state = notifications_state.setdefault("telegram", {})
        telegram_state.update(changes)

    state_transaction("update_telegram_state", mutate)


def _send_telegram_thinking_action(chat_id: str, settings: dict | None = None):
    try:
        send_telegram_chat_action("typing", settings=settings, chat_id=chat_id)
    except Exception:
        pass


def _telegram_thinking_pulse(chat_id: str, settings: dict, stop_event: threading.Event):
    while not stop_event.is_set():
        _send_telegram_thinking_action(chat_id, settings=settings)
        stop_event.wait(_TELEGRAM_THINKING_PULSE_SECONDS)


@contextlib.contextmanager
def _telegram_thinking_indicator(
    chat_id: str,
    settings: dict | None = None,
    enabled: bool = True,
):
    target_chat_id = str(chat_id).strip()
    if not enabled or not target_chat_id:
        yield
        return

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_telegram_thinking_pulse,
        args=(target_chat_id, settings or {}, stop_event),
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_event.set()


def _load_deferred_telegram_replies() -> list[dict]:
    try:
        raw_items = json.loads(DEFERRED_TELEGRAM_REPLIES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []

    if not isinstance(raw_items, list):
        return []

    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        chat_id = str(item.get("chat_id", "")).strip()
        if not text:
            continue
        items.append({
            "text": text,
            "chat_id": chat_id,
            "queued_at": item.get("queued_at", time.time()),
        })
    return items[-MAX_DEFERRED_TELEGRAM_REPLIES:]


def _save_deferred_telegram_replies(items: list[dict]):
    DEFERRED_TELEGRAM_REPLIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not items:
        try:
            DEFERRED_TELEGRAM_REPLIES_FILE.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            _write_deferred_telegram_replies([])
        return

    _write_deferred_telegram_replies(items[-MAX_DEFERRED_TELEGRAM_REPLIES:])


def _write_deferred_telegram_replies(items: list[dict]):
    tmp_file = DEFERRED_TELEGRAM_REPLIES_FILE.with_name(
        f"{DEFERRED_TELEGRAM_REPLIES_FILE.name}.tmp"
    )
    with open(tmp_file, "w", encoding="utf-8") as file:
        json.dump(items, file, ensure_ascii=False, indent=2)
    try:
        tmp_file.replace(DEFERRED_TELEGRAM_REPLIES_FILE)
    except PermissionError:
        with open(DEFERRED_TELEGRAM_REPLIES_FILE, "w", encoding="utf-8") as file:
            json.dump(items, file, ensure_ascii=False, indent=2)
        try:
            tmp_file.unlink()
        except OSError:
            pass


def _enqueue_deferred_telegram_reply(text: str, chat_id: str) -> str:
    item = {
        "text": str(text).strip(),
        "chat_id": str(chat_id).strip(),
        "queued_at": time.time(),
    }
    with _deferred_replies_lock:
        items = _load_deferred_telegram_replies()
        items.append(item)
        _save_deferred_telegram_replies(items)

    return (
        "Recibi tu respuesta por Telegram.\n\n"
        "Continuidad: estoy terminando otra operacion. "
        "Dejo tu mensaje en cola y lo procesare automaticamente en cuanto quede libre."
    )


def _pop_deferred_telegram_reply() -> dict | None:
    with _deferred_replies_lock:
        items = _load_deferred_telegram_replies()
        if not items:
            return None
        item = items.pop(0)
        _save_deferred_telegram_replies(items)
        return item


def _push_deferred_telegram_reply_front(item: dict):
    with _deferred_replies_lock:
        items = _load_deferred_telegram_replies()
        items.insert(0, item)
        _save_deferred_telegram_replies(items)


def _submit_user_reply_from_telegram(text: str, chat_id: str) -> str:
    try:
        return submit_user_reply(text, emit_notifications=False, blocking=False)
    except SessionOperationBusy:
        return _enqueue_deferred_telegram_reply(text, chat_id)


def process_deferred_telegram_replies(limit: int = 3) -> int:
    processed = 0
    for _ in range(max(1, int(limit))):
        item = _pop_deferred_telegram_reply()
        if item is None:
            break

        chat_id = str(item.get("chat_id", "")).strip()
        text = str(item.get("text", "")).strip()
        if not text:
            processed += 1
            continue

        label = "Respuesta diferida"
        operation_id = _emit_job_started(label)
        try:
            settings = load_state().get("notifications", {})
            with _telegram_thinking_indicator(chat_id, settings=settings, enabled=bool(chat_id)):
                reply = submit_user_reply(text, emit_notifications=False, blocking=False)
        except SessionOperationBusy:
            _push_deferred_telegram_reply_front(item)
            _emit_job_failed(label, "Yarbis sigue ocupado.", operation_id=operation_id)
            break
        except Exception as exc:
            error_text = redact_secrets(exc)
            _emit_job_failed(label, error_text, operation_id=operation_id)
            if chat_id:
                send_telegram_message(
                    f"No pude procesar tu respuesta pendiente: {error_text}",
                    chat_id=chat_id,
                )
            processed += 1
            continue

        _emit_job_finished(label, reply, operation_id=operation_id)
        if chat_id:
            send_telegram_message(
                format_telegram_operation_reply(label, reply),
                chat_id=chat_id,
            )
        processed += 1

    return processed


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
        "/stop - detener la operacion en curso\n"
        "/goal TEXTO - cambiar el objetivo\n"
        "/objetivo TEXTO - cambiar el objetivo\n"
        "/run - ejecutar un ciclo\n"
        "/auto - ejecutar el modo autonomo con los ciclos por defecto\n"
        "/auto N - ejecutar N ciclos\n"
        "/proveedor ollama|openrouter - elegir proveedor por defecto\n"
        "/modelo NOMBRE - cambiar el modelo de Ollama\n"
        "/timeout SEGUNDOS - cambiar el timeout del proveedor activo\n"
        "/ollama NOMBRE SEGUNDOS - cambiar modelo y timeout juntos\n"
        "/ollama host URL - cambiar host Ollama; vacio/local usa el daemon local\n"
        "/ollama cloud MODELO - usar Ollama Cloud directo con OLLAMA_API_KEY\n"
        "/ollama local MODELO - volver al daemon local\n"
        "/ollama fallback MODELO1, MODELO2 - modelos de respaldo\n"
        "/openrouter MODELO - configurar OpenRouter y usarlo por defecto\n"
        "/notas - listar notas\n"
        "/nota crear Titulo | contenido | categoria - guardar una nota\n"
        "/nota ID - ver una nota\n"
        "/nota borrar ID - eliminar una nota\n"
        "/apagar - apagar esta PC en 60 segundos\n"
        "/apagar ahora - apagar esta PC inmediatamente\n"
        "/reiniciar - reiniciar esta PC en 60 segundos\n"
        "/reiniciar ahora - reiniciar esta PC inmediatamente\n"
        "/confirmar_apagado CODIGO - confirmar un apagado solicitado\n"
        "/confirmar_reinicio CODIGO - confirmar un reinicio solicitado\n"
        "/cancelar_apagado - cancelar un apagado programado\n"
        "/cancelar_reinicio - cancelar un reinicio programado\n"
        "/help - ver esta ayuda\n\n"
        "Tambien puedes decir 'guarda una nota: ...', 'detente', 'apaga la pc', 'reinicia pc' "
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
    return compact


def _incoming_activity_text(text: str) -> str:
    trimmed_text = _trim_for_activity(text)
    return f"Telegram: recibido '{redact_secrets(trimmed_text)}'. Estoy pensando..."


def _should_format_operation_reply(label: str, content: str) -> bool:
    operation_label = str(label).strip()
    rendered = str(content).strip()
    if rendered.startswith("Uso:") or rendered.startswith("El numero de ciclos"):
        return False

    if operation_label in TELEGRAM_OPERATION_LABELS:
        return True

    return "=== CICLO" in rendered or "Modo autonomo ejecutado por" in rendered


def _telegram_reply_for_delivery(label: str, content: str) -> str:
    if not _should_format_operation_reply(label, content):
        return str(content).strip()
    return format_telegram_operation_reply(label, content)


def _is_stop_command(command: str) -> bool:
    return command in {
        "/stop",
        "/detener",
        "/parar",
        "/cancelar_operacion",
        "/cancelar_operación",
        "/abortar",
        "/abort",
    }


def _stop_intent_for_message(text: str) -> bool:
    cleaned_text = str(text).strip()
    if not cleaned_text:
        return False

    if cleaned_text.startswith("/"):
        command = cleaned_text.split()[0].split("@")[0].lower()
        return _is_stop_command(command)

    normalized = _strip_yarbis_prefix(cleaned_text)
    return normalized in {
        "detente",
        "deten",
        "deten la operacion",
        "deten la operacion actual",
        "deten lo que estas haciendo",
        "para",
        "para ya",
        "para la operacion",
        "para lo que estas haciendo",
        "deja de pensar",
        "cancela la operacion",
        "cancela la operacion actual",
        "aborta la operacion",
    }


def _parse_timeout_seconds_arg(argument_text: str) -> tuple[int | None, str | None]:
    cleaned = _normalize_intent_text(argument_text)
    if not cleaned:
        return None, "Uso: /timeout 900"

    match = re.fullmatch(r"(\d{1,6})\s*(?:segundo|segundos|second|seconds|sec|s)?", cleaned)
    if match:
        return int(match.group(1)), None

    match = re.fullmatch(r"(\d{1,4})\s*(?:minuto|minutos|minute|minutes|min|m)", cleaned)
    if match:
        return int(match.group(1)) * 60, None

    return None, "Uso: /timeout 900, /timeout 15m o /ollama modelo 900"


def _ollama_settings_reply(prefix: str = "Configuracion actual de Ollama.") -> str:
    settings = get_ollama_settings()
    fallback_models = settings.get("fallback_models", [])
    fallback_text = ", ".join(fallback_models) if fallback_models else "-"
    return (
        f"{prefix}\n"
        f"Modelo: {settings.get('model', '')}\n"
        f"Fallbacks: {fallback_text}\n"
        f"Host: {settings.get('host') or 'local'}\n"
        f"API key env: {settings.get('api_key_env_var', 'OLLAMA_API_KEY')}\n"
        f"Timeout: {settings.get('timeout_seconds', '')} segundos"
    )


def _openrouter_settings_reply(prefix: str = "Configuracion actual de OpenRouter.") -> str:
    settings = get_openrouter_settings()
    fallback_models = settings.get("fallback_models", [])
    fallback_text = ", ".join(fallback_models) if fallback_models else "-"
    return (
        f"{prefix}\n"
        f"Modelo: {settings.get('model', '') or '-'}\n"
        f"Fallbacks: {fallback_text}\n"
        f"Host: {settings.get('host') or DEFAULT_OPENROUTER_HOST}\n"
        f"API key: {'guardada' if settings.get('api_key') else 'no configurada'}\n"
        f"API key env: {settings.get('api_key_env_var') or DEFAULT_OPENROUTER_API_KEY_ENV_VAR}\n"
        f"Timeout: {settings.get('timeout_seconds', '')} segundos"
    )


def _provider_settings_reply() -> str:
    provider = get_default_model_provider()
    provider_text = "OpenRouter" if provider == MODEL_PROVIDER_OPENROUTER else "Ollama"
    return (
        f"Proveedor por defecto: {provider_text}.\n\n"
        f"{_ollama_settings_reply()}\n\n"
        f"{_openrouter_settings_reply()}"
    )


def _dispatch_provider_command(argument_text: str) -> str:
    cleaned_provider = _normalize_intent_text(argument_text)
    if not cleaned_provider:
        return _provider_settings_reply() + "\n\nUso: /proveedor ollama o /proveedor openrouter"
    if cleaned_provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
        return "Proveedor invalido. Usa /proveedor ollama o /proveedor openrouter."
    return update_model_provider(cleaned_provider)


def _dispatch_model_command(argument_text: str) -> str:
    settings = get_ollama_settings()
    cleaned_argument = str(argument_text).strip()
    if not cleaned_argument:
        return _ollama_settings_reply("Modelo actual de Ollama.") + "\n\nUso: /modelo llama3.2:3b"

    parts = cleaned_argument.split()
    model = parts[0]
    timeout_seconds = settings["timeout_seconds"]
    if len(parts) > 1:
        parsed_timeout, error = _parse_timeout_seconds_arg(" ".join(parts[1:]))
        if error:
            return "Uso: /modelo llama3.2:3b 900"
        timeout_seconds = parsed_timeout

    return update_ollama_settings(model, timeout_seconds)


def _dispatch_timeout_command(argument_text: str) -> str:
    timeout_seconds, error = _parse_timeout_seconds_arg(argument_text)
    if error:
        return error

    provider = get_default_model_provider()
    if provider == MODEL_PROVIDER_OPENROUTER:
        settings = get_openrouter_settings()
        model = str(settings.get("model", "")).strip()
        if not model:
            return "Configura primero el modelo con /openrouter MODELO."
        return update_openrouter_settings(
            model,
            timeout_seconds,
            host=settings.get("host", DEFAULT_OPENROUTER_HOST),
            fallback_models=settings.get("fallback_models", []),
            api_key_env_var=settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR),
        )

    settings = get_ollama_settings()
    return update_ollama_settings(settings["model"], timeout_seconds)


def _first_argument_tail(text: str) -> str:
    parts = str(text).strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _dispatch_ollama_command(argument_text: str) -> str:
    cleaned_argument = str(argument_text).strip()
    if not cleaned_argument:
        return (
            _ollama_settings_reply()
            + "\n\nUso: /ollama llama3.2:3b 900, /ollama cloud gpt-oss:120b, "
            "/ollama host https://ollama.com o /ollama fallback gpt-oss:120b-cloud"
        )

    normalized_argument = _normalize_intent_text(cleaned_argument)
    if normalized_argument.startswith("modelo "):
        return _dispatch_model_command(cleaned_argument.split(maxsplit=1)[1])
    if normalized_argument.startswith("model "):
        return _dispatch_model_command(cleaned_argument.split(maxsplit=1)[1])
    if normalized_argument.startswith("timeout "):
        return _dispatch_timeout_command(cleaned_argument.split(maxsplit=1)[1])
    if normalized_argument.startswith("host "):
        settings = get_ollama_settings()
        host = _first_argument_tail(cleaned_argument)
        if _normalize_intent_text(host) in {"local", "daemon local", "localhost", "vacio"}:
            host = ""
        return update_ollama_settings(
            settings["model"],
            settings["timeout_seconds"],
            host=host,
        )
    if normalized_argument in {"host local", "local"} or normalized_argument.startswith("local "):
        settings = get_ollama_settings()
        model = _first_argument_tail(cleaned_argument) or settings["model"]
        return update_ollama_settings(
            model,
            settings["timeout_seconds"],
            host="",
        )
    if normalized_argument.startswith("cloud "):
        settings = get_ollama_settings()
        model = _first_argument_tail(cleaned_argument)
        if not model:
            return "Uso: /ollama cloud gpt-oss:120b"
        return update_ollama_settings(
            model,
            settings["timeout_seconds"],
            host=DEFAULT_OLLAMA_CLOUD_HOST,
        )
    if normalized_argument.startswith("fallback ") or normalized_argument.startswith("fallbacks "):
        settings = get_ollama_settings()
        fallback_models = _first_argument_tail(cleaned_argument)
        return update_ollama_settings(
            settings["model"],
            settings["timeout_seconds"],
            fallback_models=fallback_models,
        )
    if normalized_argument.startswith("api key env") or normalized_argument.startswith("api_key_env"):
        settings = get_ollama_settings()
        if normalized_argument.strip() in {"api key env", "api_key_env"}:
            return "Uso: /ollama api_key_env OLLAMA_API_KEY"
        api_key_env_var = cleaned_argument.split()[-1].strip() if len(cleaned_argument.split()) > 1 else ""
        if not api_key_env_var:
            return "Uso: /ollama api_key_env OLLAMA_API_KEY"
        return update_ollama_settings(
            settings["model"],
            settings["timeout_seconds"],
            api_key_env_var=api_key_env_var,
        )

    parts = cleaned_argument.split()
    model = parts[0]
    settings = get_ollama_settings()
    timeout_seconds = settings["timeout_seconds"]
    if len(parts) > 1:
        parsed_timeout, error = _parse_timeout_seconds_arg(" ".join(parts[1:]))
        if error:
            return "Uso: /ollama llama3.2:3b 900"
        timeout_seconds = parsed_timeout

    return update_ollama_settings(model, timeout_seconds)


def _dispatch_openrouter_command(argument_text: str) -> str:
    cleaned_argument = str(argument_text).strip()
    if not cleaned_argument:
        return (
            _openrouter_settings_reply()
            + "\n\nUso: /openrouter openai/gpt-4o-mini, /openrouter host https://openrouter.ai/api/v1 "
            "o /openrouter fallback modelo1, modelo2"
        )

    normalized_argument = _normalize_intent_text(cleaned_argument)
    settings = get_openrouter_settings()
    if normalized_argument.startswith("host "):
        host = _first_argument_tail(cleaned_argument) or DEFAULT_OPENROUTER_HOST
        model = str(settings.get("model", "")).strip()
        if not model:
            return "Configura primero el modelo con /openrouter MODELO."
        return update_openrouter_settings(
            model,
            settings["timeout_seconds"],
            host=host,
            fallback_models=settings.get("fallback_models", []),
            api_key_env_var=settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR),
        )
    if normalized_argument.startswith("fallback ") or normalized_argument.startswith("fallbacks "):
        model = str(settings.get("model", "")).strip()
        if not model:
            return "Configura primero el modelo con /openrouter MODELO."
        return update_openrouter_settings(
            model,
            settings["timeout_seconds"],
            fallback_models=_first_argument_tail(cleaned_argument),
            host=settings.get("host", DEFAULT_OPENROUTER_HOST),
            api_key_env_var=settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR),
        )
    if normalized_argument.startswith("api key env") or normalized_argument.startswith("api_key_env"):
        api_key_env_var = cleaned_argument.split()[-1].strip() if len(cleaned_argument.split()) > 1 else ""
        if not api_key_env_var:
            return "Uso: /openrouter api_key_env OPENROUTER_API_KEY"
        model = str(settings.get("model", "")).strip()
        if not model:
            return "Configura primero el modelo con /openrouter MODELO."
        return update_openrouter_settings(
            model,
            settings["timeout_seconds"],
            api_key_env_var=api_key_env_var,
            host=settings.get("host", DEFAULT_OPENROUTER_HOST),
            fallback_models=settings.get("fallback_models", []),
        )
    if normalized_argument.startswith("timeout "):
        return _dispatch_timeout_command(cleaned_argument.split(maxsplit=1)[1])

    parts = cleaned_argument.split()
    model = parts[0]
    timeout_seconds = settings["timeout_seconds"]
    if len(parts) > 1:
        parsed_timeout, error = _parse_timeout_seconds_arg(" ".join(parts[1:]))
        if error:
            return "Uso: /openrouter openai/gpt-4o-mini 900"
        timeout_seconds = parsed_timeout

    return update_openrouter_settings(
        model,
        timeout_seconds,
        host=settings.get("host", DEFAULT_OPENROUTER_HOST),
        fallback_models=settings.get("fallback_models", []),
        api_key_env_var=settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR),
    )


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


def _format_power_delay(delay_seconds: int) -> str:
    if delay_seconds <= 0:
        return "inmediatamente"
    if delay_seconds == 60:
        return "en 1 minuto"
    if delay_seconds % 60 == 0:
        return f"en {delay_seconds // 60} minutos"
    return f"en {delay_seconds} segundos"


def _power_action_label(action: str) -> str:
    return "reinicio" if action == "restart" else "apagado"


def _power_confirmation_command(action: str) -> str:
    return "/confirmar_reinicio" if action == "restart" else "/confirmar_apagado"


def _store_power_confirmation(action: str, delay_seconds: int, chat_id: str) -> str:
    token = uuid.uuid4().hex[:6].upper()
    requested_at = datetime.now(timezone.utc).isoformat()

    def mutate(state):
        notifications = state.setdefault("notifications", {})
        telegram = notifications.setdefault("telegram", {})
        telegram["pending_power_confirmation"] = {
            "action": action,
            "delay_seconds": int(delay_seconds),
            "token": token,
            "chat_id": str(chat_id).strip(),
            "requested_at": requested_at,
        }

    state_transaction("telegram_power_confirmation_store", mutate)
    return token


def _load_power_confirmation() -> dict:
    telegram = load_state().get("notifications", {}).get("telegram", {})
    if not isinstance(telegram, dict):
        return {}
    pending = telegram.get("pending_power_confirmation", {})
    return pending if isinstance(pending, dict) else {}


def _clear_power_confirmation(action: str = "", chat_id: str = "") -> bool:
    cleared = False

    def mutate(state):
        nonlocal cleared
        telegram = state.setdefault("notifications", {}).setdefault("telegram", {})
        pending = telegram.get("pending_power_confirmation", {})
        if not isinstance(pending, dict) or not pending.get("action"):
            return
        if action and pending.get("action") != action:
            return
        if chat_id and str(pending.get("chat_id", "")).strip() != str(chat_id).strip():
            return
        telegram["pending_power_confirmation"] = {
            "action": "",
            "delay_seconds": 0,
            "token": "",
            "chat_id": "",
            "requested_at": "",
        }
        cleared = True

    state_transaction("telegram_power_confirmation_clear", mutate)
    return cleared


def _power_confirmation_expired(pending: dict) -> bool:
    requested_at = str(pending.get("requested_at", "")).strip()
    if not requested_at:
        return True
    try:
        parsed = datetime.fromisoformat(requested_at)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    return age > POWER_CONFIRMATION_TTL_SECONDS


def _request_power_confirmation(action: str, delay_seconds: int, chat_id: str) -> str:
    if not chat_id:
        return "No pude preparar la confirmacion: falta el chat de Telegram."

    token = _store_power_confirmation(action, delay_seconds, chat_id)
    label = _power_action_label(action)
    command = _power_confirmation_command(action)
    return (
        f"Confirmacion requerida para {label} de esta PC {_format_power_delay(delay_seconds)}.\n\n"
        f"Responde exactamente: {command} {token}\n"
        "Caduca en 10 minutos. Para cancelar esta solicitud usa /cancelar_apagado o /cancelar_reinicio."
    )


def _dispatch_confirm_power(action: str, argument_text: str, chat_id: str) -> str:
    token = str(argument_text).strip().upper()
    label = _power_action_label(action)
    command = _power_confirmation_command(action)
    if not token:
        return f"Uso: {command} CODIGO"

    pending = _load_power_confirmation()
    if not pending.get("action"):
        return f"No hay ningun {label} pendiente de confirmar."
    if pending.get("action") != action:
        return f"La confirmacion pendiente no corresponde a {label}."
    if str(pending.get("chat_id", "")).strip() != str(chat_id).strip():
        return "Esta confirmacion pertenece a otro chat vinculado."
    if _power_confirmation_expired(pending):
        _clear_power_confirmation(action=action, chat_id=chat_id)
        return f"La confirmacion de {label} caduco. Vuelve a solicitarla."
    if str(pending.get("token", "")).strip().upper() != token:
        return "Codigo de confirmacion incorrecto."

    delay_seconds = int(pending.get("delay_seconds", DEFAULT_SHUTDOWN_DELAY_SECONDS))
    _clear_power_confirmation(action=action, chat_id=chat_id)
    if action == "restart":
        return request_system_restart(delay_seconds=delay_seconds)
    return request_system_shutdown(delay_seconds=delay_seconds)


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


def _dispatch_shutdown(argument_text: str = "", chat_id: str = "", confirmed: bool = False) -> str:
    delay_seconds, error = _parse_shutdown_delay_seconds(argument_text)
    if error:
        return error
    if not confirmed:
        return _request_power_confirmation("shutdown", delay_seconds, chat_id)
    return request_system_shutdown(delay_seconds=delay_seconds)


def _dispatch_restart(argument_text: str = "", chat_id: str = "", confirmed: bool = False) -> str:
    delay_seconds, error = _parse_shutdown_delay_seconds(argument_text)
    if error:
        return error.replace("/apagar", "/reiniciar")
    if not confirmed:
        return _request_power_confirmation("restart", delay_seconds, chat_id)
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

    if not cleaned_text.startswith("/") and has_pending_user_question(load_state()):
        return "Respuesta"

    note_label = note_request_label(cleaned_text)
    if note_label:
        return note_label

    if _stop_intent_for_message(cleaned_text):
        return "Detener"

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
    if _is_stop_command(command):
        return "Detener"
    if command in {"/modelo", "/model", "/ollama", "/openrouter"}:
        return "Modelo"
    if command in {"/proveedor", "/provider"}:
        return "Proveedor"
    if command in {"/timeout", "/tiempo"}:
        return "Timeout"
    if command in {"/goal", "/objetivo"}:
        return "Objetivo"
    if command in {"/notas", "/nota", "/crear_nota", "/guardar_nota", "/borrar_nota", "/eliminar_nota", "/ver_nota"}:
        return note_request_label(cleaned_text) or "Notas"
    if command in {"/apagar", "/apagar_pc", "/shutdown"}:
        return "Apagado"
    if command in {"/reiniciar", "/reiniciar_pc", "/restart", "/reboot"}:
        return "Reinicio"
    if command in {"/confirmar_apagado", "/confirmarapagado"}:
        return "Confirmar apagado"
    if command in {"/confirmar_reinicio", "/confirmarreinicio"}:
        return "Confirmar reinicio"
    if _is_shutdown_cancel_command(command) or _is_restart_cancel_command(command):
        return "Cancelar apagado/reinicio"

    return ""


def _dispatch_command(command_text: str, chat_id: str = "") -> str:
    cleaned_text = str(command_text).strip()
    parts = cleaned_text.split()
    command_parts = cleaned_text.split(maxsplit=1)
    command = parts[0].split("@")[0].lower() if parts else ""
    argument_text = command_parts[1].strip() if len(command_parts) > 1 else ""

    if command in {"/start", "/help"}:
        return _help_text()

    if command == "/status":
        return get_status_text()

    if _is_stop_command(command):
        return request_stop_current_operation(source="telegram")

    if command in {"/modelo", "/model"}:
        return _dispatch_model_command(argument_text)

    if command in {"/timeout", "/tiempo"}:
        return _dispatch_timeout_command(argument_text)

    if command in {"/proveedor", "/provider"}:
        return _dispatch_provider_command(argument_text)

    if command == "/ollama":
        return _dispatch_ollama_command(argument_text)

    if command == "/openrouter":
        return _dispatch_openrouter_command(argument_text)

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
        return _dispatch_shutdown(argument_text, chat_id=chat_id)

    if command in {"/reiniciar", "/reiniciar_pc", "/restart", "/reboot"}:
        return _dispatch_restart(argument_text, chat_id=chat_id)

    if command in {"/confirmar_apagado", "/confirmarapagado"}:
        return _dispatch_confirm_power("shutdown", argument_text, chat_id=chat_id)

    if command in {"/confirmar_reinicio", "/confirmarreinicio"}:
        return _dispatch_confirm_power("restart", argument_text, chat_id=chat_id)

    if _is_restart_cancel_command(command):
        cleared = _clear_power_confirmation(action="restart", chat_id=chat_id)
        result = cancel_system_shutdown(action_label="reinicio")
        return ("Confirmacion pendiente de reinicio cancelada.\n" if cleared else "") + result

    if _is_shutdown_cancel_command(command):
        cleared = _clear_power_confirmation(action="shutdown", chat_id=chat_id)
        result = cancel_system_shutdown(action_label="apagado")
        return ("Confirmacion pendiente de apagado cancelada.\n" if cleared else "") + result

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

    stop_intent = _stop_intent_for_message(text)
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
    pending_user_question = has_pending_user_question(load_state())

    if job_label:
        operation_id = _emit_job_started(job_label)
    else:
        operation_id = ""

    try:
        settings = load_state().get("notifications", {})
        with _telegram_thinking_indicator(chat_id, settings=settings, enabled=bool(job_label)):
            if text.startswith("/"):
                reply = _dispatch_command(text, chat_id=chat_id)
            elif stop_intent:
                reply = request_stop_current_operation(source="telegram")
            elif pending_user_question:
                reply = _submit_user_reply_from_telegram(text, chat_id)
            elif power_intent == "shutdown":
                reply = _dispatch_shutdown(_argument_after_natural_delay_prefix(text), chat_id=chat_id)
            elif power_intent == "restart":
                reply = _dispatch_restart(_argument_after_natural_delay_prefix(text), chat_id=chat_id)
            elif power_intent == "cancel_power_action":
                action_label = _cancel_label_for_natural_text(text)
                action = "restart" if action_label == "reinicio" else "shutdown"
                cleared = _clear_power_confirmation(action=action, chat_id=chat_id)
                reply = cancel_system_shutdown(action_label=action_label)
                if cleared:
                    reply = f"Confirmacion pendiente de {action_label} cancelada.\n{reply}"
            else:
                note_reply = handle_note_text_request(text)
                if note_reply is not None:
                    reply = note_reply
                else:
                    reply = _submit_user_reply_from_telegram(text, chat_id)
    except Exception as exc:
        if job_label:
            _emit_job_failed(job_label, redact_secrets(exc), operation_id=operation_id)
        raise

    if job_label:
        _emit_job_finished(job_label, reply, operation_id=operation_id)

    if binding_notice:
        reply = f"{binding_notice}\n\n{_telegram_reply_for_delivery(job_label, reply)}"
    else:
        reply = _telegram_reply_for_delivery(job_label, reply)

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
            summary = redact_secrets(
                f"Telegram: error procesando update {update_id or '?'}: {exc}"
            )
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
            if process_deferred_telegram_replies(limit=1):
                did_work = True
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
