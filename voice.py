import base64
import contextlib
import tempfile
import threading
import time
import wave
from pathlib import Path

from memory import (
    DEFAULT_VOICE_MAX_AUDIO_SECONDS,
    DEFAULT_VOICE_TELEGRAM_REPLY_MODE,
    load_state,
    normalize_state,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent
VOICE_RUNTIME_DIR = WORKSPACE_ROOT / ".yarbis_runtime" / "voice"
MAX_VOICE_AUDIO_BYTES = 20 * 1024 * 1024
TELEGRAM_AUTO_VOICE_REPLY_MAX_CHARS = 800
DEFAULT_TTS_VOLUME = 1.0
DEFAULT_RECORD_SAMPLE_RATE = 16_000
DEFAULT_RECORD_CHANNELS = 1
_WHISPER_LOCK = threading.RLock()
_WHISPER_MODELS = {}


class VoiceError(RuntimeError):
    pass


def get_voice_settings(settings: dict | None = None) -> dict:
    if settings is None:
        return load_state().get("voice", {})
    if "voice" in settings and isinstance(settings.get("voice"), dict):
        return normalize_state(settings).get("voice", {})
    return normalize_state({"voice": settings}).get("voice", {})


def voice_enabled(settings: dict | None = None) -> bool:
    return bool(get_voice_settings(settings).get("enabled", True))


def ensure_voice_enabled(settings: dict | None = None) -> dict:
    voice_settings = get_voice_settings(settings)
    if not voice_settings.get("enabled", True):
        raise VoiceError("La voz esta desactivada. Usa /voz on para activarla.")
    return voice_settings


def max_audio_seconds(settings: dict | None = None) -> int:
    return int(get_voice_settings(settings).get("max_audio_seconds", DEFAULT_VOICE_MAX_AUDIO_SECONDS))


def telegram_reply_mode(settings: dict | None = None) -> str:
    mode = str(get_voice_settings(settings).get("telegram_reply_mode", DEFAULT_VOICE_TELEGRAM_REPLY_MODE)).strip()
    return mode or DEFAULT_VOICE_TELEGRAM_REPLY_MODE


def should_send_telegram_voice_reply(
    text: str,
    *,
    source_was_voice: bool,
    settings: dict | None = None,
) -> bool:
    voice_settings = get_voice_settings(settings)
    if not voice_settings.get("enabled", True):
        return False
    mode = str(voice_settings.get("telegram_reply_mode", DEFAULT_VOICE_TELEGRAM_REPLY_MODE)).strip().lower()
    if mode == "off":
        return False
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        return False
    if mode == "always":
        return True
    return bool(source_was_voice and len(cleaned_text) <= TELEGRAM_AUTO_VOICE_REPLY_MAX_CHARS)


def _suffix_for_mime_type(mime_type: str) -> str:
    safe_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if safe_mime in {"audio/ogg", "audio/opus"}:
        return ".ogg"
    if safe_mime in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if safe_mime in {"audio/mp4", "audio/m4a", "audio/x-m4a"}:
        return ".m4a"
    if safe_mime in {"audio/webm", "video/webm"}:
        return ".webm"
    if safe_mime in {"audio/wav", "audio/x-wav"}:
        return ".wav"
    return ".audio"


def decode_audio_b64(audio_b64: str, *, mime_type: str = "") -> tuple[bytes, str]:
    try:
        raw_audio = base64.b64decode(str(audio_b64 or ""), validate=True)
    except Exception as exc:
        raise VoiceError("Audio base64 invalido.") from exc
    if not raw_audio:
        raise VoiceError("El audio esta vacio.")
    if len(raw_audio) > MAX_VOICE_AUDIO_BYTES:
        raise VoiceError("El audio excede el limite de 20 MB.")
    return raw_audio, _suffix_for_mime_type(mime_type)


@contextlib.contextmanager
def _temp_audio_file(raw_audio: bytes, suffix: str):
    VOICE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="yarbis-voice-",
        suffix=suffix or ".audio",
        dir=VOICE_RUNTIME_DIR,
        delete=False,
    ) as file:
        path = Path(file.name)
        file.write(raw_audio)
    try:
        yield path
    finally:
        cleanup_voice_file(path)


def _load_whisper_model(settings: dict):
    model_name = str(settings.get("stt_model", "base")).strip() or "base"
    compute_type = str(settings.get("stt_compute_type", "int8")).strip() or "int8"
    key = (model_name, compute_type)
    with _WHISPER_LOCK:
        cached = _WHISPER_MODELS.get(key)
        if cached is not None:
            return cached
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise VoiceError(
                "Falta faster-whisper. Instala dependencias con python -m pip install -r requirements.txt."
            ) from exc
        try:
            model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
        except Exception as exc:
            raise VoiceError(f"No pude cargar el modelo local de voz '{model_name}': {exc}") from exc
        _WHISPER_MODELS[key] = model
        return model


def transcribe_audio_file(path: str | Path, settings: dict | None = None) -> str:
    voice_settings = ensure_voice_enabled(settings)
    audio_path = Path(path)
    if not audio_path.exists():
        raise VoiceError("No encontre el archivo de audio.")
    if audio_path.stat().st_size > MAX_VOICE_AUDIO_BYTES:
        raise VoiceError("El audio excede el limite de 20 MB.")

    model = _load_whisper_model(voice_settings)
    language = str(voice_settings.get("language", "es")).strip() or None
    try:
        segments, _info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
        )
        transcript = " ".join(str(segment.text).strip() for segment in segments if str(segment.text).strip())
    except Exception as exc:
        raise VoiceError(f"No pude transcribir el audio localmente: {exc}") from exc

    transcript = transcript.strip()
    if not transcript:
        raise VoiceError("No detecte voz clara en el audio.")
    return transcript


def transcribe_audio_bytes(
    raw_audio: bytes,
    *,
    mime_type: str = "",
    suffix: str = "",
    settings: dict | None = None,
) -> str:
    if len(raw_audio or b"") > MAX_VOICE_AUDIO_BYTES:
        raise VoiceError("El audio excede el limite de 20 MB.")
    with _temp_audio_file(raw_audio, suffix or _suffix_for_mime_type(mime_type)) as audio_path:
        return transcribe_audio_file(audio_path, settings=settings)


def _tts_engine(settings: dict):
    try:
        import pyttsx3
    except Exception as exc:
        raise VoiceError("Falta pyttsx3. Instala dependencias con python -m pip install -r requirements.txt.") from exc

    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", int(settings.get("tts_rate", 175)))
        engine.setProperty("volume", DEFAULT_TTS_VOLUME)
        voice_id = str(settings.get("tts_voice_id", "")).strip()
        if voice_id:
            engine.setProperty("voice", voice_id)
    except Exception as exc:
        raise VoiceError(f"No pude iniciar la voz del sistema: {exc}") from exc
    return engine


def speak_text(text: str, settings: dict | None = None) -> None:
    voice_settings = ensure_voice_enabled(settings)
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        raise VoiceError("No hay texto para leer.")
    engine = _tts_engine(voice_settings)
    engine.say(cleaned_text)
    engine.runAndWait()


def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg
    except Exception as exc:
        raise VoiceError(
            "Falta imageio-ffmpeg. Instala dependencias con python -m pip install -r requirements.txt."
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def synthesize_speech_file(text: str, settings: dict | None = None) -> Path:
    import subprocess

    voice_settings = ensure_voice_enabled(settings)
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        raise VoiceError("No hay texto para convertir a voz.")

    VOICE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    token = f"{time.time_ns()}-{threading.get_ident()}"
    wav_path = VOICE_RUNTIME_DIR / f"tts-{token}.wav"
    ogg_path = VOICE_RUNTIME_DIR / f"tts-{token}.ogg"

    engine = _tts_engine(voice_settings)
    try:
        engine.save_to_file(cleaned_text, str(wav_path))
        engine.runAndWait()
    except Exception as exc:
        cleanup_voice_file(wav_path)
        raise VoiceError(f"No pude sintetizar la voz: {exc}") from exc

    if not wav_path.exists() or wav_path.stat().st_size <= 0:
        cleanup_voice_file(wav_path)
        raise VoiceError("La voz del sistema no genero audio.")

    try:
        subprocess.run(
            [
                _ffmpeg_executable(),
                "-y",
                "-i",
                str(wav_path),
                "-c:a",
                "libopus",
                "-b:a",
                "32k",
                str(ogg_path),
            ],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=True,
        )
    except Exception as exc:
        cleanup_voice_file(wav_path)
        cleanup_voice_file(ogg_path)
        raise VoiceError(f"No pude convertir la voz para Telegram: {exc}") from exc

    cleanup_voice_file(wav_path)
    return ogg_path


def record_microphone_to_file(
    stop_event: threading.Event,
    *,
    settings: dict | None = None,
    sample_rate: int = DEFAULT_RECORD_SAMPLE_RATE,
    channels: int = DEFAULT_RECORD_CHANNELS,
) -> Path:
    voice_settings = ensure_voice_enabled(settings)
    max_seconds = int(voice_settings.get("max_audio_seconds", DEFAULT_VOICE_MAX_AUDIO_SECONDS))
    try:
        import sounddevice as sd
    except Exception as exc:
        raise VoiceError("Falta sounddevice. Instala dependencias con python -m pip install -r requirements.txt.") from exc

    chunks: list[bytes] = []

    def callback(indata, _frames, _time_info, status):
        if status:
            return
        chunks.append(bytes(indata))

    started_at = time.monotonic()
    try:
        with sd.RawInputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="int16",
            callback=callback,
        ):
            while not stop_event.wait(0.05):
                if time.monotonic() - started_at >= max_seconds:
                    break
    except Exception as exc:
        raise VoiceError(f"No pude grabar desde el microfono: {exc}") from exc

    if not chunks:
        raise VoiceError("No capture audio del microfono.")

    VOICE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    path = VOICE_RUNTIME_DIR / f"recording-{time.time_ns()}-{threading.get_ident()}.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"".join(chunks))
    return path


def cleanup_voice_file(path: str | Path | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return
    except OSError:
        return
