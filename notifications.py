import os
import importlib
import json
from urllib import request
from uuid import uuid4

ENV_NOTIFICATIONS = "YARBIS_NOTIFICATIONS"
ENV_NOTIFICATION_CHANNELS = "YARBIS_NOTIFICATION_CHANNELS"
ENV_NTFY_SERVER = "YARBIS_NTFY_SERVER"
ENV_NTFY_TOPIC = "YARBIS_NTFY_TOPIC"
ENV_NTFY_TOKEN = "YARBIS_NTFY_TOKEN"
ENV_NTFY_PRIORITY = "YARBIS_NTFY_PRIORITY"
ENV_NTFY_TAGS = "YARBIS_NTFY_TAGS"
ENV_NTFY_TIMEOUT_SECONDS = "YARBIS_NTFY_TIMEOUT_SECONDS"

DEFAULT_NOTIFICATION_CHANNELS = ("windows", "ntfy")
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
DEFAULT_NTFY_TIMEOUT_SECONDS = 10

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

    return sent


def notify_user_input_required(question: str, reason: str = "") -> bool:
    question_text = str(question).strip()
    reason_text = str(reason).strip()

    body_parts = [part for part in (question_text, reason_text) if part]
    body = "\n".join(body_parts)

    return send_notification("Yarbis necesita tu respuesta", body)
