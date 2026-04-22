import re
from pathlib import Path
from uuid import uuid4

from memory import (
    VALID_TASK_PRIORITY,
    VALID_TASK_STATUS,
    load_state,
    render_state_summary,
    save_state,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent
MAX_LIST_ITEMS = 200
MAX_READ_BYTES = 16_000
MAX_WRITE_BYTES = 64_000
MAX_WRITE_PREVIEW_CHARS = 600
IGNORED_LISTING_NAMES = {".git", ".venv", "__pycache__", "tests_runtime"}
CLEAR_VALUE = "[clear]"


def _resolve_workspace_path(path: str) -> tuple[Path | None, str | None]:
    candidate = Path(path)
    resolved = (WORKSPACE_ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return None, f"Acceso denegado. Solo puedes usar rutas dentro de: {WORKSPACE_ROOT}"

    return resolved, None


def list_files(path: str = ".") -> str:
    """
    Lista archivos y carpetas de una ruta.

    Args:
        path (str): Ruta de la carpeta a inspeccionar.

    Returns:
        str: Lista de archivos y carpetas encontrada.
    """
    directory, error = _resolve_workspace_path(path)
    if error:
        return error
    if not directory.exists():
        return f"La ruta no existe: {path}"
    if not directory.is_dir():
        return f"No es una carpeta valida: {path}"

    items = sorted(
        (
            item
            for item in directory.iterdir()
            if item.name not in IGNORED_LISTING_NAMES
        ),
        key=lambda item: (not item.is_dir(), item.name.lower()),
    )

    if not items:
        return "La carpeta esta vacia."

    rendered = []
    for item in items[:MAX_LIST_ITEMS]:
        kind = "DIR " if item.is_dir() else "FILE"
        rendered.append(f"[{kind}] {item.relative_to(WORKSPACE_ROOT).as_posix()}")

    if len(items) > MAX_LIST_ITEMS:
        rendered.append(f"... y {len(items) - MAX_LIST_ITEMS} elementos mas.")

    return "\n".join(rendered)


def read_text_file(path: str) -> str:
    """
    Lee un archivo de texto.

    Args:
        path (str): Ruta del archivo.

    Returns:
        str: Contenido del archivo.
    """
    file_path, error = _resolve_workspace_path(path)
    if error:
        return error
    if not file_path.exists():
        return f"No existe el archivo: {path}"
    if not file_path.is_file():
        return f"No es un archivo valido: {path}"

    try:
        raw_content = file_path.read_bytes()
    except OSError as exc:
        return f"Error leyendo archivo: {exc}"

    truncated = raw_content[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    if len(raw_content) > MAX_READ_BYTES:
        return (
            f"Contenido truncado de {file_path.relative_to(WORKSPACE_ROOT).as_posix()} "
            f"a {MAX_READ_BYTES} bytes de {len(raw_content)}.\n{truncated}"
        )

    return truncated


def write_text_file(path: str, content: str) -> str:
    """
    Escribe texto en un archivo.

    Args:
        path (str): Ruta destino.
        content (str): Contenido a guardar.

    Returns:
        str: Resultado de la operacion.
    """
    file_path, error = _resolve_workspace_path(path)
    if error:
        return error

    encoded_content = content.encode("utf-8")
    if len(encoded_content) > MAX_WRITE_BYTES:
        return (
            f"Contenido demasiado grande para escribir en una sola operacion: "
            f"{len(encoded_content)} bytes. Limite: {MAX_WRITE_BYTES} bytes."
        )

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error escribiendo archivo: {exc}"

    preview = content[:MAX_WRITE_PREVIEW_CHARS]
    if len(content) > MAX_WRITE_PREVIEW_CHARS:
        preview += "\n... (vista previa truncada)"

    return (
        f"Archivo guardado correctamente en: {file_path.relative_to(WORKSPACE_ROOT).as_posix()}\n"
        f"Caracteres escritos: {len(content)}\n"
        f"Vista previa:\n{preview}"
    )


def _split_text_items(value: str) -> list[str]:
    if not value:
        return []

    items = [item.strip() for item in re.split(r"[\n,;]+", value)]
    return [item for item in items if item]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _find_task(tasks: list[dict], task_id: str) -> dict | None:
    cleaned = str(task_id).strip().lower()
    if not cleaned:
        return None

    for task in tasks:
        if task["id"].lower() == cleaned:
            return task

    prefix_matches = [task for task in tasks if task["id"].lower().startswith(cleaned)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    return None


def agent_overview() -> str:
    """
    Devuelve un resumen del estado personal y operativo del agente.

    Returns:
        str: Resumen de objetivo, perfil, plan, tareas y notas.
    """
    state = load_state()
    return render_state_summary(state)


def update_profile(
    name: str = "",
    role: str = "",
    preferences: str = "",
    constraints: str = "",
) -> str:
    """
    Actualiza el perfil personal del usuario para personalizar el agente.

    Args:
        name (str): Nombre del usuario o como le gusta ser llamado.
        role (str): Rol, contexto o descripcion corta del usuario.
        preferences (str): Lista separada por comas o saltos de linea.
        constraints (str): Limites o reglas separadas por comas o saltos de linea.

    Returns:
        str: Resumen del perfil actualizado.
    """
    state = load_state()
    profile = state["profile"]

    if str(name).strip():
        profile["name"] = "" if str(name).strip() == CLEAR_VALUE else str(name).strip()

    if str(role).strip():
        profile["role"] = "" if str(role).strip() == CLEAR_VALUE else str(role).strip()

    if str(preferences).strip():
        if str(preferences).strip() == CLEAR_VALUE:
            profile["preferences"] = []
        else:
            profile["preferences"] = _split_text_items(str(preferences))

    if str(constraints).strip():
        if str(constraints).strip() == CLEAR_VALUE:
            profile["constraints"] = []
        else:
            profile["constraints"] = _split_text_items(str(constraints))

    save_state(state)
    refreshed = load_state()
    profile = refreshed["profile"]

    return (
        "Perfil actualizado.\n"
        f"Nombre: {profile['name'] or '-'}\n"
        f"Rol: {profile['role'] or '-'}\n"
        f"Preferencias: {', '.join(profile['preferences']) if profile['preferences'] else 'Sin definir.'}\n"
        f"Restricciones: {', '.join(profile['constraints']) if profile['constraints'] else 'Sin definir.'}"
    )


def request_user_input(question: str, reason: str = "", missing_fields: str = "") -> str:
    """
    Registra una pregunta pendiente para que el usuario complete informacion faltante.

    Args:
        question (str): Pregunta clara y concreta para el usuario.
        reason (str): Motivo breve de por que hace falta esa informacion.
        missing_fields (str): Campos faltantes separados por comas o saltos de linea.

    Returns:
        str: Confirmacion de la solicitud registrada.
    """
    cleaned_question = str(question).strip()
    if not cleaned_question:
        return "Debes indicar una pregunta concreta para el usuario."

    state = load_state()
    state["awaiting_user_input"] = {
        "pending": True,
        "question": cleaned_question,
        "reason": str(reason).strip(),
        "fields": _split_text_items(str(missing_fields)),
    }
    save_state(state)

    refreshed = load_state()["awaiting_user_input"]
    lines = [
        "Solicitud de informacion registrada.",
        f"Pregunta: {refreshed['question']}",
    ]

    if refreshed["reason"]:
        lines.append(f"Motivo: {refreshed['reason']}")

    if refreshed["fields"]:
        lines.append("Datos faltantes: " + ", ".join(refreshed["fields"]))

    return "\n".join(lines)


def save_note(title: str, content: str, category: str = "general") -> str:
    """
    Guarda una nota breve y persistente en la memoria del agente.

    Args:
        title (str): Titulo corto de la nota.
        content (str): Contenido de la nota.
        category (str): Categoria simple para agrupar notas.

    Returns:
        str: Confirmacion con el identificador de la nota.
    """
    if not str(title).strip() and not str(content).strip():
        return "Debes indicar al menos un titulo o contenido para la nota."

    state = load_state()
    note = {
        "id": _new_id("note"),
        "title": str(title).strip() or "Nota sin titulo",
        "content": str(content).strip(),
        "category": str(category).strip() or "general",
    }
    state["notes"].append(note)
    save_state(state)

    return f"Nota guardada con id {note['id']}: {note['title']} ({note['category']})"


def list_notes(category: str = "", limit: int = 10) -> str:
    """
    Lista notas persistentes del agente.

    Args:
        category (str): Categoria opcional para filtrar.
        limit (int): Maximo de notas a devolver.

    Returns:
        str: Listado resumido de notas.
    """
    state = load_state()
    notes = state["notes"]

    if str(category).strip():
        category_filter = str(category).strip().casefold()
        notes = [note for note in notes if note["category"].casefold() == category_filter]

    if not notes:
        return "No hay notas que coincidan."

    try:
        normalized_limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 10

    lines = []
    for note in reversed(notes[-normalized_limit:]):
        preview = note["content"][:180]
        if len(note["content"]) > 180:
            preview += "..."
        lines.append(f"[{note['id']}] {note['title']} ({note['category']}): {preview}")

    return "\n".join(lines)


def add_task(title: str, details: str = "", priority: str = "media") -> str:
    """
    Crea una tarea en la cola de trabajo del agente.

    Args:
        title (str): Titulo breve y accionable.
        details (str): Contexto adicional o criterio de exito.
        priority (str): alta, media o baja.

    Returns:
        str: Confirmacion con el identificador de la tarea.
    """
    cleaned_title = str(title).strip()
    if not cleaned_title:
        return "Debes indicar un titulo para la tarea."

    cleaned_priority = str(priority).strip().lower() or "media"
    if cleaned_priority not in VALID_TASK_PRIORITY:
        cleaned_priority = "media"

    state = load_state()
    task = {
        "id": _new_id("task"),
        "title": cleaned_title,
        "details": str(details).strip(),
        "status": "pending",
        "priority": cleaned_priority,
        "result": "",
    }
    state["tasks"].append(task)
    save_state(state)

    return (
        f"Tarea creada con id {task['id']}.\n"
        f"Titulo: {task['title']}\n"
        f"Prioridad: {task['priority']}\n"
        f"Detalles: {task['details'] or 'Sin detalles adicionales.'}"
    )


def list_tasks(status: str = "all", limit: int = 20) -> str:
    """
    Lista tareas registradas por el agente.

    Args:
        status (str): all, pending, in_progress, blocked o done.
        limit (int): Maximo de tareas a mostrar.

    Returns:
        str: Listado legible de tareas.
    """
    state = load_state()
    tasks = state["tasks"]
    cleaned_status = str(status).strip().lower() or "all"

    if cleaned_status != "all":
        tasks = [task for task in tasks if task["status"] == cleaned_status]

    if not tasks:
        return "No hay tareas que coincidan."

    try:
        normalized_limit = max(1, min(30, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 20

    lines = []
    for task in tasks[:normalized_limit]:
        detail_preview = task["details"][:140]
        if len(task["details"]) > 140:
            detail_preview += "..."
        result_preview = task["result"][:140]
        if len(task["result"]) > 140:
            result_preview += "..."

        suffix = f" Resultado: {result_preview}" if result_preview else ""
        lines.append(
            f"[{task['id']}] {task['title']} "
            f"(estado={task['status']}, prioridad={task['priority']}) "
            f"- {detail_preview or 'Sin detalles.'}{suffix}"
        )

    return "\n".join(lines)


def update_task_status(task_id: str, status: str, result: str = "") -> str:
    """
    Cambia el estado de una tarea y opcionalmente registra su resultado.

    Args:
        task_id (str): Id completo o prefijo unico de la tarea.
        status (str): pending, in_progress, blocked o done.
        result (str): Resultado breve o motivo del cambio.

    Returns:
        str: Confirmacion del cambio aplicado.
    """
    cleaned_status = str(status).strip().lower()
    if cleaned_status not in VALID_TASK_STATUS:
        return (
            "Estado invalido. Usa uno de: "
            + ", ".join(sorted(VALID_TASK_STATUS))
        )

    state = load_state()
    task = _find_task(state["tasks"], task_id)
    if not task:
        return f"No encontre una tarea con id o prefijo: {task_id}"

    task["status"] = cleaned_status
    if str(result).strip():
        task["result"] = str(result).strip()

    save_state(state)
    return (
        f"Tarea actualizada: {task['id']}\n"
        f"Titulo: {task['title']}\n"
        f"Nuevo estado: {task['status']}\n"
        f"Resultado: {task['result'] or 'Sin resultado registrado.'}"
    )


def set_plan(plan_text: str = "") -> str:
    """
    Define el plan actual del agente como una lista corta de pasos.

    Args:
        plan_text (str): Pasos separados por lineas, comas o punto y coma.

    Returns:
        str: Resumen del plan guardado.
    """
    state = load_state()
    plan_items = _split_text_items(str(plan_text))
    state["current_plan"] = plan_items
    save_state(state)

    if not plan_items:
        return "Plan actual borrado."

    rendered_items = "\n".join(
        f"{index}. {item}" for index, item in enumerate(plan_items, start=1)
    )
    return f"Plan actualizado.\n{rendered_items}"
