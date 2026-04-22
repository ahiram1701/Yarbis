import json
import re
from pathlib import Path

STATE_FILE = Path("state.json")
DEFAULT_GOAL = "Ayudar al usuario de forma autonoma con tareas locales."
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
VALID_TASK_STATUS = {"pending", "in_progress", "blocked", "done"}
VALID_TASK_PRIORITY = {"alta", "media", "baja"}
VALID_UI_THEME = {"light", "dark"}


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
        "ui": {
            "theme": "dark",
        },
    }


def _truncate_text(value, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text

    omitted = len(text) - limit
    return f"{text[:limit]}\n\n...[truncado {omitted} caracteres]"


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

        truncated = _truncate_text(item, char_limit)
        lowered = truncated.casefold()
        if lowered in seen:
            continue

        normalized.append(truncated)
        seen.add(lowered)

        if len(normalized) >= item_limit:
            break

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


def normalize_state(state):
    normalized = default_state()

    if not isinstance(state, dict):
        return normalized

    goal = state.get("goal", normalized["goal"])
    normalized["goal"] = str(goal).strip() or DEFAULT_GOAL

    try:
        normalized["cycle_count"] = max(0, int(state.get("cycle_count", 0)))
    except (TypeError, ValueError):
        normalized["cycle_count"] = 0

    normalized["last_result"] = _truncate_text(state.get("last_result", ""), MAX_LAST_RESULT_CHARS)
    normalized["profile"] = _normalize_profile(state.get("profile", {}))
    normalized["current_plan"] = _normalize_plan(state.get("current_plan", []))
    normalized["awaiting_user_input"] = _normalize_awaiting_user_input(
        state.get("awaiting_user_input", {}),
    )
    normalized["autonomy"] = _normalize_autonomy(state.get("autonomy", {}))
    normalized["ui"] = _normalize_ui(state.get("ui", {}))

    raw_messages = state.get("messages", [])
    if isinstance(raw_messages, list):
        for message in raw_messages[-MAX_MESSAGES:]:
            normalized_message = _normalize_message(message)
            if normalized_message:
                normalized["messages"].append(normalized_message)

    raw_notes = state.get("notes", [])
    if isinstance(raw_notes, list):
        for note in raw_notes[-MAX_NOTES:]:
            normalized_note = _normalize_note(note)
            if normalized_note:
                normalized["notes"].append(normalized_note)

    raw_tasks = state.get("tasks", [])
    if isinstance(raw_tasks, list):
        for task in raw_tasks[-MAX_TASKS:]:
            normalized_task = _normalize_task(task)
            if normalized_task:
                normalized["tasks"].append(normalized_task)

    return normalized


def render_state_summary(state, task_limit: int = 8, note_limit: int = 3) -> str:
    normalized = normalize_state(state)
    profile = normalized["profile"]
    pending_tasks = [
        task for task in normalized["tasks"]
        if task["status"] in {"pending", "in_progress", "blocked"}
    ]
    done_tasks = [task for task in normalized["tasks"] if task["status"] == "done"]

    lines = [
        f"Objetivo: {normalized['goal']}",
        f"Ciclos ejecutados: {normalized['cycle_count']}",
        (
            "Autonomia: "
            f"{normalized['autonomy']['max_steps_per_cycle']} pasos/ciclo, "
            f"{normalized['autonomy']['auto_cycles_default']} ciclos por defecto"
        ),
        f"Ultimo resultado: {normalized['last_result'] or 'Sin resultados previos.'}",
    ]

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
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(normalized, file, ensure_ascii=False, indent=2)
