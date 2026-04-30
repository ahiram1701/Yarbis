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

    def test_process_telegram_update_routes_note_create_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_note_create_state.json"
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
            "update_id": 41,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/nota crear Rutina | Revisar pendientes cada manana | personal",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                summary = telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertEqual(len(state["notes"]), 1)
        self.assertEqual(state["notes"][0]["title"], "Rutina")
        self.assertEqual(state["notes"][0]["category"], "personal")
        send_mock.assert_called_once()
        self.assertIn("Nota guardada", send_mock.call_args.args[0])
        self.assertIn("Telegram: procesado", summary)

    def test_process_telegram_update_does_not_repeat_same_update_id(self):
        state_path = TEST_RUNTIME_DIR / "telegram_note_duplicate_update_state.json"
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
            "update_id": 44,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/nota crear Rutina | Revisar pendientes cada manana | personal",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                first_summary = telegram_inbox.process_telegram_update(update)
                second_summary = telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertIn("Telegram: procesado", first_summary)
        self.assertEqual(second_summary, "")
        self.assertEqual(len(state["notes"]), 1)
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 44)
        send_mock.assert_called_once()

    def test_process_telegram_update_routes_natural_note_without_model_reply(self):
        state_path = TEST_RUNTIME_DIR / "telegram_natural_note_state.json"
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
            "update_id": 42,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "guarda una nota: Idea | Probar notas por Telegram | trabajo",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "submit_user_reply") as reply_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        reply_mock.assert_not_called()
        self.assertEqual(state["notes"][0]["title"], "Idea")
        self.assertIn("Nota guardada", send_mock.call_args.args[0])

    def test_process_telegram_update_routes_note_delete_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_note_delete_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notes": [
                {
                    "id": "note-123",
                    "title": "Rutina",
                    "content": "Revisar pendientes",
                    "category": "personal",
                }
            ],
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
            "update_id": 43,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/nota borrar note-123",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertEqual(state["notes"], [])
        self.assertIn("Nota eliminada", send_mock.call_args.args[0])

    def test_process_telegram_update_routes_shutdown_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_shutdown_state.json"
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
            "update_id": 5,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/apagar 5m",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_shutdown",
                return_value="Apagado programado.",
            ) as shutdown_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    summary = telegram_inbox.process_telegram_update(update)

        shutdown_mock.assert_called_once_with(delay_seconds=300)
        send_mock.assert_called_once_with("Apagado programado.", chat_id="123")
        self.assertIn("Telegram: procesado", summary)

    def test_process_telegram_update_routes_restart_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_restart_state.json"
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
            "update_id": 6,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/reiniciar 3m",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_restart",
                return_value="Reinicio programado.",
            ) as restart_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    summary = telegram_inbox.process_telegram_update(update)

        restart_mock.assert_called_once_with(delay_seconds=180)
        send_mock.assert_called_once_with("Reinicio programado.", chat_id="123")
        self.assertIn("Telegram: procesado", summary)

    def test_process_telegram_update_routes_natural_shutdown_phrase(self):
        state_path = TEST_RUNTIME_DIR / "telegram_natural_shutdown_state.json"
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
            "update_id": 6,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Yarbis, apaga la pc",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_shutdown",
                return_value="Apagado programado.",
            ) as shutdown_mock:
                with patch.object(telegram_inbox, "submit_user_reply") as reply_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)

        shutdown_mock.assert_called_once_with(delay_seconds=60)
        reply_mock.assert_not_called()
        send_mock.assert_called_once_with("Apagado programado.", chat_id="123")

    def test_process_telegram_update_routes_natural_restart_phrase(self):
        state_path = TEST_RUNTIME_DIR / "telegram_natural_restart_state.json"
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
            "update_id": 7,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Yarbis, reinicia pc",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_restart",
                return_value="Reinicio programado.",
            ) as restart_mock:
                with patch.object(telegram_inbox, "submit_user_reply") as reply_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)

        restart_mock.assert_called_once_with(delay_seconds=60)
        reply_mock.assert_not_called()
        send_mock.assert_called_once_with("Reinicio programado.", chat_id="123")

    def test_process_telegram_update_routes_short_natural_shutdown_phrase(self):
        state_path = TEST_RUNTIME_DIR / "telegram_short_natural_shutdown_state.json"
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
            "update_id": 7,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "apaga pc",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_shutdown",
                return_value="Apagado programado.",
            ) as shutdown_mock:
                with patch.object(telegram_inbox, "submit_user_reply") as reply_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)

        shutdown_mock.assert_called_once_with(delay_seconds=60)
        reply_mock.assert_not_called()
        send_mock.assert_called_once_with("Apagado programado.", chat_id="123")

    def test_process_telegram_update_routes_shutdown_cancel_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_cancel_shutdown_state.json"
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
            "update_id": 7,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/cancelar_apagado",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "cancel_system_shutdown",
                return_value="Apagado cancelado.",
            ) as cancel_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)

        cancel_mock.assert_called_once_with(action_label="apagado")
        send_mock.assert_called_once_with("Apagado cancelado.", chat_id="123")

    def test_process_telegram_update_routes_restart_cancel_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_cancel_restart_state.json"
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
            "update_id": 8,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/cancelar_reinicio",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "cancel_system_shutdown",
                return_value="Reinicio cancelado.",
            ) as cancel_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)

        cancel_mock.assert_called_once_with(action_label="reinicio")
        send_mock.assert_called_once_with("Reinicio cancelado.", chat_id="123")

    def test_shutdown_command_only_binds_first_private_chat(self):
        state_path = TEST_RUNTIME_DIR / "telegram_shutdown_bind_only_state.json"
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
            "update_id": 8,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/apagar",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "request_system_shutdown") as shutdown_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    summary = telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        self.assertEqual(state["notifications"]["telegram"]["chat_id"], "123")
        shutdown_mock.assert_not_called()
        self.assertIn("vuelve a enviar", send_mock.call_args.args[0])
        self.assertIn("Telegram: vinculo chat", summary)

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

    def test_poll_updates_once_advances_after_processing_error(self):
        state_path = TEST_RUNTIME_DIR / "telegram_poll_error_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                    "last_update_id": 8,
                },
            },
        })

        updates = [
            {
                "update_id": 9,
                "message": {
                    "chat": {"id": 123, "type": "private"},
                    "text": "/cancelar_apagado",
                },
            },
            {
                "update_id": 10,
                "message": {
                    "chat": {"id": 123, "type": "private"},
                    "text": "/status",
                },
            },
        ]

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "telegram_api_request",
                return_value={"ok": True, "result": updates},
            ):
                with patch.object(
                    telegram_inbox,
                    "process_telegram_update",
                    side_effect=[RuntimeError("fallo cancelando"), "ok"],
                ) as process_mock:
                    with patch.object(telegram_inbox, "_emit_event") as emit_mock:
                        result = telegram_inbox._poll_updates_once()
                        state = memory.load_state()

        self.assertTrue(result)
        self.assertEqual(process_mock.call_count, 2)
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 10)
        emitted = [call.args[0] for call in emit_mock.call_args_list]
        self.assertTrue(any("fallo cancelando" in str(item) for item in emitted))
        self.assertIn("ok", emitted)
