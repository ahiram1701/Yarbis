from ollama import Client
from memory import load_state, save_state
from tools import list_files, read_text_file, write_text_file

MODEL = "qwen3.5:2b"

client = Client()

available_functions = {
    "list_files": list_files,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
}

SYSTEM_PROMPT = """
Eres Yarbis, un agente autónomo local.
Tu trabajo es avanzar paso a paso hacia el objetivo del usuario.

Reglas:
- Sé útil, preciso y orientado a acciones.
- Usa herramientas cuando sea necesario.
- No inventes resultados de herramientas.
- Trabaja en pasos pequeños y claros.
- Después de cada acción, evalúa qué sigue.
- Si una tarea ya quedó resuelta, dilo claramente.
- Responde en español.
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

Último resultado:
{last_result}
""".strip()
        }
    ]

    messages.extend(state["messages"][-20:])
    return messages


def run_one_cycle(max_steps=5):
    state = load_state()
    state["cycle_count"] += 1

    print(f"\n=== CICLO {state['cycle_count']} ===")

    for step in range(1, max_steps + 1):
        print(f"\n--- Paso {step} ---")

        response = client.chat(
            model=MODEL,
            messages=build_messages(state),
            tools=[list_files, read_text_file, write_text_file],
        )

        assistant_message = response.message
        assistant_content = assistant_message.content or ""

        if assistant_message.tool_calls:
            print("Yarbis decidió usar tools.")

            state["messages"].append({
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [
                    {
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in assistant_message.tool_calls
                ]
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
                    except Exception as e:
                        tool_output = f"Error ejecutando {tool_name}: {e}"

                print(f"> Resultado:\n{tool_output}")

                state["messages"].append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": str(tool_output)
                })

                state["last_result"] = str(tool_output)

            save_state(state)
            continue

        else:
            final_text = assistant_content.strip()

            if not final_text:
                final_text = "(respuesta vacía del modelo)"

            print(f"\nYarbis:\n{final_text}")

            state["messages"].append({
                "role": "assistant",
                "content": final_text
            })

            state["last_result"] = final_text
            save_state(state)
            return

    final_text = f"Se alcanzó el máximo de pasos ({max_steps}) sin una respuesta final."
    print(f"\nYarbis:\n{final_text}")

    state["messages"].append({
        "role": "assistant",
        "content": final_text
    })
    state["last_result"] = final_text
    save_state(state)