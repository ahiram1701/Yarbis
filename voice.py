import base64
import contextlib
import tempfile
import threading
import time
import wave
from pathlib import Path

from memory import (
    DEFAULT_VOICE_MAX_AUDIO_SECONDS,
    DEFAULT_VOICE_BROWSER_TTS_PITCH,
    DEFAULT_VOICE_BROWSER_TTS_RATE,
    DEFAULT_VOICE_KOKORO_VOICE_ID,
    DEFAULT_VOICE_TELEGRAM_REPLY_MODE,
    DEFAULT_VOICE_TTS_RATE,
    DEFAULT_VOICE_TTS_PROVIDER,
    MAX_VOICE_BROWSER_TTS_PITCH,
    MAX_VOICE_BROWSER_TTS_RATE,
    MAX_VOICE_TTS_RATE,
    MIN_VOICE_BROWSER_TTS_PITCH,
    MIN_VOICE_BROWSER_TTS_RATE,
    MIN_VOICE_TTS_RATE,
    VALID_VOICE_TTS_PROVIDERS,
    VALID_VOICE_TELEGRAM_REPLY_MODES,
    load_state,
    normalize_state,
    state_transaction,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent
VOICE_RUNTIME_DIR = WORKSPACE_ROOT / ".yarbis_runtime" / "voice"
KOKORO_DOWNLOAD_PAGE_URL = "https://huggingface.co/hexgrad/Kokoro-82M"
KOKORO_VOICES = (
    ("ef_dora", "Dora - Espanol femenino", ("es",)),
    ("em_alex", "Alex - Espanol masculino", ("es",)),
    ("em_santa", "Santa - Espanol masculino", ("es",)),
    ("af_heart", "Heart - Ingles US femenino", ("en-us",)),
    ("af_bella", "Bella - Ingles US femenino", ("en-us",)),
    ("af_nicole", "Nicole - Ingles US femenino", ("en-us",)),
    ("am_michael", "Michael - Ingles US masculino", ("en-us",)),
    ("bf_alice", "Alice - Ingles UK femenino", ("en-gb",)),
    ("bm_george", "George - Ingles UK masculino", ("en-gb",)),
    ("ff_siwis", "Siwis - Frances femenino", ("fr-fr",)),
    ("pf_dora", "Dora - Portugues BR femenino", ("pt-br",)),
    ("pm_alex", "Alex - Portugues BR masculino", ("pt-br",)),
    ("if_sara", "Sara - Italiano femenino", ("it",)),
    ("im_nicola", "Nicola - Italiano masculino", ("it",)),
    ("jf_alpha", "Alpha - Japones femenino", ("ja",)),
    ("jm_kumo", "Kumo - Japones masculino", ("ja",)),
    ("zf_xiaoxiao", "Xiaoxiao - Mandarin femenino", ("cmn",)),
    ("zm_yunxi", "Yunxi - Mandarin masculino", ("cmn",)),
)
KOKORO_LANG_BY_PREFIX = {
    "af": "en-us",
    "am": "en-us",
    "bf": "en-gb",
    "bm": "en-gb",
    "ef": "es",
    "em": "es",
    "ff": "fr-fr",
    "hf": "hi",
    "hm": "hi",
    "if": "it",
    "im": "it",
    "jf": "ja",
    "jm": "ja",
    "pf": "pt-br",
    "pm": "pt-br",
    "zf": "cmn",
    "zm": "cmn",
}
MAX_VOICE_AUDIO_BYTES = 20 * 1024 * 1024
TELEGRAM_AUTO_VOICE_REPLY_MAX_CHARS = 800
DEFAULT_TTS_VOLUME = 1.0
DEFAULT_RECORD_SAMPLE_RATE = 16_000
DEFAULT_RECORD_CHANNELS = 1
_WHISPER_LOCK = threading.RLock()
_WHISPER_MODELS = {}
_TTS_LOCK = threading.RLock()
_TTS_ENGINE = None
_KOKORO_LOCK = threading.RLock()
_KOKORO_PIPELINES = {}


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


def _kokoro_voice_language(voice_id: str) -> str:
    prefix = str(voice_id or "")[:2].lower()
    return KOKORO_LANG_BY_PREFIX.get(prefix, "es")


def _kokoro_language_matches(voice_id: str, language: str | None) -> bool:
    cleaned = str(language or "").strip().lower()
    if not cleaned or cleaned == "all":
        return True
    voice_language = _kokoro_voice_language(voice_id)
    return cleaned in {voice_language, voice_language.split("-", 1)[0]}


def _safe_kokoro_voice_id(value: str) -> str:
    cleaned = str(value or "").strip()
    known_ids = {voice_id for voice_id, _name, _languages in KOKORO_VOICES}
    if not cleaned:
        return DEFAULT_VOICE_KOKORO_VOICE_ID
    if cleaned not in known_ids:
        raise VoiceError("ID de voz Kokoro invalido.")
    return cleaned


def _render_kokoro_voice(voice_id: str, name: str, languages: tuple[str, ...], *, index: int) -> dict:
    return {
        "index": index,
        "id": voice_id,
        "voice_id": voice_id,
        "name": name,
        "languages": list(languages),
        "provider": "kokoro",
        "status": "local",
        "installed": True,
        "downloadable": False,
    }


def _list_kokoro_voices(*, language: str | None = None, start_index: int = 1) -> list[dict]:
    rendered = []
    next_index = start_index
    for voice_id, name, languages in KOKORO_VOICES:
        if not _kokoro_language_matches(voice_id, language):
            continue
        rendered.append(_render_kokoro_voice(voice_id, name, languages, index=next_index))
        next_index += 1
    return rendered


def _list_system_voices(settings: dict | None = None) -> list[dict]:
    voice_settings = get_voice_settings(settings)
    engine = _tts_engine(voice_settings)
    try:
        voices = engine.getProperty("voices") or []
    except Exception as exc:
        raise VoiceError(f"No pude listar voces del sistema: {exc}") from exc

    rendered = []
    for index, item in enumerate(voices, start=1):
        voice_id = str(getattr(item, "id", "") or "").strip()
        name = str(getattr(item, "name", "") or voice_id or f"Voz {index}").strip()
        languages = getattr(item, "languages", []) or []
        rendered_languages = []
        for language in languages:
            if isinstance(language, bytes):
                rendered_languages.append(language.decode("utf-8", errors="ignore"))
            else:
                rendered_languages.append(str(language))
        rendered.append({
            "index": index,
            "id": voice_id,
            "voice_id": voice_id,
            "name": name,
            "languages": [item for item in rendered_languages if item],
            "provider": "system",
            "status": "system",
            "installed": True,
            "downloadable": False,
            "gender": str(getattr(item, "gender", "") or "").strip(),
            "age": str(getattr(item, "age", "") or "").strip(),
        })
    try:
        engine.stop()
    except Exception:
        pass
    return rendered


def list_tts_voices(
    settings: dict | None = None,
    *,
    include_downloadable: bool = False,
    language: str | None = None,
    refresh_catalog: bool = False,
) -> list[dict]:
    rendered = _list_system_voices(settings)
    rendered.extend(_list_kokoro_voices(language=language, start_index=len(rendered) + 1))
    return rendered


def find_tts_voice(selection: str, *, include_downloadable: bool = True, language: str | None = None) -> dict | None:
    target = str(selection or "").strip()
    if not target:
        return None
    voices = list_tts_voices(include_downloadable=include_downloadable, language=language)
    if target.isdigit():
        index = int(target)
        for item in voices:
            if int(item.get("index", 0) or 0) == index:
                return item
        return None
    normalized = target.lower()
    for item in voices:
        values = {
            str(item.get("id", "")).strip().lower(),
            str(item.get("voice_id", "")).strip().lower(),
            str(item.get("name", "")).strip().lower(),
        }
        if normalized in values:
            return item
    return None


def kokoro_catalog_text(language: str | None = "es", *, limit: int = 30) -> str:
    voices = [
        item for item in list_tts_voices(include_downloadable=True, language=language)
        if item.get("provider") == "kokoro"
    ]
    if not voices:
        return "No encontre voces Kokoro para ese idioma."
    lines = [f"Voces Kokoro ({language or 'all'}):"]
    for item in voices[:limit]:
        lines.append(f"{item.get('index')}. {item.get('name')}\n   {item.get('id')}")
    if len(voices) > limit:
        lines.append(f"... y {len(voices) - limit} mas. Usa /voz catalogo all para ver mas idiomas.")
    return "\n".join(lines)


def stop_speaking() -> bool:
    with _TTS_LOCK:
        engine = _TTS_ENGINE
    if engine is None:
        return False
    try:
        engine.stop()
    except Exception:
        return False
    return True


class _AudioPlayback:
    def __init__(self):
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()
        try:
            import winsound

            winsound.PlaySound(None, getattr(winsound, "SND_PURGE", 0))
        except Exception:
            pass


def _kokoro_speed_from_rate(rate) -> float:
    numeric_rate = max(MIN_VOICE_TTS_RATE, min(MAX_VOICE_TTS_RATE, _optional_int(rate, DEFAULT_VOICE_TTS_RATE)))
    return max(0.5, min(2.0, numeric_rate / DEFAULT_VOICE_TTS_RATE))


def _kokoro_voice_for_settings(settings: dict) -> str:
    return _safe_kokoro_voice_id(str(settings.get("kokoro_voice_id", DEFAULT_VOICE_KOKORO_VOICE_ID)).strip())


def _load_kokoro_pipeline(settings: dict):
    voice_id = _kokoro_voice_for_settings(settings)
    language = _kokoro_voice_language(voice_id)
    speed = _kokoro_speed_from_rate(settings.get("tts_rate"))
    key = (voice_id, language, speed)
    with _KOKORO_LOCK:
        cached = _KOKORO_PIPELINES.get(key)
        if cached is not None:
            return cached
        try:
            from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig
        except Exception as exc:
            raise VoiceError("Falta pykokoro. Instala dependencias con python -m pip install -r requirements.txt.") from exc
        try:
            pipeline = KokoroPipeline(
                PipelineConfig(
                    voice=voice_id,
                    generation=GenerationConfig(lang=language, speed=speed),
                )
            )
        except Exception as exc:
            raise VoiceError(f"No pude cargar Kokoro con la voz '{voice_id}': {exc}") from exc
        _KOKORO_PIPELINES.clear()
        _KOKORO_PIPELINES[key] = pipeline
        return pipeline


def _write_float_audio_wav(audio, sample_rate: int, wav_path: Path) -> None:
    try:
        import numpy as np
    except Exception as exc:
        raise VoiceError("Falta numpy para guardar audio Kokoro.") from exc
    samples = np.asarray(audio)
    if samples.size <= 0:
        raise VoiceError("Kokoro no genero audio.")
    if samples.ndim == 1:
        channels = 1
    elif samples.ndim == 2:
        channels = int(samples.shape[1])
        samples = samples.reshape(-1)
    else:
        samples = samples.reshape(-1)
        channels = 1
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(max(1, channels))
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate) or 24000)
        wav_file.writeframes(pcm.tobytes())


def _settings_tts_provider(settings: dict) -> str:
    provider = str(settings.get("tts_provider", DEFAULT_VOICE_TTS_PROVIDER)).strip().lower()
    if provider == "sistema":
        provider = "system"
    if provider not in VALID_VOICE_TTS_PROVIDERS:
        provider = DEFAULT_VOICE_TTS_PROVIDER
    return provider


def _synthesize_system_wav(cleaned_text: str, wav_path: Path, settings: dict) -> None:
    engine = _tts_engine(settings)
    try:
        engine.save_to_file(cleaned_text, str(wav_path))
        engine.runAndWait()
    except Exception as exc:
        cleanup_voice_file(wav_path)
        raise VoiceError(f"No pude sintetizar la voz: {exc}") from exc


def _synthesize_kokoro_wav(cleaned_text: str, wav_path: Path, settings: dict) -> None:
    pipeline = _load_kokoro_pipeline(settings)
    try:
        result = pipeline.run(cleaned_text)
        _write_float_audio_wav(result.audio, int(result.sample_rate), wav_path)
    except Exception as exc:
        cleanup_voice_file(wav_path)
        raise VoiceError(f"No pude sintetizar la voz Kokoro: {exc}") from exc


def _synthesize_wav_file(cleaned_text: str, settings: dict) -> Path:
    VOICE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    token = f"{time.time_ns()}-{threading.get_ident()}"
    wav_path = VOICE_RUNTIME_DIR / f"tts-{token}.wav"
    if _settings_tts_provider(settings) == "kokoro":
        _synthesize_kokoro_wav(cleaned_text, wav_path, settings)
    else:
        _synthesize_system_wav(cleaned_text, wav_path, settings)
    if not wav_path.exists() or wav_path.stat().st_size <= 0:
        cleanup_voice_file(wav_path)
        raise VoiceError("La voz no genero audio.")
    return wav_path


def _wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav_file:
            rate = wav_file.getframerate() or 1
            return wav_file.getnframes() / rate
    except Exception:
        return 0.0


def speak_text(text: str, settings: dict | None = None, cancellable: bool = True) -> None:
    global _TTS_ENGINE
    voice_settings = ensure_voice_enabled(settings)
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        raise VoiceError("No hay texto para leer.")
    if _settings_tts_provider(voice_settings) == "kokoro":
        playback = _AudioPlayback()
        wav_path = _synthesize_wav_file(cleaned_text, voice_settings)
        if cancellable:
            with _TTS_LOCK:
                _TTS_ENGINE = playback
        try:
            try:
                import winsound
            except Exception as exc:
                raise VoiceError("No pude reproducir la voz Kokoro en este sistema.") from exc
            winsound.PlaySound(str(wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            deadline = time.monotonic() + max(0.1, _wav_duration_seconds(wav_path) + 0.25)
            while time.monotonic() < deadline and not playback.stop_event.wait(0.05):
                pass
            winsound.PlaySound(None, getattr(winsound, "SND_PURGE", 0))
        finally:
            cleanup_voice_file(wav_path)
            if cancellable:
                with _TTS_LOCK:
                    if _TTS_ENGINE is playback:
                        _TTS_ENGINE = None
        return

    engine = _tts_engine(voice_settings)
    if cancellable:
        with _TTS_LOCK:
            _TTS_ENGINE = engine
    try:
        engine.say(cleaned_text)
        engine.runAndWait()
    finally:
        try:
            engine.stop()
        except Exception:
            pass
        if cancellable:
            with _TTS_LOCK:
                if _TTS_ENGINE is engine:
                    _TTS_ENGINE = None


def _optional_int(value, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def update_voice_settings_text(
    *,
    enabled: bool | None = None,
    tts_provider: str | None = None,
    tts_voice_id: str | None = None,
    tts_rate=None,
    kokoro_voice_id: str | None = None,
    browser_voice_name: str | None = None,
    browser_tts_rate=None,
    browser_tts_pitch=None,
    telegram_reply_mode: str | None = None,
) -> str:
    current = get_voice_settings()
    next_provider = str(tts_provider if tts_provider is not None else current.get("tts_provider", "system")).strip().lower()
    if next_provider == "sistema":
        next_provider = "system"
    if next_provider not in VALID_VOICE_TTS_PROVIDERS:
        raise ValueError("Proveedor de voz invalido. Usa system o kokoro.")
    next_tts_rate = max(
        MIN_VOICE_TTS_RATE,
        min(MAX_VOICE_TTS_RATE, _optional_int(tts_rate, int(current.get("tts_rate", DEFAULT_VOICE_TTS_RATE)))),
    )
    next_kokoro_voice_id = str(
        kokoro_voice_id if kokoro_voice_id is not None else current.get("kokoro_voice_id", DEFAULT_VOICE_KOKORO_VOICE_ID)
    ).strip()
    next_kokoro_voice_id = _safe_kokoro_voice_id(next_kokoro_voice_id)
    next_browser_rate = max(
        MIN_VOICE_BROWSER_TTS_RATE,
        min(
            MAX_VOICE_BROWSER_TTS_RATE,
            _optional_float(browser_tts_rate, float(current.get("browser_tts_rate", DEFAULT_VOICE_BROWSER_TTS_RATE))),
        ),
    )
    next_browser_pitch = max(
        MIN_VOICE_BROWSER_TTS_PITCH,
        min(
            MAX_VOICE_BROWSER_TTS_PITCH,
            _optional_float(browser_tts_pitch, float(current.get("browser_tts_pitch", DEFAULT_VOICE_BROWSER_TTS_PITCH))),
        ),
    )
    next_reply_mode = str(
        telegram_reply_mode if telegram_reply_mode is not None else current.get("telegram_reply_mode", "auto")
    ).strip().lower()
    if next_reply_mode not in VALID_VOICE_TELEGRAM_REPLY_MODES:
        raise ValueError("Modo Telegram de voz invalido. Usa off, auto o always.")

    def mutate(state):
        voice = state.setdefault("voice", {})
        if enabled is not None:
            voice["enabled"] = bool(enabled)
        voice["tts_provider"] = next_provider
        if tts_voice_id is not None:
            voice["tts_voice_id"] = str(tts_voice_id).strip()
        voice["kokoro_voice_id"] = next_kokoro_voice_id
        voice["tts_rate"] = next_tts_rate
        if browser_voice_name is not None:
            voice["browser_voice_name"] = str(browser_voice_name).strip()
        voice["browser_tts_rate"] = next_browser_rate
        voice["browser_tts_pitch"] = next_browser_pitch
        voice["telegram_reply_mode"] = next_reply_mode

    state_transaction("update_voice_settings", mutate)
    return (
        "Voz actualizada: "
        f"{'activa' if (bool(enabled) if enabled is not None else current.get('enabled', True)) else 'desactivada'}, "
        f"proveedor={next_provider}, "
        f"sistema={'predeterminada' if not str(tts_voice_id if tts_voice_id is not None else current.get('tts_voice_id', '')).strip() else 'personalizada'}, "
        f"kokoro={next_kokoro_voice_id}, "
        f"velocidad={next_tts_rate}, navegador={next_browser_rate:g}/{next_browser_pitch:g}, "
        f"Telegram={next_reply_mode}."
    )


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
    ogg_path = VOICE_RUNTIME_DIR / f"tts-{token}.ogg"
    wav_path = _synthesize_wav_file(cleaned_text, voice_settings)

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
