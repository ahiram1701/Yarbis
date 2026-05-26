import base64
import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

from memory import (
    DEFAULT_VOICE_MAX_AUDIO_SECONDS,
    DEFAULT_VOICE_BROWSER_TTS_PITCH,
    DEFAULT_VOICE_BROWSER_TTS_RATE,
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
PIPER_RUNTIME_DIR = VOICE_RUNTIME_DIR / "piper"
PIPER_VOICES_DIR = PIPER_RUNTIME_DIR / "voices"
PIPER_CATALOG_PATH = PIPER_RUNTIME_DIR / "voices.json"
PIPER_CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"
PIPER_DOWNLOAD_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
MAX_VOICE_AUDIO_BYTES = 20 * 1024 * 1024
MAX_PIPER_CATALOG_BYTES = 8 * 1024 * 1024
MAX_PIPER_MODEL_BYTES = 512 * 1024 * 1024
MAX_PIPER_CONFIG_BYTES = 4 * 1024 * 1024
TELEGRAM_AUTO_VOICE_REPLY_MAX_CHARS = 800
DEFAULT_TTS_VOLUME = 1.0
DEFAULT_RECORD_SAMPLE_RATE = 16_000
DEFAULT_RECORD_CHANNELS = 1
_WHISPER_LOCK = threading.RLock()
_WHISPER_MODELS = {}
_TTS_LOCK = threading.RLock()
_TTS_ENGINE = None
_PIPER_LOCK = threading.RLock()
_PIPER_VOICES = {}


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


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_piper_voice_id(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or any(part in cleaned for part in ("..", "/", "\\")):
        raise VoiceError("ID de voz Piper invalido.")
    return cleaned


def _piper_voice_dir(voice_id: str) -> Path:
    return PIPER_VOICES_DIR / _safe_piper_voice_id(voice_id)


def _load_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def _download_to_file(url: str, target_path: Path, *, max_bytes: int, expected_md5: str = "") -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".part")
    if temp_path.exists():
        temp_path.unlink()
    digest = hashlib.md5() if expected_md5 else None
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise VoiceError("La descarga de voz excede el limite permitido.")
            with temp_path.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise VoiceError("La descarga de voz excede el limite permitido.")
                    if digest is not None:
                        digest.update(chunk)
                    file.write(chunk)
        if total <= 0:
            raise VoiceError("La descarga de voz llego vacia.")
        if digest is not None and digest.hexdigest().lower() != expected_md5.lower():
            raise VoiceError("La descarga de voz no coincide con su checksum.")
        os.replace(temp_path, target_path)
    except Exception:
        with contextlib.suppress(Exception):
            temp_path.unlink()
        raise


def _download_piper_catalog() -> dict:
    _download_to_file(
        PIPER_CATALOG_URL,
        PIPER_CATALOG_PATH,
        max_bytes=MAX_PIPER_CATALOG_BYTES,
    )
    catalog = _load_json_file(PIPER_CATALOG_PATH)
    timestamp = _utc_now_text()

    def mutate(state):
        voice = state.setdefault("voice", {})
        voice["piper_catalog_updated_at"] = timestamp

    state_transaction("update_piper_catalog_timestamp", mutate)
    return catalog


def _load_piper_catalog(*, refresh: bool = False, allow_download: bool = False) -> dict:
    with _PIPER_LOCK:
        if refresh or (allow_download and not PIPER_CATALOG_PATH.exists()):
            return _download_piper_catalog()
        if not PIPER_CATALOG_PATH.exists():
            return {}
        try:
            return _load_json_file(PIPER_CATALOG_PATH)
        except Exception as exc:
            if allow_download:
                return _download_piper_catalog()
            raise VoiceError(f"No pude leer el catalogo Piper local: {exc}") from exc


def refresh_piper_catalog() -> dict:
    return _load_piper_catalog(refresh=True, allow_download=True)


def _language_matches(entry: dict, language: str | None) -> bool:
    cleaned = str(language or "").strip().lower()
    if not cleaned or cleaned == "all":
        return True
    info = entry.get("language", {}) if isinstance(entry.get("language"), dict) else {}
    code = str(info.get("code", "")).strip().lower()
    family = str(info.get("family", "")).strip().lower()
    return cleaned in {code, family} or code.startswith(cleaned + "_")


def _piper_model_files(entry: dict) -> tuple[str, dict, str, dict]:
    files = entry.get("files", {}) if isinstance(entry.get("files"), dict) else {}
    model_path = ""
    config_path = ""
    for path in files:
        if str(path).endswith(".onnx"):
            model_path = str(path)
        elif str(path).endswith(".onnx.json"):
            config_path = str(path)
    if not model_path or not config_path:
        raise VoiceError("La voz Piper no tiene archivos .onnx completos.")
    return model_path, files.get(model_path, {}), config_path, files.get(config_path, {})


def _piper_local_paths(voice_id: str, entry: dict | None = None) -> tuple[Path, Path]:
    voice_dir = _piper_voice_dir(voice_id)
    if entry:
        model_path, _model_meta, config_path, _config_meta = _piper_model_files(entry)
        return voice_dir / Path(model_path).name, voice_dir / Path(config_path).name
    models = sorted(voice_dir.glob("*.onnx"))
    configs = sorted(voice_dir.glob("*.onnx.json"))
    if models and configs:
        return models[0], configs[0]
    return voice_dir / f"{voice_id}.onnx", voice_dir / f"{voice_id}.onnx.json"


def _piper_voice_installed(voice_id: str, entry: dict | None = None) -> bool:
    model_path, config_path = _piper_local_paths(voice_id, entry)
    return model_path.exists() and config_path.exists() and model_path.stat().st_size > 0 and config_path.stat().st_size > 0


def _installed_piper_voice_ids() -> set[str]:
    if not PIPER_VOICES_DIR.exists():
        return set()
    return {
        item.name
        for item in PIPER_VOICES_DIR.iterdir()
        if item.is_dir() and list(item.glob("*.onnx")) and list(item.glob("*.onnx.json"))
    }


def _piper_voice_label(entry: dict) -> str:
    info = entry.get("language", {}) if isinstance(entry.get("language"), dict) else {}
    language_code = str(info.get("code", "")).strip()
    name = str(entry.get("name", "")).strip() or str(entry.get("key", "")).strip()
    quality = str(entry.get("quality", "")).strip()
    parts = [part for part in (language_code, name, quality) if part]
    return " - ".join(parts) or str(entry.get("key", "Voz Piper"))


def _render_piper_voice(voice_id: str, entry: dict | None, *, index: int, installed: bool) -> dict:
    info = entry.get("language", {}) if isinstance(entry, dict) and isinstance(entry.get("language"), dict) else {}
    languages = [str(info.get("code", "")).strip()] if info.get("code") else []
    return {
        "index": index,
        "id": voice_id,
        "voice_id": voice_id,
        "name": _piper_voice_label(entry or {"key": voice_id}),
        "languages": [item for item in languages if item],
        "provider": "piper",
        "status": "installed" if installed else "downloadable",
        "installed": installed,
        "downloadable": not installed,
        "quality": str((entry or {}).get("quality", "") or "").strip(),
        "num_speakers": int((entry or {}).get("num_speakers", 1) or 1),
    }


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
    installed_ids = _installed_piper_voice_ids()
    catalog = _load_piper_catalog(
        refresh=refresh_catalog,
        allow_download=include_downloadable or refresh_catalog,
    )

    next_index = len(rendered) + 1
    used_piper_ids = set()
    for voice_id, entry in catalog.items():
        if not isinstance(entry, dict) or not _language_matches(entry, language):
            continue
        installed = voice_id in installed_ids or _piper_voice_installed(voice_id, entry)
        if not installed and not include_downloadable:
            continue
        rendered.append(_render_piper_voice(voice_id, entry, index=next_index, installed=installed))
        next_index += 1
        used_piper_ids.add(voice_id)

    for voice_id in sorted(installed_ids - used_piper_ids):
        rendered.append(_render_piper_voice(voice_id, None, index=next_index, installed=True))
        next_index += 1

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


def piper_catalog_text(language: str | None = "es", *, limit: int = 30) -> str:
    voices = [
        item for item in list_tts_voices(include_downloadable=True, language=language)
        if item.get("provider") == "piper"
    ]
    if not voices:
        return "No encontre voces Piper en ese catalogo."
    lines = [f"Catalogo Piper ({language or 'all'}):"]
    for item in voices[:limit]:
        status = "instalada" if item.get("installed") else "descargable"
        lines.append(f"{item.get('index')}. {item.get('name')} - {status}\n   {item.get('id')}")
    if len(voices) > limit:
        lines.append(f"... y {len(voices) - limit} mas. Usa /voz catalogo all para ver mas idiomas.")
    return "\n".join(lines)


def download_piper_voice(voice_id: str) -> dict:
    voice_id = _safe_piper_voice_id(voice_id)
    catalog = _load_piper_catalog(allow_download=True)
    entry = catalog.get(voice_id)
    if not isinstance(entry, dict):
        raise VoiceError(f"No encontre la voz Piper '{voice_id}' en el catalogo.")

    model_remote, model_meta, config_remote, config_meta = _piper_model_files(entry)
    model_path, config_path = _piper_local_paths(voice_id, entry)
    if _piper_voice_installed(voice_id, entry):
        return _render_piper_voice(voice_id, entry, index=0, installed=True)

    model_size = int(model_meta.get("size_bytes", 0) or 0)
    config_size = int(config_meta.get("size_bytes", 0) or 0)
    if model_size > MAX_PIPER_MODEL_BYTES or config_size > MAX_PIPER_CONFIG_BYTES:
        raise VoiceError("La voz Piper excede el limite de descarga permitido.")

    _download_to_file(
        PIPER_DOWNLOAD_BASE_URL + model_remote,
        model_path,
        max_bytes=MAX_PIPER_MODEL_BYTES,
        expected_md5=str(model_meta.get("md5_digest", "") or ""),
    )
    try:
        _download_to_file(
            PIPER_DOWNLOAD_BASE_URL + config_remote,
            config_path,
            max_bytes=MAX_PIPER_CONFIG_BYTES,
            expected_md5=str(config_meta.get("md5_digest", "") or ""),
        )
    except Exception:
        cleanup_voice_file(model_path)
        raise
    return _render_piper_voice(voice_id, entry, index=0, installed=True)


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


class _PiperPlayback:
    def __init__(self):
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()
        try:
            import winsound

            winsound.PlaySound(None, getattr(winsound, "SND_PURGE", 0))
        except Exception:
            pass


def _settings_tts_provider(settings: dict) -> str:
    provider = str(settings.get("tts_provider", DEFAULT_VOICE_TTS_PROVIDER)).strip().lower()
    if provider == "sistema":
        provider = "system"
    if provider not in VALID_VOICE_TTS_PROVIDERS:
        provider = DEFAULT_VOICE_TTS_PROVIDER
    return provider


def _load_piper_voice_for_settings(settings: dict):
    voice_id = str(settings.get("piper_voice_id", "")).strip()
    if not voice_id:
        raise VoiceError("Elige una voz Piper antes de hablar con Piper.")
    voice_id = _safe_piper_voice_id(voice_id)
    catalog = _load_piper_catalog(allow_download=True)
    entry = catalog.get(voice_id) if isinstance(catalog.get(voice_id), dict) else None
    if not _piper_voice_installed(voice_id, entry):
        download_piper_voice(voice_id)
    model_path, config_path = _piper_local_paths(voice_id, entry)
    key = (voice_id, str(model_path), model_path.stat().st_mtime_ns, config_path.stat().st_mtime_ns)
    with _PIPER_LOCK:
        cached = _PIPER_VOICES.get(key)
        if cached is not None:
            return cached
        try:
            from piper import PiperVoice
        except Exception as exc:
            raise VoiceError("Falta piper-tts. Instala dependencias con python -m pip install -r requirements.txt.") from exc
        try:
            loaded = PiperVoice.load(str(model_path), config_path=str(config_path))
        except Exception as exc:
            raise VoiceError(f"No pude cargar la voz Piper '{voice_id}': {exc}") from exc
        _PIPER_VOICES.clear()
        _PIPER_VOICES[key] = loaded
        return loaded


def _piper_syn_config(settings: dict):
    try:
        from piper import SynthesisConfig
    except Exception as exc:
        raise VoiceError("Falta piper-tts. Instala dependencias con python -m pip install -r requirements.txt.") from exc
    rate = max(MIN_VOICE_TTS_RATE, min(MAX_VOICE_TTS_RATE, _optional_int(settings.get("tts_rate"), DEFAULT_VOICE_TTS_RATE)))
    length_scale = max(0.5, min(2.0, DEFAULT_VOICE_TTS_RATE / max(rate, 1)))
    return SynthesisConfig(
        speaker_id=max(0, _optional_int(settings.get("piper_speaker_id"), 0)),
        length_scale=length_scale,
        volume=DEFAULT_TTS_VOLUME,
    )


def _synthesize_system_wav(cleaned_text: str, wav_path: Path, settings: dict) -> None:
    engine = _tts_engine(settings)
    try:
        engine.save_to_file(cleaned_text, str(wav_path))
        engine.runAndWait()
    except Exception as exc:
        cleanup_voice_file(wav_path)
        raise VoiceError(f"No pude sintetizar la voz: {exc}") from exc


def _synthesize_piper_wav(cleaned_text: str, wav_path: Path, settings: dict) -> None:
    piper_voice = _load_piper_voice_for_settings(settings)
    try:
        with wave.open(str(wav_path), "wb") as wav_file:
            piper_voice.synthesize_wav(cleaned_text, wav_file, syn_config=_piper_syn_config(settings))
    except Exception as exc:
        cleanup_voice_file(wav_path)
        raise VoiceError(f"No pude sintetizar la voz Piper: {exc}") from exc


def _synthesize_wav_file(cleaned_text: str, settings: dict) -> Path:
    VOICE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    token = f"{time.time_ns()}-{threading.get_ident()}"
    wav_path = VOICE_RUNTIME_DIR / f"tts-{token}.wav"
    if _settings_tts_provider(settings) == "piper":
        _synthesize_piper_wav(cleaned_text, wav_path, settings)
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
    if _settings_tts_provider(voice_settings) == "piper":
        playback = _PiperPlayback()
        wav_path = _synthesize_wav_file(cleaned_text, voice_settings)
        if cancellable:
            with _TTS_LOCK:
                _TTS_ENGINE = playback
        try:
            try:
                import winsound
            except Exception as exc:
                raise VoiceError("No pude reproducir la voz Piper en este sistema.") from exc
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
    piper_voice_id: str | None = None,
    piper_speaker_id=None,
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
        raise ValueError("Proveedor de voz invalido. Usa system o piper.")
    next_tts_rate = max(
        MIN_VOICE_TTS_RATE,
        min(MAX_VOICE_TTS_RATE, _optional_int(tts_rate, int(current.get("tts_rate", DEFAULT_VOICE_TTS_RATE)))),
    )
    next_piper_voice_id = str(
        piper_voice_id if piper_voice_id is not None else current.get("piper_voice_id", "")
    ).strip()
    if next_piper_voice_id:
        _safe_piper_voice_id(next_piper_voice_id)
    next_piper_speaker_id = max(
        0,
        min(9999, _optional_int(piper_speaker_id, int(current.get("piper_speaker_id", 0) or 0))),
    )
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
        voice["piper_voice_id"] = next_piper_voice_id
        voice["piper_speaker_id"] = next_piper_speaker_id
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
        f"piper={next_piper_voice_id or 'sin voz'}, "
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
