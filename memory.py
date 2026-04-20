import json
from pathlib import Path

STATE_FILE = Path("state.json")


def load_state():
    if not STATE_FILE.exists():
        return {
            "goal": "Ayudar al usuario de forma autónoma con tareas locales.",
            "messages": [],
            "notes": [],
            "last_result": "",
            "cycle_count": 0
        }

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)