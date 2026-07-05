import activity
from memory import load_state
from pc_context import (
    load_latest_snapshot,
    local_context_enabled,
    normalize_local_context_settings,
    render_snapshot_summary,
)

# Tipos de evento que representan "como le fue" a Yarbis (senal de aprendizaje).
_EXPERIENCE_EVENT_TYPES = {
    "operation_finished",
    "remote_job_failed",
    "remote_job_completed",
    "proactive_pulse_completed",
    "proactive_pulse_skipped",
    "proactive_pulse_timed_out",
    "self_evolution_completed",
    "self_evolution_timed_out",
    "service_failed",
    "stop_requested",
}

PROACTIVE_TICK_BASE_MESSAGE = (
    "Pulso proactivo 24/7 del servicio: revisa objetivo, perfil, notas, tareas "
    "y autoconocimiento usando todas las herramientas disponibles del agente. "
    "Avanza el siguiente paso util y verificable si existe. Puedes leer y "
    "escribir archivos, ejecutar comandos, correr tests o builds, usar internet, "
    "automatizar navegador, calendario, correo y abrir targets del sistema cuando "
    "ayude al objetivo. Si el usuario pidio cambiar el objetivo principal, usa "
    "update_goal. Si algo impide avanzar porque falta informacion que no puedes "
    "obtener con herramientas, pide ayuda con request_user_input. Si no hay nada "
    "accionable, deja una salida breve sin inventar trabajo."
)


def _battery_hint(snapshot: dict) -> str:
    health = snapshot.get("system_health", {})
    if not isinstance(health, dict):
        return ""
    power = health.get("power", {})
    if not isinstance(power, dict) or not power.get("available"):
        return ""
    battery_percent = power.get("battery_percent")
    ac_line_status = str(power.get("ac_line_status", "")).strip()
    if not isinstance(battery_percent, int):
        return ""
    if ac_line_status != "plugged" and battery_percent <= 20:
        return (
            f"Bateria baja ({battery_percent}%): evita tareas largas y considera sugerir conectar corriente."
        )
    return ""


def _disk_hint(snapshot: dict) -> str:
    health = snapshot.get("system_health", {})
    if not isinstance(health, dict):
        return ""
    disk = health.get("disk", {})
    if not isinstance(disk, dict) or not disk.get("available"):
        return ""
    free_percent = disk.get("free_percent")
    if isinstance(free_percent, (int, float)) and free_percent <= 10:
        return (
            f"Disco con poco espacio ({free_percent}% libre): prioriza limpieza o evita descargas pesadas."
        )
    return ""


def _memory_hint(snapshot: dict) -> str:
    health = snapshot.get("system_health", {})
    if not isinstance(health, dict):
        return ""
    memory = health.get("memory", {})
    if not isinstance(memory, dict) or not memory.get("available"):
        return ""
    load_percent = memory.get("load_percent")
    if isinstance(load_percent, int) and load_percent >= 90:
        return f"Memoria alta ({load_percent}%): prefiere acciones ligeras o sugiere cerrar procesos pesados."
    return ""


def _presence_hint(snapshot: dict) -> str:
    presence = snapshot.get("user_presence", {})
    if not isinstance(presence, dict):
        return ""
    state = str(presence.get("state", "")).strip()
    idle_seconds = presence.get("idle_seconds")
    if state == "away":
        suffix = f" ({idle_seconds}s idle)" if idle_seconds is not None else ""
        return (
            "Usuario ausente"
            f"{suffix}: prepara trabajo reversible y evita interrumpir con preguntas triviales."
        )
    if state == "active":
        return "Usuario activo: las sugerencias pueden ser inmediatas y concretas."
    return ""


def _workspace_hint(snapshot: dict) -> str:
    workspace = snapshot.get("workspace", {})
    if not isinstance(workspace, dict):
        return ""
    git = workspace.get("git", {})
    if isinstance(git, dict) and git.get("available"):
        change_count = git.get("change_count", 0)
        if isinstance(change_count, int) and change_count > 0:
            return (
                f"Workspace con {change_count} cambio(s): revisa continuidad antes de proponer ediciones nuevas."
            )
    recent_files = workspace.get("recent_files", [])
    if isinstance(recent_files, list) and recent_files:
        return "Hay archivos tocados recientemente: puede haber contexto operativo fresco."
    return ""


def _build_context_hints(snapshot: dict) -> list[str]:
    hints = [
        _battery_hint(snapshot),
        _disk_hint(snapshot),
        _memory_hint(snapshot),
        _presence_hint(snapshot),
        _workspace_hint(snapshot),
    ]
    return [hint for hint in hints if hint]


def _render_context_section(state: dict) -> str:
    settings = normalize_local_context_settings(state.get("local_context", {}))
    if not local_context_enabled(settings):
        return (
            "Contexto local observado:\n"
            "- Desactivado por configuracion. No infieras actividad actual de la PC."
        )

    snapshot = load_latest_snapshot(max_age_seconds=settings["max_snapshot_age_seconds"])
    if not snapshot:
        return (
            "Contexto local observado:\n"
            "- No hay snapshot fresco de la PC. Puede que la app de escritorio no este abierta "
            "o el observador local aun no haya capturado datos."
        )

    summary = render_snapshot_summary(snapshot)
    hints = _build_context_hints(snapshot)
    if not hints:
        return "Contexto local observado:\n" + summary

    return (
        "Contexto local observado:\n"
        f"{summary}\n\n"
        "Senales accionables:\n"
        + "\n".join(f"- {hint}" for hint in hints)
    )


SELF_EVOLUTION_TICK_BASE_MESSAGE = (
    "Autoevolucion de Yarbis: revisa tu propio proyecto y PROPON mejoras "
    "concretas de alto valor para ti mismo, sin aplicar nada."
)


def build_self_evolution_tick_message(state: dict | None = None) -> str:
    if state is None:
        state = load_state()

    evolution = state.get("evolution", {}) if isinstance(state, dict) else {}
    if not isinstance(evolution, dict):
        evolution = {}
    dimensions = evolution.get("dimensions", []) if isinstance(evolution.get("dimensions"), list) else []
    try:
        max_pending = int(evolution.get("max_pending", 2))
    except (TypeError, ValueError):
        max_pending = 2

    dim_labels = {
        "code": "codigo (corrige bugs, optimiza, agrega features utiles) -> `coding_propose_changes`",
        "skills": "skills/herramientas (propon tools nuevas como diff de codigo) -> `coding_propose_changes`",
        "behavior": "comportamiento (reglas/estrategia) -> `evolution_propose_directive`",
        "goals": "objetivo principal -> `evolution_propose_goal`; memoria/aprendizajes -> `evolution_propose_memory`",
    }
    active = [f"- {dim_labels[d]}" for d in ("code", "skills", "behavior", "goals") if d in dimensions and d in dim_labels]
    dims_text = "\n".join(active) if active else "- codigo -> `coding_propose_changes`"

    return (
        f"{SELF_EVOLUTION_TICK_BASE_MESSAGE}\n\n"
        "El workspace de coding activo ya apunta a tu propio proyecto. Usa "
        "`coding_workspace_overview`, `coding_list_files`, `coding_search_text` y "
        "`coding_read_text_range` para estudiar tu codigo antes de proponer.\n\n"
        "Dimensiones a revisar (cada una con su herramienta de PROPUESTA):\n"
        f"{dims_text}\n\n"
        "Reglas ESTRICTAS:\n"
        f"- Antes de proponer, revisa cuantas propuestas 'pending' hay. Si ya hay {max_pending} o mas, "
        "NO crees nuevas: resume el estado y termina.\n"
        "- Para codigo/skills crea la propuesta con `coding_propose_changes` incluyendo titulo, resumen, "
        "motivo y `validation_command` = el comando de tests del repo (usa `coding_detect_validation_command` "
        "si no lo sabes). Prefiere cambios pequenos, seguros y de alta confianza (un bugfix, una limpieza, "
        "un test, una mejora acotada).\n"
        "- Para comportamiento usa `evolution_propose_directive`. Para objetivo usa `evolution_propose_goal` "
        "y para memoria/aprendizajes usa `evolution_propose_memory`.\n"
        "- NUNCA apliques cambios: no uses `coding_apply_proposal`, `update_goal`, `save_note`, "
        "`evolution_apply_*` ni edites archivos directamente. Solo PROPONES; el usuario aprueba.\n"
        "- No toques rutas protegidas (.git, .venv, state.json, runtime, instancias).\n"
        "- Si no encuentras una mejora clara de alto valor, deja una salida breve sin inventar trabajo."
    )


def _recent_user_corrections(state: dict, limit: int = 4) -> list[str]:
    messages = state.get("messages", []) if isinstance(state, dict) else []
    if not isinstance(messages, list):
        return []
    tick_prefix = PROACTIVE_TICK_BASE_MESSAGE.split(":", 1)[0]
    collected = []
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = str(message.get("content", "")).strip()
        if not content or content.startswith(tick_prefix) or content.startswith("Autoevolucion"):
            continue
        collected.append(content[:200])
        if len(collected) >= limit:
            break
    return list(reversed(collected))


def _render_experience_section(state: dict) -> str:
    parts = []

    try:
        events = activity.read_recent_events(limit=40)
    except Exception:
        events = []
    outcome_lines = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", "")).strip()
        if event_type not in _EXPERIENCE_EVENT_TYPES:
            continue
        label = str(event.get("label", "")).strip()
        detail = str(event.get("reason") or event.get("content") or event.get("error") or "").strip()
        piece = event_type + (f" · {label}" if label else "") + (f" · {detail}" if detail else "")
        outcome_lines.append("- " + piece[:160])
    outcome_lines = outcome_lines[-10:]
    if outcome_lines:
        parts.append("Resultados recientes:\n" + "\n".join(outcome_lines))

    last_result = str(state.get("last_result", "")).strip()
    if last_result:
        parts.append("Ultimo resultado: " + last_result[:400])

    corrections = _recent_user_corrections(state)
    if corrections:
        parts.append(
            "Ultimas indicaciones/correcciones del usuario:\n"
            + "\n".join(f"- {item}" for item in corrections)
        )

    if not parts:
        return "Experiencia reciente:\n- Sin senales nuevas relevantes."
    return "Experiencia reciente:\n" + "\n\n".join(parts)


def build_proactive_tick_message(state: dict | None = None) -> str:
    if state is None:
        state = load_state()

    return (
        f"{PROACTIVE_TICK_BASE_MESSAGE}\n\n"
        f"{_render_experience_section(state)}\n\n"
        f"{_render_context_section(state)}\n\n"
        "Aprendizaje continuo (parte de tu forma de operar, no una tarea aparte):\n"
        "- Reflexiona brevemente sobre los resultados recientes y las correcciones del usuario antes de avanzar.\n"
        "- Si observas un hecho o preferencia estable del usuario o del entorno, guardalo con `save_note` "
        "(categoria 'aprendizaje'); no dupliques notas existentes ni guardes secretos.\n"
        "- Si notas una forma mejor y estable de comportarte, proponla con `evolution_propose_directive` "
        "(queda pendiente de aprobacion; NO cambies tu conducta base sin aprobacion).\n"
        "- No inventes aprendizajes: si no hay una leccion clara, no guardes ni propongas nada.\n\n"
        "Reglas para usar este contexto local:\n"
        "- Tratalo como senal auxiliar, no como certeza absoluta.\n"
        "- No menciones datos sensibles si no aportan al siguiente paso.\n"
        "- Usa las herramientas completas disponibles cuando el objetivo lo justifique.\n"
        "- Si una accion depende de un dato que no esta disponible, obtenlo con herramientas o pregunta."
    )
