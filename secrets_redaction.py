import os
import re
from collections.abc import Callable, Iterable

REDACTED = "[redacted]"
SECRET_ENV_NAMES = (
    "YARBIS_TELEGRAM_BOT_TOKEN",
    "YARBIS_NTFY_TOKEN",
    "YARBIS_META_APP_SECRET",
    "YARBIS_LINKEDIN_CLIENT_SECRET",
    "YARBIS_FACEBOOK_ACCESS_TOKEN",
    "YARBIS_LINKEDIN_ACCESS_TOKEN",
    "YARBIS_OPENROUTER_API_KEY",
)
_TELEGRAM_BOT_URL_RE = re.compile(r"/bot([^/\s]+)/")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{3,}\b")
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE)
_ENV_ASSIGNMENT_RE = re.compile(
    r"\b(YARBIS_TELEGRAM_BOT_TOKEN|YARBIS_NTFY_TOKEN|YARBIS_META_APP_SECRET|"
    r"YARBIS_LINKEDIN_CLIENT_SECRET|YARBIS_FACEBOOK_ACCESS_TOKEN|YARBIS_LINKEDIN_ACCESS_TOKEN|"
    r"YARBIS_OPENROUTER_API_KEY)"
    r"(\s*[:=]\s*)([^\s,;]+)",
    re.IGNORECASE,
)
_TOKEN_PARAM_RE = re.compile(
    r"\b(access_token|client_secret|fb_exchange_token|refresh_token|code)(=)([^&\s]+)",
    re.IGNORECASE,
)


def _notification_settings(state: dict | None) -> dict:
    if not isinstance(state, dict):
        return {}
    notifications = state.get("notifications")
    if isinstance(notifications, dict):
        return notifications
    if any(key in state for key in ("telegram", "ntfy")):
        return state
    return {}


def _state_secret_values(state: dict | None) -> Iterable[str]:
    notifications = _notification_settings(state)

    telegram = notifications.get("telegram", {})
    if isinstance(telegram, dict):
        yield str(telegram.get("bot_token", "")).strip()

    ntfy = notifications.get("ntfy", {})
    if isinstance(ntfy, dict):
        yield str(ntfy.get("token", "")).strip()

    social = state.get("social", {}) if isinstance(state, dict) else {}
    if isinstance(social, dict):
        for account in social.get("accounts", []):
            if not isinstance(account, dict):
                continue
            token_ref = str(account.get("token_ref", "")).strip()
            if not token_ref:
                continue
            try:
                from credential_store import load_secret

                yield load_secret(token_ref)
            except Exception:
                continue

    model_provider = state.get("model_provider", {}) if isinstance(state, dict) else {}
    if isinstance(model_provider, dict):
        openrouter = model_provider.get("openrouter", {})
        if isinstance(openrouter, dict):
            yield str(openrouter.get("api_key", "")).strip()


def _load_current_state() -> dict:
    try:
        from memory import load_state

        return load_state()
    except Exception:
        return {}


def _secret_values(state: dict | None) -> list[str]:
    values = []
    source_state = state if state is not None else _load_current_state()

    for value in _state_secret_values(source_state):
        if value:
            values.append(value)

    for env_name in SECRET_ENV_NAMES:
        value = os.getenv(env_name, "").strip()
        if value:
            values.append(value)

    unique_values = []
    seen = set()
    for value in values:
        if len(value) < 4 or value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _redact_rendered(rendered: str, secret_values: Iterable[str]) -> str:
    if not rendered:
        return rendered

    redacted = _TELEGRAM_BOT_URL_RE.sub(f"/bot{REDACTED}/", rendered)
    redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", redacted)
    redacted = _ENV_ASSIGNMENT_RE.sub(rf"\1\2{REDACTED}", redacted)
    redacted = _TOKEN_PARAM_RE.sub(rf"\1\2{REDACTED}", redacted)

    for value in secret_values:
        redacted = redacted.replace(value, REDACTED)

    return _TELEGRAM_TOKEN_RE.sub(REDACTED, redacted)


def build_secret_redactor(state: dict | None = None) -> Callable[[object], str]:
    secret_values = _secret_values(state)

    def redact(text: object) -> str:
        return _redact_rendered(str(text), secret_values)

    return redact


def redact_secrets(text: object, state: dict | None = None) -> str:
    return _redact_rendered(str(text), _secret_values(state))
