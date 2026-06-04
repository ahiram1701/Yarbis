import re

import conversation_ux


def _open_task_count(state: dict) -> int:
    return sum(
        1
        for task in state.get("tasks", [])
        if task.get("status") in {"pending", "in_progress", "blocked"}
    )


def clean_telegram_operation_text(text: str) -> str:
    rendered = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not rendered:
        return ""

    rendered = re.sub(
        r"\n?\.\.\.\[truncado \d+ caracteres\]",
        "",
        rendered,
        flags=re.IGNORECASE,
    )
    rendered = re.sub(
        r"\n?\.\.\. diff truncado, \d+ lineas mas\.",
        "",
        rendered,
        flags=re.IGNORECASE,
    )
    rendered = rendered.replace("Contenido truncado: si", "Contenido resumido: si")
    return rendered.strip()


def extract_telegram_operation_body(text: str) -> str:
    cleaned = clean_telegram_operation_text(text)
    if not cleaned:
        return ""

    marker = "\nYarbis:\n"
    marker_index = cleaned.rfind(marker)
    if marker_index >= 0:
        cleaned = cleaned[marker_index + len(marker):].strip()
    elif cleaned.startswith("Yarbis:\n"):
        cleaned = cleaned[len("Yarbis:\n"):].strip()

    cleaned = re.sub(r"\n={3,} CICLO \d+ ={3,}\n?", "\n", cleaned)
    cleaned = re.sub(r"\n--- Paso \d+ ---\n?", "\n", cleaned)
    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if stripped in {"Yarbis decidio usar tools.", "Decidi usar herramientas."}:
            continue
        if stripped.startswith("> Ejecutando tool:") or stripped.startswith("> Argumentos:"):
            continue
        lines.append(line.rstrip())

    return "\n".join(lines).strip()


def telegram_continuity_line(state: dict) -> str:
    cycle_count = state.get("cycle_count", 0)
    open_tasks = _open_task_count(state)
    awaiting_user_input = state.get("awaiting_user_input", {})
    pending = bool(awaiting_user_input.get("pending") and awaiting_user_input.get("question"))
    status = "esperando tu respuesta" if pending else "listo para seguir"
    return f"Continuidad: {cycle_count} ciclo(s) | {open_tasks} tarea(s) abierta(s) | {status}."


def telegram_next_step_line(state: dict) -> str:
    awaiting_user_input = state.get("awaiting_user_input", {})
    if awaiting_user_input.get("pending") and awaiting_user_input.get("question"):
        return "Siguiente: responde por este chat y Yarbis retomara los ciclos."
    if _open_task_count(state):
        return "Siguiente: puedes mandar /run o /auto para continuar desde este mismo punto."
    return "Siguiente: manda contexto nuevo, /status o /auto cuando quieras seguir."


def format_telegram_operation_reply(label: str, content: str, state: dict | None = None) -> str:
    operation_label = str(label).strip() or "Yarbis"
    if state is None:
        try:
            from memory import load_state

            state = load_state()
        except Exception:
            state = {}

    body = (
        extract_telegram_operation_body(conversation_ux.spoken_reply_text(content))
        or extract_telegram_operation_body(content)
        or "Operacion completada sin salida visible."
    )

    lines = [
        f"Yarbis | {operation_label}",
        f"Estado: {telegram_continuity_line(state)}",
        "",
        "Resultado:",
        body,
    ]

    awaiting_user_input = state.get("awaiting_user_input", {})
    question = str(awaiting_user_input.get("question", "")).strip()
    if awaiting_user_input.get("pending") and question and question not in body:
        lines.extend(["", f"Pregunta pendiente: {question}"])

    lines.extend(["", telegram_next_step_line(state)])
    return "\n".join(line.rstrip() for line in lines).strip()
