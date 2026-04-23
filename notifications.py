import os
import importlib
from uuid import uuid4

ENV_NOTIFICATIONS = "YARBIS_NOTIFICATIONS"

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
    raw_value = os.getenv(ENV_NOTIFICATIONS, "1").strip().lower()
    return raw_value not in {"0", "false", "off", "no"}


def send_notification(title: str, body: str = "") -> bool:
    if not notifications_enabled():
        return False

    notify_provider = _get_notify_provider()
    if notify_provider is None:
        return False

    safe_title = str(title).strip() or "Yarbis"
    safe_body = str(body).strip()
    tag = f"yarbis-{uuid4().hex}"

    try:
        notify_provider(safe_title, safe_body, tag=tag, group="yarbis")
    except Exception:
        return False

    return True


def notify_user_input_required(question: str, reason: str = "") -> bool:
    question_text = str(question).strip()
    reason_text = str(reason).strip()

    body_parts = [part for part in (question_text, reason_text) if part]
    body = "\n".join(body_parts)

    return send_notification("Yarbis necesita tu respuesta", body)
