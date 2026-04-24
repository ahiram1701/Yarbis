import contextlib
import io
import threading

from agent import run_autonomous_session, run_one_cycle
from memory import load_state, render_state_summary, save_state
from notifications import (
    get_telegram_settings,
    notify_user_input_required,
    send_notification,
    try_link_telegram_chat,
)
from tools import add_task, save_note, update_profile

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


def add_task_text(title: str, details: str = "", priority: str = "media") -> str:
    with SESSION_LOCK:
        return add_task(title=title, details=details, priority=priority or "media")


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
