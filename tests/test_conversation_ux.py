import unittest

import conversation_ux
import memory


class ConversationUxTestCase(unittest.TestCase):
    def test_build_conversation_view_prioritizes_pending_question(self):
        state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que tono quieres usar?",
                "reason": "Hace falta para continuar.",
                "fields": ["tono"],
            },
            "messages": [{"role": "assistant", "content": "Necesito un dato."}],
        })

        view = conversation_ux.build_conversation_view(state)

        self.assertEqual(view["mode"], "waiting_user")
        self.assertEqual(view["pending"]["question"], "Que tono quieres usar?")
        self.assertEqual(view["composer"]["primary_label"], "Responder y continuar")
        self.assertFalse(view["composer"]["disabled"])
        self.assertEqual(view["timeline"][-1]["kind"], "pending")

    def test_build_conversation_view_filters_tool_noise_and_reports_voice(self):
        state = memory.normalize_state({
            "messages": [
                {"role": "user", "content": "Hola"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
                {"role": "tool", "content": "resultado tecnico"},
                {"role": "assistant", "content": "Listo."},
            ],
            "voice": {
                "live_conversation": {
                    "enabled": True,
                    "wake_phrase": "Yarbis",
                },
            },
        })

        view = conversation_ux.build_conversation_view(
            state,
            voice_status={"state": "wake_listening", "detail": "Escuchando"},
        )

        self.assertEqual(view["mode"], "wake_listening")
        self.assertEqual([item["text"] for item in view["timeline"]], ["Hola", "Listo."])
        self.assertTrue(view["voice"]["live_enabled"])
        self.assertIn("Yarbis", view["voice"]["headline"])

    def test_spoken_reply_text_removes_operational_prefix(self):
        text = (
            "Respuesta guardada. Ejecutando un ciclo con esta informacion.\n\n"
            "=== CICLO 1 ===\n\n"
            "--- Paso 1 ---\n\n"
            "Yarbis:\nLa respuesta final."
        )

        self.assertEqual(conversation_ux.spoken_reply_text(text), "La respuesta final.")


if __name__ == "__main__":
    unittest.main()
