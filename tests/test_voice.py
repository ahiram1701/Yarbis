import base64
import json
import tempfile
import unittest
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

    def test_list_tts_voices_includes_piper_catalog_and_language_filter(self):
        engine = Mock()
        engine.getProperty.return_value = []
        catalog = {
            "es_MX-claude-high": {
                "key": "es_MX-claude-high",
                "name": "claude",
                "language": {"code": "es_MX", "family": "es"},
                "quality": "high",
                "num_speakers": 1,
                "files": {
                    "es/es_MX/claude/high/es_MX-claude-high.onnx": {"size_bytes": 10},
                    "es/es_MX/claude/high/es_MX-claude-high.onnx.json": {"size_bytes": 10},
                },
            },
            "en_US-amy-low": {
                "key": "en_US-amy-low",
                "name": "amy",
                "language": {"code": "en_US", "family": "en"},
                "quality": "low",
                "num_speakers": 1,
                "files": {
                    "en/en_US/amy/low/en_US-amy-low.onnx": {"size_bytes": 10},
                    "en/en_US/amy/low/en_US-amy-low.onnx.json": {"size_bytes": 10},
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog_path = tmp_path / "voices.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            with patch.object(voice, "PIPER_CATALOG_PATH", catalog_path):
                with patch.object(voice, "PIPER_VOICES_DIR", tmp_path / "voices"):
                    with patch.object(voice, "_tts_engine", return_value=engine):
                        voices = voice.list_tts_voices({"enabled": True}, include_downloadable=True, language="es")

        self.assertEqual(len(voices), 1)
        self.assertEqual(voices[0]["provider"], "piper")
        self.assertEqual(voices[0]["id"], "es_MX-claude-high")
        self.assertEqual(voices[0]["status"], "downloadable")

    def test_download_piper_voice_downloads_model_and_config(self):
        entry = {
            "key": "es_MX-claude-high",
            "name": "claude",
            "language": {"code": "es_MX", "family": "es"},
            "quality": "high",
            "files": {
                "es/es_MX/claude/high/es_MX-claude-high.onnx": {"size_bytes": 10, "md5_digest": ""},
                "es/es_MX/claude/high/es_MX-claude-high.onnx.json": {"size_bytes": 10, "md5_digest": ""},
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def fake_download(_url, target_path, **_kwargs):
                Path(target_path).parent.mkdir(parents=True, exist_ok=True)
                Path(target_path).write_bytes(b"voice")

            with patch.object(voice, "PIPER_VOICES_DIR", tmp_path / "voices"):
                with patch.object(voice, "_load_piper_catalog", return_value={"es_MX-claude-high": entry}):
                    with patch.object(voice, "_download_to_file", side_effect=fake_download) as download_mock:
                        rendered = voice.download_piper_voice("es_MX-claude-high")

            self.assertEqual(rendered["provider"], "piper")
            self.assertTrue(rendered["installed"])
            self.assertEqual(download_mock.call_count, 2)

    def test_synthesize_speech_file_uses_piper_and_converts_to_ogg(self):
        fake_voice = Mock()

        def fake_synthesize(_text, wav_file, syn_config=None):
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\0\0" * 16)

        fake_voice.synthesize_wav.side_effect = fake_synthesize

        def fake_run(args, **_kwargs):
            Path(args[-1]).write_bytes(b"ogg")
            return SimpleNamespace(returncode=0)

        with patch.object(voice, "_load_piper_voice_for_settings", return_value=fake_voice):
            with patch.object(voice, "_piper_syn_config", return_value=object()):
                with patch.object(voice, "_ffmpeg_executable", return_value="ffmpeg"):
                    with patch("subprocess.run", side_effect=fake_run):
                        audio_path = voice.synthesize_speech_file(
                            "Hola",
                            settings={"enabled": True, "tts_provider": "piper", "piper_voice_id": "es_MX-claude-high"},
                        )
        try:
            self.assertEqual(audio_path.read_bytes(), b"ogg")
            fake_voice.synthesize_wav.assert_called_once()
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
