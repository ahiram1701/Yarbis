from ollama import Client

from memory import load_state, save_state
from tools import list_files, read_text_file, write_text_file

MODEL = "qwen3.5:2b"
OLLAMA_TIMEOUT_SECONDS = 60

client = Client(timeout=OLLAMA_TIMEOUT_SECONDS)
tool_definitions = [list_files, read_text_file, write_text_file]

available_functions = {
    "list_files": list_files,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
}

SYSTEM_PROMPT = """
Eres Yarbis, un agente autonomo local.
Tu trabajo es avanzar paso a paso hacia el objetivo del usuario.

Reglas:
- Se util, preciso y orientado a acciones.
- Usa herramientas cuando sea necesario.
- No inventes resultados de herramientas.
- Trabaja en pasos pequenos y claros.
- Despues de cada accion, evalua que sigue.
- Si una tarea ya quedo resuelta, dilo claramente.
- Responde en espanol.
"""


def build_messages(state):
    memory_block = "\n".join(f"- {note}" for note in state["notes"]) or "Sin notas previas."
    last_result = state.get("last_result", "")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": f"""
Objetivo actual:
{state['goal']}

Notas guardadas:
{memory_block}

Ultimo resultado:
{last_result}
""".strip(),
        },
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
    print(f"\nYarbis:\n{error_text}")
    _record_assistant_message(state, error_text)


def run_one_cycle(max_steps=5):
    state = load_state()
    state["cycle_count"] += 1

    print(f"\n=== CICLO {state['cycle_count']} ===")

    for step in range(1, max_steps + 1):
        print(f"\n--- Paso {step} ---")

        try:
            response = client.chat(
                model=MODEL,
                messages=build_messages(state),
                tools=tool_definitions,
            )
        except Exception as exc:
            _handle_chat_error(state, exc)
            return

        assistant_message = response.message
        assistant_content = assistant_message.content or ""

        if assistant_message.tool_calls:
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

                print(f"> Resultado:\n{tool_output}")

                state["messages"].append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": str(tool_output),
                })
                state["last_result"] = str(tool_output)

            save_state(state)
            continue

        final_text = assistant_content.strip() or "(respuesta vacia del modelo)"
        print(f"\nYarbis:\n{final_text}")
        _record_assistant_message(state, final_text)
        return

    final_text = f"Se alcanzo el maximo de pasos ({max_steps}) sin una respuesta final."
    print(f"\nYarbis:\n{final_text}")
    _record_assistant_message(state, final_text)
