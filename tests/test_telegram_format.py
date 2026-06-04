import unittest

import memory
import telegram_format


class TelegramFormatTestCase(unittest.TestCase):
    def test_format_operation_reply_uses_shared_conversation_cleaner(self):
        state = memory.default_state()
        rendered = telegram_format.format_telegram_operation_reply(
            "Respuesta",
            (
                "Respuesta guardada. Ejecutando un ciclo con esta informacion.\n\n"
                "Yarbis:\nHecho sin ruido operativo."
            ),
            state=state,
        )

        self.assertIn("Hecho sin ruido operativo.", rendered)
        self.assertNotIn("Respuesta guardada", rendered)


if __name__ == "__main__":
    unittest.main()
