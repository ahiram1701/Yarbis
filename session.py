import contextlib
import io

from agent import run_autonomous_session, run_one_cycle
from memory import load_state, render_state_summary, save_state
from tools import add_task, save_note, update_profile


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


def update_goal(new_goal: str) -> str:
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
    return render_state_summary(load_state())


def get_ui_theme() -> str:
    state = load_state()
    return state.get("ui", {}).get("theme", "dark")


def update_ui_theme(theme: str) -> str:
    cleaned_theme = str(theme).strip().lower()
    if cleaned_theme not in {"light", "dark"}:
        raise ValueError("Tema invalido. Usa 'light' o 'dark'.")

    state = load_state()
    state.setdefault("ui", {})
    state["ui"]["theme"] = cleaned_theme
    save_state(state)
    return f"Tema actualizado a {cleaned_theme}."


def update_profile_text(
    name: str = "",
    role: str = "",
    preferences: str = "",
    constraints: str = "",
) -> str:
    return update_profile(
        name=name,
        role=role,
        preferences=preferences,
        constraints=constraints,
    )


def save_note_text(title: str, content: str, category: str = "general") -> str:
    return save_note(title=title, content=content, category=category or "general")


def add_task_text(title: str, details: str = "", priority: str = "media") -> str:
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


def run_cycle_with_output() -> str:
    output, _ = _capture_operation_output(run_one_cycle)
    return output or "Ciclo ejecutado sin salida visible."


def run_auto_with_output(cycles=None) -> str:
    output, executed_cycles = _capture_operation_output(
        run_autonomous_session,
        cycles=cycles,
    )
    summary = f"Modo autonomo ejecutado por {executed_cycles} ciclo(s)."
    if not output:
        return summary
    return f"{output}\n\n{summary}"


def submit_user_reply(reply_text: str) -> str:
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
        auto_output = run_auto_with_output(cycles=auto_cycles_default)
        return (
            "Respuesta guardada. Retomando el modo autonomo con esta informacion.\n\n"
            f"{auto_output}"
        )

    cycle_output = run_cycle_with_output()
    return f"Respuesta guardada. Ejecutando un ciclo con esta informacion.\n\n{cycle_output}"
