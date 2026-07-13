import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import memory
import session
import tools
import yarbis_bus
import yarbis_instance

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class ListPendingUserQuestionsTestCase(unittest.TestCase):
    def _write_state(self, path: Path, *, pending: bool, question: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        state = memory.default_state()
        if pending:
            state["awaiting_user_input"] = {
                "pending": True,
                "question": question,
                "reason": "",
                "fields": [],
            }
        path.write_text(json.dumps(state), encoding="utf-8")

    def test_lists_only_instances_waiting_for_user(self):
        base = TEST_RUNTIME_DIR / f"pending-{uuid4().hex[:8]}"
        beta_state = base / "beta" / "state.json"
        gamma_state = base / "gamma" / "state.json"
        self._write_state(beta_state, pending=True, question="¿publico el post de las 3pm?")
        self._write_state(gamma_state, pending=False)

        fake_instances = [
            {"id": "alpha", "state_file": str(base / "alpha" / "state.json")},
            {"id": "beta", "state_file": str(beta_state)},
            {"id": "gamma", "state_file": str(gamma_state)},
        ]
        with patch.object(yarbis_instance, "list_instances", return_value=fake_instances), \
             patch.object(yarbis_instance, "current_instance_id", return_value="alpha"), \
             patch.object(yarbis_bus, "instance_is_active", return_value=True):
            out = tools.list_pending_user_questions()

        self.assertIn("beta", out)
        self.assertIn("¿publico el post de las 3pm?", out)
        self.assertNotIn("gamma", out)  # sin pregunta pendiente
        self.assertNotIn("alpha", out)  # es la instancia actual

    def test_reports_none_when_nobody_waiting(self):
        base = TEST_RUNTIME_DIR / f"pending-none-{uuid4().hex[:8]}"
        self._write_state(base / "beta" / "state.json", pending=False)
        fake_instances = [{"id": "beta", "state_file": str(base / "beta" / "state.json")}]
        with patch.object(yarbis_instance, "list_instances", return_value=fake_instances), \
             patch.object(yarbis_instance, "current_instance_id", return_value="alpha"), \
             patch.object(yarbis_bus, "instance_is_active", return_value=False):
            out = tools.list_pending_user_questions()
        self.assertIn("Ninguna", out)


class SubmitUserReplyAttributionTestCase(unittest.TestCase):
    def _state_path(self) -> Path:
        path = TEST_RUNTIME_DIR / f"answer-{uuid4().hex[:8]}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _seed_pending(self, state_path: Path, question: str = "¿publico el post?") -> None:
        state = memory.default_state()
        state["awaiting_user_input"] = {"pending": True, "question": question, "reason": "", "fields": []}
        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(state)

    def test_attribution_prepended_and_pending_cleared(self):
        state_path = self._state_path()
        self._seed_pending(state_path)
        with patch.object(memory, "STATE_FILE", state_path), \
             patch.object(session, "run_auto_with_output", return_value="(ciclos)"), \
             patch.object(session, "run_cycle_with_output", return_value="(ciclo)"):
            result = session.submit_user_reply(
                "si, publicalo",
                emit_notifications=False,
                attribution="instancia asistente",
                intercept_commands=False,
            )
            reloaded = memory.load_state()

        self.assertIn("(ciclos)", result)
        self.assertFalse(reloaded["awaiting_user_input"]["pending"])
        last_user = [m for m in reloaded["messages"] if m["role"] == "user"][-1]
        self.assertIn("[Respondido en tu nombre por la instancia asistente]", last_user["content"])
        self.assertIn("si, publicalo", last_user["content"])

    def test_intercept_commands_false_skips_note_shortcut(self):
        state_path = self._state_path()
        self._seed_pending(state_path)
        with patch.object(memory, "STATE_FILE", state_path), \
             patch.object(session, "handle_note_text_request") as note_spy, \
             patch.object(session, "is_self_analysis_request") as self_spy, \
             patch.object(session, "run_auto_with_output", return_value="ok"), \
             patch.object(session, "run_cycle_with_output", return_value="ok"):
            session.submit_user_reply(
                "guarda una nota: recordar algo",
                emit_notifications=False,
                attribution="instancia dev",
                intercept_commands=False,
            )
        note_spy.assert_not_called()
        self_spy.assert_not_called()


class UserAnswerBusRoutingTestCase(unittest.TestCase):
    def _instances_root(self) -> Path:
        root = TEST_RUNTIME_DIR / f"answerbus-{uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_user_answer_message_routes_to_submit_user_reply(self):
        root = self._instances_root()
        captured = {}

        def fake_submit(content, **kwargs):
            captured["content"] = content
            captured.update(kwargs)
            return "retomado"

        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                sent = yarbis_bus.send_user_answer("beta", "si, publicalo", wait_for_reply=False)
            self.assertEqual(sent["kind"], yarbis_bus.KIND_USER_ANSWER)

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "beta"}), \
                 patch.object(session, "submit_user_reply", side_effect=fake_submit):
                processed = yarbis_bus.process_pending_messages()

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                updated = yarbis_bus.wait_for_response(sent["id"], timeout_seconds=0)

        self.assertEqual(processed, 1)
        self.assertEqual(captured["content"], "si, publicalo")
        self.assertEqual(captured["attribution"], "instancia alpha")
        self.assertFalse(captured["intercept_commands"])
        self.assertEqual(updated["status"], yarbis_bus.STATUS_DONE)
        self.assertEqual(updated["response"], "retomado")


if __name__ == "__main__":
    unittest.main()
