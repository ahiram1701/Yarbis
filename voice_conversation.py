import threading
import time
import uuid

import conversation_ux
import voice as yarbis_voice
from memory import load_state, normalize_state


STATE_IDLE = "idle"
STATE_WAKE_LISTENING = "wake_listening"
STATE_CAPTURING = "capturing"
STATE_TRANSCRIBING = "transcribing"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_INTERRUPTED = "interrupted"
STATE_ERROR = "error"

_STATUS_LOCK = threading.RLock()
_DESKTOP_STATUS = {
    "state": STATE_IDLE,
    "detail": "",
    "last_transcript": "",
    "last_reply": "",
    "updated_at": "",
}
_MOBILE_LOCK = threading.RLock()
_MOBILE_SESSIONS: dict[str, dict] = {}
_MOBILE_SESSION_LIMIT = 8
_MOBILE_SESSION_TTL_SECONDS = 30 * 60


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_for_match(text: str) -> str:
    rendered = str(text or "").lower()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
        "ñ": "n",
    }
    for source, target in replacements.items():
        rendered = rendered.replace(source, target)
    return " ".join(rendered.split())


def live_voice_settings(settings: dict | None = None) -> dict:
    state_or_voice = load_state() if settings is None else settings
    voice_settings = (
        normalize_state(state_or_voice).get("voice", {})
        if isinstance(state_or_voice, dict) and "voice" in state_or_voice
        else normalize_state({"voice": state_or_voice if isinstance(state_or_voice, dict) else {}}).get("voice", {})
    )
    live = voice_settings.get("live_conversation", {})
    if not isinstance(live, dict):
        live = {}
    return {
        **live,
        "voice_enabled": bool(voice_settings.get("enabled", True)),
        "language": voice_settings.get("language", "es"),
        "stt_model": voice_settings.get("stt_model", "base"),
        "stt_compute_type": voice_settings.get("stt_compute_type", "int8"),
        "tts_provider": voice_settings.get("tts_provider", "system"),
    }


def wake_phrase_detected(text: str, wake_phrase: str = "Yarbis") -> bool:
    normalized_text = _normalize_for_match(text)
    normalized_wake = _normalize_for_match(wake_phrase or "Yarbis")
    return bool(normalized_wake and normalized_wake in normalized_text)


def text_after_wake_phrase(text: str, wake_phrase: str = "Yarbis") -> str:
    cleaned = str(text or "").strip()
    normalized_wake = _normalize_for_match(wake_phrase or "Yarbis")
    if not cleaned or not normalized_wake:
        return cleaned
    words = cleaned.split()
    normalized_words = [_normalize_for_match(word.strip(" ,.:;!?")) for word in words]
    wake_parts = normalized_wake.split()
    for index in range(0, len(normalized_words) - len(wake_parts) + 1):
        if normalized_words[index:index + len(wake_parts)] == wake_parts:
            return " ".join(words[index + len(wake_parts):]).strip(" ,.:;!?")
    return cleaned


def desktop_status() -> dict:
    with _STATUS_LOCK:
        return dict(_DESKTOP_STATUS)


def _set_desktop_status(state: str, detail: str = "", **extra) -> dict:
    with _STATUS_LOCK:
        _DESKTOP_STATUS.update({
            "state": state,
            "detail": str(detail or "").strip(),
            "updated_at": _utc_now_text(),
            **{key: value for key, value in extra.items() if value is not None},
        })
        return dict(_DESKTOP_STATUS)


def reset_desktop_status() -> dict:
    return _set_desktop_status(
        STATE_IDLE,
        "",
        last_transcript="",
        last_reply="",
    )


def _stt_settings(base_settings: dict, model: str = "") -> dict:
    voice_settings = yarbis_voice.get_voice_settings(base_settings)
    if str(model or "").strip():
        voice_settings["stt_model"] = str(model).strip()
    return voice_settings


def transcribe_live_audio_file(path, *, settings: dict | None = None, wake: bool = False) -> str:
    live = live_voice_settings(settings)
    model = live.get("wake_stt_model") if wake else live.get("turn_stt_model")
    return yarbis_voice.transcribe_audio_file(path, settings=_stt_settings(settings or load_state(), str(model or "")))


def transcribe_live_audio_bytes(raw_audio: bytes, *, mime_type: str = "", settings: dict | None = None) -> str:
    live = live_voice_settings(settings)
    return yarbis_voice.transcribe_audio_bytes(
        raw_audio,
        mime_type=mime_type,
        settings=_stt_settings(settings or load_state(), str(live.get("wake_stt_model", ""))),
    )


def process_voice_turn(
    transcript: str,
    *,
    source: str = "voice_live",
    settings: dict | None = None,
    speak: bool = True,
    runner=None,
    speaker=None,
) -> dict:
    current_settings = settings or load_state()
    live = live_voice_settings(current_settings)
    wake_phrase = str(live.get("wake_phrase", "Yarbis")).strip() or "Yarbis"
    cleaned_turn = text_after_wake_phrase(transcript, wake_phrase).strip()
    if not cleaned_turn:
        return {
            "state": STATE_WAKE_LISTENING,
            "transcript": str(transcript or "").strip(),
            "reply": "",
            "spoken_text": "Te escucho. Di lo que necesitas después de Yarbis.",
        }

    if runner is None:
        from session import submit_user_reply

        runner = submit_user_reply
    if speaker is None:
        speaker = yarbis_voice.speak_text

    result = runner(cleaned_turn, emit_notifications=False, blocking=True)
    spoken_text = conversation_ux.spoken_reply_text(result)
    if speak and spoken_text and live.get("auto_speak", True):
        speaker(spoken_text, settings=current_settings, cancellable=True)
    return {
        "state": STATE_SPEAKING if spoken_text else STATE_WAKE_LISTENING,
        "transcript": cleaned_turn,
        "reply": str(result or "").strip(),
        "spoken_text": spoken_text,
        "source": source,
    }


def run_desktop_live_conversation(
    stop_event: threading.Event,
    *,
    settings: dict | None = None,
    status_callback=None,
    runner=None,
    speaker=None,
) -> dict:
    current_settings = settings or load_state()
    live = live_voice_settings(current_settings)
    wake_phrase = str(live.get("wake_phrase", "Yarbis")).strip() or "Yarbis"
    if not live.get("voice_enabled", True):
        raise yarbis_voice.VoiceError("La voz esta desactivada.")
    if not live.get("enabled", True):
        raise yarbis_voice.VoiceError("La conversación en vivo está desactivada.")
    if "desktop" not in live.get("surfaces", ["desktop", "mobile"]):
        raise yarbis_voice.VoiceError("La conversación en vivo no está habilitada para escritorio.")

    def update(state: str, detail: str = "", **extra):
        snapshot = _set_desktop_status(state, detail, **extra)
        if status_callback is not None:
            status_callback(snapshot)
        return snapshot

    update(STATE_WAKE_LISTENING, f"Di '{wake_phrase}' para hablar.")
    last_result = {}
    while not stop_event.is_set():
        audio_path = None
        try:
            update(STATE_CAPTURING, "Escuchando un turno de voz.")
            audio_path = yarbis_voice.record_microphone_until_silence(
                stop_event,
                settings=current_settings,
                silence_ms=int(live.get("silence_ms", 900)),
                max_seconds=int(live.get("max_turn_seconds", 45)),
            )
            if stop_event.is_set():
                break
            update(STATE_TRANSCRIBING, "Transcribiendo voz local.")
            transcript = transcribe_live_audio_file(audio_path, settings=current_settings, wake=True)
            update(STATE_WAKE_LISTENING, f"Escuché: {transcript}", last_transcript=transcript)
            if not wake_phrase_detected(transcript, wake_phrase):
                continue
            update(STATE_THINKING, "Yarbis está preparando una respuesta.", last_transcript=transcript)
            last_result = process_voice_turn(
                transcript,
                source="desktop_voice_live",
                settings=current_settings,
                speak=bool(live.get("auto_speak", True)),
                runner=runner,
                speaker=speaker,
            )
            update(
                STATE_SPEAKING if last_result.get("spoken_text") else STATE_WAKE_LISTENING,
                "Respuesta hablada lista." if last_result.get("spoken_text") else "Respuesta lista.",
                last_transcript=last_result.get("transcript", transcript),
                last_reply=last_result.get("spoken_text") or last_result.get("reply", ""),
            )
        except yarbis_voice.VoiceError as exc:
            update(STATE_ERROR, str(exc))
            if "No capture voz clara" not in str(exc):
                time.sleep(0.35)
        finally:
            if audio_path and not live.get("save_audio_debug", False):
                yarbis_voice.cleanup_voice_file(audio_path)
        if not stop_event.wait(0.1):
            update(STATE_WAKE_LISTENING, f"Di '{wake_phrase}' para hablar.")
    update(STATE_IDLE, "Conversación en vivo detenida.")
    return last_result


def _prune_mobile_sessions_unlocked() -> None:
    now = time.time()
    expired = [
        session_id
        for session_id, session in _MOBILE_SESSIONS.items()
        if now - float(session.get("updated_monotonic", now)) > _MOBILE_SESSION_TTL_SECONDS
    ]
    for session_id in expired:
        _MOBILE_SESSIONS.pop(session_id, None)
    while len(_MOBILE_SESSIONS) > _MOBILE_SESSION_LIMIT:
        oldest = min(_MOBILE_SESSIONS, key=lambda item: _MOBILE_SESSIONS[item].get("updated_monotonic", 0))
        _MOBILE_SESSIONS.pop(oldest, None)


def start_mobile_session(settings: dict | None = None) -> dict:
    current_settings = settings or load_state()
    live = live_voice_settings(current_settings)
    wake_phrase = str(live.get("wake_phrase", "Yarbis")).strip() or "Yarbis"
    if not live.get("enabled", True):
        raise yarbis_voice.VoiceError("La conversación en vivo está desactivada.")
    if "mobile" not in live.get("surfaces", ["desktop", "mobile"]):
        raise yarbis_voice.VoiceError("La conversación en vivo no está habilitada para móvil.")
    session_id = uuid.uuid4().hex
    session = {
        "id": session_id,
        "state": STATE_WAKE_LISTENING,
        "detail": f"Di '{wake_phrase}' para hablar.",
        "wake_phrase": wake_phrase,
        "last_transcript": "",
        "last_reply": "",
        "spoken_text": "",
        "created_at": _utc_now_text(),
        "updated_at": _utc_now_text(),
        "updated_monotonic": time.time(),
    }
    with _MOBILE_LOCK:
        _prune_mobile_sessions_unlocked()
        _MOBILE_SESSIONS[session_id] = session
    return dict(session)


def mobile_session_status(session_id: str) -> dict | None:
    with _MOBILE_LOCK:
        session = _MOBILE_SESSIONS.get(str(session_id or "").strip())
        return dict(session) if session else None


def stop_mobile_session(session_id: str) -> dict:
    with _MOBILE_LOCK:
        session = _MOBILE_SESSIONS.pop(str(session_id or "").strip(), None)
    if not session:
        return {"id": str(session_id or "").strip(), "state": STATE_IDLE, "detail": "Sesión de voz detenida."}
    session["state"] = STATE_IDLE
    session["detail"] = "Sesión de voz detenida."
    session["updated_at"] = _utc_now_text()
    return dict(session)


def append_mobile_audio_chunk(
    session_id: str,
    raw_audio: bytes,
    *,
    mime_type: str = "",
    settings: dict | None = None,
    runner=None,
) -> dict:
    current_settings = settings or load_state()
    with _MOBILE_LOCK:
        session = _MOBILE_SESSIONS.get(str(session_id or "").strip())
        if not session:
            raise yarbis_voice.VoiceError("Sesión de voz en vivo no encontrada.")
        session["state"] = STATE_TRANSCRIBING
        session["detail"] = "Transcribiendo voz local."
        session["updated_at"] = _utc_now_text()
        session["updated_monotonic"] = time.time()

    try:
        transcript = transcribe_live_audio_bytes(raw_audio, mime_type=mime_type, settings=current_settings)
    except yarbis_voice.VoiceError as exc:
        with _MOBILE_LOCK:
            session = _MOBILE_SESSIONS[str(session_id).strip()]
            session["state"] = STATE_WAKE_LISTENING
            session["detail"] = str(exc)
            session["updated_at"] = _utc_now_text()
            session["updated_monotonic"] = time.time()
            return dict(session)

    live = live_voice_settings(current_settings)
    wake_phrase = str(live.get("wake_phrase", "Yarbis")).strip() or "Yarbis"
    with _MOBILE_LOCK:
        session = _MOBILE_SESSIONS[str(session_id).strip()]
        session["last_transcript"] = transcript
        session["spoken_text"] = ""
        session["updated_at"] = _utc_now_text()
        session["updated_monotonic"] = time.time()

    if not wake_phrase_detected(transcript, wake_phrase):
        with _MOBILE_LOCK:
            session["state"] = STATE_WAKE_LISTENING
            session["detail"] = f"Escuché voz, pero no la frase '{wake_phrase}'."
            return dict(session)

    with _MOBILE_LOCK:
        session["state"] = STATE_THINKING
        session["detail"] = "Yarbis está preparando una respuesta."

    result = process_voice_turn(
        transcript,
        source="mobile_voice_live",
        settings=current_settings,
        speak=False,
        runner=runner,
    )
    with _MOBILE_LOCK:
        session = _MOBILE_SESSIONS[str(session_id).strip()]
        session["state"] = STATE_SPEAKING if result.get("spoken_text") else STATE_WAKE_LISTENING
        session["detail"] = "Respuesta lista para escuchar." if result.get("spoken_text") else "Respuesta lista."
        session["last_transcript"] = result.get("transcript", transcript)
        session["last_reply"] = result.get("reply", "")
        session["spoken_text"] = result.get("spoken_text", "")
        session["updated_at"] = _utc_now_text()
        session["updated_monotonic"] = time.time()
        return dict(session)
