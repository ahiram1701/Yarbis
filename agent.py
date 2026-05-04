import json
import os
import re
import sys
from collections.abc import Mapping

from ollama import Client

from intent_text import (
    looks_like_affirmative_action_reply as _looks_like_affirmative_action_reply,
    normalize_intent_text as _normalize_intent_text,
)
from memory import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    MAX_OLLAMA_TIMEOUT_SECONDS,
    MIN_OLLAMA_TIMEOUT_SECONDS,
    load_state,
    render_state_summary,
    state_transaction,
)
from self_knowledge import render_self_knowledge_summary
from tools import (
    add_task,
    agent_overview,
    delete_note,
    fetch_web_page,
    get_note,
    list_files,
    list_checkpoints,
    list_notes,
    list_tasks,
    read_text_file,
    request_user_input,
    restore_checkpoint,
    run_project_tests,
    save_note,
    set_plan,
    self_overview,
    update_goal,
    update_internet_settings,
    update_profile,
    update_task_status,
    web_search,
    write_text_file,
)

DEFAULT_MODEL = DEFAULT_OLLAMA_MODEL
DEFAULT_EMPTY_RESPONSE_RETRIES = 1
WAITING_FOR_INSTRUCTIONS_QUESTION = "Que instruccion quieres que siga ahora?"
NON_ACTIONABLE_RETRY_MESSAGE = (
    "El ultimo mensaje del usuario ya autoriza avanzar con la propuesta anterior. "
    "Si tu respuesta anterior fue una presentacion generica, un menu de capacidades "
    "o menciono hardware/sistema sin que el usuario lo pidiera, descartala. Ejecuta "
    "el siguiente paso util usando herramientas cuando aplique: revisa el estado, "
    "crea plan o tareas, lee archivos o corre pruebas seguras. No vuelvas a pedir "
    "que elija entre opciones generales salvo que falte un dato privado, una decision "
    "real o un archivo concreto. No afirmes CPU, GPU, arquitectura, RAM o sistema "
    "operativo salvo que el usuario lo pida; AMD64 es una arquitectura, no una marca "
    "de procesador."
)
MAX_NON_ACTIONABLE_RETRIES = 1


def _get_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    try:
        return int(raw_value)
    except ValueError:
        return default


MODEL = os.getenv("YARBIS_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
OLLAMA_TIMEOUT_SECONDS = _get_env_int(
    "YARBIS_OLLAMA_TIMEOUT_SECONDS",
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
)
OLLAMA_TIMEOUT_SECONDS = max(
    MIN_OLLAMA_TIMEOUT_SECONDS,
    min(MAX_OLLAMA_TIMEOUT_SECONDS, OLLAMA_TIMEOUT_SECONDS),
)
EMPTY_RESPONSE_RETRIES = max(
    0,
    _get_env_int("YARBIS_EMPTY_RESPONSE_RETRIES", DEFAULT_EMPTY_RESPONSE_RETRIES),
)

client = Client(timeout=OLLAMA_TIMEOUT_SECONDS)
_client_timeout_seconds = OLLAMA_TIMEOUT_SECONDS
tool_definitions = [
    agent_overview,
    update_profile,
    update_internet_settings,
    request_user_input,
    save_note,
    list_notes,
    get_note,
    delete_note,
    add_task,
    list_tasks,
    update_task_status,
    update_goal,
    set_plan,
    list_files,
    read_text_file,
    write_text_file,
    list_checkpoints,
    restore_checkpoint,
    run_project_tests,
    web_search,
    fetch_web_page,
    self_overview,
]

available_functions = {
    "agent_overview": agent_overview,
    "update_profile": update_profile,
    "update_internet_settings": update_internet_settings,
    "request_user_input": request_user_input,
    "save_note": save_note,
    "list_notes": list_notes,
    "get_note": get_note,
    "delete_note": delete_note,
    "add_task": add_task,
    "list_tasks": list_tasks,
    "update_task_status": update_task_status,
    "update_goal": update_goal,
    "set_plan": set_plan,
    "list_files": list_files,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
    "list_checkpoints": list_checkpoints,
    "restore_checkpoint": restore_checkpoint,
    "run_project_tests": run_project_tests,
    "web_search": web_search,
    "fetch_web_page": fetch_web_page,
    "self_overview": self_overview,
}

ACTION_PROOF_TOOL_NAMES = {
    "update_profile",
    "update_internet_settings",
    "request_user_input",
    "save_note",
    "delete_note",
    "add_task",
    "update_task_status",
    "update_goal",
    "set_plan",
    "write_text_file",
    "restore_checkpoint",
    "run_project_tests",
}

TOOL_FAILURE_PREFIXES = (
    "acceso denegado",
    "argumentos de tool",
    "contenido demasiado grande",
    "debes indicar",
    "error ",
    "error:",
    "la ruta no existe",
    "modo de internet invalido",
    "no encontre",
    "no existe",
    "no es ",
    "no pude",
    "proveedor de busqueda invalido",
    "solo puedo",
    "tool no encontrada",
)


def _normalize_timeout_seconds(value, default: int = DEFAULT_OLLAMA_TIMEOUT_SECONDS) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    return max(MIN_OLLAMA_TIMEOUT_SECONDS, min(MAX_OLLAMA_TIMEOUT_SECONDS, parsed))


def _normalize_tool_arguments(raw_arguments) -> tuple[dict | None, str | None]:
    if raw_arguments is None:
        return {}, None

    if isinstance(raw_arguments, Mapping):
        return dict(raw_arguments), None

    if hasattr(raw_arguments, "model_dump"):
        dumped = raw_arguments.model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped), None

    if isinstance(raw_arguments, str):
        cleaned = raw_arguments.strip()
        if not cleaned:
            return {}, None
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            return None, f"Argumentos de tool no son JSON valido: {exc}"
        if parsed is None:
            return {}, None
        if isinstance(parsed, Mapping):
            return dict(parsed), None
        return None, "Argumentos de tool deben ser un objeto JSON."

    return None, (
        "Argumentos de tool invalidos: "
        f"se esperaba un objeto, no {type(raw_arguments).__name__}."
    )


def _tool_output_looks_successful(output) -> bool:
    normalized = _normalize_intent_text(str(output))
    if not normalized:
        return False

    return not any(
        normalized.startswith(prefix)
        for prefix in TOOL_FAILURE_PREFIXES
    )


def _tool_call_proves_action(tool_name: str, output) -> bool:
    return (
        str(tool_name).strip() in ACTION_PROOF_TOOL_NAMES
        and _tool_output_looks_successful(output)
    )


def _resolve_ollama_runtime_settings(state=None) -> dict:
    if state is None:
        state = load_state()

    ollama = state.get("ollama", {}) if isinstance(state, dict) else {}
    if not isinstance(ollama, dict):
        ollama = {}

    model = str(ollama.get("model", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
    timeout_seconds = _normalize_timeout_seconds(
        ollama.get("timeout_seconds", DEFAULT_OLLAMA_TIMEOUT_SECONDS),
    )

    env_model = os.getenv("YARBIS_MODEL", "").strip()
    if env_model:
        model = env_model

    env_timeout = os.getenv("YARBIS_OLLAMA_TIMEOUT_SECONDS", "").strip()
    if env_timeout:
        timeout_seconds = _normalize_timeout_seconds(env_timeout, timeout_seconds)

    return {
        "model": model,
        "timeout_seconds": timeout_seconds,
    }


def _apply_ollama_runtime_settings(state=None):
    global MODEL, OLLAMA_TIMEOUT_SECONDS, client, _client_timeout_seconds

    settings = _resolve_ollama_runtime_settings(state)
    MODEL = settings["model"]
    OLLAMA_TIMEOUT_SECONDS = settings["timeout_seconds"]

    if _client_timeout_seconds != OLLAMA_TIMEOUT_SECONDS:
        client = Client(timeout=OLLAMA_TIMEOUT_SECONDS)
        _client_timeout_seconds = OLLAMA_TIMEOUT_SECONDS

    return MODEL, OLLAMA_TIMEOUT_SECONDS, client


SYSTEM_PROMPT = """
Eres Yarbis, el agente inteligente personal, autonomo, local, proactivo y todologo practico del usuario.
Tu trabajo es avanzar paso a paso hacia el objetivo del usuario, organizar el trabajo para que siga progresando entre ciclos y cuidar continuidad 24/7 cuando el servicio de fondo este activo.

Reglas:
- Se util, preciso y orientado a acciones.
- Usa herramientas cuando sea necesario.
- No inventes resultados de herramientas.
- No afirmes que creaste, modificaste, ejecutaste, apagaste, instalaste, borraste o probaste algo salvo que una herramienta haya devuelto evidencia de esa accion en este ciclo. Si no hay evidencia, dilo como pendiente o como limitacion.
- Trabaja en pasos pequenos y claros.
- Aprende y adaptate al usuario: guarda contexto personal estable con `update_profile` y hallazgos utiles con `save_note`.
- Si detectas un siguiente paso util, conviertelo en plan, tarea o accion concreta. No crees tareas duplicadas.
- Persigue mejora continua: revisa tu autoconocimiento, identifica limitaciones reales y propone o ejecuta mejoras pequenas cuando ayuden al objetivo.
- No prometas capacidades que no tienes. Tu autonomia depende de Ollama, del servicio activo, permisos, herramientas disponibles, politica de internet y contexto del usuario.
- Si la mejor salida del ciclo es texto util para el usuario, entregalo directamente en este ciclo.
- No cortes respuestas con marcadores como "truncado". Si hay demasiado material para responder bien, resume con criterio: conserva conclusiones, decisiones, pasos accionables y detalles que el usuario necesita; indica que estas resumiendo por volumen y donde queda el detalle completo cuando exista.
- Una respuesta resumida debe seguir siendo completa para su proposito: no dejes ideas partidas, datos clave fuera ni preguntas pendientes escondidas.
- No respondas con metacomentarios como "voy a empezar", "ahora me enfoco", "mi objetivo es" o "trabajare paso a paso" si todavia no has dado un resultado util.
- Yarbis eres tu, el asistente. No llames "Yarbis" al usuario salvo que el perfil indique explicitamente que ese es su nombre; si no conoces su nombre, hablale directamente en segunda persona.
- Si el usuario solo saluda, responde al usuario sin renombrarlo: nunca empieces con "Hola, Yarbis" salvo que el perfil diga explicitamente que el usuario se llama Yarbis.
- Si el usuario pide una accion directa o responde afirmativamente a una pregunta tuya ("si", "hazlo", "adelante", "procede"), interpreta eso como permiso para avanzar. No respondas con menus de opciones ni pidas otra confirmacion general.
- Si el objetivo aun no esta aterrizado, crea un plan corto con `set_plan` y tareas concretas con `add_task`.
- Si el usuario pide cambiar o reemplazar el objetivo principal de forma explicita, usa `update_goal`. No cambies el objetivo por iniciativa propia durante un pulso proactivo; si no es claro, pide confirmacion.
- Si el usuario pide mejorar tu rendimiento o velocidad, empieza con acciones verificables: revisa estado/autoconocimiento, crea plan/tareas, inspecciona codigo o configuracion relevante y corre tests seguros cuando aplique.
- Si falta un dato clave para avanzar bien (por ejemplo nicho, audiencia, tono, archivo exacto, formato o criterio de exito), no lo inventes.
- Si falta informacion publica, verificable o reciente, prioriza `web_search` y luego `fetch_web_page` antes de preguntarle al usuario.
- Usa `request_user_input` solo cuando falte contexto privado, preferencias, decisiones, archivos concretos o criterios que el usuario debe definir.
- Respeta la politica de internet visible en el estado. Si el usuario pide cambiarla, usa `update_internet_settings`.
- Cuando necesites una respuesta del usuario, usa `request_user_input` con una sola pregunta clara y concreta, explica brevemente por que falta ese dato y detente. No sigas produciendo contenido que dependa de esa respuesta.
- No uses el autoconocimiento como saludo ni como relleno. No te presentes con listas de capacidades salvo que el usuario pregunte que puedes hacer.
- No menciones sistema operativo, CPU, GPU, RAM, arquitectura o hardware salvo que el usuario lo pida o la tarea lo requiera. Si lo mencionas, copia valores verificados literalmente desde herramientas/autoconocimiento; nunca infieras marca o modelo. `AMD64` significa arquitectura x86_64, no procesador AMD.
- Manten las tareas sincronizadas: usa `update_task_status` para moverlas a `in_progress`, `blocked` o `done`.
- Si una tarea queda frenada por falta de informacion del usuario, marcalo con `update_task_status(..., status="blocked", result="...")`.
- Para consultar o eliminar notas persistentes, usa `list_notes`, `get_note` y `delete_note`.
- Tienes autoconocimiento local: identidad, mapa de codigo fuente, sistema operativo y hardware actual. Si necesitas refrescarlo o verlo completo, usa `self_overview`.
- El servicio administrado por SCM solo inicia, detiene o registra el proceso de fondo. No digas que SCM impide usar herramientas, ver notas, actualizar tareas o ejecutar ciclos; esas acciones dependen del servicio activo, permisos del proceso y herramientas disponibles. Instalar, quitar o reconfigurar el servicio puede requerir administrador.
- Antes de actuar a ciegas, revisa el estado con `agent_overview`, `list_tasks` o `list_notes`.
- Antes de razonar sobre tu propio codigo con detalle, usa `self_overview`, `list_files` o `read_text_file` segun haga falta.
- Antes de editar archivos de codigo, lee primero el archivo actual con `read_text_file`.
- `write_text_file` crea un checkpoint automatico y devuelve un diff. Usalo para cambios pequenos, enfocados y bien entendidos.
- Despues de modificar codigo o tests, ejecuta `run_project_tests`.
- Si un cambio rompe algo, revisa `list_checkpoints` y usa `restore_checkpoint` para volver al estado anterior.
- Despues de cada accion, evalua el siguiente mejor paso.
- Si una tarea ya quedo resuelta, dilo claramente y deja evidencia en el estado.
- Responde en espanol.
"""


def _print_output(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text)


def build_messages(state):
    user_name = str(state.get("profile", {}).get("name", "")).strip()
    user_line = (
        f"- El usuario actual es {user_name}."
        if user_name
        else "- El usuario actual no tiene nombre definido en el perfil."
    )
    memory_contract = (
        "Identidad y memoria compartida:\n"
        "- Tu nombre es Yarbis; Yarbis es el asistente, no el usuario.\n"
        f"{user_line}\n"
        "- Cuando saludes, no uses Yarbis como nombre del usuario salvo que el perfil lo diga explicitamente.\n"
        "- Interfaz, Telegram y pulso proactivo leen y escriben la misma memoria persistente en state.json.\n"
        "- El pulso proactivo ejecuta las mismas herramientas del agente para notas, tareas, plan y objetivo cuando hay instruccion explicita del usuario.\n"
        "- El estado guarda respuestas completas; cuando haya demasiado volumen, resume con criterio en la respuesta en vez de cortar texto.\n"
        "- Todo aprendizaje estable debe guardarse en perfil, notas, tareas o plan con herramientas.\n"
        "- Antes de asumir que olvidaste algo, revisa perfil, notas, tareas, plan y autoconocimiento."
    )
    state_summary = render_state_summary(
        state,
        task_limit=10,
        note_limit=10,
        include_runtime=False,
        include_last_result=False,
    )
    self_summary = render_self_knowledge_summary()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {
            "role": "system",
            "content": (
                f"{memory_contract}\n\n"
                f"Contexto actual del agente:\n{state_summary}\n\n"
                f"Autoconocimiento de Yarbis:\n{self_summary}"
            ),
        },
    ]

    messages.extend(state["messages"][-20:])
    return messages


def _record_assistant_message(state, content: str):
    awaiting_user_input = state.get("awaiting_user_input", {})

    def mutate(current_state):
        if awaiting_user_input.get("pending") and awaiting_user_input.get("question"):
            current_state["awaiting_user_input"] = awaiting_user_input
        current_state["messages"].append({
            "role": "assistant",
            "content": content,
        })
        current_state["last_result"] = content

    state_transaction("record_assistant_message", mutate)


def _user_is_named_yarbis(state) -> bool:
    user_name = str(state.get("profile", {}).get("name", "")).strip()
    return _normalize_intent_text(user_name) == "yarbis"


def _strip_yarbis_addressee_from_greeting_line(line: str) -> str:
    match = re.match(
        r"^(\s*(?:hola|buenas|buenos d.as|buenas tardes|buenas noches))\s*,?\s+yarbis\b(.*)$",
        str(line),
        flags=re.IGNORECASE,
    )
    if not match:
        return line

    greeting = match.group(1).strip()
    tail = match.group(2).strip()
    normalized_tail = _normalize_intent_text(tail)
    if not normalized_tail:
        return f"{greeting.capitalize()}."

    if tail[:1] in {".", ",", ":", ";", "!", "?", "¡", "¿"}:
        tail = tail[1:].strip()
    while tail and not tail[0].isalnum() and tail[0] not in {"¿", "¡"}:
        tail = tail[1:].strip()

    if not tail:
        return f"{greeting.capitalize()}."
    if tail[0] in {"¿", "¡"}:
        return f"{greeting.capitalize()}. {tail}"
    return f"{greeting.capitalize()}. {tail[:1].upper()}{tail[1:]}"


def _sanitize_assistant_identity(text: str, state) -> str:
    rendered = str(text).strip()
    if not rendered or _user_is_named_yarbis(state):
        return rendered

    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        lines[index] = _strip_yarbis_addressee_from_greeting_line(line)
        break

    return "\n".join(lines).strip()


def _exception_chain(exc: Exception):
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_timeout_error(exc: Exception) -> bool:
    for chained in _exception_chain(exc):
        error_type = type(chained).__name__.lower()
        error_text = str(chained).strip().lower()
        if "timeout" in error_type or "timed out" in error_text or "timeout" in error_text:
            return True
    return False


def _format_chat_error(exc: Exception) -> str:
    error_text = f"No pude consultar Ollama en este ciclo: {exc}"
    if not _is_timeout_error(exc):
        return error_text

    return (
        f"{error_text}\n\n"
        "Diagnostico: Ollama no respondio dentro del tiempo configurado "
        f"({OLLAMA_TIMEOUT_SECONDS}s) usando el modelo {MODEL}.\n"
        "Para resolverlo, verifica que Ollama este activo, calienta el modelo con "
        f"`ollama run {MODEL}`, aumenta el timeout en la interfaz grafica o con "
        "`YARBIS_OLLAMA_TIMEOUT_SECONDS`, o usa un modelo mas ligero desde la interfaz "
        "o con `YARBIS_MODEL`."
    )


def _handle_chat_error(state, exc: Exception):
    error_text = _format_chat_error(exc)
    _print_output(f"\nYarbis:\n{error_text}")
    _record_assistant_message(state, error_text)
    return {
        "status": "error",
        "content": error_text,
        "used_tools": False,
        "looks_meta": False,
        "needs_user_input": False,
        "error_type": type(exc).__name__,
    }


def _handle_empty_response(state):
    error_text = "El modelo devolvio una respuesta vacia en este ciclo."
    _print_output(f"\nYarbis:\n{error_text}")
    _record_assistant_message(state, error_text)
    return error_text


def _build_waiting_for_user_input_result(state, used_tools: bool = False) -> dict:
    question = state["awaiting_user_input"]["question"]
    message = "Estoy esperando una respuesta del usuario antes de continuar."
    if question:
        message = f"{message}\nPregunta pendiente: {question}"

    _print_output(f"\nYarbis:\n{message}")
    return {
        "status": "waiting_for_user_input",
        "content": message,
        "used_tools": used_tools,
        "looks_meta": False,
        "needs_user_input": True,
    }


def _looks_like_meta_response(text: str) -> bool:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return False

    meta_phrases = (
        "voy a empezar",
        "ahora me estoy enfocando",
        "ahora me enfoco",
        "mi objetivo es",
        "trabajar paso a paso",
        "trabajare paso a paso",
        "primer paso",
        "segundo ciclo",
        "primero creare",
    )
    matches = sum(phrase in normalized for phrase in meta_phrases)
    return len(normalized) < 500 and matches >= 2


def _last_message_content(state, role: str) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, dict) and message.get("role") == role:
            return str(message.get("content", "")).strip()
    return ""


def _last_user_authorized_action(state) -> bool:
    last_user_message = _last_message_content(state, "user")
    normalized = _normalize_intent_text(last_user_message)
    return (
        "usuario autorizo avanzar" in normalized
        or _looks_like_affirmative_action_reply(last_user_message)
    )


def _last_user_requested_action(state) -> bool:
    normalized = _normalize_intent_text(_last_message_content(state, "user"))
    if not normalized:
        return False

    action_phrases = (
        "mejora",
        "optimiza",
        "arregla",
        "corrige",
        "implementa",
        "ejecuta",
        "haz ",
        "crea",
        "actualiza",
        "analiza",
        "revisa",
    )
    return any(phrase in normalized for phrase in action_phrases)


def _numbered_option_count(text: str) -> int:
    return len(re.findall(r"(?m)^\s*\d+[\.\)]\s+", str(text)))


def _looks_like_choice_menu(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    asks_for_choice = any(
        phrase in normalized
        for phrase in (
            "que prefieres",
            "cual prefieres",
            "elige una",
            "elige opcion",
            "elige una opcion",
            "opciones",
        )
    )
    return asks_for_choice and _numbered_option_count(text) >= 2


def _looks_like_generic_help_prompt(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "en que puedo ayudarte hoy",
            "como puedo ayudarte hoy",
            "que puedo hacer por ti",
            "cuentame que necesitas hacer",
            "que necesitas mejorar o resolver hoy",
            "cual es lo que necesitas mejorar o resolver hoy",
        )
    )


def _looks_like_deferred_action_confirmation(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "te gustaria que implemente",
            "quieres que implemente",
            "deseas que implemente",
            "confirmas que avance",
            "lo implemento ahora",
            "lo hago ahora",
        )
    )


def _looks_like_unverified_action_claim(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "he realizado",
            "he implementado",
            "implemente",
            "actualice",
            "modifique",
            "reduje",
            "ejecute",
            "he creado",
        )
    )


def _last_user_asked_for_identity_or_capabilities(state) -> bool:
    normalized = _normalize_intent_text(_last_message_content(state, "user"))
    if not normalized:
        return False

    return any(
        phrase in normalized
        for phrase in (
            "quien eres",
            "que eres",
            "presentate",
            "que puedes hacer",
            "como me puedes ayudar",
            "cuales son tus capacidades",
            "lista tus capacidades",
        )
    )


def _has_normalized_phrase(normalized: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized) is not None


def _last_user_asked_about_environment(state) -> bool:
    normalized = _normalize_intent_text(_last_message_content(state, "user"))
    if not normalized:
        return False

    environment_terms = (
        "sistema operativo",
        "hardware",
        "equipo",
        "maquina",
        "procesador",
        "cpu",
        "gpu",
        "ram",
        "arquitectura",
        "windows",
        "amd",
        "ryzen",
        "intel",
        "autoconocimiento",
        "self overview",
        "entorno local",
    )
    return any(_has_normalized_phrase(normalized, term) for term in environment_terms)


def _looks_like_unsolicited_self_intro_or_capabilities(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    intro_phrases = (
        "soy yarbis",
        "tu agente local",
        "agente local optimizado",
        "como tu agente local",
    )
    if any(phrase in normalized for phrase in intro_phrases):
        return True

    has_capability_menu = (
        "puedo" in normalized
        and (
            _numbered_option_count(text) >= 2
            or re.search(r"(?mi)^\s*puedo\s*:", str(text)) is not None
        )
    )
    return bool(has_capability_menu)


def _looks_like_environment_claim(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    environment_terms = (
        "sistema operativo",
        "windows 11",
        "windows 10",
        "arquitectura",
        "amd64",
        "x86 64",
        "cpu",
        "gpu",
        "ram",
        "procesador",
        "ryzen",
        "genuineintel",
        "intel64",
    )
    return any(_has_normalized_phrase(normalized, term) for term in environment_terms)


def _looks_like_non_actionable_prompt(text: str) -> bool:
    return (
        _looks_like_choice_menu(text)
        or _looks_like_generic_help_prompt(text)
        or _looks_like_deferred_action_confirmation(text)
    )


def _should_reject_non_actionable_final(
    state,
    text: str,
    used_tools: bool,
    action_tools_used: bool = False,
) -> bool:
    if used_tools and action_tools_used:
        return False

    if used_tools:
        if not (_last_user_authorized_action(state) or _last_user_requested_action(state)):
            return False
        return (
            _looks_like_non_actionable_prompt(text)
            or _looks_like_unverified_action_claim(text)
        )

    if (
        _looks_like_unsolicited_self_intro_or_capabilities(text)
        and not _last_user_asked_for_identity_or_capabilities(state)
    ):
        return True

    if _looks_like_environment_claim(text) and not _last_user_asked_about_environment(state):
        return True

    if not (_last_user_authorized_action(state) or _last_user_requested_action(state)):
        return False

    return (
        _looks_like_non_actionable_prompt(text)
        or _looks_like_unverified_action_claim(text)
    )


def _safe_rejected_final_message(state, text: str) -> str:
    if _looks_like_environment_claim(text) and not _last_user_asked_about_environment(state):
        return (
            "No tengo una tarea concreta registrada. Dime que quieres que haga y "
            "avanzare sin inventar datos del equipo."
        )

    if _last_user_authorized_action(state) or _last_user_requested_action(state):
        return (
            "No complete una accion verificable en este ciclo. Necesito una "
            "instruccion mas concreta o un dato faltante para avanzar bien."
        )

    return "Dime que quieres que haga y avanzare sin presentarme ni listar capacidades."


def _looks_like_waiting_for_instructions(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    negated_phrases = (
        "no espero instrucciones",
        "no estoy a la espera",
        "no quedo a la espera",
        "sin esperar instrucciones",
        "sin esperar indicaciones",
    )
    if any(phrase in normalized for phrase in negated_phrases):
        return False

    return bool(re.search(
        (
            r"\b(?:a la espera|en espera|esperando|pendiente|atento|"
            r"listo para recibir|listo para tus)\s+"
            r"(?:de\s+|a\s+)?(?:tus\s+|nuevas\s+|mas\s+|las\s+)?"
            r"(?:instrucciones|indicaciones|ordenes)\b"
        ),
        normalized,
    ))


def _has_open_tasks(state) -> bool:
    return any(task["status"] in {"pending", "in_progress", "blocked"} for task in state["tasks"])


def _is_waiting_for_user_input(state) -> bool:
    awaiting_user_input = state.get("awaiting_user_input", {})
    return bool(
        awaiting_user_input.get("pending")
        and str(awaiting_user_input.get("question", "")).strip()
    )


def _extract_user_input_request(text: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in str(text).split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return ""

    interactive_phrases = (
        "deseas",
        "prefieres",
        "quieres que",
        "puedes decirme",
        "necesito que me digas",
        "necesito saber",
        "para continuar",
        "para seguir",
        "antes de continuar",
        "antes de seguir",
        "comparteme",
        "confirmame",
        "confirma",
        "que",
        "qué",
        "cual",
        "cuál",
        "cuales",
        "cuáles",
        "quien",
        "quién",
        "como",
        "cómo",
        "donde",
        "dónde",
        "cuando",
        "cuándo",
        "cuanto",
        "cuánto",
    )

    candidates = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        for line in lines:
            candidate = re.sub(r"^[-*•\d\.\)\s]+", "", line).strip()
            if candidate:
                candidates.append(candidate)
        candidates.append(paragraph)

    for candidate in candidates:
        if "?" not in candidate:
            continue

        normalized = " ".join(candidate.lower().split())
        if any(phrase in normalized for phrase in interactive_phrases):
            return candidate

    if _looks_like_waiting_for_instructions(text):
        return WAITING_FOR_INSTRUCTIONS_QUESTION

    return ""


def run_one_cycle(max_steps=None):
    state = load_state()
    if _is_waiting_for_user_input(state):
        return _build_waiting_for_user_input_result(state)

    state_transaction(
        "run_one_cycle_increment",
        lambda current_state: current_state.__setitem__(
            "cycle_count",
            current_state["cycle_count"] + 1,
        ),
    )
    state = load_state()

    if max_steps is None:
        max_steps = state["autonomy"]["max_steps_per_cycle"]

    model, _timeout_seconds, ollama_client = _apply_ollama_runtime_settings(state)

    print(f"\n=== CICLO {state['cycle_count']} ===")
    used_tools = False
    action_tools_used = False
    empty_response_retries = 0
    non_actionable_retries = 0

    for step in range(1, max_steps + 1):
        print(f"\n--- Paso {step} ---")

        try:
            response = ollama_client.chat(
                model=model,
                messages=build_messages(state),
                tools=tool_definitions,
                think=False,
            )
        except Exception as exc:
            return _handle_chat_error(state, exc)

        assistant_message = response.message
        assistant_content = assistant_message.content or ""

        if assistant_message.tool_calls:
            used_tools = True
            print("Decidi usar herramientas.")

            assistant_tool_message = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [
                    {
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        }
                    }
                    for tool_call in assistant_message.tool_calls
                ],
            }
            state_transaction(
                "record_assistant_tool_calls",
                lambda current_state: current_state["messages"].append(assistant_tool_message),
            )
            state = load_state()

            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                raw_tool_args = tool_call.function.arguments
                tool_args, tool_args_error = _normalize_tool_arguments(raw_tool_args)

                print(f"\n> Ejecutando tool: {tool_name}")
                print(f"> Argumentos: {raw_tool_args}")

                function_to_call = available_functions.get(tool_name)
                if not function_to_call:
                    tool_output = f"Tool no encontrada: {tool_name}"
                elif tool_args_error:
                    tool_output = tool_args_error
                else:
                    try:
                        tool_output = function_to_call(**tool_args)
                    except Exception as exc:
                        tool_output = f"Error ejecutando {tool_name}: {exc}"
                    else:
                        if _tool_call_proves_action(tool_name, tool_output):
                            action_tools_used = True

                _print_output(f"> Resultado:\n{tool_output}")

                def record_tool_output(current_state):
                    current_state["messages"].append({
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": str(tool_output),
                    })
                    current_state["last_result"] = str(tool_output)

                state_transaction("record_tool_output", record_tool_output)
                state = load_state()

            if _is_waiting_for_user_input(state):
                return _build_waiting_for_user_input_result(state, used_tools=used_tools)
            continue

        final_text = assistant_content.strip()
        if not final_text:
            if empty_response_retries < EMPTY_RESPONSE_RETRIES:
                empty_response_retries += 1
                retry_message = {
                    "role": "user",
                    "content": (
                        "Tu respuesta anterior llego vacia. Responde ahora con una salida util, "
                        "concreta y final para este ciclo."
                    ),
                }
                state_transaction(
                    "record_empty_response_retry",
                    lambda current_state: current_state["messages"].append(retry_message),
                )
                state = load_state()
                continue

            final_text = _handle_empty_response(state)
            return {
                "status": "empty",
                "content": final_text,
                "used_tools": used_tools,
                "action_tools_used": action_tools_used,
                "looks_meta": False,
            }

        non_actionable_final = _should_reject_non_actionable_final(
            state,
            final_text,
            used_tools=used_tools,
            action_tools_used=action_tools_used,
        )
        if (
            non_actionable_final
            and non_actionable_retries < MAX_NON_ACTIONABLE_RETRIES
            and step < max_steps
        ):
            non_actionable_retries += 1
            def record_non_actionable_retry(current_state):
                current_state["messages"].append({
                    "role": "assistant",
                    "content": final_text,
                })
                current_state["messages"].append({
                    "role": "user",
                    "content": NON_ACTIONABLE_RETRY_MESSAGE,
                })

            state_transaction("record_non_actionable_retry", record_non_actionable_retry)
            state = load_state()
            continue

        if non_actionable_final:
            final_text = _safe_rejected_final_message(state, final_text)

        final_text = _sanitize_assistant_identity(final_text, state)
        _print_output(f"\nYarbis:\n{final_text}")

        pending_question = ""
        if not (non_actionable_final and _looks_like_non_actionable_prompt(final_text)):
            pending_question = _extract_user_input_request(final_text)
        if pending_question and not _is_waiting_for_user_input(state):
            state["awaiting_user_input"] = {
                "pending": True,
                "question": pending_question,
                "reason": "Yarbis necesita una respuesta del usuario para continuar.",
                "fields": [],
            }
        _record_assistant_message(state, final_text)
        return {
            "status": "final",
            "content": final_text,
            "used_tools": used_tools,
            "action_tools_used": action_tools_used,
            "looks_meta": _looks_like_meta_response(final_text),
            "needs_user_input": _is_waiting_for_user_input(load_state()),
        }

    final_text = f"Se alcanzo el maximo de pasos ({max_steps}) sin una respuesta final."
    _print_output(f"\nYarbis:\n{final_text}")
    _record_assistant_message(state, final_text)
    return {
        "status": "max_steps",
        "content": final_text,
        "used_tools": used_tools,
        "action_tools_used": action_tools_used,
        "looks_meta": False,
    }


def run_autonomous_session(cycles=None):
    state = load_state()
    if cycles is None:
        cycles = state["autonomy"]["auto_cycles_default"]

    completed_cycles = 0
    saw_tasks = bool(state["tasks"])

    for _ in range(cycles):
        current_state = load_state()
        if _is_waiting_for_user_input(current_state):
            print("\nYarbis: estoy esperando una respuesta del usuario antes de continuar.")
            print(f"Pregunta pendiente: {current_state['awaiting_user_input']['question']}")
            break

        cycle_result = run_one_cycle()
        if not isinstance(cycle_result, dict):
            cycle_result = {
                "status": "error",
                "content": str(cycle_result),
                "used_tools": False,
                "looks_meta": False,
                "needs_user_input": False,
            }
        completed_cycles += 1

        updated_state = load_state()
        saw_tasks = saw_tasks or bool(updated_state["tasks"])
        if cycle_result.get("needs_user_input") or _is_waiting_for_user_input(updated_state):
            print("\nYarbis: falta informacion del usuario. Deteniendo modo autonomo.")
            break

        if saw_tasks and not _has_open_tasks(updated_state):
            print("\nYarbis: no quedan tareas abiertas. Deteniendo modo autonomo.")
            break

        if (
            not _has_open_tasks(updated_state)
            and cycle_result["status"] in {"final", "empty", "error"}
            and not cycle_result["used_tools"]
        ):
            print("\nYarbis: no hay tareas abiertas y este ciclo ya cerro sin seguimiento adicional.")
            break

        if cycle_result["looks_meta"] and not _has_open_tasks(updated_state):
            print("\nYarbis: el modelo se quedo describiendo el proceso sin abrir tareas. Deteniendo modo autonomo.")
            break

    return completed_cycles
