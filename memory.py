import json
import re
import threading
from pathlib import Path

STATE_FILE = Path("state.json")
STATE_LOCK = threading.RLock()
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
DEFAULT_OLLAMA_MODEL = "qwen3.5:2b"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 900
MIN_OLLAMA_TIMEOUT_SECONDS = 1
MAX_OLLAMA_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_OLLAMA_MODEL_CHARS = 120
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
            "timeout_seconds": DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        },
        "service": {
            "proactive": {
                "enabled": DEFAULT_SERVICE_PROACTIVE_ENABLED,
                "interval_seconds": DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS,
                "cycles": DEFAULT_SERVICE_PROACTIVE_CYCLES,
                "start_delay_seconds": DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS,
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
            },
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
            },
        },
    }


def _truncate_text(value, limit: int) -> str:
    return str(value)


def _normalize_message(message):
    if not isinstance(message, dict):
        return None

    role = str(message.get("role", "assistant"))
    normalized = {"role": role}

    content = message.get("content", "")
    normalized["content"] = _truncate_text(content, MAX_MESSAGE_CHARS)

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

        normalized_item = _truncate_text(item, char_limit)
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

    title = _truncate_text(note.get("title", ""), MAX_NOTE_TITLE_CHARS).strip()
    content = _truncate_text(note.get("content", ""), MAX_NOTE_CONTENT_CHARS).strip()
    category = _truncate_text(note.get("category", "general"), 40).strip() or "general"

    if not title and not content:
        return None

    note_id = _truncate_text(note.get("id") or _fallback_id("note", title or content), 32).strip()

    return {
        "id": note_id,
        "title": title or "Nota sin titulo",
        "content": content,
        "category": category,
    }


def _normalize_task(task):
    if not isinstance(task, dict):
        return None

    title = _truncate_text(task.get("title", ""), MAX_TASK_TITLE_CHARS).strip()
    details = _truncate_text(task.get("details", ""), MAX_TASK_DETAILS_CHARS).strip()
    result = _truncate_text(task.get("result", ""), MAX_TASK_RESULT_CHARS).strip()

    if not title:
        return None

    raw_status = str(task.get("status", "pending")).strip().lower()
    raw_priority = str(task.get("priority", "media")).strip().lower()

    status = raw_status if raw_status in VALID_TASK_STATUS else "pending"
    priority = raw_priority if raw_priority in VALID_TASK_PRIORITY else "media"
    task_id = _truncate_text(task.get("id") or _fallback_id("task", title), 32).strip()

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
        "name": _truncate_text(profile.get("name", ""), 80).strip(),
        "role": _truncate_text(profile.get("role", ""), 120).strip(),
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

    model = _truncate_text(
        ollama.get("model", defaults["model"]),
        MAX_OLLAMA_MODEL_CHARS,
    ).strip()
    if not model:
        model = defaults["model"]

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
        },
    }


def _normalize_awaiting_user_input(awaiting_user_input):
    if not isinstance(awaiting_user_input, dict):
        awaiting_user_input = {}

    question = _truncate_text(
        awaiting_user_input.get("question", ""),
        MAX_AWAITING_INPUT_QUESTION_CHARS,
    ).strip()
    reason = _truncate_text(
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

    thinking = runtime.get("thinking", {})
    if not isinstance(thinking, dict):
        thinking = {}

    label = _truncate_text(
        thinking.get("label", ""),
        MAX_RUNTIME_OPERATION_LABEL_CHARS,
    ).strip()
    source = _truncate_text(
        thinking.get("source", ""),
        MAX_RUNTIME_OPERATION_SOURCE_CHARS,
    ).strip()
    started_at = _truncate_text(
        thinking.get("started_at", ""),
        MAX_RUNTIME_TIMESTAMP_CHARS,
    ).strip()
    active = bool(thinking.get("active")) and bool(label)

    if not active:
        return defaults

    return {
        "thinking": {
            "active": True,
            "label": label,
            "source": source,
            "started_at": started_at,
        },
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
        "last_analyzed_at": _truncate_text(
            self_knowledge.get("last_analyzed_at", ""),
            MAX_SELF_KNOWLEDGE_TIMESTAMP_CHARS,
        ).strip(),
        "summary": _truncate_text(
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

    priority = _truncate_text(ntfy.get("priority", defaults["ntfy"]["priority"]), 20).strip().lower()
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

    return {
        "enabled": bool(notifications.get("enabled", defaults["enabled"])),
        "channels": _normalize_notification_channels(
            notifications.get("channels", defaults["channels"]),
        ),
        "ntfy": {
            "server": _truncate_text(
                ntfy.get("server", defaults["ntfy"]["server"]),
                200,
            ).strip() or defaults["ntfy"]["server"],
            "topic": _truncate_text(ntfy.get("topic", ""), 180).strip().strip("/"),
            "token": _truncate_text(ntfy.get("token", ""), 240).strip(),
            "priority": priority,
            "tags": _truncate_text(ntfy.get("tags", ""), 120).strip(),
            "timeout_seconds": timeout_seconds,
        },
        "telegram": {
            "api_base": _truncate_text(
                telegram.get("api_base", defaults["telegram"]["api_base"]),
                200,
            ).strip() or defaults["telegram"]["api_base"],
            "bot_token": _truncate_text(telegram.get("bot_token", ""), 240).strip(),
            "chat_id": _truncate_text(telegram.get("chat_id", ""), 80).strip(),
            "timeout_seconds": telegram_timeout_seconds,
            "poll_timeout_seconds": telegram_poll_timeout_seconds,
            "last_update_id": telegram_last_update_id,
        },
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
    normalized["internet"] = _normalize_internet(state.get("internet", {}))
    normalized["self_knowledge"] = _normalize_self_knowledge(state.get("self_knowledge", {}))
    normalized["notifications"] = _normalize_notifications(state.get("notifications", {}))

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
            f"timeout={normalized['ollama']['timeout_seconds']}s"
        ),
        (
            "Pulso proactivo: "
            f"{'activo' if normalized['service']['proactive']['enabled'] else 'desactivado'}, "
            f"{normalized['service']['proactive']['cycles']} ciclo(s) cada "
            f"{normalized['service']['proactive']['interval_seconds']}s, "
            f"espera inicial {normalized['service']['proactive']['start_delay_seconds']}s"
        ),
    ]

    if include_last_result:
        lines.append(
            f"Ultimo resultado: {normalized['last_result'] or 'Sin resultados previos.'}"
        )

    if include_runtime:
        thinking = normalized["runtime"]["thinking"]
        if thinking["active"]:
            started_at = f" desde {thinking['started_at']}" if thinking["started_at"] else ""
            lines.append(
                f"Estado operativo: estoy pensando en {thinking['label']}{started_at}."
            )
        else:
            lines.append("Estado operativo: listo.")

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


def load_state():
    with STATE_LOCK:
        if not STATE_FILE.exists():
            return default_state()

        try:
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                state = json.load(file)
        except (OSError, json.JSONDecodeError):
            return default_state()

        return normalize_state(state)


def save_state(state):
    normalized = normalize_state(state)

    with STATE_LOCK:
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
