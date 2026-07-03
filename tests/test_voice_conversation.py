import unittest
from unittest.mock import Mock, patch

import voice_conversation


class VoiceConversationTestCase(unittest.TestCase):
    def test_wake_phrase_detection_accepts_accents_and_extracts_turn(self):
        self.assertTrue(voice_conversation.wake_phrase_detected("Oye, Yárbis ayúdame", "Yarbis"))
        self.assertTrue(voice_conversation.wake_phrase_detected("Oye, Jarvis, ayúdame", "Yarbis"))
        for heard in ("Gerbis", "Gervis", "Garbis", "Garvis"):
            self.assertTrue(
                voice_conversation.wake_phrase_detected(f"Oye, {heard}, ayúdame", "Yarbis"),
                heard,
            )
        for heard in ("ger bis", "ger vis", "gar bis", "gar vis"):
            self.assertTrue(
                voice_conversation.wake_phrase_detected(f"Oye, {heard}, ayudame", "Yarbis"),
                heard,
            )
        self.assertTrue(voice_conversation.wake_phrase_detected("Oye, gervis, ayudame", "Jarvis"))
        self.assertEqual(
            voice_conversation.text_after_wake_phrase("Oye Yarbis crea una nota", "Yarbis"),
            "crea una nota",
        )
        self.assertEqual(
            voice_conversation.text_after_wake_phrase("Oye, Jarvis, crea una nota", "Yarbis"),
            "crea una nota",
        )
        self.assertEqual(
            voice_conversation.text_after_wake_phrase("Oye, gar vis, crea una nota", "Yarbis"),
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

    def test_process_voice_turn_arms_next_turn_when_only_wake_phrase_is_heard(self):
        runner = Mock()
        speaker = Mock()

        result = voice_conversation.process_voice_turn(
            "Jarvis",
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

        runner.assert_not_called()
        self.assertTrue(result["awaiting_command"])
        self.assertEqual(result["state"], voice_conversation.STATE_CAPTURING)
        self.assertEqual(result["spoken_text"], "Te escucho. Dime lo que necesitas.")
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
        self.assertTrue(updated["spoken_turn_id"])

    def test_mobile_session_clears_spoken_text_after_transcription_error(self):
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

        self.assertEqual(updated["spoken_text"], "Hecho.")
        self.assertTrue(updated["spoken_turn_id"])

        with patch.object(
            voice_conversation,
            "transcribe_live_audio_bytes",
            side_effect=voice_conversation.yarbis_voice.VoiceError("No pude transcribir"),
        ):
            errored = voice_conversation.append_mobile_audio_chunk(
                session["id"],
                b"noise",
                mime_type="audio/webm",
                settings={
                    "voice": {
                        "enabled": True,
                        "live_conversation": {"wake_phrase": "Yarbis"},
                    }
                },
                runner=runner,
            )

        self.assertEqual(errored["state"], voice_conversation.STATE_WAKE_LISTENING)
        self.assertEqual(errored["spoken_text"], "")
        self.assertEqual(errored["spoken_turn_id"], "")

    def test_mobile_session_accepts_command_after_wake_only_chunk(self):
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
            return_value="Jarvis",
        ):
            armed = voice_conversation.append_mobile_audio_chunk(
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

        self.assertTrue(armed["awaiting_command"])
        runner.assert_not_called()

        with patch.object(
            voice_conversation,
            "transcribe_live_audio_bytes",
            return_value="crea una nota",
        ):
            updated = voice_conversation.append_mobile_audio_chunk(
                session["id"],
                b"audio2",
                mime_type="audio/webm",
                settings={
                    "voice": {
                        "enabled": True,
                        "live_conversation": {"wake_phrase": "Yarbis"},
                    }
                },
                runner=runner,
            )

        runner.assert_called_once_with("crea una nota", emit_notifications=False, blocking=True)
        self.assertFalse(updated["awaiting_command"])
        self.assertEqual(updated["last_transcript"], "crea una nota")
        self.assertEqual(updated["spoken_text"], "Hecho.")

    def test_mobile_chunk_survives_session_pruned_during_transcription(self):
        session = voice_conversation.start_mobile_session({
            "voice": {
                "enabled": True,
                "live_conversation": {"wake_phrase": "Yarbis"},
            }
        })
        session_id = session["id"]
        runner = Mock(return_value="Yarbis:\nHecho.")

        def stop_mid_transcription(*_args, **_kwargs):
            # Simula que la sesión se detiene (o caduca) mientras transcribimos,
            # justo cuando el lock está liberado.
            voice_conversation.stop_mobile_session(session_id)
            return "Yarbis suma contexto"

        with patch.object(
            voice_conversation,
            "transcribe_live_audio_bytes",
            side_effect=stop_mid_transcription,
        ):
            result = voice_conversation.append_mobile_audio_chunk(
                session_id,
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

        self.assertEqual(result["state"], voice_conversation.STATE_IDLE)
        self.assertEqual(result["id"], session_id)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
