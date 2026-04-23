import os
import re
import sys

from ollama import Client

from memory import load_state, render_state_summary, save_state
from tools import (
    add_task,
    agent_overview,
    list_files,
    list_notes,
    list_tasks,
    read_text_file,
    request_user_input,
    save_note,
    set_plan,
    update_profile,
    update_task_status,
    write_text_file,
)

DEFAULT_MODEL = "qwen3.5:2b"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 600
DEFAULT_EMPTY_RESPONSE_RETRIES = 1


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
EMPTY_RESPONSE_RETRIES = max(
    0,
    _get_env_int("YARBIS_EMPTY_RESPONSE_RETRIES", DEFAULT_EMPTY_RESPONSE_RETRIES),
)

client = Client(timeout=OLLAMA_TIMEOUT_SECONDS)
tool_definitions = [
    agent_overview,
    update_profile,
    request_user_input,
    save_note,
    list_notes,
    add_task,
    list_tasks,
    update_task_status,
    set_plan,
    list_files,
    read_text_file,
    write_text_file,
]

available_functions = {
    "agent_overview": agent_overview,
    "update_profile": update_profile,
    "request_user_input": request_user_input,
    "save_note": save_note,
    "list_notes": list_notes,
    "add_task": add_task,
    "list_tasks": list_tasks,
    "update_task_status": update_task_status,
    "set_plan": set_plan,
    "list_files": list_files,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
}

SYSTEM_PROMPT = """
Eres Yarbis, un agente inteligente personal, autonomo y local.
Tu trabajo es avanzar paso a paso hacia el objetivo del usuario y organizar el trabajo para que siga progresando entre ciclos.

Reglas:
- Se util, preciso y orientado a acciones.
- Usa herramientas cuando sea necesario.
- No inventes resultados de herramientas.
- Trabaja en pasos pequenos y claros.
- Si la mejor salida del ciclo es texto util para el usuario, entregalo directamente en este ciclo.
- No respondas con metacomentarios como "voy a empezar", "ahora me enfoco", "mi objetivo es" o "trabajare paso a paso" si todavia no has dado un resultado util.
- Si el objetivo aun no esta aterrizado, crea un plan corto con `set_plan` y tareas concretas con `add_task`.
- Si falta un dato clave para avanzar bien (por ejemplo nicho, audiencia, tono, archivo exacto, formato o criterio de exito), no lo inventes.
- Cuando necesites una respuesta del usuario, usa `request_user_input` con una sola pregunta clara y concreta, explica brevemente por que falta ese dato y detente. No sigas produciendo contenido que dependa de esa respuesta.
- Manten las tareas sincronizadas: usa `update_task_status` para moverlas a `in_progress`, `blocked` o `done`.
- Si una tarea queda frenada por falta de informacion del usuario, marcalo con `update_task_status(..., status="blocked", result="...")`.
- Guarda contexto personal estable con `update_profile` y hallazgos utiles con `save_note`.
- Antes de actuar a ciegas, revisa el estado con `agent_overview`, `list_tasks` o `list_notes`.
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
    state_summary = render_state_summary(state, task_limit=10, note_limit=5)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "system", "content": f"Contexto actual del agente:\n{state_summary}"},
    ]

    messages.extend(state["messages"][-20:])
    return messages


def _record_assistant_message(state, content: str):
    state["messages"].append({
        "role": "assistant",
        "content": content,
    })
    state["last_result"] = content
    save_state(state)


def _handle_chat_error(state, exc: Exception):
    error_text = f"No pude consultar Ollama en este ciclo: {exc}"
    _print_output(f"\nYarbis:\n{error_text}")
    _record_assistant_message(state, error_text)
    return error_text


def _handle_empty_response(state):
    error_text = "El modelo devolvio una respuesta vacia en este ciclo."
    _print_output(f"\nYarbis:\n{error_text}")
    _record_assistant_message(state, error_text)
    return error_text


def _build_waiting_for_user_input_result(state, used_tools: bool = False) -> dict:
    question = state["awaiting_user_input"]["question"]
    message = "Yarbis esta esperando una respuesta del usuario antes de continuar."
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

    return ""


def run_one_cycle(max_steps=None):
    state = load_state()
    if _is_waiting_for_user_input(state):
        return _build_waiting_for_user_input_result(state)

    state["cycle_count"] += 1
    save_state(state)

    if max_steps is None:
        max_steps = state["autonomy"]["max_steps_per_cycle"]

    print(f"\n=== CICLO {state['cycle_count']} ===")
    used_tools = False
    empty_response_retries = 0

    for step in range(1, max_steps + 1):
        print(f"\n--- Paso {step} ---")

        try:
            response = client.chat(
                model=MODEL,
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
            print("Yarbis decidio usar tools.")

            state["messages"].append({
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
            })
            save_state(state)

            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments

                print(f"\n> Ejecutando tool: {tool_name}")
                print(f"> Argumentos: {tool_args}")

                function_to_call = available_functions.get(tool_name)
                if not function_to_call:
                    tool_output = f"Tool no encontrada: {tool_name}"
                else:
                    try:
                        tool_output = function_to_call(**tool_args)
                    except Exception as exc:
                        tool_output = f"Error ejecutando {tool_name}: {exc}"

                _print_output(f"> Resultado:\n{tool_output}")

                refreshed_state = load_state()
                refreshed_state["messages"].append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": str(tool_output),
                })
                refreshed_state["last_result"] = str(tool_output)
                save_state(refreshed_state)
                state = refreshed_state

            if _is_waiting_for_user_input(state):
                return _build_waiting_for_user_input_result(state, used_tools=used_tools)
            continue

        final_text = assistant_content.strip()
        if not final_text:
            if empty_response_retries < EMPTY_RESPONSE_RETRIES:
                empty_response_retries += 1
                state["messages"].append({
                    "role": "user",
                    "content": (
                        "Tu respuesta anterior llego vacia. Responde ahora con una salida util, "
                        "concreta y final para este ciclo."
                    ),
                })
                save_state(state)
                continue

            final_text = _handle_empty_response(state)
            return {
                "status": "empty",
                "content": final_text,
                "used_tools": used_tools,
                "looks_meta": False,
            }

        _print_output(f"\nYarbis:\n{final_text}")
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
