from memory import load_state, render_state_summary
from session import (
    has_pending_user_question,
    run_auto_with_output,
    run_cycle_with_output,
    run_self_analysis_with_output,
    run_startup_self_analysis,
    is_self_analysis_request,
    submit_user_reply,
    update_goal,
)
from telegram_inbox import start_telegram_polling, stop_telegram_polling
from tools import add_task, save_note, self_overview, update_profile


def main():
    startup_message = run_startup_self_analysis()
    state = load_state()
    start_telegram_polling()

    try:
        print("=== YARBIS ===")
        print(startup_message)
        print(f"Objetivo actual: {state['goal']}")
        print("Comandos: goal, run, auto, status, self, profile, note, task, reply, exit")
        if has_pending_user_question(state):
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

                print(update_goal(new_goal))
                continue

            if cmd == "run":
                state = load_state()
                if has_pending_user_question(state):
                    print("Yarbis esta esperando tu respuesta antes de continuar.")
                    print(f"Pregunta pendiente: {state['awaiting_user_input']['question']}")
                    print("Usa `reply` o escribe la respuesta directamente en la consola.")
                    continue

                print(run_cycle_with_output())
                continue

            if cmd == "status":
                state = load_state()
                print(render_state_summary(state))
                continue

            if cmd == "self":
                print(self_overview(refresh=True))
                continue

            if is_self_analysis_request(cmd):
                print(run_self_analysis_with_output())
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

                try:
                    print(submit_user_reply(reply_text))
                except ValueError as exc:
                    print(exc)
                continue

            if cmd == "auto":
                state = load_state()
                if has_pending_user_question(state):
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

                print(run_auto_with_output(cycles=cycles))
                continue

            state = load_state()
            if has_pending_user_question(state):
                try:
                    print(submit_user_reply(cmd))
                except ValueError as exc:
                    print(exc)
                continue

            print("Comando no reconocido. Usa `reply` para enviar contexto libre al agente.")
    finally:
        stop_telegram_polling()


if __name__ == "__main__":
    main()
