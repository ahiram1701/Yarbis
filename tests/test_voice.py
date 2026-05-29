import base64
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import voice


class VoiceTestCase(unittest.TestCase):
    def test_decode_audio_b64_enforces_limit_and_suffix(self):
        raw_audio = b"audio"
        encoded = base64.b64encode(raw_audio).decode("ascii")

        decoded, suffix = voice.decode_audio_b64(encoded, mime_type="audio/webm;codecs=opus")

        self.assertEqual(decoded, raw_audio)
        self.assertEqual(suffix, ".webm")

    def test_should_send_telegram_voice_reply_modes(self):
        self.assertFalse(voice.should_send_telegram_voice_reply(
            "Hola",
            source_was_voice=True,
            settings={"telegram_reply_mode": "off"},
        ))
        self.assertTrue(voice.should_send_telegram_voice_reply(
            "Hola",
            source_was_voice=True,
            settings={"telegram_reply_mode": "auto"},
        ))
        self.assertFalse(voice.should_send_telegram_voice_reply(
            "x" * (voice.TELEGRAM_AUTO_VOICE_REPLY_MAX_CHARS + 1),
            source_was_voice=True,
            settings={"telegram_reply_mode": "auto"},
        ))
        self.assertTrue(voice.should_send_telegram_voice_reply(
            "x" * (voice.TELEGRAM_AUTO_VOICE_REPLY_MAX_CHARS + 1),
            source_was_voice=False,
            settings={"telegram_reply_mode": "always"},
        ))

    def test_list_tts_voices_renders_system_voices(self):
        engine = Mock()
        engine.getProperty.return_value = [
            SimpleNamespace(id="voice-1", name="Voz Uno", languages=[b"es-MX"], gender="female", age=None),
        ]

        with patch.object(voice, "_tts_engine", return_value=engine):
            voices = voice.list_tts_voices({"enabled": True})

        self.assertEqual(voices[0]["index"], 1)
        self.assertEqual(voices[0]["id"], "voice-1")
        self.assertEqual(voices[0]["name"], "Voz Uno")
        self.assertEqual(voices[0]["languages"], ["es-MX"])
        engine.stop.assert_called_once()

    def test_list_tts_voices_includes_kokoro_language_filter(self):
        engine = Mock()
        engine.getProperty.return_value = []

        with patch.object(voice, "_tts_engine", return_value=engine):
            voices = voice.list_tts_voices({"enabled": True}, include_downloadable=True, language="es")

        self.assertTrue(voices)
        self.assertTrue(all(item["provider"] == "kokoro" for item in voices))
        self.assertIn("ef_dora", {item["id"] for item in voices})

    def test_list_tts_voices_keeps_kokoro_when_system_voices_fail(self):
        with patch.object(voice, "_tts_engine", side_effect=voice.VoiceError("sin voces de sistema")):
            voices = voice.list_tts_voices({"enabled": True}, include_downloadable=True, language="es")

        self.assertTrue(voices)
        self.assertTrue(all(item["provider"] == "kokoro" for item in voices))
        self.assertIn("ef_dora", {item["id"] for item in voices})

    def test_synthesize_speech_file_uses_kokoro_and_converts_to_ogg(self):
        def fake_run(args, **_kwargs):
            Path(args[-1]).write_bytes(b"ogg")
            return SimpleNamespace(returncode=0)

        def fake_kokoro(_text, wav_path, _settings):
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(24000)
                wav_file.writeframes(b"\0\0" * 16)

        with patch.object(voice, "_synthesize_kokoro_wav", side_effect=fake_kokoro) as kokoro_mock:
            with patch.object(voice, "_ffmpeg_executable", return_value="ffmpeg"):
                with patch("subprocess.run", side_effect=fake_run):
                    audio_path = voice.synthesize_speech_file(
                        "Hola",
                        settings={"enabled": True, "tts_provider": "kokoro", "kokoro_voice_id": "ef_dora"},
                    )
        try:
            self.assertEqual(audio_path.read_bytes(), b"ogg")
            kokoro_mock.assert_called_once()
        finally:
            voice.cleanup_voice_file(audio_path)

    def test_stop_speaking_stops_active_engine(self):
        engine = Mock()
        previous = voice._TTS_ENGINE
        voice._TTS_ENGINE = engine
        try:
            self.assertTrue(voice.stop_speaking())
        finally:
            voice._TTS_ENGINE = previous

        engine.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
