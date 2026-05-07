from memory import load_state
from pc_context import (
    load_latest_snapshot,
    local_context_enabled,
    normalize_local_context_settings,
    render_snapshot_summary,
)

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


def build_proactive_tick_message(state: dict | None = None) -> str:
    if state is None:
        state = load_state()

    return (
        f"{PROACTIVE_TICK_BASE_MESSAGE}\n\n"
        f"{_render_context_section(state)}\n\n"
        "Reglas para usar este contexto local:\n"
        "- Tratalo como senal auxiliar, no como certeza absoluta.\n"
        "- No menciones datos sensibles si no aportan al siguiente paso.\n"
        "- Usa las herramientas completas disponibles cuando el objetivo lo justifique.\n"
        "- Si una accion depende de un dato que no esta disponible, obtenlo con herramientas o pregunta."
    )
