import re


MAX_TIMELINE_TEXT_CHARS = 1400
MAX_SPOKEN_REPLY_CHARS = 1800


def _clean_text(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _compact_text(value: object, max_chars: int = MAX_TIMELINE_TEXT_CHARS) -> str:
    text = _clean_text(value)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."


def _open_task_count(state: dict) -> int:
    tasks = state.get("tasks", [])
    if not isinstance(tasks, list):
        return 0
    return sum(
        1
        for task in tasks
        if isinstance(task, dict) and task.get("status") in {"pending", "in_progress", "blocked"}
    )


def _pending_input(state: dict) -> dict:
    awaiting = state.get("awaiting_user_input", {})
    if not isinstance(awaiting, dict):
        awaiting = {}
    question = _clean_text(awaiting.get("question", ""))
    active = bool(awaiting.get("pending") and question)
    fields = awaiting.get("fields", [])
    if not isinstance(fields, list):
        fields = []
    return {
        "active": active,
        "question": question if active else "",
        "reason": _clean_text(awaiting.get("reason", "")) if active else "",
        "fields": [str(item).strip() for item in fields if str(item).strip()] if active else [],
    }


def _operation_status(state: dict) -> dict:
    runtime = state.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
    thinking = runtime.get("thinking", {})
    if not isinstance(thinking, dict):
        thinking = {}
    active = bool(thinking.get("active") and _clean_text(thinking.get("label", "")))
    return {
        "active": active,
        "label": _clean_text(thinking.get("label", "")) if active else "",
        "operation_id": _clean_text(thinking.get("operation_id", "")) if active else "",
        "started_at": _clean_text(thinking.get("started_at", "")) if active else "",
    }


def _voice_settings(state: dict, voice_status: dict | None = None) -> dict:
    voice = state.get("voice", {})
    if not isinstance(voice, dict):
        voice = {}
    live = voice.get("live_conversation", {})
    if not isinstance(live, dict):
        live = {}
    status = voice_status if isinstance(voice_status, dict) else {}
    status_state = _clean_text(status.get("state", "")) or "idle"
    wake_phrase = _clean_text(live.get("wake_phrase", "Yarbis")) or "Yarbis"
    return {
        "enabled": bool(voice.get("enabled", True)),
        "live_enabled": bool(live.get("enabled", False)),
        "live_state": status_state,
        "wake_phrase": wake_phrase,
        "headline": _voice_headline(status_state, wake_phrase),
        "detail": _clean_text(status.get("detail", "")),
        "last_transcript": _compact_text(status.get("last_transcript", ""), 600),
        "last_reply": _compact_text(status.get("last_reply", ""), 600),
        "can_live_desktop": "desktop" in live.get("surfaces", ["desktop", "mobile"]),
        "can_live_mobile": "mobile" in live.get("surfaces", ["desktop", "mobile"]),
        "auto_speak": bool(live.get("auto_speak", True)),
        "barge_in": bool(live.get("barge_in", True)),
    }


def _voice_headline(state: str, wake_phrase: str) -> str:
    if state == "wake_listening":
        return f"Di '{wake_phrase}' para hablar."
    if state == "capturing":
        return "Te estoy escuchando."
    if state == "transcribing":
        return "Estoy entendiendo tu voz."
    if state == "thinking":
        return "Estoy pensando la respuesta."
    if state == "speaking":
        return "Estoy respondiendo en voz."
    if state == "interrupted":
        return "Voz interrumpida."
    if state == "error":
        return "La voz necesita atención."
    return f"Voz lista con activación '{wake_phrase}'."


def _timeline_from_messages(state: dict, limit: int) -> list[dict]:
    messages = state.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    rendered = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "")).strip()
        content = _clean_text(message.get("content", ""))
        if not content or role == "tool":
            continue
        if role == "assistant" and message.get("tool_calls"):
            continue
        rendered_role = "yarbis" if role == "assistant" else role
        if rendered_role not in {"user", "yarbis", "system"}:
            continue
        rendered.append({
            "role": rendered_role,
            "kind": "message",
            "text": _compact_text(content),
        })
    return rendered[-max(1, int(limit or 8)):]


def _actions_for(mode: str, pending: dict, operation: dict, voice: dict) -> list[dict]:
    waiting = bool(pending.get("active"))
    thinking = bool(operation.get("active"))
    actions = [
        {
            "id": "reply",
            "label": "Responder y continuar" if waiting else "Enviar y ejecutar",
            "style": "primary",
            "enabled": not thinking,
        },
        {
            "id": "run_cycle",
            "label": "Ejecutar ciclo",
            "style": "secondary",
            "enabled": not thinking and not waiting,
        },
        {
            "id": "run_auto",
            "label": "Modo autónomo",
            "style": "secondary",
            "enabled": not thinking and not waiting,
        },
        {
            "id": "stop_operation",
            "label": "Detener pensando",
            "style": "danger",
            "enabled": thinking,
        },
        {
            "id": "voice_live",
            "label": "Detener voz en vivo" if voice.get("live_state") != "idle" else "Conversación en vivo",
            "style": "primary" if voice.get("live_state") == "idle" else "danger",
            "enabled": bool(voice.get("enabled")),
        },
        {
            "id": "speak",
            "label": "Escuchar",
            "style": "plain",
            "enabled": bool(voice.get("enabled")),
        },
    ]
    if mode == "speaking":
        actions.append({
            "id": "stop_speaking",
            "label": "Detener voz",
            "style": "danger",
            "enabled": True,
        })
    return actions


def build_conversation_view(
    state: dict,
    *,
    service_status: dict | None = None,
    jobs: list[dict] | None = None,
    voice_status: dict | None = None,
    channel: str = "desktop",
    limit: int = 8,
) -> dict:
    state = state if isinstance(state, dict) else {}
    pending = _pending_input(state)
    operation = _operation_status(state)
    voice = _voice_settings(state, voice_status=voice_status)
    live_state = voice.get("live_state", "idle")
    mode = "ready"
    headline = "Yarbis está listo."
    detail = "Puedes ejecutar un ciclo, enviar contexto o iniciar voz en vivo."

    if live_state in {"wake_listening", "capturing", "transcribing", "thinking", "speaking", "error"}:
        mode = live_state
        headline = voice["headline"]
        detail = voice.get("detail") or "La conversación por voz está activa."
    elif operation["active"]:
        mode = "thinking"
        headline = f"Estoy pensando: {operation['label']}."
        detail = "Puedes detener la operación si hace falta."
    elif pending["active"]:
        mode = "waiting_user"
        headline = "Yarbis necesita tu respuesta."
        detail = pending["question"]
    elif _clean_text(state.get("last_result", "")):
        mode = "result"
        headline = "Última respuesta lista."
        detail = "Puedes continuar con más contexto o escucharla en voz."

    timeline = _timeline_from_messages(state, limit=limit)
    if pending["active"]:
        timeline.append({
            "role": "yarbis",
            "kind": "pending",
            "text": pending["question"],
        })
    elif not timeline and _clean_text(state.get("last_result", "")):
        timeline.append({
            "role": "yarbis",
            "kind": "result",
            "text": _compact_text(state.get("last_result", "")),
        })

    recent_jobs = jobs if isinstance(jobs, list) else []
    return {
        "channel": str(channel or "desktop"),
        "mode": mode,
        "headline": headline,
        "detail": detail,
        "pending": pending,
        "operation": operation,
        "voice": voice,
        "composer": {
            "placeholder": "Responde la pregunta pendiente." if pending["active"] else "Escribe o dicta contexto para Yarbis.",
            "primary_label": "Responder y continuar" if pending["active"] else "Enviar y ejecutar",
            "disabled": bool(operation["active"]),
            "lock_reason": f"Estoy pensando: {operation['label']}." if operation["active"] else "",
        },
        "actions": _actions_for(mode, pending, operation, voice),
        "timeline": timeline,
        "continuity": {
            "cycle_count": state.get("cycle_count", 0),
            "open_tasks": _open_task_count(state),
            "service_running": bool((service_status or {}).get("running")),
            "jobs": len(recent_jobs),
        },
    }


def spoken_reply_text(text: str) -> str:
    rendered = _clean_text(text)
    if not rendered:
        return ""
    rendered = re.sub(r"(?m)^={3,}\s*CICLO\s+\d+\s*={3,}\s*$", "", rendered)
    rendered = re.sub(r"(?m)^---\s*Paso\s+\d+\s*---\s*$", "", rendered)
    rendered = re.sub(r"(?m)^>\s*(Ejecutando tool|Argumentos|Resultado):.*$", "", rendered)
    rendered = rendered.replace("Yarbis decidio usar tools.", "")
    rendered = rendered.replace("Decidi usar herramientas.", "")
    marker = "\nYarbis:\n"
    if marker in rendered:
        rendered = rendered.rsplit(marker, 1)[-1]
    elif rendered.startswith("Yarbis:\n"):
        rendered = rendered[len("Yarbis:\n"):]
    for prefix in (
        "Respuesta guardada. Retomando el modo autonomo con esta informacion.",
        "Respuesta guardada. Ejecutando un ciclo con esta informacion.",
    ):
        if rendered.startswith(prefix):
            rendered = rendered[len(prefix):].strip()
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
    if len(rendered) > MAX_SPOKEN_REPLY_CHARS:
        rendered = rendered[: MAX_SPOKEN_REPLY_CHARS - 1].rstrip() + "..."
    return rendered
