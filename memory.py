import json
from pathlib import Path

STATE_FILE = Path("state.json")
DEFAULT_GOAL = "Ayudar al usuario de forma autonoma con tareas locales."
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 4_000
MAX_NOTES = 20
MAX_NOTE_CHARS = 400
MAX_LAST_RESULT_CHARS = 4_000


def default_state():
    return {
        "goal": DEFAULT_GOAL,
        "messages": [],
        "notes": [],
        "last_result": "",
        "cycle_count": 0,
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

    raw_notes = state.get("notes", [])
    if isinstance(raw_notes, list):
        normalized["notes"] = [
            _truncate_text(note, MAX_NOTE_CHARS)
            for note in raw_notes[-MAX_NOTES:]
        ]

    raw_messages = state.get("messages", [])
    if isinstance(raw_messages, list):
        for message in raw_messages[-MAX_MESSAGES:]:
            normalized_message = _normalize_message(message)
            if normalized_message:
                normalized["messages"].append(normalized_message)

    return normalized


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
