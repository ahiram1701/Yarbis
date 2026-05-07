import unittest
from pathlib import Path
from unittest.mock import patch

import memory
import session
import telegram_inbox
import yarbis_service

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class SharedMemoryContinuityTestCase(unittest.TestCase):
    def test_interface_telegram_and_proactive_pulse_share_memory(self):
        state_path = TEST_RUNTIME_DIR / "shared_memory_channels_state.json"
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
            "service": {
                "proactive": {
                    "enabled": True,
                    "interval_seconds": 1800,
                    "cycles": 1,
                    "start_delay_seconds": 0,
                },
            },
        })

        telegram_update = {
            "update_id": 101,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/nota crear Telegram | Contexto guardado desde Telegram | canal",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            session.update_profile_text(
                name="Ahiram",
                preferences="local first",
                constraints="mantener contexto compartido",
            )

            with patch.object(telegram_inbox, "send_telegram_message", return_value=True):
                telegram_inbox.process_telegram_update(telegram_update)

            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(
                    yarbis_service,
                    "run_auto_with_output",
                    return_value="Modo autonomo ejecutado por 1 ciclo(s).",
                ) as auto_mock:
                    with patch.object(yarbis_service, "send_telegram_message", return_value=True):
                        result = yarbis_service._run_proactive_pulse_inline(
                            {"cycles": 1},
                            "2026-05-06T12:00:00+00:00",
                        )

            state = memory.load_state()

        self.assertIn("Modo autonomo ejecutado", result)
        auto_mock.assert_called_once_with(cycles=1, emit_notifications=False)
        self.assertEqual(state["profile"]["name"], "Ahiram")
        self.assertEqual(state["profile"]["preferences"], ["local first"])
        self.assertEqual(state["profile"]["constraints"], ["mantener contexto compartido"])
        self.assertTrue(any(note["title"] == "Telegram" for note in state["notes"]))
        self.assertTrue(any(
            "Contexto guardado desde Telegram" in note["content"]
            for note in state["notes"]
        ))
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 101)
        self.assertEqual(state["messages"][-1]["role"], "user")
        self.assertIn("Pulso proactivo 24/7", state["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
