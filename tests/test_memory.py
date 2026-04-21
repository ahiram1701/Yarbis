import json
import unittest
from pathlib import Path
from unittest.mock import patch

import memory

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class MemoryTestCase(unittest.TestCase):
    def test_load_state_returns_defaults_for_invalid_json(self):
        state_path = TEST_RUNTIME_DIR / "memory_invalid_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", state_path):
            state = memory.load_state()

        self.assertEqual(state["goal"], memory.DEFAULT_GOAL)
        self.assertEqual(state["messages"], [])

    def test_save_state_trims_messages(self):
        state_path = TEST_RUNTIME_DIR / "memory_trim_state.json"
        oversized_state = {
            "goal": "demo",
            "messages": [
                {"role": "assistant", "content": "x" * (memory.MAX_MESSAGE_CHARS + 10)}
                for _ in range(memory.MAX_MESSAGES + 5)
            ],
            "notes": [],
            "last_result": "ok",
            "cycle_count": 3,
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(oversized_state)
            stored_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(len(stored_state["messages"]), memory.MAX_MESSAGES)
        self.assertIn("[truncado", stored_state["messages"][-1]["content"])
