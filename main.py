from agent import run_autonomous_session, run_one_cycle
from memory import load_state, render_state_summary, save_state
from tools import add_task, save_note, update_profile


def _has_pending_user_question(state) -> bool:
    awaiting_user_input = state.get("awaiting_user_input", {})
    return bool(
        awaiting_user_input.get("pending")
        and str(awaiting_user_input.get("question", "")).strip()
    )


def _clear_pending_user_question(state):
    state["awaiting_user_input"] = {
        "pending": False,
        "question": "",
        "reason": "",
        "fields": [],
    }


def _submit_user_reply(reply_text: str):
    cleaned_reply = str(reply_text).strip()
    if not cleaned_reply:
        print("La respuesta no puede quedar vacia.")
        return

    state = load_state()
    state["messages"].append({
        "role": "user",
        "content": cleaned_reply,
    })
    _clear_pending_user_question(state)
    save_state(state)

    print("Respuesta guardada. Ejecutando un ciclo con esta informacion.")
    run_one_cycle()


def main():
    state = load_state()

    print("=== YARBIS ===")
    print(f"Objetivo actual: {state['goal']}")
    print("Comandos: goal, run, auto, status, profile, note, task, reply, exit")
    if _has_pending_user_question(state):
        print(f"Pendiente: {state['awaiting_user_input']['question']}")
    print()

    while True:
        try:
            cmd = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaliendo.")
            break

        if not cmd:
            continue

        if cmd == "exit":
            break

        if cmd == "goal":
            try:
                new_goal = input("Nuevo objetivo: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nOperacion cancelada.")
                continue

            if not new_goal:
                print("El objetivo no puede quedar vacio.")
                continue

            state = load_state()
            state["goal"] = new_goal
            state["messages"] = []
            state["tasks"] = []
            state["current_plan"] = []
            state["last_result"] = ""
            _clear_pending_user_question(state)
            state["messages"].append({
                "role": "user",
                "content": f"Tu objetivo actual es: {new_goal}",
            })
            save_state(state)
            print("Objetivo actualizado. Contexto operativo reiniciado para el nuevo objetivo.")
            continue

        if cmd == "run":
            state = load_state()
            if _has_pending_user_question(state):
                print("Yarbis esta esperando tu respuesta antes de continuar.")
                print(f"Pregunta pendiente: {state['awaiting_user_input']['question']}")
                print("Usa `reply` o escribe la respuesta directamente en la consola.")
                continue

            run_one_cycle()
            continue

        if cmd == "status":
            state = load_state()
            print(render_state_summary(state))
            continue

        if cmd == "profile":
            print("Deja un campo vacio para no cambiarlo. Usa [clear] para borrarlo.")

            try:
                name = input("Nombre: ").strip()
                role = input("Rol o contexto: ").strip()
                preferences = input("Preferencias (coma o salto de linea): ").strip()
                constraints = input("Restricciones (coma o salto de linea): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nOperacion cancelada.")
                continue

            print(update_profile(
                name=name,
                role=role,
                preferences=preferences,
                constraints=constraints,
            ))
            continue

        if cmd == "note":
            try:
                title = input("Titulo de la nota: ").strip()
                content = input("Contenido: ").strip()
                category = input("Categoria (opcional): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nOperacion cancelada.")
                continue

            print(save_note(title=title, content=content, category=category or "general"))
            continue

        if cmd == "task":
            try:
                title = input("Titulo de la tarea: ").strip()
                details = input("Detalles (opcional): ").strip()
                priority = input("Prioridad [alta/media/baja]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nOperacion cancelada.")
                continue

            print(add_task(title=title, details=details, priority=priority or "media"))
            continue

        if cmd == "reply":
            try:
                reply_text = input("Tu respuesta: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nOperacion cancelada.")
                continue

            _submit_user_reply(reply_text)
            continue

        if cmd == "auto":
            state = load_state()
            if _has_pending_user_question(state):
                print("Yarbis esta esperando tu respuesta antes de continuar.")
                print(f"Pregunta pendiente: {state['awaiting_user_input']['question']}")
                print("Usa `reply` o escribe la respuesta directamente en la consola.")
                continue

            try:
                cycles_text = input("Cuantos ciclos? (vacio = por defecto): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nOperacion cancelada.")
                continue

            if not cycles_text:
                cycles = None
            else:
                try:
                    cycles = int(cycles_text)
                except ValueError:
                    print("Numero invalido.")
                    continue

            if cycles is not None and cycles <= 0:
                print("Debes indicar un numero mayor que cero.")
                continue

            executed_cycles = run_autonomous_session(cycles=cycles)
            print(f"Modo autonomo ejecutado por {executed_cycles} ciclo(s).")
            continue

        state = load_state()
        if _has_pending_user_question(state):
            _submit_user_reply(cmd)
            continue

        print("Comando no reconocido. Usa `reply` para enviar contexto libre al agente.")


if __name__ == "__main__":
    main()
