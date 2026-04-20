from memory import load_state, save_state
from agent import run_one_cycle


def main():
    state = load_state()

    print("=== YARBIS ===")
    print(f"Objetivo actual: {state['goal']}")
    print("Comandos: goal, run, auto, exit\n")

    while True:
        cmd = input(">>> ").strip()

        if cmd == "exit":
            break

        elif cmd == "goal":
            new_goal = input("Nuevo objetivo: ").strip()
            state = load_state()
            state["goal"] = new_goal
            state["messages"].append({
                "role": "user",
                "content": f"Tu objetivo actual es: {new_goal}"
            })
            save_state(state)
            print("Objetivo actualizado.")

        elif cmd == "run":
            run_one_cycle()

        elif cmd == "auto":
            cycles = input("¿Cuántos ciclos?: ").strip()
            try:
                cycles = int(cycles)
            except ValueError:
                print("Número inválido.")
                continue

            for _ in range(cycles):
                run_one_cycle()

        else:
            print("Comando no reconocido.")


if __name__ == "__main__":
    main()