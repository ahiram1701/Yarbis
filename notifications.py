import os
import importlib
import json
from urllib import request
from uuid import uuid4

from secrets_redaction import redact_secrets
from telegram_format import format_telegram_operation_reply

ENV_NOTIFICATIONS = "YARBIS_NOTIFICATIONS"
ENV_NOTIFICATION_CHANNELS = "YARBIS_NOTIFICATION_CHANNELS"
ENV_NTFY_SERVER = "YARBIS_NTFY_SERVER"
ENV_NTFY_TOPIC = "YARBIS_NTFY_TOPIC"
ENV_NTFY_TOKEN = "YARBIS_NTFY_TOKEN"
ENV_NTFY_PRIORITY = "YARBIS_NTFY_PRIORITY"
ENV_NTFY_TAGS = "YARBIS_NTFY_TAGS"
ENV_NTFY_TIMEOUT_SECONDS = "YARBIS_NTFY_TIMEOUT_SECONDS"
ENV_TELEGRAM_API_BASE = "YARBIS_TELEGRAM_API_BASE"
ENV_TELEGRAM_BOT_TOKEN = "YARBIS_TELEGRAM_BOT_TOKEN"
ENV_TELEGRAM_CHAT_ID = "YARBIS_TELEGRAM_CHAT_ID"
ENV_TELEGRAM_TIMEOUT_SECONDS = "YARBIS_TELEGRAM_TIMEOUT_SECONDS"
ENV_TELEGRAM_POLL_TIMEOUT_SECONDS = "YARBIS_TELEGRAM_POLL_TIMEOUT_SECONDS"

DEFAULT_NOTIFICATION_CHANNELS = ("windows", "ntfy")
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
DEFAULT_NTFY_TIMEOUT_SECONDS = 10
DEFAULT_TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TELEGRAM_TIMEOUT_SECONDS = 10
DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS = 25
MAX_TELEGRAM_MESSAGE_CHARS = 3800
TELEGRAM_API_MESSAGE_LIMIT = 4096
VALID_TELEGRAM_CHAT_ACTIONS = {
    "typing",
    "upload_photo",
    "record_video",
    "upload_video",
    "record_voice",
    "upload_voice",
    "upload_document",
    "choose_sticker",
    "find_location",
    "record_video_note",
    "upload_video_note",
}

_win11toast_notify = None


def _get_notify_provider():
    global _win11toast_notify

    if _win11toast_notify is not None:
        return _win11toast_notify

    try:
        module = importlib.import_module("win11toast")
    except Exception:  # pragma: no cover - depende del entorno Windows y la dependencia
        return None

    _win11toast_notify = getattr(module, "notify", None)
    return _win11toast_notify


def notifications_enabled() -> bool:
    raw_value = os.getenv(ENV_NOTIFICATIONS)
    if raw_value is None:
        settings = _load_notification_settings()
        return bool(settings.get("enabled", True))

    return raw_value.strip().lower() not in {"0", "false", "off", "no"}


def _get_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    try:
        return int(raw_value)
    except ValueError:
        return default


def _load_notification_settings() -> dict:
    try:
        from memory import load_state
    except Exception:  # pragma: no cover - respaldo si el modulo no esta disponible
        return {}

    try:
        return load_state().get("notifications", {})
    except Exception:
        return {}


def _configured_channels(settings: dict | None = None) -> tuple[str, ...]:
    raw_value = os.getenv(ENV_NOTIFICATION_CHANNELS)
    if raw_value is None:
        channels = (settings or {}).get("channels")
        if isinstance(channels, list):
            normalized_channels = tuple(str(channel).strip().lower() for channel in channels if channel)
            return normalized_channels or DEFAULT_NOTIFICATION_CHANNELS

        return DEFAULT_NOTIFICATION_CHANNELS

    raw_value = raw_value.strip()
    if not raw_value:
        return tuple()

    channels = []
    for item in raw_value.split(","):
        channel = item.strip().lower()
        if channel and channel not in channels:
            channels.append(channel)

    return tuple(channels)


def _setting_or_env(env_name: str, settings_value="", default="") -> str:
    raw_value = os.getenv(env_name)
    if raw_value is not None:
        return raw_value.strip()

    value = settings_value if settings_value is not None else default
    return str(value).strip()


def _int_setting_or_env(env_name: str, settings_value, default: int) -> int:
    raw_value = os.getenv(env_name)
    value = raw_value if raw_value is not None else settings_value

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _send_windows_notification(title: str, body: str) -> bool:
    notify_provider = _get_notify_provider()
    if notify_provider is None:
        return False

    tag = f"yarbis-{uuid4().hex}"

    try:
        notify_provider(title, body, tag=tag, group="yarbis")
    except Exception:
        return False

    return True


def _ntfy_payload(title: str, body: str, settings: dict | None = None) -> dict:
    settings = settings or {}
    ntfy_settings = settings.get("ntfy", {}) if isinstance(settings.get("ntfy", {}), dict) else {}

    payload = {
        "topic": _setting_or_env(
            ENV_NTFY_TOPIC,
            ntfy_settings.get("topic", ""),
        ).strip("/"),
        "title": title,
        "message": body or title,
    }

    priority = _setting_or_env(
        ENV_NTFY_PRIORITY,
        ntfy_settings.get("priority", ""),
    )
    if priority:
        payload["priority"] = priority

    tags = [
        tag.strip()
        for tag in _setting_or_env(ENV_NTFY_TAGS, ntfy_settings.get("tags", "")).split(",")
        if tag.strip()
    ]
    if tags:
        payload["tags"] = tags

    return payload


def _post_ntfy_payload(server_url: str, payload: dict, token: str = "", timeout: int = 10):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    ntfy_request = request.Request(
        server_url,
        data=data,
        headers=headers,
        method="POST",
    )
    with request.urlopen(ntfy_request, timeout=timeout) as response:
        response.read()


def _post_json(url: str, payload: dict, timeout: int = 10) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    json_request = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(json_request, timeout=timeout) as response:
        raw_body = response.read()

    if not raw_body:
        return {}

    return json.loads(raw_body.decode("utf-8"))


def _send_ntfy_notification(title: str, body: str, settings: dict | None = None) -> bool:
    settings = settings or {}
    ntfy_settings = settings.get("ntfy", {}) if isinstance(settings.get("ntfy", {}), dict) else {}
    payload = _ntfy_payload(title, body, settings=settings)
    if not payload["topic"]:
        return False

    server_url = _setting_or_env(
        ENV_NTFY_SERVER,
        ntfy_settings.get("server", ""),
        DEFAULT_NTFY_SERVER,
    ) or DEFAULT_NTFY_SERVER
    server_url = server_url.rstrip("/")
    token = _setting_or_env(ENV_NTFY_TOKEN, ntfy_settings.get("token", ""))
    timeout = max(
        1,
        _int_setting_or_env(
            ENV_NTFY_TIMEOUT_SECONDS,
            ntfy_settings.get("timeout_seconds", DEFAULT_NTFY_TIMEOUT_SECONDS),
            DEFAULT_NTFY_TIMEOUT_SECONDS,
        ),
    )

    try:
        _post_ntfy_payload(server_url, payload, token=token, timeout=timeout)
    except Exception:
        return False

    return True


def get_telegram_settings(settings: dict | None = None) -> dict:
    settings = settings or _load_notification_settings()
    telegram_settings = settings.get("telegram", {}) if isinstance(settings.get("telegram", {}), dict) else {}
    channels = _configured_channels(settings)

    return {
        "notifications_enabled": notifications_enabled(),
        "channel_enabled": "telegram" in channels,
        "enabled": notifications_enabled() and "telegram" in channels,
        "api_base": (
            _setting_or_env(
                ENV_TELEGRAM_API_BASE,
                telegram_settings.get("api_base", ""),
                DEFAULT_TELEGRAM_API_BASE,
            )
            or DEFAULT_TELEGRAM_API_BASE
        ).rstrip("/"),
        "bot_token": _setting_or_env(ENV_TELEGRAM_BOT_TOKEN, telegram_settings.get("bot_token", "")),
        "chat_id": _setting_or_env(ENV_TELEGRAM_CHAT_ID, telegram_settings.get("chat_id", "")),
        "timeout_seconds": max(
            1,
            _int_setting_or_env(
                ENV_TELEGRAM_TIMEOUT_SECONDS,
                telegram_settings.get("timeout_seconds", DEFAULT_TELEGRAM_TIMEOUT_SECONDS),
                DEFAULT_TELEGRAM_TIMEOUT_SECONDS,
            ),
        ),
        "poll_timeout_seconds": max(
            1,
            _int_setting_or_env(
                ENV_TELEGRAM_POLL_TIMEOUT_SECONDS,
                telegram_settings.get("poll_timeout_seconds", DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS),
                DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS,
            ),
        ),
    }


def try_link_telegram_chat(settings: dict | None = None) -> bool:
    config = get_telegram_settings(settings)
    if config["chat_id"]:
        return True

    if not config["enabled"] or not config["bot_token"]:
        return False

    try:
        response = telegram_api_request(
            "getUpdates",
            {
                "offset": 0,
                "timeout": 1,
                "allowed_updates": ["message"],
            },
            settings=settings,
            timeout=config["timeout_seconds"] + 1,
        )
    except Exception:
        return False

    updates = response.get("result", [])
    if not isinstance(updates, list):
        return False

    latest_private_chat_id = ""
    latest_update_id = 0
    for update in reversed(updates):
        try:
            latest_update_id = max(latest_update_id, int(update.get("update_id", 0)))
        except (TypeError, ValueError):
            pass

        message = update.get("message")
        if not isinstance(message, dict):
            continue

        chat = message.get("chat", {})
        incoming_chat_id = str(chat.get("id", "")).strip()
        chat_type = str(chat.get("type", "")).strip().lower()
        if incoming_chat_id and chat_type == "private":
            latest_private_chat_id = incoming_chat_id
            break

    if not latest_private_chat_id:
        return False

    try:
        from memory import state_transaction
    except Exception:
        return False

    try:
        def mutate(state):
            notifications_state = state.setdefault("notifications", {})
            telegram_state = notifications_state.setdefault("telegram", {})
            telegram_state["chat_id"] = latest_private_chat_id
            if latest_update_id > 0:
                try:
                    current_last_update_id = int(telegram_state.get("last_update_id", 0) or 0)
                except (TypeError, ValueError):
                    current_last_update_id = 0
                telegram_state["last_update_id"] = max(
                    current_last_update_id,
                    latest_update_id,
                )

        state_transaction("try_link_telegram_chat", mutate)
    except Exception:
        return False

    return True


def telegram_api_request(
    method_name: str,
    payload: dict | None = None,
    settings: dict | None = None,
    timeout: int | None = None,
) -> dict:
    config = get_telegram_settings(settings)
    if not config["bot_token"]:
        raise ValueError("Falta configurar el bot token de Telegram.")

    api_url = f"{config['api_base']}/bot{config['bot_token']}/{method_name}"
    redaction_state = {
        "notifications": {
            "telegram": {
                "bot_token": config["bot_token"],
            }
        }
    }
    try:
        response = _post_json(
            api_url,
            payload or {},
            timeout=timeout or config["timeout_seconds"],
        )
    except Exception as exc:
        raise RuntimeError(
            redact_secrets(
                f"No pude llamar a Telegram ({method_name}): {exc}",
                state=redaction_state,
            )
        ) from exc

    if response.get("ok") is False:
        description = redact_secrets(
            str(response.get("description", "")).strip() or "Error desconocido",
            state=redaction_state,
        )
        raise RuntimeError(f"Telegram rechazo la solicitud: {description}")

    return response


def _compose_notification_text(title: str, body: str) -> str:
    safe_title = str(title).strip() or "Yarbis"
    safe_body = str(body).strip()
    if not safe_body:
        return safe_title
    return f"{safe_title}\n\n{safe_body}"


def _pending_user_input_body_from_state() -> str:
    try:
        from memory import load_state
    except Exception:  # pragma: no cover - respaldo si memoria no esta disponible
        return ""

    try:
        awaiting_user_input = load_state().get("awaiting_user_input", {})
    except Exception:
        return ""

    if not isinstance(awaiting_user_input, dict):
        return ""

    question = str(awaiting_user_input.get("question", "")).strip()
    reason = str(awaiting_user_input.get("reason", "")).strip()
    if not question:
        return ""

    return "\n".join(part for part in (question, reason) if part)


def _compose_telegram_notification_text(title: str, body: str) -> str:
    safe_title = str(title).strip() or "Yarbis"
    safe_body = str(body).strip()
    if safe_title == "Yarbis necesita tu respuesta" and not safe_body:
        safe_body = _pending_user_input_body_from_state()

    rendered = _compose_notification_text(safe_title, safe_body)

    if safe_title == "Yarbis necesita tu respuesta":
        if not safe_body:
            rendered += "\n\nNo encontre la pregunta pendiente completa en memoria."
        rendered += (
            "\n\nPuedes responder en la app de escritorio o por este chat. "
            "Yarbis guardara la respuesta y retomara los ciclos sin perder continuidad."
        )

    return rendered


def _split_telegram_text(text: str) -> list[str]:
    safe_text = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not safe_text:
        return []

    if len(safe_text) <= MAX_TELEGRAM_MESSAGE_CHARS:
        return [safe_text]

    chunks = []
    remaining = safe_text
    while remaining:
        if len(remaining) <= MAX_TELEGRAM_MESSAGE_CHARS:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, MAX_TELEGRAM_MESSAGE_CHARS)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, MAX_TELEGRAM_MESSAGE_CHARS)
        if split_at <= 0:
            split_at = MAX_TELEGRAM_MESSAGE_CHARS

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    return [chunk for chunk in chunks if chunk]


def _add_telegram_chunk_headers(chunks: list[str]) -> list[str]:
    if len(chunks) <= 1:
        return chunks

    total = len(chunks)
    rendered_chunks = []
    for index, chunk in enumerate(chunks, start=1):
        header = f"Yarbis (parte {index}/{total})\n\n"
        if len(header) + len(chunk) <= TELEGRAM_API_MESSAGE_LIMIT:
            rendered_chunks.append(header + chunk)
        else:
            rendered_chunks.append(chunk)

    return rendered_chunks


def send_telegram_message(text: str, settings: dict | None = None, chat_id: str = "") -> bool:
    config = get_telegram_settings(settings)
    target_chat_id = str(chat_id).strip() or config["chat_id"]
    if not target_chat_id and not chat_id and try_link_telegram_chat(settings=settings):
        config = get_telegram_settings(settings)
        target_chat_id = config["chat_id"]
    message_chunks = _add_telegram_chunk_headers(_split_telegram_text(text))
    if not config["bot_token"] or not target_chat_id or not message_chunks:
        return False

    for chunk in message_chunks:
        telegram_api_request(
            "sendMessage",
            {
                "chat_id": target_chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            settings=settings,
        )

    return True


def send_telegram_operation_reply(
    label: str,
    content: str,
    settings: dict | None = None,
    chat_id: str = "",
) -> bool:
    rendered_content = str(content).strip()
    if not rendered_content:
        return False

    notification_settings = settings if settings is not None else _load_notification_settings()
    config = get_telegram_settings(notification_settings)
    if not config.get("enabled") or not config.get("bot_token"):
        return False

    try:
        return send_telegram_message(
            format_telegram_operation_reply(label, rendered_content),
            settings=notification_settings,
            chat_id=chat_id,
        )
    except Exception:
        return False


def send_telegram_chat_action(
    action: str = "typing",
    settings: dict | None = None,
    chat_id: str = "",
) -> bool:
    config = get_telegram_settings(settings)
    target_chat_id = str(chat_id).strip() or config["chat_id"]
    safe_action = str(action).strip() or "typing"
    if safe_action not in VALID_TELEGRAM_CHAT_ACTIONS:
        safe_action = "typing"

    if not config["bot_token"] or not target_chat_id:
        return False

    # Telegram bot tokens always contain a numeric bot id and a ':' separator.
    # This avoids slow network attempts from local tests or placeholder config.
    token_prefix = config["bot_token"].split(":", 1)[0]
    if ":" not in config["bot_token"] or not token_prefix.isdigit():
        return False

    try:
        telegram_api_request(
            "sendChatAction",
            {
                "chat_id": target_chat_id,
                "action": safe_action,
            },
            settings=settings,
        )
    except Exception:
        return False

    return True


def _send_telegram_notification(title: str, body: str, settings: dict | None = None) -> bool:
    try:
        return send_telegram_message(_compose_telegram_notification_text(title, body), settings=settings)
    except Exception:
        return False


def send_notification(title: str, body: str = "") -> bool:
    settings = _load_notification_settings()

    if not notifications_enabled():
        return False

    safe_title = str(title).strip() or "Yarbis"
    safe_body = str(body).strip()
    channels = _configured_channels(settings)
    sent = False

    if "windows" in channels:
        sent = _send_windows_notification(safe_title, safe_body) or sent

    if "ntfy" in channels:
        sent = _send_ntfy_notification(safe_title, safe_body, settings=settings) or sent

    if "telegram" in channels:
        sent = _send_telegram_notification(safe_title, safe_body, settings=settings) or sent

    return sent


def notify_user_input_required(question: str, reason: str = "") -> bool:
    question_text = str(question).strip()
    reason_text = str(reason).strip()

    body_parts = [part for part in (question_text, reason_text) if part]
    body = "\n".join(body_parts)
    if not body:
        body = _pending_user_input_body_from_state()

    return send_notification("Yarbis necesita tu respuesta", body)
