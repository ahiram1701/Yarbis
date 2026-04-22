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
        self.assertEqual(state["notes"], [])
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["current_plan"], [])
        self.assertEqual(state["profile"]["preferences"], [])
        self.assertEqual(
            state["autonomy"]["max_steps_per_cycle"],
            memory.DEFAULT_MAX_STEPS_PER_CYCLE,
        )

    def test_save_state_trims_messages(self):
        state_path = TEST_RUNTIME_DIR / "memory_trim_state.json"
        oversized_state = {
            "goal": "demo",
            "messages": [
                {"role": "assistant", "content": "x" * (memory.MAX_MESSAGE_CHARS + 10)}
                for _ in range(memory.MAX_MESSAGES + 5)
            ],
            "last_result": "ok",
            "cycle_count": 3,
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(oversized_state)
            stored_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(len(stored_state["messages"]), memory.MAX_MESSAGES)
        self.assertIn("[truncado", stored_state["messages"][-1]["content"])

    def test_normalize_state_sanitizes_profile_notes_and_tasks(self):
        normalized = memory.normalize_state({
            "profile": {
                "name": "A" * 200,
                "preferences": "rapido, rapido, local",
                "constraints": ["sin nube", "", "sin destruir archivos"],
            },
            "notes": [
                {"title": "", "content": ""},
                {"title": "Contexto", "content": "x" * (memory.MAX_NOTE_CONTENT_CHARS + 50)},
            ],
            "tasks": [
                {"title": "Preparar backlog", "status": "desconocido", "priority": "urgente"},
            ],
            "current_plan": "Definir objetivo\nCrear tareas\nEjecutar",
        })

        self.assertEqual(normalized["profile"]["preferences"], ["rapido", "local"])
        self.assertEqual(normalized["profile"]["constraints"], ["sin nube", "sin destruir archivos"])
        self.assertEqual(len(normalized["notes"]), 1)
        self.assertIn("[truncado", normalized["notes"][0]["content"])
        self.assertEqual(normalized["tasks"][0]["status"], "pending")
        self.assertEqual(normalized["tasks"][0]["priority"], "media")
        self.assertEqual(len(normalized["current_plan"]), 3)

    def test_normalize_state_keeps_pending_user_question_only_when_valid(self):
        normalized = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "x" * (memory.MAX_AWAITING_INPUT_QUESTION_CHARS + 20),
                "reason": "Falta definir el nicho principal",
                "fields": "nicho, audiencia, audiencia",
            }
        })

        pending = normalized["awaiting_user_input"]

        self.assertTrue(pending["pending"])
        self.assertIn("x", pending["question"])
        self.assertLessEqual(
            len(pending["question"]),
            memory.MAX_AWAITING_INPUT_QUESTION_CHARS + len("\n\n...[truncado 99 caracteres]"),
        )
        self.assertEqual(pending["fields"], ["nicho", "audiencia"])
