from agent import run_one_cycle
from memory import load_state, save_state


def main():
    state = load_state()

    print("=== YARBIS ===")
    print(f"Objetivo actual: {state['goal']}")
    print("Comandos: goal, run, auto, exit\n")

    while True:
        try:
            cmd = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaliendo.")
            break

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
            state["messages"].append({
                "role": "user",
                "content": f"Tu objetivo actual es: {new_goal}",
            })
            save_state(state)
            print("Objetivo actualizado.")
            continue

        if cmd == "run":
            run_one_cycle()
            continue

        if cmd == "auto":
            try:
                cycles_text = input("Cuantos ciclos?: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nOperacion cancelada.")
                continue

            try:
                cycles = int(cycles_text)
            except ValueError:
                print("Numero invalido.")
                continue

            if cycles <= 0:
                print("Debes indicar un numero mayor que cero.")
                continue

            for _ in range(cycles):
                run_one_cycle()
            continue

        print("Comando no reconocido.")


if __name__ == "__main__":
    main()
