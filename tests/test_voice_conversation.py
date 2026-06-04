import unittest
from unittest.mock import Mock, patch

import voice_conversation


class VoiceConversationTestCase(unittest.TestCase):
    def test_wake_phrase_detection_accepts_accents_and_extracts_turn(self):
        self.assertTrue(voice_conversation.wake_phrase_detected("Oye, Yárbis ayúdame", "Yarbis"))
        self.assertEqual(
            voice_conversation.text_after_wake_phrase("Oye Yarbis crea una nota", "Yarbis"),
            "crea una nota",
        )

    def test_process_voice_turn_routes_clean_text_and_returns_spoken_reply(self):
        runner = Mock(return_value=(
            "Respuesta guardada. Ejecutando un ciclo con esta informacion.\n\n"
            "Yarbis:\nClaro, ya lo tengo."
        ))
        speaker = Mock()

        result = voice_conversation.process_voice_turn(
            "Yarbis guarda esto",
            settings={
                "enabled": True,
                "live_conversation": {
                    "wake_phrase": "Yarbis",
                    "auto_speak": True,
                },
            },
            runner=runner,
            speaker=speaker,
        )

        runner.assert_called_once_with("guarda esto", emit_notifications=False, blocking=True)
        self.assertEqual(result["transcript"], "guarda esto")
        self.assertEqual(result["spoken_text"], "Claro, ya lo tengo.")
        speaker.assert_called_once()

    def test_mobile_session_processes_wake_chunk(self):
        session = voice_conversation.start_mobile_session({
            "voice": {
                "enabled": True,
                "live_conversation": {"wake_phrase": "Yarbis"},
            }
        })
        runner = Mock(return_value="Yarbis:\nHecho.")

        with patch.object(
            voice_conversation,
            "transcribe_live_audio_bytes",
            return_value="Yarbis suma contexto",
        ):
            updated = voice_conversation.append_mobile_audio_chunk(
                session["id"],
                b"audio",
                mime_type="audio/webm",
                settings={
                    "voice": {
                        "enabled": True,
                        "live_conversation": {"wake_phrase": "Yarbis"},
                    }
                },
                runner=runner,
            )

        self.assertEqual(updated["state"], voice_conversation.STATE_SPEAKING)
        self.assertEqual(updated["last_transcript"], "suma contexto")
        self.assertEqual(updated["spoken_text"], "Hecho.")


if __name__ == "__main__":
    unittest.main()
