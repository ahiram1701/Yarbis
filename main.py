import yarbis_instance

yarbis_instance.configure_from_argv()
yarbis_instance.ensure_instance_registered()

from memory import load_state, render_state_summary
from session import (
    coding_apply_proposal_text,
    coding_detect_validation_command_text,
    coding_discard_proposal_text,
    coding_git_status_text,
    coding_get_proposal_text,
    coding_list_proposals_text,
    coding_run_validation_text,
    coding_set_workspace_text,
    coding_update_validation_command_text,
    coding_workspace_overview_text,
    delete_note_text,
    get_note_text,
    has_pending_user_question,
    handle_note_text_request,
    list_notes_text,
    run_auto_with_output,
    run_cycle_with_output,
    run_self_analysis_with_output,
    run_startup_self_analysis,
    save_note_text,
    is_self_analysis_request,
    submit_user_reply,
    update_goal,
)
from service_manager import is_service_running
from telegram_inbox import start_telegram_polling, stop_telegram_polling
from tools import add_task, self_overview, update_profile


COMMAND_HELP = """Comandos principales:
  goal          Cambiar el objetivo
  run           Ejecutar un ciclo
  auto          Ejecutar ciclos hasta terminar o hasta el límite indicado
  status        Ver estado completo
  reply         Enviar respuesta o contexto libre

Trabajo diario:
  coding        Ver workspace, propuestas y validación
  profile       Editar perfil
  note/notas    Crear, ver, listar o borrar notas
  task          Crear tarea
  self          Refrescar autoanálisis

Sistema:
  help          Ver esta ayuda
  exit          Salir
"""


def main():
    startup_message = run_startup_self_analysis(force=False, background=True)
    state = load_state()
    telegram_polling_started = False
    if not is_service_running():
        start_telegram_polling()
        telegram_polling_started = True

    try:
        print("=== YARBIS ===")
        print(startup_message)
        if not telegram_polling_started:
            print("Servicio de fondo activo: Telegram queda atendido por el servicio.")
        print(f"Objetivo actual: {state['goal']}")
        print("Comandos principales: run, auto, status, reply. Escribe help para ver todo.")
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

            if cmd in {"help", "ayuda", "?"}:
                print(COMMAND_HELP)
                continue

            if cmd == "goal":
                try:
                    new_goal = input("Nuevo objetivo: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nOperación cancelada.")
                    continue

                if not new_goal:
                    print("El objetivo no puede quedar vacío.")
                    continue

                print(update_goal(new_goal))
                continue

            if cmd == "run":
                state = load_state()
                if has_pending_user_question(state):
                    print("Estoy esperando tu respuesta antes de continuar.")
                    print(f"Pregunta pendiente: {state['awaiting_user_input']['question']}")
                    print("Usa `reply` o escribe la respuesta directamente en la consola.")
                    continue

                print(run_cycle_with_output())
                continue

            if cmd == "status":
                state = load_state()
                print(render_state_summary(state))
                continue

            if cmd == "coding":
                print(coding_workspace_overview_text())
                continue

            if cmd.startswith("coding "):
                coding_args = cmd.split(maxsplit=2)
                coding_action = coding_args[1].strip().lower() if len(coding_args) > 1 else ""
                coding_value = coding_args[2].strip() if len(coding_args) > 2 else ""

                if coding_action == "workspace":
                    if not coding_value:
                        try:
                            coding_value = input("Ruta del repositorio: ").strip()
                        except (EOFError, KeyboardInterrupt):
                            print("\nOperación cancelada.")
                            continue
                    print(coding_set_workspace_text(coding_value))
                    continue

                if coding_action in {"status", "git"}:
                    print(coding_git_status_text())
                    continue

                if coding_action in {"proposals", "propuestas"}:
                    print(coding_list_proposals_text(status="pending", limit=20))
                    continue

                if coding_action in {"get", "ver"}:
                    if not coding_value:
                        print("Uso: coding get <proposal_id>")
                        continue
                    print(coding_get_proposal_text(coding_value))
                    continue

                if coding_action == "apply":
                    if not coding_value:
                        print("Uso: coding apply <proposal_id>")
                        continue
                    print(coding_apply_proposal_text(coding_value))
                    continue

                if coding_action in {"validate", "validar"}:
                    print(coding_run_validation_text(proposal_id=coding_value))
                    continue

                if coding_action in {"validation", "validacion"}:
                    if coding_value:
                        print(coding_update_validation_command_text(coding_value))
                    else:
                        print(coding_detect_validation_command_text())
                    continue

                if coding_action in {"discard", "descartar"}:
                    if not coding_value:
                        print("Uso: coding discard <proposal_id>")
                        continue
                    print(coding_discard_proposal_text(coding_value))
                    continue

                print("Uso: coding [workspace|status|proposals|get|apply|discard|validate|validation]")
                continue

            if cmd == "self":
                print(self_overview(refresh=True))
                continue

            if is_self_analysis_request(cmd):
                print(run_self_analysis_with_output())
                continue

            if cmd == "profile":
                print("Deja un campo vacío para no cambiarlo. Usa [clear] para borrarlo.")

                try:
                    name = input("Nombre: ").strip()
                    role = input("Rol o contexto: ").strip()
                    preferences = input("Preferencias (coma o salto de línea): ").strip()
                    constraints = input("Restricciones (coma o salto de línea): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nOperación cancelada.")
                    continue

                print(update_profile(
                    name=name,
                    role=role,
                    preferences=preferences,
                    constraints=constraints,
                ))
                continue

            if cmd in {"notes", "notas"}:
                print(list_notes_text(limit=20))
                continue

            if cmd in {"note", "nota"}:
                try:
                    action = input("Acción [crear/listar/ver/borrar]: ").strip().lower() or "crear"
                except (EOFError, KeyboardInterrupt):
                    print("\nOperación cancelada.")
                    continue

                if action in {"listar", "lista", "list", "ver todas"}:
                    try:
                        category = input("Categoria (opcional): ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\nOperación cancelada.")
                        continue
                    print(list_notes_text(category=category, limit=20))
                    continue

                if action in {"ver", "mostrar", "show"}:
                    try:
                        identifier = input("Id o titulo de la nota: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\nOperación cancelada.")
                        continue
                    print(get_note_text(identifier))
                    continue

                if action in {"borrar", "eliminar", "delete"}:
                    try:
                        identifier = input("Id o titulo de la nota: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\nOperación cancelada.")
                        continue
                    print(delete_note_text(identifier))
                    continue

                try:
                    title = input("Titulo de la nota: ").strip()
                    content = input("Contenido: ").strip()
                    category = input("Categoria (opcional): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nOperación cancelada.")
                    continue

                print(save_note_text(title=title, content=content, category=category or "general"))
                continue

            if cmd.startswith(("note ", "nota ", "notes ", "notas ")):
                note_result = handle_note_text_request(cmd)
                if note_result is not None:
                    print(note_result)
                    continue

            note_result = handle_note_text_request(cmd)
            if note_result is not None:
                print(note_result)
                continue

            if cmd == "task":
                try:
                    title = input("Titulo de la tarea: ").strip()
                    details = input("Detalles (opcional): ").strip()
                    priority = input("Prioridad [alta/media/baja]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nOperación cancelada.")
                    continue

                print(add_task(title=title, details=details, priority=priority or "media"))
                continue

            if cmd == "reply":
                try:
                    reply_text = input("Tu respuesta: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nOperación cancelada.")
                    continue

                try:
                    print(submit_user_reply(reply_text))
                except ValueError as exc:
                    print(exc)
                continue

            if cmd == "auto":
                state = load_state()
                if has_pending_user_question(state):
                    print("Estoy esperando tu respuesta antes de continuar.")
                    print(f"Pregunta pendiente: {state['awaiting_user_input']['question']}")
                    print("Usa `reply` o escribe la respuesta directamente en la consola.")
                    continue

                try:
                    cycles_text = input("¿Cuántos ciclos? (vacío = hasta terminar): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nOperación cancelada.")
                    continue

                if not cycles_text:
                    cycles = None
                else:
                    try:
                        cycles = int(cycles_text)
                    except ValueError:
                        print("Número inválido.")
                        continue

                if cycles is not None and cycles <= 0:
                    print("Debes indicar un número mayor que cero.")
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
        if telegram_polling_started:
            stop_telegram_polling()


if __name__ == "__main__":
    main()
