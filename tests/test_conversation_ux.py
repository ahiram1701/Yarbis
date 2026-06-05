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

    def test_build_conversation_view_includes_communication_and_next_step(self):
        state = memory.normalize_state({
            "communication": {
                "tone": "human",
                "detail_level": "brief",
                "proactivity": "low",
            },
            "tasks": [{"id": "task-1", "title": "Seguir", "status": "pending"}],
        })

        view = conversation_ux.build_conversation_view(state)

        self.assertEqual(view["communication"]["tone"], "human")
        self.assertEqual(view["communication"]["detail_level"], "brief")
        self.assertIn("ciclo", view["next_step"])
        self.assertFalse(view["attention"]["active"])

    def test_spoken_reply_text_removes_operational_prefix(self):
        text = (
            "Respuesta guardada. Ejecutando un ciclo con esta informacion.\n\n"
            "=== CICLO 1 ===\n\n"
            "--- Paso 1 ---\n\n"
            "Yarbis:\nLa respuesta final."
        )

        self.assertEqual(conversation_ux.spoken_reply_text(text), "La respuesta final.")

    def test_format_channel_reply_keeps_telegram_continuity_and_cleans_noise(self):
        state = memory.normalize_state({
            "cycle_count": 2,
            "tasks": [{"id": "task-1", "title": "Seguir", "status": "pending"}],
        })

        reply = conversation_ux.format_channel_reply(
            "telegram",
            "Ciclo",
            "Yarbis:\nListo.\n\n...[truncado 20 caracteres]",
            state=state,
        )

        self.assertIn("Estado: Continuidad: 2 ciclo(s) | 1 tarea(s) abierta(s)", reply["text"])
        self.assertIn("Resultado:", reply["text"])
        self.assertNotIn("truncado", reply["text"].lower())
        self.assertIn("/run o /auto", reply["text"])


if __name__ == "__main__":
    unittest.main()
