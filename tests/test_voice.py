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

    def test_list_system_voices_stops_engine_on_error(self):
        engine = Mock()
        engine.getProperty.side_effect = RuntimeError("driver caído")

        with patch.object(voice, "_tts_engine", return_value=engine):
            with self.assertRaises(voice.VoiceError):
                voice._list_system_voices({"enabled": True})

        engine.stop.assert_called_once()

    def test_kokoro_pipeline_cache_is_bounded_not_cleared(self):
        import sys
        import types

        fake_pykokoro = types.ModuleType("pykokoro")
        fake_pykokoro.GenerationConfig = lambda **kwargs: SimpleNamespace(**kwargs)
        fake_pykokoro.PipelineConfig = lambda **kwargs: SimpleNamespace(**kwargs)
        sentinel = object()
        fake_pykokoro.KokoroPipeline = lambda _config: sentinel
        fake_tokenizer = types.ModuleType("pykokoro.tokenizer")
        fake_tokenizer.TokenizerConfig = lambda **kwargs: SimpleNamespace(**kwargs)

        original_cache = dict(voice._KOKORO_PIPELINES)
        try:
            voice._KOKORO_PIPELINES.clear()
            # Sembramos el cache al límite con dos entradas distintas al key nuevo.
            oldest_key = ("seed_old", "en", 1.0)
            newest_seed_key = ("seed_new", "en", 1.0)
            voice._KOKORO_PIPELINES[oldest_key] = object()
            voice._KOKORO_PIPELINES[newest_seed_key] = object()
            self.assertEqual(len(voice._KOKORO_PIPELINES), voice._KOKORO_PIPELINE_CACHE_LIMIT)

            with patch.dict(sys.modules, {"pykokoro": fake_pykokoro, "pykokoro.tokenizer": fake_tokenizer}):
                pipeline = voice._load_kokoro_pipeline({"kokoro_voice_id": "ef_dora", "tts_rate": 175})

            self.assertIs(pipeline, sentinel)
            # El cache no crece más allá del límite y NO se vació por completo.
            self.assertEqual(len(voice._KOKORO_PIPELINES), voice._KOKORO_PIPELINE_CACHE_LIMIT)
            self.assertNotIn(oldest_key, voice._KOKORO_PIPELINES)
            self.assertIn(newest_seed_key, voice._KOKORO_PIPELINES)
        finally:
            voice._KOKORO_PIPELINES.clear()
            voice._KOKORO_PIPELINES.update(original_cache)

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

    def test_synthesize_speech_wav_file_uses_kokoro_without_ogg_conversion(self):
        def fake_kokoro(_text, wav_path, _settings):
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(24000)
                wav_file.writeframes(b"\0\0" * 16)

        with patch.object(voice, "_synthesize_kokoro_wav", side_effect=fake_kokoro) as kokoro_mock:
            audio_path = voice.synthesize_speech_wav_file(
                "Hola",
                settings={"enabled": True, "tts_provider": "kokoro", "kokoro_voice_id": "ef_dora"},
            )
        try:
            self.assertEqual(audio_path.suffix, ".wav")
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


class VoiceTtsFallbackTestCase(unittest.TestCase):
    def test_kokoro_failure_falls_back_to_system(self):
        import voice
        from pathlib import Path
        from unittest.mock import patch

        calls = {"system": 0}

        def fake_system(text, wav_path, settings):
            calls["system"] += 1
            Path(wav_path).write_bytes(b"RIFF____WAVE")

        def fake_kokoro(text, wav_path, settings):
            raise RuntimeError("bad allocation")

        with patch.object(voice, "_settings_tts_provider", return_value="kokoro"):
            with patch.object(voice, "_synthesize_kokoro_wav", side_effect=fake_kokoro):
                with patch.object(voice, "_synthesize_system_wav", side_effect=fake_system):
                    wav = voice._synthesize_wav_file("hola", {"tts_provider": "kokoro"})
        try:
            self.assertEqual(calls["system"], 1)
            self.assertTrue(wav.exists())
        finally:
            voice.cleanup_voice_file(wav)


class VoiceEdgeTtsTestCase(unittest.TestCase):
    def test_edge_provider_valid_and_default_voice(self):
        import memory
        v = memory.default_state()["voice"]
        self.assertEqual(v["edge_voice"], "es-MX-DaliaNeural")
        self.assertIn("edge", memory.VALID_VOICE_TTS_PROVIDERS)

    def test_edge_failure_falls_back_to_system(self):
        import voice
        from pathlib import Path
        from unittest.mock import patch

        calls = {"system": 0}

        def fake_system(text, wav_path, settings):
            calls["system"] += 1
            Path(wav_path).write_bytes(b"RIFF____WAVE")

        def fake_edge(text, wav_path, settings):
            raise RuntimeError("no internet")

        with patch.object(voice, "_settings_tts_provider", return_value="edge"):
            with patch.object(voice, "_synthesize_edge_wav", side_effect=fake_edge):
                with patch.object(voice, "_synthesize_system_wav", side_effect=fake_system):
                    wav = voice._synthesize_wav_file("hola", {"tts_provider": "edge"})
        try:
            self.assertEqual(calls["system"], 1)
            self.assertTrue(wav.exists())
        finally:
            voice.cleanup_voice_file(wav)
