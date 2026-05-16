import contextlib
import json
import os
import re
import threading
import time
from pathlib import Path

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows path is covered locally.
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX fallback only.
    fcntl = None

STATE_FILE = Path("state.json")
STATE_LOCK_FILE = Path(__file__).resolve().parent / ".yarbis_runtime" / "state.lock"
STATE_LOCK = threading.RLock()
_STATE_TRANSACTION_LOCAL = threading.local()
_STATE_LOCK_POLL_SECONDS = 0.05
_STATE_LOCK_TIMEOUT_SECONDS = 10.0
DEFAULT_GOAL = ""
LEGACY_DEFAULT_GOALS = {
    "Ayudar al usuario de forma autonoma con tareas locales.",
}
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 4_000
MAX_LAST_RESULT_CHARS = 4_000
MAX_PROFILE_ITEMS = 12
MAX_PROFILE_ITEM_CHARS = 140
MAX_NOTES = 30
MAX_NOTE_TITLE_CHARS = 120
MAX_NOTE_CONTENT_CHARS = 1_200
MAX_TASKS = 60
MAX_TASK_TITLE_CHARS = 160
MAX_TASK_DETAILS_CHARS = 1_200
MAX_TASK_RESULT_CHARS = 600
MAX_PLAN_ITEMS = 12
MAX_PLAN_ITEM_CHARS = 220
MAX_AWAITING_INPUT_QUESTION_CHARS = 280
MAX_AWAITING_INPUT_REASON_CHARS = 240
MAX_AWAITING_INPUT_FIELDS = 8
DEFAULT_MAX_STEPS_PER_CYCLE = 5
DEFAULT_AUTO_CYCLES = 5
DEFAULT_SERVICE_PROACTIVE_ENABLED = True
DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS = 30 * 60
DEFAULT_SERVICE_PROACTIVE_CYCLES = 1
DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS = 60
DEFAULT_SERVICE_PROACTIVE_MODEL = ""
DEFAULT_OLLAMA_MODEL = "qwen3.5:2b"
DEFAULT_OLLAMA_HOST = ""
DEFAULT_OLLAMA_CLOUD_HOST = "https://ollama.com"
DEFAULT_OLLAMA_API_KEY_ENV_VAR = "OLLAMA_API_KEY"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 900
MIN_OLLAMA_TIMEOUT_SECONDS = 1
MAX_OLLAMA_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_OLLAMA_MODEL_CHARS = 120
MAX_SERVICE_PROACTIVE_MODEL_CHARS = MAX_OLLAMA_MODEL_CHARS
MAX_OLLAMA_HOST_CHARS = 240
MAX_OLLAMA_API_KEY_ENV_VAR_CHARS = 80
MAX_OLLAMA_FALLBACK_MODELS = 8
VALID_TASK_STATUS = {"pending", "in_progress", "blocked", "done"}
VALID_TASK_PRIORITY = {"alta", "media", "baja"}
VALID_UI_THEME = {"light", "dark"}
VALID_NOTIFICATION_CHANNELS = {"windows", "ntfy", "telegram"}
VALID_NTFY_PRIORITIES = {"", "min", "low", "default", "high", "urgent", "1", "2", "3", "4", "5"}
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
DEFAULT_NTFY_TIMEOUT_SECONDS = 10
DEFAULT_TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TELEGRAM_TIMEOUT_SECONDS = 10
DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS = 25
DEFAULT_INTERNET_MODE = "auto"
VALID_INTERNET_MODES = {"off", "auto"}
DEFAULT_SEARCH_PROVIDER = "duckduckgo_html"
VALID_SEARCH_PROVIDERS = {DEFAULT_SEARCH_PROVIDER}
DEFAULT_INTERNET_MAX_SEARCH_RESULTS = 5
DEFAULT_INTERNET_MAX_PAGE_CHARS = 12_000
DEFAULT_INTERNET_REQUEST_TIMEOUT_SECONDS = 10
MAX_INTERNET_DOMAIN_ITEMS = 20
MAX_INTERNET_DOMAIN_CHARS = 120
MAX_SELF_KNOWLEDGE_SUMMARY_CHARS = 20_000
MAX_SELF_KNOWLEDGE_TIMESTAMP_CHARS = 80
MAX_RUNTIME_OPERATION_LABEL_CHARS = 80
MAX_RUNTIME_OPERATION_SOURCE_CHARS = 40
MAX_RUNTIME_TIMESTAMP_CHARS = 80
MAX_RUNTIME_OPERATION_ID_CHARS = 80
MAX_RUNTIME_STOP_REASON_CHARS = 240
DEFAULT_LOCAL_CONTEXT_MODE = "safe"
VALID_LOCAL_CONTEXT_MODES = {"off", "safe", "detailed"}
DEFAULT_LOCAL_CONTEXT_SAMPLE_INTERVAL_SECONDS = 30
DEFAULT_LOCAL_CONTEXT_MAX_SNAPSHOT_AGE_SECONDS = 180
MAX_LOCAL_CONTEXT_MODE_CHARS = 20
DEFAULT_META_GRAPH_VERSION = "v24.0"
DEFAULT_LINKEDIN_VERSION = "202604"
VALID_SOCIAL_PLATFORMS = {
    "facebook_page",
    "facebook_personal",
    "instagram",
    "linkedin",
}
VALID_SOCIAL_ACCOUNT_TYPES = {
    "facebook_page",
    "facebook_personal",
    "instagram_professional",
    "linkedin_member",
    "linkedin_organization",
}
VALID_SOCIAL_DRAFT_STATUS = {"draft", "pending", "published", "failed", "archived"}
VALID_SOCIAL_PUBLICATION_STATUS = {"pending_confirmation", "published", "failed", "assisted_opened"}
MAX_SOCIAL_ITEMS = 80
MAX_SOCIAL_TEXT_CHARS = 8_000
MAX_SOCIAL_METADATA_CHARS = 2_000


def default_state():
    return {
        "goal": DEFAULT_GOAL,
        "messages": [],
        "last_result": "",
        "cycle_count": 0,
        "profile": {
            "name": "",
            "role": "",
            "preferences": [],
            "constraints": [],
        },
        "notes": [],
        "tasks": [],
        "current_plan": [],
        "awaiting_user_input": {
            "pending": False,
            "question": "",
            "reason": "",
            "fields": [],
        },
        "autonomy": {
            "max_steps_per_cycle": DEFAULT_MAX_STEPS_PER_CYCLE,
            "auto_cycles_default": DEFAULT_AUTO_CYCLES,
        },
        "ollama": {
            "model": DEFAULT_OLLAMA_MODEL,
            "fallback_models": [],
            "host": DEFAULT_OLLAMA_HOST,
            "api_key_env_var": DEFAULT_OLLAMA_API_KEY_ENV_VAR,
            "timeout_seconds": DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        },
        "service": {
            "proactive": {
                "enabled": DEFAULT_SERVICE_PROACTIVE_ENABLED,
                "interval_seconds": DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS,
                "cycles": DEFAULT_SERVICE_PROACTIVE_CYCLES,
                "start_delay_seconds": DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS,
                "model": DEFAULT_SERVICE_PROACTIVE_MODEL,
                "last_pulse_at": "",
            },
        },
        "ui": {
            "theme": "dark",
        },
        "runtime": {
            "thinking": {
                "active": False,
                "label": "",
                "source": "",
                "started_at": "",
                "operation_id": "",
            },
            "stop_requested": {
                "active": False,
                "operation_id": "",
                "requested_at": "",
                "source": "",
                "reason": "",
            },
        },
        "local_context": {
            "enabled": True,
            "mode": DEFAULT_LOCAL_CONTEXT_MODE,
            "sample_interval_seconds": DEFAULT_LOCAL_CONTEXT_SAMPLE_INTERVAL_SECONDS,
            "max_snapshot_age_seconds": DEFAULT_LOCAL_CONTEXT_MAX_SNAPSHOT_AGE_SECONDS,
            "include_window_title": False,
            "include_process_name": True,
            "include_workspace_changes": True,
            "include_system_health": True,
        },
        "internet": {
            "mode": DEFAULT_INTERNET_MODE,
            "provider": DEFAULT_SEARCH_PROVIDER,
            "max_search_results": DEFAULT_INTERNET_MAX_SEARCH_RESULTS,
            "max_page_chars": DEFAULT_INTERNET_MAX_PAGE_CHARS,
            "request_timeout_seconds": DEFAULT_INTERNET_REQUEST_TIMEOUT_SECONDS,
            "allowed_domains": [],
            "blocked_domains": [],
        },
        "self_knowledge": {
            "last_analyzed_at": "",
            "summary": "",
        },
        "notifications": {
            "enabled": True,
            "channels": ["windows"],
            "ntfy": {
                "server": DEFAULT_NTFY_SERVER,
                "topic": "",
                "token": "",
                "priority": "",
                "tags": "",
                "timeout_seconds": DEFAULT_NTFY_TIMEOUT_SECONDS,
            },
            "telegram": {
                "api_base": DEFAULT_TELEGRAM_API_BASE,
                "bot_token": "",
                "chat_id": "",
                "timeout_seconds": DEFAULT_TELEGRAM_TIMEOUT_SECONDS,
                "poll_timeout_seconds": DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS,
                "last_update_id": 0,
                "pending_power_confirmation": {
                    "action": "",
                    "delay_seconds": 0,
                    "token": "",
                    "chat_id": "",
                    "requested_at": "",
                },
            },
        },
        "social": {
            "settings": {
                "meta_graph_version": DEFAULT_META_GRAPH_VERSION,
                "linkedin_version": DEFAULT_LINKEDIN_VERSION,
                "require_confirmation": True,
            },
            "accounts": [],
            "drafts": [],
            "pending_publications": [],
            "history": [],
        },
    }


def _coerce_text(value, limit: int) -> str:
    return str(value)


def _prepare_state_lock_file(handle):
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b" ")
        handle.flush()
    handle.seek(0)


def _lock_state_handle(handle, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))

    if msvcrt is not None:
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(_STATE_LOCK_POLL_SECONDS)

    if fcntl is not None:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(_STATE_LOCK_POLL_SECONDS)

    return True


def _unlock_state_handle(handle):
    try:
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _state_file_lock(label: str = "state"):
    depth = int(getattr(_STATE_TRANSACTION_LOCAL, "depth", 0) or 0)
    if depth > 0:
        _STATE_TRANSACTION_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _STATE_TRANSACTION_LOCAL.depth = depth
        return

    STATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(STATE_LOCK_FILE, "a+b")
    try:
        _prepare_state_lock_file(handle)
        if not _lock_state_handle(handle, _STATE_LOCK_TIMEOUT_SECONDS):
            raise TimeoutError(
                "No pude tomar el lock de estado "
                f"({STATE_LOCK_FILE}) para {str(label).strip() or 'state'}."
            )

        _STATE_TRANSACTION_LOCAL.depth = 1
        try:
            yield
        finally:
            _STATE_TRANSACTION_LOCAL.depth = 0
            _unlock_state_handle(handle)
    finally:
        handle.close()


def _normalize_message(message):
    if not isinstance(message, dict):
        return None

    role = str(message.get("role", "assistant"))
    normalized = {"role": role}

    content = message.get("content", "")
    normalized["content"] = _coerce_text(content, MAX_MESSAGE_CHARS)

    tool_name = message.get("tool_name")
    if tool_name:
        normalized["tool_name"] = str(tool_name)

    tool_calls = message.get("tool_calls")
    if tool_calls:
        normalized["tool_calls"] = tool_calls

    return normalized


def _normalize_string_list(value, item_limit: int, char_limit: int) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in re.split(r"[\n,;]+", value)]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = []

    normalized = []
    seen = set()
    for item in raw_items:
        if not item:
            continue

        normalized_item = _coerce_text(item, char_limit)
        lowered = normalized_item.casefold()
        if lowered in seen:
            continue

        normalized.append(normalized_item)
        seen.add(lowered)

    return normalized


def _fallback_id(prefix: str, seed: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(seed).strip().lower()).strip("-")
    if not cleaned:
        cleaned = "item"
    return f"{prefix}-{cleaned[:12]}"


def _normalize_note(note):
    if not isinstance(note, dict):
        return None

    title = _coerce_text(note.get("title", ""), MAX_NOTE_TITLE_CHARS).strip()
    content = _coerce_text(note.get("content", ""), MAX_NOTE_CONTENT_CHARS).strip()
    category = _coerce_text(note.get("category", "general"), 40).strip() or "general"

    if not title and not content:
        return None

    note_id = _coerce_text(note.get("id") or _fallback_id("note", title or content), 32).strip()

    return {
        "id": note_id,
        "title": title or "Nota sin titulo",
        "content": content,
        "category": category,
    }


def _normalize_task(task):
    if not isinstance(task, dict):
        return None

    title = _coerce_text(task.get("title", ""), MAX_TASK_TITLE_CHARS).strip()
    details = _coerce_text(task.get("details", ""), MAX_TASK_DETAILS_CHARS).strip()
    result = _coerce_text(task.get("result", ""), MAX_TASK_RESULT_CHARS).strip()

    if not title:
        return None

    raw_status = str(task.get("status", "pending")).strip().lower()
    raw_priority = str(task.get("priority", "media")).strip().lower()

    status = raw_status if raw_status in VALID_TASK_STATUS else "pending"
    priority = raw_priority if raw_priority in VALID_TASK_PRIORITY else "media"
    task_id = _coerce_text(task.get("id") or _fallback_id("task", title), 32).strip()

    return {
        "id": task_id,
        "title": title,
        "details": details,
        "status": status,
        "priority": priority,
        "result": result,
    }


def _normalize_profile(profile):
    if not isinstance(profile, dict):
        profile = {}

    return {
        "name": _coerce_text(profile.get("name", ""), 80).strip(),
        "role": _coerce_text(profile.get("role", ""), 120).strip(),
        "preferences": _normalize_string_list(
            profile.get("preferences", []),
            item_limit=MAX_PROFILE_ITEMS,
            char_limit=MAX_PROFILE_ITEM_CHARS,
        ),
        "constraints": _normalize_string_list(
            profile.get("constraints", []),
            item_limit=MAX_PROFILE_ITEMS,
            char_limit=MAX_PROFILE_ITEM_CHARS,
        ),
    }


def _normalize_plan(plan):
    return _normalize_string_list(
        plan,
        item_limit=MAX_PLAN_ITEMS,
        char_limit=MAX_PLAN_ITEM_CHARS,
    )


def _normalize_autonomy(autonomy):
    defaults = default_state()["autonomy"]
    if not isinstance(autonomy, dict):
        autonomy = {}

    normalized = {}

    try:
        normalized["max_steps_per_cycle"] = max(
            1,
            min(12, int(autonomy.get("max_steps_per_cycle", defaults["max_steps_per_cycle"]))),
        )
    except (TypeError, ValueError):
        normalized["max_steps_per_cycle"] = defaults["max_steps_per_cycle"]

    try:
        normalized["auto_cycles_default"] = max(
            1,
            min(20, int(autonomy.get("auto_cycles_default", defaults["auto_cycles_default"]))),
        )
    except (TypeError, ValueError):
        normalized["auto_cycles_default"] = defaults["auto_cycles_default"]

    return normalized


def _normalize_ollama(ollama):
    defaults = default_state()["ollama"]
    if not isinstance(ollama, dict):
        ollama = {}

    model = _coerce_text(
        ollama.get("model", defaults["model"]),
        MAX_OLLAMA_MODEL_CHARS,
    ).strip()
    if not model:
        model = defaults["model"]

    host = _coerce_text(
        ollama.get("host", defaults["host"]),
        MAX_OLLAMA_HOST_CHARS,
    ).strip()
    if host.endswith("/api"):
        host = host[:-4].rstrip("/")
    host = host.rstrip("/")

    api_key_env_var = _coerce_text(
        ollama.get("api_key_env_var", defaults["api_key_env_var"]),
        MAX_OLLAMA_API_KEY_ENV_VAR_CHARS,
    ).strip() or defaults["api_key_env_var"]

    raw_fallback_models = ollama.get("fallback_models", defaults["fallback_models"])
    if isinstance(raw_fallback_models, str):
        fallback_candidates = re.split(r"[,;\n]+", raw_fallback_models)
    elif isinstance(raw_fallback_models, list):
        fallback_candidates = raw_fallback_models
    else:
        fallback_candidates = []

    fallback_models = []
    seen_models = {model}
    for candidate in fallback_candidates:
        fallback_model = _coerce_text(candidate, MAX_OLLAMA_MODEL_CHARS).strip()
        if not fallback_model or fallback_model in seen_models:
            continue
        fallback_models.append(fallback_model)
        seen_models.add(fallback_model)
        if len(fallback_models) >= MAX_OLLAMA_FALLBACK_MODELS:
            break

    try:
        timeout_seconds = max(
            MIN_OLLAMA_TIMEOUT_SECONDS,
            min(
                MAX_OLLAMA_TIMEOUT_SECONDS,
                int(ollama.get("timeout_seconds", defaults["timeout_seconds"])),
            ),
        )
    except (TypeError, ValueError):
        timeout_seconds = defaults["timeout_seconds"]

    return {
        "model": model,
        "fallback_models": fallback_models,
        "host": host,
        "api_key_env_var": api_key_env_var,
        "timeout_seconds": timeout_seconds,
    }


def _normalize_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if not cleaned:
            return default
        return cleaned not in {"0", "false", "off", "no", "disabled"}
    if value is None:
        return default
    return bool(value)


def _normalize_service(service):
    defaults = default_state()["service"]
    if not isinstance(service, dict):
        service = {}

    proactive = service.get("proactive", {})
    if not isinstance(proactive, dict):
        proactive = {}
    proactive_defaults = defaults["proactive"]

    try:
        interval_seconds = max(
            60,
            min(
                24 * 60 * 60,
                int(proactive.get(
                    "interval_seconds",
                    proactive_defaults["interval_seconds"],
                )),
            ),
        )
    except (TypeError, ValueError):
        interval_seconds = proactive_defaults["interval_seconds"]

    try:
        cycles = max(
            1,
            min(5, int(proactive.get("cycles", proactive_defaults["cycles"]))),
        )
    except (TypeError, ValueError):
        cycles = proactive_defaults["cycles"]

    try:
        start_delay_seconds = max(
            0,
            min(
                24 * 60 * 60,
                int(proactive.get(
                    "start_delay_seconds",
                    proactive_defaults["start_delay_seconds"],
                )),
            ),
        )
    except (TypeError, ValueError):
        start_delay_seconds = proactive_defaults["start_delay_seconds"]

    return {
        "proactive": {
            "enabled": _normalize_bool(
                proactive.get("enabled", proactive_defaults["enabled"]),
                proactive_defaults["enabled"],
            ),
            "interval_seconds": interval_seconds,
            "cycles": cycles,
            "start_delay_seconds": start_delay_seconds,
            "model": _coerce_text(
                proactive.get(
                    "model",
                    proactive_defaults.get("model", DEFAULT_SERVICE_PROACTIVE_MODEL),
                ),
                MAX_SERVICE_PROACTIVE_MODEL_CHARS,
            ).strip(),
            "last_pulse_at": _coerce_text(
                proactive.get("last_pulse_at", proactive_defaults.get("last_pulse_at", "")),
                MAX_RUNTIME_TIMESTAMP_CHARS,
            ).strip(),
        },
    }


def _normalize_awaiting_user_input(awaiting_user_input):
    if not isinstance(awaiting_user_input, dict):
        awaiting_user_input = {}

    question = _coerce_text(
        awaiting_user_input.get("question", ""),
        MAX_AWAITING_INPUT_QUESTION_CHARS,
    ).strip()
    reason = _coerce_text(
        awaiting_user_input.get("reason", ""),
        MAX_AWAITING_INPUT_REASON_CHARS,
    ).strip()
    fields = _normalize_string_list(
        awaiting_user_input.get("fields", []),
        item_limit=MAX_AWAITING_INPUT_FIELDS,
        char_limit=80,
    )
    pending = bool(awaiting_user_input.get("pending")) and bool(question)

    return {
        "pending": pending,
        "question": question if pending else "",
        "reason": reason if pending else "",
        "fields": fields if pending else [],
    }


def _normalize_ui(ui):
    defaults = default_state()["ui"]
    if not isinstance(ui, dict):
        ui = {}

    theme = str(ui.get("theme", defaults["theme"])).strip().lower()
    if theme not in VALID_UI_THEME:
        theme = defaults["theme"]

    return {
        "theme": theme,
    }


def _normalize_runtime(runtime):
    defaults = default_state()["runtime"]
    if not isinstance(runtime, dict):
        runtime = {}

    normalized = {
        "thinking": defaults["thinking"],
        "stop_requested": defaults["stop_requested"],
    }

    thinking = runtime.get("thinking", {})
    if not isinstance(thinking, dict):
        thinking = {}

    label = _coerce_text(
        thinking.get("label", ""),
        MAX_RUNTIME_OPERATION_LABEL_CHARS,
    ).strip()
    source = _coerce_text(
        thinking.get("source", ""),
        MAX_RUNTIME_OPERATION_SOURCE_CHARS,
    ).strip()
    started_at = _coerce_text(
        thinking.get("started_at", ""),
        MAX_RUNTIME_TIMESTAMP_CHARS,
    ).strip()
    operation_id = _coerce_text(
        thinking.get("operation_id", ""),
        MAX_RUNTIME_OPERATION_ID_CHARS,
    ).strip()
    active = bool(thinking.get("active")) and bool(label)

    if active:
        normalized["thinking"] = {
            "active": True,
            "label": label,
            "source": source,
            "started_at": started_at,
            "operation_id": operation_id,
        }

    stop_requested = runtime.get("stop_requested", {})
    if not isinstance(stop_requested, dict):
        stop_requested = {}

    stop_operation_id = _coerce_text(
        stop_requested.get("operation_id", ""),
        MAX_RUNTIME_OPERATION_ID_CHARS,
    ).strip()
    stop_requested_at = _coerce_text(
        stop_requested.get("requested_at", ""),
        MAX_RUNTIME_TIMESTAMP_CHARS,
    ).strip()
    stop_source = _coerce_text(
        stop_requested.get("source", ""),
        MAX_RUNTIME_OPERATION_SOURCE_CHARS,
    ).strip()
    stop_reason = _coerce_text(
        stop_requested.get("reason", ""),
        MAX_RUNTIME_STOP_REASON_CHARS,
    ).strip()

    if bool(stop_requested.get("active")):
        normalized["stop_requested"] = {
            "active": True,
            "operation_id": stop_operation_id,
            "requested_at": stop_requested_at,
            "source": stop_source,
            "reason": stop_reason,
        }

    return normalized


def _normalize_local_context(local_context):
    defaults = default_state()["local_context"]
    if not isinstance(local_context, dict):
        local_context = {}

    mode = _coerce_text(
        local_context.get("mode", defaults["mode"]),
        MAX_LOCAL_CONTEXT_MODE_CHARS,
    ).strip().lower()
    if mode not in VALID_LOCAL_CONTEXT_MODES:
        mode = defaults["mode"]

    try:
        sample_interval_seconds = max(
            5,
            min(
                24 * 60 * 60,
                int(local_context.get(
                    "sample_interval_seconds",
                    defaults["sample_interval_seconds"],
                )),
            ),
        )
    except (TypeError, ValueError):
        sample_interval_seconds = defaults["sample_interval_seconds"]

    try:
        max_snapshot_age_seconds = max(
            15,
            min(
                24 * 60 * 60,
                int(local_context.get(
                    "max_snapshot_age_seconds",
                    defaults["max_snapshot_age_seconds"],
                )),
            ),
        )
    except (TypeError, ValueError):
        max_snapshot_age_seconds = defaults["max_snapshot_age_seconds"]

    enabled = _normalize_bool(local_context.get("enabled", defaults["enabled"]), defaults["enabled"])
    if mode == "off":
        enabled = False

    return {
        "enabled": enabled,
        "mode": mode,
        "sample_interval_seconds": sample_interval_seconds,
        "max_snapshot_age_seconds": max_snapshot_age_seconds,
        "include_window_title": (
            mode == "detailed"
            and _normalize_bool(
                local_context.get("include_window_title", defaults["include_window_title"]),
                defaults["include_window_title"],
            )
        ),
        "include_process_name": _normalize_bool(
            local_context.get("include_process_name", defaults["include_process_name"]),
            defaults["include_process_name"],
        ),
        "include_workspace_changes": _normalize_bool(
            local_context.get("include_workspace_changes", defaults["include_workspace_changes"]),
            defaults["include_workspace_changes"],
        ),
        "include_system_health": _normalize_bool(
            local_context.get("include_system_health", defaults["include_system_health"]),
            defaults["include_system_health"],
        ),
    }


def _normalize_domain_list(value) -> list[str]:
    items = _normalize_string_list(
        value,
        item_limit=MAX_INTERNET_DOMAIN_ITEMS,
        char_limit=MAX_INTERNET_DOMAIN_CHARS,
    )

    normalized = []
    seen = set()
    for item in items:
        domain = str(item).strip().lower()
        domain = re.sub(r"^https?://", "", domain)
        domain = domain.strip("/")
        if not domain:
            continue
        if domain in seen:
            continue
        normalized.append(domain)
        seen.add(domain)

    return normalized


def _normalize_internet(internet):
    defaults = default_state()["internet"]
    if not isinstance(internet, dict):
        internet = {}

    mode = str(internet.get("mode", defaults["mode"])).strip().lower()
    if mode not in VALID_INTERNET_MODES:
        mode = defaults["mode"]

    provider = str(internet.get("provider", defaults["provider"])).strip().lower()
    if provider not in VALID_SEARCH_PROVIDERS:
        provider = defaults["provider"]

    try:
        max_search_results = max(
            1,
            min(
                10,
                int(internet.get("max_search_results", defaults["max_search_results"])),
            ),
        )
    except (TypeError, ValueError):
        max_search_results = defaults["max_search_results"]

    try:
        max_page_chars = max(
            1_000,
            min(30_000, int(internet.get("max_page_chars", defaults["max_page_chars"]))),
        )
    except (TypeError, ValueError):
        max_page_chars = defaults["max_page_chars"]

    try:
        request_timeout_seconds = max(
            3,
            min(
                60,
                int(
                    internet.get(
                        "request_timeout_seconds",
                        defaults["request_timeout_seconds"],
                    )
                ),
            ),
        )
    except (TypeError, ValueError):
        request_timeout_seconds = defaults["request_timeout_seconds"]

    return {
        "mode": mode,
        "provider": provider,
        "max_search_results": max_search_results,
        "max_page_chars": max_page_chars,
        "request_timeout_seconds": request_timeout_seconds,
        "allowed_domains": _normalize_domain_list(internet.get("allowed_domains", [])),
        "blocked_domains": _normalize_domain_list(internet.get("blocked_domains", [])),
    }


def _normalize_self_knowledge(self_knowledge):
    if not isinstance(self_knowledge, dict):
        self_knowledge = {}

    return {
        "last_analyzed_at": _coerce_text(
            self_knowledge.get("last_analyzed_at", ""),
            MAX_SELF_KNOWLEDGE_TIMESTAMP_CHARS,
        ).strip(),
        "summary": _coerce_text(
            self_knowledge.get("summary", ""),
            MAX_SELF_KNOWLEDGE_SUMMARY_CHARS,
        ).strip(),
    }


def _normalize_notification_channels(value):
    if isinstance(value, str):
        raw_channels = [item.strip().lower() for item in re.split(r"[\n,;]+", value)]
    elif isinstance(value, list):
        raw_channels = [str(item).strip().lower() for item in value]
    else:
        raw_channels = []

    channels = []
    for channel in raw_channels:
        if channel in VALID_NOTIFICATION_CHANNELS and channel not in channels:
            channels.append(channel)

    return channels or ["windows"]


def _normalize_notifications(notifications):
    defaults = default_state()["notifications"]
    if not isinstance(notifications, dict):
        notifications = {}

    ntfy = notifications.get("ntfy", {})
    if not isinstance(ntfy, dict):
        ntfy = {}

    telegram = notifications.get("telegram", {})
    if not isinstance(telegram, dict):
        telegram = {}

    priority = _coerce_text(ntfy.get("priority", defaults["ntfy"]["priority"]), 20).strip().lower()
    if priority not in VALID_NTFY_PRIORITIES:
        priority = defaults["ntfy"]["priority"]

    try:
        timeout_seconds = max(
            1,
            min(
                60,
                int(ntfy.get("timeout_seconds", defaults["ntfy"]["timeout_seconds"])),
            ),
        )
    except (TypeError, ValueError):
        timeout_seconds = defaults["ntfy"]["timeout_seconds"]

    try:
        telegram_timeout_seconds = max(
            1,
            min(
                60,
                int(telegram.get("timeout_seconds", defaults["telegram"]["timeout_seconds"])),
            ),
        )
    except (TypeError, ValueError):
        telegram_timeout_seconds = defaults["telegram"]["timeout_seconds"]

    try:
        telegram_poll_timeout_seconds = max(
            1,
            min(
                60,
                int(
                    telegram.get(
                        "poll_timeout_seconds",
                        defaults["telegram"]["poll_timeout_seconds"],
                    )
                ),
            ),
        )
    except (TypeError, ValueError):
        telegram_poll_timeout_seconds = defaults["telegram"]["poll_timeout_seconds"]

    try:
        telegram_last_update_id = max(
            0,
            int(telegram.get("last_update_id", defaults["telegram"]["last_update_id"])),
        )
    except (TypeError, ValueError):
        telegram_last_update_id = defaults["telegram"]["last_update_id"]

    raw_pending_power = telegram.get("pending_power_confirmation", {})
    if not isinstance(raw_pending_power, dict):
        raw_pending_power = {}
    pending_action = _coerce_text(raw_pending_power.get("action", ""), 20).strip().lower()
    if pending_action not in {"shutdown", "restart"}:
        pending_action = ""
    try:
        pending_delay = max(0, min(3600, int(raw_pending_power.get("delay_seconds", 0))))
    except (TypeError, ValueError):
        pending_delay = 0

    return {
        "enabled": bool(notifications.get("enabled", defaults["enabled"])),
        "channels": _normalize_notification_channels(
            notifications.get("channels", defaults["channels"]),
        ),
        "ntfy": {
            "server": _coerce_text(
                ntfy.get("server", defaults["ntfy"]["server"]),
                200,
            ).strip() or defaults["ntfy"]["server"],
            "topic": _coerce_text(ntfy.get("topic", ""), 180).strip().strip("/"),
            "token": _coerce_text(ntfy.get("token", ""), 240).strip(),
            "priority": priority,
            "tags": _coerce_text(ntfy.get("tags", ""), 120).strip(),
            "timeout_seconds": timeout_seconds,
        },
        "telegram": {
            "api_base": _coerce_text(
                telegram.get("api_base", defaults["telegram"]["api_base"]),
                200,
            ).strip() or defaults["telegram"]["api_base"],
            "bot_token": _coerce_text(telegram.get("bot_token", ""), 240).strip(),
            "chat_id": _coerce_text(telegram.get("chat_id", ""), 80).strip(),
            "timeout_seconds": telegram_timeout_seconds,
            "poll_timeout_seconds": telegram_poll_timeout_seconds,
            "last_update_id": telegram_last_update_id,
            "pending_power_confirmation": {
                "action": pending_action,
                "delay_seconds": pending_delay,
                "token": _coerce_text(raw_pending_power.get("token", ""), 40).strip(),
                "chat_id": _coerce_text(raw_pending_power.get("chat_id", ""), 80).strip(),
                "requested_at": _coerce_text(raw_pending_power.get("requested_at", ""), 80).strip(),
            },
        },
    }


def _normalize_metadata(value, limit: int = MAX_SOCIAL_METADATA_CHARS) -> dict:
    if not isinstance(value, dict):
        return {}

    try:
        rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        return {}

    if len(rendered) > limit:
        return {"summary": rendered[:limit]}

    return value


def _normalize_social_settings(settings):
    defaults = default_state()["social"]["settings"]
    if not isinstance(settings, dict):
        settings = {}

    meta_graph_version = _coerce_text(
        settings.get("meta_graph_version", defaults["meta_graph_version"]),
        20,
    ).strip()
    if not re.fullmatch(r"v\d+\.\d+", meta_graph_version):
        meta_graph_version = defaults["meta_graph_version"]

    linkedin_version = _coerce_text(
        settings.get("linkedin_version", defaults["linkedin_version"]),
        20,
    ).strip()
    if not re.fullmatch(r"\d{6}", linkedin_version):
        linkedin_version = defaults["linkedin_version"]

    return {
        "meta_graph_version": meta_graph_version,
        "linkedin_version": linkedin_version,
        "require_confirmation": True,
    }


def _normalize_social_platform(value: str, default: str = "facebook_page") -> str:
    platform = str(value).strip().lower()
    return platform if platform in VALID_SOCIAL_PLATFORMS else default


def _normalize_social_account(account):
    if not isinstance(account, dict):
        return None

    account_id = _coerce_text(
        account.get("id") or _fallback_id("social-account", account.get("display_name", "")),
        64,
    ).strip()
    account_type = str(account.get("account_type", "facebook_page")).strip().lower()
    if account_type not in VALID_SOCIAL_ACCOUNT_TYPES:
        return None

    default_platform_by_account_type = {
        "facebook_page": "facebook_page",
        "facebook_personal": "facebook_personal",
        "instagram_professional": "instagram",
        "linkedin_member": "linkedin",
        "linkedin_organization": "linkedin",
    }
    platform = _normalize_social_platform(
        account.get("platform", default_platform_by_account_type.get(account_type, "facebook_page"))
    )
    display_name = _coerce_text(account.get("display_name", ""), 160).strip()
    external_id = _coerce_text(account.get("external_id", ""), 160).strip()
    token_ref = _coerce_text(account.get("token_ref", ""), 160).strip()

    if not account_id or not display_name:
        return None

    return {
        "id": account_id,
        "platform": platform,
        "account_type": account_type,
        "display_name": display_name,
        "external_id": external_id,
        "token_ref": token_ref,
        "scopes": _normalize_string_list(account.get("scopes", []), 40, 80),
        "connected_at": _coerce_text(account.get("connected_at", ""), 80).strip(),
        "expires_at": _coerce_text(account.get("expires_at", ""), 80).strip(),
        "metadata": _normalize_metadata(account.get("metadata", {})),
    }


def _normalize_social_draft(draft):
    if not isinstance(draft, dict):
        return None

    title = _coerce_text(draft.get("title", ""), 180).strip()
    body = _coerce_text(draft.get("body", ""), MAX_SOCIAL_TEXT_CHARS).strip()
    if not title and not body:
        return None

    status = str(draft.get("status", "draft")).strip().lower()
    if status not in VALID_SOCIAL_DRAFT_STATUS:
        status = "draft"

    return {
        "id": _coerce_text(draft.get("id") or _fallback_id("draft", title or body), 64).strip(),
        "title": title or "Draft social",
        "platform": _normalize_social_platform(draft.get("platform", "facebook_page")),
        "target_account_id": _coerce_text(draft.get("target_account_id", ""), 64).strip(),
        "body": body,
        "link_url": _coerce_text(draft.get("link_url", ""), 500).strip(),
        "media_url": _coerce_text(draft.get("media_url", ""), 500).strip(),
        "media_path": _coerce_text(draft.get("media_path", ""), 500).strip(),
        "media_type": _coerce_text(draft.get("media_type", ""), 40).strip().lower(),
        "alt_text": _coerce_text(draft.get("alt_text", ""), 500).strip(),
        "hashtags": _normalize_string_list(draft.get("hashtags", []), 40, 80),
        "status": status,
        "created_at": _coerce_text(draft.get("created_at", ""), 80).strip(),
        "scheduled_for": _coerce_text(draft.get("scheduled_for", ""), 80).strip(),
        "metadata": _normalize_metadata(draft.get("metadata", {})),
    }


def _normalize_social_publication(publication):
    if not isinstance(publication, dict):
        return None

    title = _coerce_text(publication.get("title", ""), 180).strip()
    body = _coerce_text(publication.get("body", ""), MAX_SOCIAL_TEXT_CHARS).strip()
    platform = _normalize_social_platform(publication.get("platform", "facebook_page"))
    publication_id = _coerce_text(
        publication.get("id") or _fallback_id("pub", title or body or platform),
        64,
    ).strip()
    if not publication_id or (not body and not publication.get("link_url") and not publication.get("media_url")):
        return None

    status = str(publication.get("status", "pending_confirmation")).strip().lower()
    if status not in VALID_SOCIAL_PUBLICATION_STATUS:
        status = "pending_confirmation"

    return {
        "id": publication_id,
        "draft_id": _coerce_text(publication.get("draft_id", ""), 64).strip(),
        "title": title or "Publicacion social",
        "platform": platform,
        "target_account_id": _coerce_text(publication.get("target_account_id", ""), 64).strip(),
        "target_label": _coerce_text(publication.get("target_label", ""), 180).strip(),
        "body": body,
        "link_url": _coerce_text(publication.get("link_url", ""), 500).strip(),
        "media_url": _coerce_text(publication.get("media_url", ""), 500).strip(),
        "media_path": _coerce_text(publication.get("media_path", ""), 500).strip(),
        "media_type": _coerce_text(publication.get("media_type", ""), 40).strip().lower(),
        "alt_text": _coerce_text(publication.get("alt_text", ""), 500).strip(),
        "hashtags": _normalize_string_list(publication.get("hashtags", []), 40, 80),
        "status": status,
        "confirmation_phrase": _coerce_text(
            publication.get("confirmation_phrase", f"PUBLICAR {publication_id}"),
            100,
        ).strip() or f"PUBLICAR {publication_id}",
        "created_at": _coerce_text(publication.get("created_at", ""), 80).strip(),
        "scheduled_for": _coerce_text(publication.get("scheduled_for", ""), 80).strip(),
        "published_at": _coerce_text(publication.get("published_at", ""), 80).strip(),
        "external_post_id": _coerce_text(publication.get("external_post_id", ""), 200).strip(),
        "last_error": _coerce_text(publication.get("last_error", ""), 600).strip(),
        "metadata": _normalize_metadata(publication.get("metadata", {})),
    }


def _normalize_social(social):
    defaults = default_state()["social"]
    if not isinstance(social, dict):
        social = {}

    accounts = []
    for account in social.get("accounts", []):
        normalized_account = _normalize_social_account(account)
        if normalized_account:
            accounts.append(normalized_account)
            if len(accounts) >= MAX_SOCIAL_ITEMS:
                break

    drafts = []
    for draft in social.get("drafts", []):
        normalized_draft = _normalize_social_draft(draft)
        if normalized_draft:
            drafts.append(normalized_draft)
            if len(drafts) >= MAX_SOCIAL_ITEMS:
                break

    pending_publications = []
    for publication in social.get("pending_publications", []):
        normalized_publication = _normalize_social_publication(publication)
        if normalized_publication:
            pending_publications.append(normalized_publication)
            if len(pending_publications) >= MAX_SOCIAL_ITEMS:
                break

    history = []
    for publication in social.get("history", []):
        normalized_publication = _normalize_social_publication(publication)
        if normalized_publication:
            history.append(normalized_publication)
            if len(history) >= MAX_SOCIAL_ITEMS:
                break

    return {
        "settings": _normalize_social_settings(social.get("settings", defaults["settings"])),
        "accounts": accounts,
        "drafts": drafts,
        "pending_publications": pending_publications,
        "history": history,
    }


def normalize_state(state):
    normalized = default_state()

    if not isinstance(state, dict):
        return normalized

    goal = str(state.get("goal", normalized["goal"])).strip()
    if goal in LEGACY_DEFAULT_GOALS:
        goal = DEFAULT_GOAL
    normalized["goal"] = goal

    try:
        normalized["cycle_count"] = max(0, int(state.get("cycle_count", 0)))
    except (TypeError, ValueError):
        normalized["cycle_count"] = 0

    normalized["last_result"] = str(state.get("last_result", ""))
    normalized["profile"] = _normalize_profile(state.get("profile", {}))
    normalized["current_plan"] = _normalize_plan(state.get("current_plan", []))
    normalized["awaiting_user_input"] = _normalize_awaiting_user_input(
        state.get("awaiting_user_input", {}),
    )
    normalized["autonomy"] = _normalize_autonomy(state.get("autonomy", {}))
    normalized["ollama"] = _normalize_ollama(state.get("ollama", {}))
    normalized["service"] = _normalize_service(state.get("service", {}))
    normalized["ui"] = _normalize_ui(state.get("ui", {}))
    normalized["runtime"] = _normalize_runtime(state.get("runtime", {}))
    normalized["local_context"] = _normalize_local_context(state.get("local_context", {}))
    normalized["internet"] = _normalize_internet(state.get("internet", {}))
    normalized["self_knowledge"] = _normalize_self_knowledge(state.get("self_knowledge", {}))
    normalized["notifications"] = _normalize_notifications(state.get("notifications", {}))
    normalized["social"] = _normalize_social(state.get("social", {}))

    raw_messages = state.get("messages", [])
    if isinstance(raw_messages, list):
        for message in raw_messages:
            normalized_message = _normalize_message(message)
            if normalized_message:
                normalized["messages"].append(normalized_message)

    raw_notes = state.get("notes", [])
    if isinstance(raw_notes, list):
        for note in raw_notes:
            normalized_note = _normalize_note(note)
            if normalized_note:
                normalized["notes"].append(normalized_note)

    raw_tasks = state.get("tasks", [])
    if isinstance(raw_tasks, list):
        for task in raw_tasks:
            normalized_task = _normalize_task(task)
            if normalized_task:
                normalized["tasks"].append(normalized_task)

    return normalized


def render_state_summary(
    state,
    task_limit: int = 8,
    note_limit: int = 3,
    include_runtime: bool = True,
    include_last_result: bool = True,
) -> str:
    normalized = normalize_state(state)
    profile = normalized["profile"]
    pending_tasks = [
        task for task in normalized["tasks"]
        if task["status"] in {"pending", "in_progress", "blocked"}
    ]
    done_tasks = [task for task in normalized["tasks"] if task["status"] == "done"]

    lines = [
        f"Objetivo: {normalized['goal']}",
        (
            "Identidad: "
            "asistente=Yarbis; "
            f"usuario={profile['name'] or 'sin nombre definido'}"
        ),
        f"Ciclos ejecutados: {normalized['cycle_count']}",
        (
            "Autonomia: "
            f"{normalized['autonomy']['max_steps_per_cycle']} pasos/ciclo, "
            f"{normalized['autonomy']['auto_cycles_default']} ciclos por defecto"
        ),
        (
            "Ollama: "
            f"modelo={normalized['ollama']['model']}, "
            f"fallbacks={', '.join(normalized['ollama']['fallback_models']) or '-'}, "
            f"host={normalized['ollama']['host'] or 'local'}, "
            f"timeout={normalized['ollama']['timeout_seconds']}s"
        ),
        (
            "Pulso proactivo: "
            f"{'activo' if normalized['service']['proactive']['enabled'] else 'desactivado'}, "
            f"modelo={normalized['service']['proactive']['model'] or 'Ollama principal'}, "
            f"{normalized['service']['proactive']['cycles']} ciclo(s) cada "
            f"{normalized['service']['proactive']['interval_seconds']}s, "
            f"espera inicial {normalized['service']['proactive']['start_delay_seconds']}s"
        ),
    ]

    local_context = normalized["local_context"]
    local_context_status = "activo" if local_context["enabled"] else "desactivado"
    lines.append(
        "Contexto local: "
        f"{local_context_status}, modo={local_context['mode']}, "
        f"muestra cada {local_context['sample_interval_seconds']}s, "
        f"vigencia {local_context['max_snapshot_age_seconds']}s, "
        f"proceso={'si' if local_context['include_process_name'] else 'no'}, "
        f"titulos={'si' if local_context['include_window_title'] else 'no'}, "
        f"workspace={'si' if local_context['include_workspace_changes'] else 'no'}, "
        f"sistema={'si' if local_context['include_system_health'] else 'no'}"
    )

    if include_last_result:
        lines.append(
            f"Ultimo resultado: {normalized['last_result'] or 'Sin resultados previos.'}"
        )

    if include_runtime:
        thinking = normalized["runtime"]["thinking"]
        stop_requested = normalized["runtime"]["stop_requested"]
        if thinking["active"]:
            started_at = f" desde {thinking['started_at']}" if thinking["started_at"] else ""
            lines.append(
                f"Estado operativo: estoy pensando en {thinking['label']}{started_at}."
            )
        else:
            lines.append("Estado operativo: listo.")
        if stop_requested["active"]:
            requested_at = f" en {stop_requested['requested_at']}" if stop_requested["requested_at"] else ""
            lines.append(
                "Parada solicitada: activa"
                f"{requested_at}"
                + (f" por {stop_requested['source']}" if stop_requested["source"] else "")
                + "."
            )

    lines.append(
        f"Perfil: nombre={profile['name'] or '-'}, rol={profile['role'] or '-'}"
    )
    lines.append(
        "Preferencias: "
        + (", ".join(profile["preferences"]) if profile["preferences"] else "Sin definir.")
    )
    lines.append(
        "Restricciones: "
        + (", ".join(profile["constraints"]) if profile["constraints"] else "Sin definir.")
    )

    if normalized["current_plan"]:
        lines.append("Plan actual:")
        for index, item in enumerate(normalized["current_plan"], start=1):
            lines.append(f"{index}. {item}")
    else:
        lines.append("Plan actual: sin plan explicito.")

    awaiting_user_input = normalized["awaiting_user_input"]
    if awaiting_user_input["pending"]:
        lines.append(
            "Esperando respuesta del usuario: "
            + awaiting_user_input["question"]
        )
        if awaiting_user_input["reason"]:
            lines.append("Motivo de la pausa: " + awaiting_user_input["reason"])
        if awaiting_user_input["fields"]:
            lines.append(
                "Datos faltantes: " + ", ".join(awaiting_user_input["fields"])
            )
    else:
        lines.append("Esperando respuesta del usuario: no.")

    internet_settings = normalized["internet"]
    lines.append(
        "Internet: "
        f"modo={internet_settings['mode']}, "
        f"proveedor={internet_settings['provider']}, "
        f"max_resultados={internet_settings['max_search_results']}, "
        f"max_pagina={internet_settings['max_page_chars']} chars, "
        f"timeout={internet_settings['request_timeout_seconds']}s"
    )
    if internet_settings["allowed_domains"]:
        lines.append(
            "Internet permitido solo para: "
            + ", ".join(internet_settings["allowed_domains"])
        )
    if internet_settings["blocked_domains"]:
        lines.append(
            "Internet bloqueado para: "
            + ", ".join(internet_settings["blocked_domains"])
        )

    social = normalized["social"]
    pending_social = [
        publication
        for publication in social["pending_publications"]
        if publication["status"] == "pending_confirmation"
    ]
    lines.append(
        "Redes sociales: "
        f"{len(social['accounts'])} cuenta(s), "
        f"{len(social['drafts'])} draft(s), "
        f"{len(pending_social)} pendiente(s) de confirmacion, "
        f"Meta={social['settings']['meta_graph_version']}, "
        f"LinkedIn={social['settings']['linkedin_version']}"
    )
    if pending_social:
        for publication in pending_social[:3]:
            lines.append(
                f"- Pendiente social [{publication['id']}]: "
                f"{publication['platform']} -> {publication['target_label'] or publication['target_account_id'] or 'sin destino'}; "
                f"confirmar con {publication['confirmation_phrase']}"
            )

    self_knowledge = normalized["self_knowledge"]
    if self_knowledge["last_analyzed_at"]:
        lines.append(
            "Autoconocimiento: "
            f"actualizado en {self_knowledge['last_analyzed_at']}."
        )
    else:
        lines.append("Autoconocimiento: pendiente de autoanalisis inicial.")

    notification_settings = normalized["notifications"]
    notification_status = "activadas" if notification_settings["enabled"] else "desactivadas"
    lines.append(
        "Notificaciones: "
        f"{notification_status}, canales={', '.join(notification_settings['channels'])}"
    )
    if "telegram" in notification_settings["channels"]:
        telegram = notification_settings.get("telegram", {})
        if telegram.get("chat_id"):
            lines.append(f"Telegram: vinculado al chat {telegram['chat_id']}.")
        elif telegram.get("bot_token"):
            lines.append("Telegram: pendiente de vincular. Envia /start al bot para completar el enlace.")
        else:
            lines.append("Telegram: activado, pero falta configurar el bot token.")

    if pending_tasks:
        lines.append("Tareas abiertas:")
        for task in pending_tasks[:task_limit]:
            lines.append(
                f"- [{task['id']}] {task['title']} "
                f"(estado={task['status']}, prioridad={task['priority']})"
            )
    else:
        lines.append("Tareas abiertas: ninguna.")

    if done_tasks:
        lines.append(f"Tareas completadas registradas: {len(done_tasks)}")

    recent_notes = normalized["notes"][-note_limit:]
    if recent_notes:
        lines.append("Notas recientes:")
        for note in recent_notes:
            preview = note["content"][:140] if note["content"] else ""
            if note["content"] and len(note["content"]) > 140:
                preview += "..."
            lines.append(f"- [{note['id']}] {note['title']} ({note['category']}): {preview}")
    else:
        lines.append("Notas recientes: ninguna.")

    return "\n".join(lines)


def _load_state_unlocked():
    if not STATE_FILE.exists():
        return default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError):
        return default_state()

    return normalize_state(state)


def _save_state_unlocked(state):
    normalized = normalize_state(state)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = STATE_FILE.with_name(f"{STATE_FILE.name}.tmp")

    with open(tmp_file, "w", encoding="utf-8") as file:
        json.dump(normalized, file, ensure_ascii=False, indent=2)

    try:
        tmp_file.replace(STATE_FILE)
    except PermissionError:
        with open(STATE_FILE, "w", encoding="utf-8") as file:
            json.dump(normalized, file, ensure_ascii=False, indent=2)
        try:
            tmp_file.unlink()
        except OSError:
            pass


def load_state():
    with STATE_LOCK:
        with _state_file_lock("load_state"):
            return _load_state_unlocked()


def save_state(state):
    with STATE_LOCK:
        with _state_file_lock("save_state"):
            _save_state_unlocked(state)


def state_transaction(label: str, mutator):
    if not callable(mutator):
        raise TypeError("state_transaction requiere un mutator callable.")

    with STATE_LOCK:
        with _state_file_lock(label):
            state = _load_state_unlocked()
            result = mutator(state)
            _save_state_unlocked(state)
            return result
