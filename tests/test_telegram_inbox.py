import unittest
from pathlib import Path
from unittest.mock import patch

import memory
import telegram_inbox

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class TelegramInboxTestCase(unittest.TestCase):
    def test_process_telegram_update_binds_first_private_chat_and_runs_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_bind_state.json"
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

        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/run",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "run_cycle_with_output", return_value="Ciclo hecho.") as run_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    summary = telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        self.assertEqual(state["notifications"]["telegram"]["chat_id"], "123")
        run_mock.assert_called_once_with(emit_notifications=False)
        send_mock.assert_called_once()
        self.assertIn("vinculado", send_mock.call_args.args[0].lower())
        self.assertIn("Ciclo hecho.", send_mock.call_args.args[0])
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "123")
        self.assertIn("Telegram: procesado", summary)

    def test_process_telegram_update_routes_free_text_to_submit_user_reply(self):
        state_path = TEST_RUNTIME_DIR / "telegram_reply_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Trabajemos el nicho fitness",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "submit_user_reply",
                return_value="Respuesta procesada.",
            ) as reply_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)

        reply_mock.assert_called_once_with(
            "Trabajemos el nicho fitness",
            emit_notifications=False,
        )
        send_mock.assert_called_once_with("Respuesta procesada.", chat_id="123")

    def test_process_telegram_update_routes_goal_command_to_update_goal(self):
        state_path = TEST_RUNTIME_DIR / "telegram_goal_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "goal": "Objetivo anterior",
            "messages": [{"role": "assistant", "content": "avance previo"}],
            "tasks": [{"id": "task-1", "title": "Vieja tarea", "status": "pending"}],
            "current_plan": ["Paso viejo"],
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/objetivo Preparar el lanzamiento del producto",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                summary = telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertEqual(state["goal"], "Preparar el lanzamiento del producto")
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["current_plan"], [])
        self.assertIn("Objetivo actualizado", send_mock.call_args.args[0])
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "123")
        self.assertIn("Telegram: procesado", summary)

    def test_goal_command_without_text_returns_usage(self):
        state_path = TEST_RUNTIME_DIR / "telegram_goal_usage_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "goal": "Objetivo anterior",
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 4,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/goal",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertEqual(state["goal"], "Objetivo anterior")
        send_mock.assert_called_once_with("Uso: /goal nuevo objetivo", chat_id="123")

    def test_process_telegram_update_emits_activity_before_finishing_processing(self):
        state_path = TEST_RUNTIME_DIR / "telegram_activity_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Mensaje remoto",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "_emit_event") as emit_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True):
                    def fake_submit_user_reply(*args, **kwargs):
                        emitted_messages = [call.args[0] for call in emit_mock.call_args_list]
                        self.assertTrue(
                            any(
                                isinstance(item, str) and "Telegram: recibido" in item
                                for item in emitted_messages
                            ),
                        )
                        self.assertTrue(
                            any(
                                isinstance(item, dict)
                                and item.get("type") == "remote_job_started"
                                and item.get("label") == "Respuesta"
                                for item in emitted_messages
                            ),
                        )
                        return "Respuesta procesada."

                    with patch.object(
                        telegram_inbox,
                        "submit_user_reply",
                        side_effect=fake_submit_user_reply,
                        ):
                            summary = telegram_inbox.process_telegram_update(update)

        self.assertIn("Telegram: procesado", summary)
        emitted_messages = [call.args[0] for call in emit_mock.call_args_list]
        self.assertTrue(
            any(
                isinstance(item, dict)
                and item.get("type") == "remote_job_finished"
                and item.get("label") == "Respuesta"
                and item.get("content") == "Respuesta procesada."
                for item in emitted_messages
            ),
        )

    def test_poll_updates_once_persists_last_update_id(self):
        state_path = TEST_RUNTIME_DIR / "telegram_poll_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                    "last_update_id": 4,
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "telegram_api_request",
                return_value={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 5,
                            "message": {
                                "chat": {"id": 123, "type": "private"},
                                "text": "Hola",
                            },
                        }
                    ],
                },
            ) as api_mock:
                with patch.object(telegram_inbox, "process_telegram_update", return_value="ok"):
                    result = telegram_inbox._poll_updates_once()
                    state = memory.load_state()

        self.assertTrue(result)
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 5)
        self.assertEqual(api_mock.call_args.args[0], "getUpdates")
