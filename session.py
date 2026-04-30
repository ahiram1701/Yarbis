import contextlib
import io
import re
import threading
import unicodedata
from datetime import datetime, timezone

from agent import run_autonomous_session, run_one_cycle
from memory import load_state, render_state_summary, save_state
from notifications import (
    get_telegram_settings,
    notify_user_input_required,
    send_notification,
    try_link_telegram_chat,
)
from self_knowledge import render_self_knowledge_summary
from tools import add_task, delete_note, get_note, list_notes, save_note, update_profile

SESSION_LOCK = threading.RLock()


def has_pending_user_question(state) -> bool:
    awaiting_user_input = state.get("awaiting_user_input", {})
    return bool(
        awaiting_user_input.get("pending")
        and str(awaiting_user_input.get("question", "")).strip()
    )


def clear_pending_user_question(state):
    state["awaiting_user_input"] = {
        "pending": False,
        "question": "",
        "reason": "",
        "fields": [],
    }


def _normalize_intent_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text).strip().lower())
    without_accents = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return " ".join(without_accents.replace("_", " ").split())


def is_self_analysis_request(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    exact_commands = {
        "self",
        "self overview",
        "autoanalisis",
        "auto analisis",
        "hazte un autoanalisis",
        "hazte un auto analisis",
        "refresca tu autoanalisis",
        "refresca tu auto analisis",
    }
    if normalized in exact_commands:
        return True

    direct_phrases = (
        "autoanalisis",
        "auto analisis",
        "self overview",
        "conocete",
        "que sabes de ti",
        "quien eres y donde estas",
    )
    return any(phrase in normalized for phrase in direct_phrases)


def run_startup_self_analysis() -> str:
    with SESSION_LOCK:
        summary = render_self_knowledge_summary(refresh=True)
        state = load_state()
        state["self_knowledge"] = {
            "last_analyzed_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }
        save_state(state)
        return (
            "Autoanalisis inicial completado. "
            "Yarbis actualizo identidad, codigo fuente, sistema operativo y hardware."
        )


def run_self_analysis_with_output() -> str:
    with SESSION_LOCK:
        message = run_startup_self_analysis()
        state = load_state()
        summary = state.get("self_knowledge", {}).get("summary", "").strip()
        if not summary:
            return message

        return f"{message}\n\n{summary}"


def _has_open_tasks(state) -> bool:
    return any(
        task["status"] in {"pending", "in_progress", "blocked"}
        for task in state.get("tasks", [])
    )


def update_goal(new_goal: str) -> str:
    with SESSION_LOCK:
        cleaned_goal = str(new_goal).strip()
        if not cleaned_goal:
            raise ValueError("El objetivo no puede quedar vacio.")

        state = load_state()
        state["goal"] = cleaned_goal
        state["messages"] = []
        state["tasks"] = []
        state["current_plan"] = []
        state["last_result"] = ""
        clear_pending_user_question(state)
        state["messages"].append({
            "role": "user",
            "content": f"Tu objetivo actual es: {cleaned_goal}",
        })
        save_state(state)
        return "Objetivo actualizado. Contexto operativo reiniciado para el nuevo objetivo."


def get_status_text() -> str:
    with SESSION_LOCK:
        return render_state_summary(load_state())


def get_ui_theme() -> str:
    with SESSION_LOCK:
        state = load_state()
        return state.get("ui", {}).get("theme", "dark")


def update_ui_theme(theme: str) -> str:
    with SESSION_LOCK:
        cleaned_theme = str(theme).strip().lower()
        if cleaned_theme not in {"light", "dark"}:
            raise ValueError("Tema invalido. Usa 'light' o 'dark'.")

        state = load_state()
        state.setdefault("ui", {})
        state["ui"]["theme"] = cleaned_theme
        save_state(state)
        return f"Tema actualizado a {cleaned_theme}."


def get_notification_settings() -> dict:
    with SESSION_LOCK:
        return load_state().get("notifications", {})


def update_notification_settings(
    enabled: bool,
    windows_enabled: bool,
    ntfy_enabled: bool,
    telegram_enabled: bool = False,
    ntfy_server: str = "",
    ntfy_topic: str = "",
    ntfy_token: str = "",
    ntfy_priority: str = "",
    ntfy_tags: str = "",
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
) -> str:
    with SESSION_LOCK:
        channels = []
        if windows_enabled:
            channels.append("windows")
        if ntfy_enabled:
            channels.append("ntfy")
        if telegram_enabled:
            channels.append("telegram")

        if enabled and not channels:
            raise ValueError("Activa al menos un canal de notificacion o desactiva las notificaciones.")

        cleaned_topic = str(ntfy_topic).strip().strip("/")
        if enabled and ntfy_enabled and not cleaned_topic:
            raise ValueError("Para usar ntfy necesitas indicar un topic.")

        cleaned_telegram_bot_token = str(telegram_bot_token).strip()
        cleaned_telegram_chat_id = str(telegram_chat_id).strip()
        if enabled and telegram_enabled and not cleaned_telegram_bot_token:
            raise ValueError("Para usar Telegram necesitas indicar el bot token.")

        state = load_state()
        defaults = state.get("notifications", {})
        default_ntfy = defaults.get("ntfy", {}) if isinstance(defaults.get("ntfy", {}), dict) else {}
        default_telegram = (
            defaults.get("telegram", {})
            if isinstance(defaults.get("telegram", {}), dict)
            else {}
        )

        previous_telegram_token = str(default_telegram.get("bot_token", "")).strip()
        telegram_last_update_id = default_telegram.get("last_update_id", 0)
        if cleaned_telegram_bot_token and cleaned_telegram_bot_token != previous_telegram_token:
            telegram_last_update_id = 0

        state["notifications"] = {
            "enabled": bool(enabled),
            "channels": channels,
            "ntfy": {
                "server": str(ntfy_server).strip() or default_ntfy.get("server", "https://ntfy.sh"),
                "topic": cleaned_topic,
                "token": str(ntfy_token).strip(),
                "priority": str(ntfy_priority).strip().lower(),
                "tags": str(ntfy_tags).strip(),
                "timeout_seconds": default_ntfy.get("timeout_seconds", 10),
            },
            "telegram": {
                "api_base": default_telegram.get("api_base", "https://api.telegram.org"),
                "bot_token": cleaned_telegram_bot_token,
                "chat_id": cleaned_telegram_chat_id,
                "timeout_seconds": default_telegram.get("timeout_seconds", 10),
                "poll_timeout_seconds": default_telegram.get("poll_timeout_seconds", 25),
                "last_update_id": telegram_last_update_id,
            },
        }
        save_state(state)

        if not enabled:
            return "Notificaciones desactivadas."

        summary = "Notificaciones actualizadas: " + ", ".join(channels) + "."
        if telegram_enabled and cleaned_telegram_bot_token and not cleaned_telegram_chat_id:
            summary += (
                "\n\nTelegram quedo configurado, pero aun no hay un chat vinculado. "
                "Abre el bot y envia /start para completar el enlace."
            )

        return summary


def send_test_notification() -> str:
    with SESSION_LOCK:
        settings = load_state().get("notifications", {})
        channels = settings.get("channels", [])
        telegram_settings = get_telegram_settings(settings)
        if (
            settings.get("enabled", True)
            and "telegram" in channels
            and telegram_settings.get("bot_token")
            and not telegram_settings.get("chat_id")
        ):
            if try_link_telegram_chat(settings=settings):
                settings = load_state().get("notifications", {})
            else:
                return (
                    "Telegram ya esta configurado, pero aun no hay un chat vinculado. "
                    "Escribe /start al bot desde tu iPhone y vuelve a probar."
                )

        sent = send_notification(
            "Prueba de Yarbis",
            "Las notificaciones estan configuradas correctamente.",
        )
        if sent:
            return "Notificacion de prueba enviada."

        return "No pude enviar la notificacion de prueba. Revisa el canal, el topic y tu conexion."


def update_profile_text(
    name: str = "",
    role: str = "",
    preferences: str = "",
    constraints: str = "",
) -> str:
    with SESSION_LOCK:
        return update_profile(
            name=name,
            role=role,
            preferences=preferences,
            constraints=constraints,
        )


def save_note_text(title: str, content: str, category: str = "general") -> str:
    with SESSION_LOCK:
        return save_note(title=title, content=content, category=category or "general")


def list_notes_text(category: str = "", limit: int = 10) -> str:
    with SESSION_LOCK:
        return list_notes(category=category, limit=limit)


def get_note_text(identifier: str) -> str:
    with SESSION_LOCK:
        return get_note(identifier=identifier)


def delete_note_text(identifier: str) -> str:
    with SESSION_LOCK:
        return delete_note(identifier=identifier)


def add_task_text(title: str, details: str = "", priority: str = "media") -> str:
    with SESSION_LOCK:
        return add_task(title=title, details=details, priority=priority or "media")


def _normalize_command_text(text: str) -> str:
    normalized = _normalize_intent_text(text)
    without_punctuation = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(without_punctuation.split())


def _title_from_note_content(content: str) -> str:
    compact = " ".join(str(content).strip().split())
    if len(compact) <= 64:
        return compact or "Nota sin titulo"
    return compact[:64].rstrip() + "..."


def _parse_note_create_payload(payload: str) -> dict:
    cleaned_payload = str(payload).strip()
    parts = [part.strip() for part in cleaned_payload.split("|")]
    parts = [part for part in parts if part]

    if not parts:
        return {"action": "usage"}

    if len(parts) == 1:
        content = parts[0]
        return {
            "action": "create",
            "title": _title_from_note_content(content),
            "content": content,
            "category": "general",
        }

    return {
        "action": "create",
        "title": parts[0],
        "content": parts[1],
        "category": parts[2] if len(parts) > 2 else "general",
    }


def _parse_note_subcommand(argument_text: str) -> dict:
    argument_text = str(argument_text).strip()
    normalized_argument = _normalize_command_text(argument_text)

    if not argument_text:
        return {"action": "usage"}

    if normalized_argument in {"listar", "lista", "list", "ls", "ver todas", "todas"}:
        return {"action": "list", "category": ""}

    for prefix in (
        "crear",
        "crea",
        "agregar",
        "agrega",
        "guardar",
        "guarda",
        "add",
        "save",
    ):
        prefix_with_space = prefix + " "
        if normalized_argument == prefix:
            return {"action": "usage"}
        if normalized_argument.startswith(prefix_with_space):
            return _parse_note_create_payload(argument_text[len(prefix):].strip())

    for prefix in ("borrar", "borra", "eliminar", "elimina", "delete", "rm"):
        prefix_with_space = prefix + " "
        if normalized_argument == prefix:
            return {"action": "usage"}
        if normalized_argument.startswith(prefix_with_space):
            return {
                "action": "delete",
                "identifier": argument_text[len(prefix):].strip(),
            }

    for prefix in ("ver", "mostrar", "muestra", "lee", "show", "open"):
        prefix_with_space = prefix + " "
        if normalized_argument == prefix:
            return {"action": "list", "category": ""}
        if normalized_argument.startswith(prefix_with_space):
            return {
                "action": "show",
                "identifier": argument_text[len(prefix):].strip(),
            }

    if "|" in argument_text:
        return _parse_note_create_payload(argument_text)

    return {"action": "show", "identifier": argument_text}


def parse_note_text_request(text: str) -> dict | None:
    cleaned_text = str(text).strip()
    if not cleaned_text:
        return None

    parts = cleaned_text.split(maxsplit=1)
    command = parts[0].split("@")[0].lower() if parts else ""
    argument_text = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/notas", "notas", "notes"}:
        return {"action": "list", "category": argument_text}

    if command in {"/nota", "nota", "note"}:
        return _parse_note_subcommand(argument_text)

    if command in {"/crear_nota", "/guardar_nota"}:
        return _parse_note_create_payload(argument_text)

    if command in {"/borrar_nota", "/eliminar_nota", "/delete_note"}:
        return {"action": "delete", "identifier": argument_text}

    if command in {"/ver_nota", "/mostrar_nota", "/show_note"}:
        return {"action": "show", "identifier": argument_text}

    normalized_text = _normalize_command_text(cleaned_text)
    if normalized_text in {
        "ver notas",
        "listar notas",
        "lista notas",
        "muestra notas",
        "mostrar notas",
        "muestrame notas",
        "muestrame mis notas",
        "mis notas",
    }:
        return {"action": "list", "category": ""}

    list_match = re.match(
        r"^\s*(?:ver|listar|muestra|mostrar)\s+(?:mis\s+)?notas(?:\s+(?:de|categoria)\s+(.+))?\s*$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if list_match:
        return {
            "action": "list",
            "category": (list_match.group(1) or "").strip(),
        }

    create_match = re.match(
        r"^\s*(?:guarda|guardar|crea|crear|agrega|agregar)\s+(?:una\s+)?nota\b\s*[:\-]?\s*(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if create_match:
        return _parse_note_create_payload(create_match.group(1))

    note_colon_match = re.match(
        r"^\s*nota\s*:\s*(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if note_colon_match:
        return _parse_note_create_payload(note_colon_match.group(1))

    anota_match = re.match(
        r"^\s*anota\s*[:\-]?\s*(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if anota_match:
        return _parse_note_create_payload(anota_match.group(1))

    delete_match = re.match(
        r"^\s*(?:borra|borrar|elimina|eliminar)\s+(?:la\s+)?nota\s+(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if delete_match:
        return {
            "action": "delete",
            "identifier": delete_match.group(1).strip(),
        }

    show_match = re.match(
        r"^\s*(?:ver|muestra|mostrar|lee|abre)\s+(?:la\s+)?nota\s+(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if show_match:
        return {
            "action": "show",
            "identifier": show_match.group(1).strip(),
        }

    return None


def note_request_label(text: str) -> str:
    parsed = parse_note_text_request(text)
    if not parsed:
        return ""

    labels = {
        "create": "Guardar nota",
        "list": "Notas",
        "show": "Notas",
        "delete": "Eliminar nota",
        "usage": "Notas",
    }
    return labels.get(parsed.get("action", ""), "Notas")


def _note_usage_text() -> str:
    return (
        "Uso de notas:\n"
        "- notas [categoria]\n"
        "- nota crear Titulo | contenido | categoria\n"
        "- nota ver ID_o_titulo\n"
        "- nota borrar ID_o_titulo\n"
        "Tambien puedes decir: guarda una nota: Titulo | contenido | categoria"
    )


def handle_note_text_request(text: str) -> str | None:
    parsed = parse_note_text_request(text)
    if not parsed:
        return None

    action = parsed.get("action", "")
    if action == "usage":
        return _note_usage_text()
    if action == "list":
        return list_notes_text(category=parsed.get("category", ""), limit=20)
    if action == "show":
        return get_note_text(parsed.get("identifier", ""))
    if action == "delete":
        return delete_note_text(parsed.get("identifier", ""))
    if action == "create":
        return save_note_text(
            title=parsed.get("title", ""),
            content=parsed.get("content", ""),
            category=parsed.get("category", "general"),
        )

    return _note_usage_text()


def _capture_operation_output(func, *args, **kwargs) -> tuple[str, object]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = func(*args, **kwargs)

    output = buffer.getvalue().strip()
    if output:
        return output, result

    if isinstance(result, dict):
        content = str(result.get("content", "")).strip()
        if content:
            return content, result

    if result is None:
        return "", result

    return str(result).strip(), result


def run_cycle_with_output(emit_notifications: bool = True) -> str:
    with SESSION_LOCK:
        output, _ = _capture_operation_output(run_one_cycle)
        state = load_state()

        if emit_notifications and has_pending_user_question(state):
            notify_user_input_required(
                state["awaiting_user_input"].get("question", ""),
                state["awaiting_user_input"].get("reason", ""),
            )

        return output or "Ciclo ejecutado sin salida visible."


def run_auto_with_output(cycles=None, emit_notifications: bool = True) -> str:
    with SESSION_LOCK:
        output, executed_cycles = _capture_operation_output(
            run_autonomous_session,
            cycles=cycles,
        )
        state = load_state()

        if emit_notifications and has_pending_user_question(state):
            notify_user_input_required(
                state["awaiting_user_input"].get("question", ""),
                state["awaiting_user_input"].get("reason", ""),
            )
        elif emit_notifications and executed_cycles > 0 and not _has_open_tasks(state):
            send_notification(
                "Yarbis termino el trabajo actual",
                state.get("last_result", "").strip() or "No quedan tareas abiertas.",
            )
        elif emit_notifications and executed_cycles > 0:
            send_notification(
                "Yarbis termino el modo autonomo",
                f"Se ejecutaron {executed_cycles} ciclo(s).",
            )

        summary = f"Modo autonomo ejecutado por {executed_cycles} ciclo(s)."
        if not output:
            return summary
        return f"{output}\n\n{summary}"


def submit_user_reply(reply_text: str, emit_notifications: bool = True) -> str:
    with SESSION_LOCK:
        cleaned_reply = str(reply_text).strip()
        if not cleaned_reply:
            raise ValueError("La respuesta no puede quedar vacia.")

        if is_self_analysis_request(cleaned_reply):
            return run_self_analysis_with_output()

        note_result = handle_note_text_request(cleaned_reply)
        if note_result is not None:
            return note_result

        state = load_state()
        had_pending_question = has_pending_user_question(state)
        auto_cycles_default = state["autonomy"]["auto_cycles_default"]
        state["messages"].append({
            "role": "user",
            "content": cleaned_reply,
        })
        clear_pending_user_question(state)
        save_state(state)

        if had_pending_question:
            auto_output = run_auto_with_output(
                cycles=auto_cycles_default,
                emit_notifications=emit_notifications,
            )
            return (
                "Respuesta guardada. Retomando el modo autonomo con esta informacion.\n\n"
                f"{auto_output}"
            )

        cycle_output = run_cycle_with_output(emit_notifications=emit_notifications)
        return f"Respuesta guardada. Ejecutando un ciclo con esta informacion.\n\n{cycle_output}"
