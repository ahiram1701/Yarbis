import unittest
from pathlib import Path
from unittest.mock import patch

import memory
import session

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class SessionTestCase(unittest.TestCase):
    def test_update_goal_resets_operational_context(self):
        state_path = TEST_RUNTIME_DIR / "session_goal_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "goal": "Objetivo viejo",
            "messages": [{"role": "assistant", "content": "avance previo"}],
            "tasks": [{"id": "task-1", "title": "Vieja tarea", "status": "pending"}],
            "current_plan": ["Paso 1"],
            "last_result": "resultado anterior",
            "awaiting_user_input": {
                "pending": True,
                "question": "Que prioridad tiene esto?",
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            result = session.update_goal("Nuevo objetivo claro")
            state = memory.load_state()

        self.assertIn("Objetivo actualizado", result)
        self.assertEqual(state["goal"], "Nuevo objetivo claro")
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["current_plan"], [])
        self.assertEqual(state["last_result"], "")
        self.assertFalse(state["awaiting_user_input"]["pending"])
        self.assertEqual(len(state["messages"]), 1)
        self.assertIn("Nuevo objetivo claro", state["messages"][0]["content"])

    def test_submit_user_reply_clears_pending_question_and_resumes_autonomy(self):
        state_path = TEST_RUNTIME_DIR / "session_reply_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que nicho quieres trabajar?",
                "reason": "Falta contexto",
            },
            "autonomy": {
                "auto_cycles_default": 4,
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "run_auto_with_output", return_value="Modo autonomo ejecutado por 4 ciclo(s).") as auto_mock:
                result = session.submit_user_reply("Trabajemos el nicho fitness")
                state = memory.load_state()

        self.assertIn("Respuesta guardada", result)
        self.assertIn("Retomando el modo autonomo", result)
        self.assertIn("Modo autonomo ejecutado por 4 ciclo(s).", result)
        self.assertEqual(auto_mock.call_count, 1)
        self.assertEqual(auto_mock.call_args.kwargs["cycles"], 4)
        self.assertFalse(state["awaiting_user_input"]["pending"])
        self.assertEqual(state["messages"][-1]["role"], "user")
        self.assertEqual(state["messages"][-1]["content"], "Trabajemos el nicho fitness")

    def test_submit_user_reply_without_pending_question_runs_single_cycle(self):
        state_path = TEST_RUNTIME_DIR / "session_reply_freeform_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(session, "run_cycle_with_output", return_value="Ciclo ejecutado.") as cycle_mock:
                result = session.submit_user_reply("Te comparto mas contexto")
                state = memory.load_state()

        self.assertIn("Ejecutando un ciclo", result)
        self.assertIn("Ciclo ejecutado.", result)
        self.assertEqual(cycle_mock.call_count, 1)
        self.assertEqual(state["messages"][-1]["content"], "Te comparto mas contexto")

    def test_run_auto_with_output_adds_summary_even_without_stdout(self):
        with patch.object(session, "run_autonomous_session", return_value=3):
            result = session.run_auto_with_output(cycles=3)

        self.assertIn("Modo autonomo ejecutado por 3 ciclo(s).", result)

    def test_run_cycle_with_output_notifies_when_user_input_is_pending(self):
        state_path = TEST_RUNTIME_DIR / "session_notification_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que nicho quieres trabajar?",
                "reason": "Falta contexto",
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                session,
                "_capture_operation_output",
                return_value=("Ciclo ejecutado.", {"status": "waiting_for_user_input"}),
            ):
                with patch.object(session, "notify_user_input_required", return_value=True) as notify_mock:
                    result = session.run_cycle_with_output()

        self.assertEqual(result, "Ciclo ejecutado.")
        notify_mock.assert_called_once_with(
            "Que nicho quieres trabajar?",
            "Falta contexto",
        )

    def test_update_ui_theme_persists_theme(self):
        state_path = TEST_RUNTIME_DIR / "session_theme_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_ui_theme("light")
            state = memory.load_state()

        self.assertIn("Tema actualizado", result)
        self.assertEqual(state["ui"]["theme"], "light")

    def test_update_notification_settings_persists_ntfy_channel(self):
        state_path = TEST_RUNTIME_DIR / "session_notifications_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_notification_settings(
                enabled=True,
                windows_enabled=True,
                ntfy_enabled=True,
                ntfy_server="https://ntfy.sh",
                ntfy_topic="yarbis-secret",
                ntfy_token="",
                ntfy_priority="high",
                ntfy_tags="yarbis",
            )
            state = memory.load_state()

        self.assertIn("windows, ntfy", result)
        self.assertTrue(state["notifications"]["enabled"])
        self.assertEqual(state["notifications"]["channels"], ["windows", "ntfy"])
        self.assertEqual(state["notifications"]["ntfy"]["topic"], "yarbis-secret")
        self.assertEqual(state["notifications"]["ntfy"]["priority"], "high")

    def test_update_notification_settings_persists_telegram_channel(self):
        state_path = TEST_RUNTIME_DIR / "session_notifications_telegram_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_notification_settings(
                enabled=True,
                windows_enabled=False,
                ntfy_enabled=False,
                telegram_enabled=True,
                telegram_bot_token="bot-123",
                telegram_chat_id="",
            )
            state = memory.load_state()

        self.assertIn("telegram", result)
        self.assertTrue(state["notifications"]["enabled"])
        self.assertEqual(state["notifications"]["channels"], ["telegram"])
        self.assertEqual(state["notifications"]["telegram"]["bot_token"], "bot-123")
        self.assertEqual(state["notifications"]["telegram"]["chat_id"], "")
        self.assertIn("envia /start", result.lower())

    def test_update_notification_settings_requires_topic_for_ntfy(self):
        state_path = TEST_RUNTIME_DIR / "session_notifications_invalid_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                session.update_notification_settings(
                    enabled=True,
                    windows_enabled=False,
                    ntfy_enabled=True,
                    ntfy_server="https://ntfy.sh",
                    ntfy_topic="",
                )

    def test_update_notification_settings_requires_bot_token_for_telegram(self):
        state_path = TEST_RUNTIME_DIR / "session_notifications_invalid_telegram_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                session.update_notification_settings(
                    enabled=True,
                    windows_enabled=False,
                    ntfy_enabled=False,
                    telegram_enabled=True,
                    telegram_bot_token="",
                    telegram_chat_id="",
                )

    def test_send_test_notification_attempts_telegram_link_before_failing(self):
        state_path = TEST_RUNTIME_DIR / "session_test_notification_telegram_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "try_link_telegram_chat", return_value=True) as link_mock:
                with patch.object(session, "send_notification", return_value=True) as send_mock:
                    result = session.send_test_notification()

        link_mock.assert_called_once()
        send_mock.assert_called_once()
        self.assertEqual(result, "Notificacion de prueba enviada.")
