import unittest
from pathlib import Path
from unittest.mock import patch

import memory
import session

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class SessionTestCase(unittest.TestCase):
    def test_update_goal_resets_operational_context(self):
        state_path = TEST_RUNTIME_DIR / "session_goal_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "goal": "Objetivo viejo",
            "messages": [{"role": "assistant", "content": "avance previo"}],
            "tasks": [{"id": "task-1", "title": "Vieja tarea", "status": "pending"}],
            "current_plan": ["Paso 1"],
            "last_result": "resultado anterior",
            "awaiting_user_input": {
                "pending": True,
                "question": "Que prioridad tiene esto?",
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            result = session.update_goal("Nuevo objetivo claro")
            state = memory.load_state()

        self.assertIn("Objetivo actualizado", result)
        self.assertEqual(state["goal"], "Nuevo objetivo claro")
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["current_plan"], [])
        self.assertEqual(state["last_result"], "")
        self.assertFalse(state["awaiting_user_input"]["pending"])
        self.assertEqual(len(state["messages"]), 1)
        self.assertIn("Nuevo objetivo claro", state["messages"][0]["content"])

    def test_run_startup_self_analysis_persists_latest_summary(self):
        state_path = TEST_RUNTIME_DIR / "session_self_analysis_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(
                session,
                "render_self_knowledge_summary",
                return_value="Identidad:\n- Nombre: Yarbis.\n\nEntorno actual:\nSistema operativo: demo",
            ) as summary_mock:
                result = session.run_startup_self_analysis()
                state = memory.load_state()

        self.assertIn("Autoanalisis inicial completado", result)
        summary_mock.assert_called_once_with(refresh=True)
        self.assertIn("Nombre: Yarbis", state["self_knowledge"]["summary"])
        self.assertTrue(state["self_knowledge"]["last_analyzed_at"])

    def test_submit_user_reply_routes_self_analysis_request_without_model_cycle(self):
        state_path = TEST_RUNTIME_DIR / "session_self_analysis_reply_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(
                session,
                "render_self_knowledge_summary",
                return_value="Identidad:\n- Nombre: Yarbis.\n\nCodigo fuente:\n- agent.py\n\nEntorno actual:\nSistema operativo: demo\nCPU: demo\nRAM: demo",
            ) as summary_mock:
                with patch.object(session, "run_cycle_with_output", side_effect=RuntimeError("no debe llamarse")):
                    result = session.submit_user_reply("hazte un autoanálisis")
                    state = memory.load_state()

        self.assertIn("Autoanalisis inicial completado", result)
        self.assertIn("Nombre: Yarbis", result)
        summary_mock.assert_called_once_with(refresh=True)
        self.assertIn("agent.py", state["self_knowledge"]["summary"])
        self.assertEqual(state["messages"], [])

    def test_submit_user_reply_routes_note_requests_without_model_cycle(self):
        state_path = TEST_RUNTIME_DIR / "session_note_reply_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(session, "run_cycle_with_output", side_effect=RuntimeError("no debe llamarse")):
                create_result = session.submit_user_reply(
                    "guarda una nota: Rutina | Revisar pendientes cada manana | personal"
                )
                state_after_create = memory.load_state()
                note_id = state_after_create["notes"][0]["id"]
                list_result = session.submit_user_reply("notas personal")
                show_result = session.submit_user_reply(f"ver nota {note_id}")
                delete_result = session.submit_user_reply(f"borra la nota {note_id}")
                state_after_delete = memory.load_state()

        self.assertIn("Nota guardada", create_result)
        self.assertIn("Rutina", list_result)
        self.assertIn("Revisar pendientes cada manana", show_result)
        self.assertIn("Nota eliminada", delete_result)
        self.assertEqual(state_after_delete["notes"], [])
        self.assertEqual(state_after_delete["messages"], [])

    def test_is_self_analysis_request_accepts_common_phrases(self):
        self.assertTrue(session.is_self_analysis_request("hazte un autoanálisis"))
        self.assertTrue(session.is_self_analysis_request("refresca tu auto analisis y muestramelo"))
        self.assertTrue(session.is_self_analysis_request("dime qué sabes de ti"))
        self.assertFalse(session.is_self_analysis_request("ayudame a escribir un correo"))

    def test_note_request_label_accepts_commands_and_natural_phrases(self):
        self.assertEqual(session.note_request_label("nota crear Idea | Probar"), "Guardar nota")
        self.assertEqual(session.note_request_label("ver notas"), "Notas")
        self.assertEqual(session.note_request_label("elimina la nota note-123"), "Eliminar nota")
        self.assertEqual(session.note_request_label("Te comparto mas contexto"), "")

    def test_submit_user_reply_clears_pending_question_and_resumes_autonomy(self):
        state_path = TEST_RUNTIME_DIR / "session_reply_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que nicho quieres trabajar?",
                "reason": "Falta contexto",
            },
            "autonomy": {
                "auto_cycles_default": 4,
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "run_auto_with_output", return_value="Modo autonomo ejecutado por 4 ciclo(s).") as auto_mock:
                result = session.submit_user_reply("Trabajemos el nicho fitness")
                state = memory.load_state()

        self.assertIn("Respuesta guardada", result)
        self.assertIn("Retomando el modo autonomo", result)
        self.assertIn("Modo autonomo ejecutado por 4 ciclo(s).", result)
        self.assertEqual(auto_mock.call_count, 1)
        self.assertEqual(auto_mock.call_args.kwargs["cycles"], 4)
        self.assertFalse(state["awaiting_user_input"]["pending"])
        self.assertEqual(state["messages"][-1]["role"], "user")
        self.assertEqual(state["messages"][-1]["content"], "Trabajemos el nicho fitness")

    def test_submit_user_reply_without_pending_question_runs_single_cycle(self):
        state_path = TEST_RUNTIME_DIR / "session_reply_freeform_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(session, "run_cycle_with_output", return_value="Ciclo ejecutado.") as cycle_mock:
                result = session.submit_user_reply("Te comparto mas contexto")
                state = memory.load_state()

        self.assertIn("Ejecutando un ciclo", result)
        self.assertIn("Ciclo ejecutado.", result)
        self.assertEqual(cycle_mock.call_count, 1)
        self.assertEqual(state["messages"][-1]["content"], "Te comparto mas contexto")

    def test_run_auto_with_output_adds_summary_even_without_stdout(self):
        with patch.object(session, "run_autonomous_session", return_value=3):
            result = session.run_auto_with_output(cycles=3)

        self.assertIn("Modo autonomo ejecutado por 3 ciclo(s).", result)

    def test_run_cycle_with_output_notifies_when_user_input_is_pending(self):
        state_path = TEST_RUNTIME_DIR / "session_notification_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que nicho quieres trabajar?",
                "reason": "Falta contexto",
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                session,
                "_capture_operation_output",
                return_value=("Ciclo ejecutado.", {"status": "waiting_for_user_input"}),
            ):
                with patch.object(session, "notify_user_input_required", return_value=True) as notify_mock:
                    result = session.run_cycle_with_output()

        self.assertEqual(result, "Ciclo ejecutado.")
        notify_mock.assert_called_once_with(
            "Que nicho quieres trabajar?",
            "Falta contexto",
        )

    def test_run_cycle_marks_runtime_thinking_while_running(self):
        state_path = TEST_RUNTIME_DIR / "session_thinking_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        def fake_capture(_func):
            state = memory.load_state()
            thinking = state["runtime"]["thinking"]
            self.assertTrue(thinking["active"])
            self.assertEqual(thinking["label"], "Ciclo")
            self.assertTrue(thinking["started_at"])
            return "Ciclo ejecutado.", {"status": "final"}

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(session, "_capture_operation_output", side_effect=fake_capture):
                result = session.run_cycle_with_output(emit_notifications=False)
            state = memory.load_state()

        self.assertEqual(result, "Ciclo ejecutado.")
        self.assertFalse(state["runtime"]["thinking"]["active"])

    def test_run_cycle_clears_runtime_thinking_after_error(self):
        state_path = TEST_RUNTIME_DIR / "session_thinking_error_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(
                session,
                "_capture_operation_output",
                side_effect=RuntimeError("fallo controlado"),
            ):
                with self.assertRaises(RuntimeError):
                    session.run_cycle_with_output(emit_notifications=False)
            state = memory.load_state()

        self.assertFalse(state["runtime"]["thinking"]["active"])

    def test_recover_unanswered_user_message_runs_one_cycle(self):
        state_path = TEST_RUNTIME_DIR / "session_recover_user_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "messages": [
                {"role": "assistant", "content": "Avance anterior."},
                {"role": "user", "content": "Mejora tu rendimiento"},
            ],
            "last_result": "Avance anterior.",
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                session,
                "_capture_operation_output",
                return_value=("Ciclo recuperado.", {"status": "final"}),
            ) as capture_mock:
                result = session.recover_unanswered_user_message_with_output()

        self.assertEqual(result, "Ciclo recuperado.")
        capture_mock.assert_called_once_with(session.run_one_cycle)

    def test_recover_unanswered_user_message_ignores_answered_state(self):
        state_path = TEST_RUNTIME_DIR / "session_recover_answered_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "messages": [
                {"role": "user", "content": "Mejora tu rendimiento"},
                {"role": "assistant", "content": "Listo."},
            ],
            "last_result": "Listo.",
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                session,
                "_capture_operation_output",
                side_effect=RuntimeError("no debe ejecutarse"),
            ):
                result = session.recover_unanswered_user_message_with_output()

        self.assertEqual(result, "")

    def test_update_ui_theme_persists_theme(self):
        state_path = TEST_RUNTIME_DIR / "session_theme_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_ui_theme("light")
            state = memory.load_state()

        self.assertIn("Tema actualizado", result)
        self.assertEqual(state["ui"]["theme"], "light")

    def test_update_service_proactive_settings_persists_pulse(self):
        state_path = TEST_RUNTIME_DIR / "session_service_pulse_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_service_proactive_settings(
                enabled=False,
                interval_seconds=900,
                cycles=2,
                start_delay_seconds=30,
            )
            settings = session.get_service_proactive_settings()

        self.assertIn("Pulso proactivo actualizado", result)
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["interval_seconds"], 900)
        self.assertEqual(settings["cycles"], 2)
        self.assertEqual(settings["start_delay_seconds"], 30)

    def test_update_service_proactive_settings_rejects_invalid_values(self):
        state_path = TEST_RUNTIME_DIR / "session_service_pulse_invalid_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                session.update_service_proactive_settings(
                    enabled=True,
                    interval_seconds=10,
                    cycles=1,
                    start_delay_seconds=60,
                )

    def test_update_notification_settings_persists_ntfy_channel(self):
        state_path = TEST_RUNTIME_DIR / "session_notifications_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_notification_settings(
                enabled=True,
                windows_enabled=True,
                ntfy_enabled=True,
                ntfy_server="https://ntfy.sh",
                ntfy_topic="yarbis-secret",
                ntfy_token="",
                ntfy_priority="high",
                ntfy_tags="yarbis",
            )
            state = memory.load_state()

        self.assertIn("windows, ntfy", result)
        self.assertTrue(state["notifications"]["enabled"])
        self.assertEqual(state["notifications"]["channels"], ["windows", "ntfy"])
        self.assertEqual(state["notifications"]["ntfy"]["topic"], "yarbis-secret")
        self.assertEqual(state["notifications"]["ntfy"]["priority"], "high")

    def test_update_notification_settings_persists_telegram_channel(self):
        state_path = TEST_RUNTIME_DIR / "session_notifications_telegram_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_notification_settings(
                enabled=True,
                windows_enabled=False,
                ntfy_enabled=False,
                telegram_enabled=True,
                telegram_bot_token="bot-123",
                telegram_chat_id="",
            )
            state = memory.load_state()

        self.assertIn("telegram", result)
        self.assertTrue(state["notifications"]["enabled"])
        self.assertEqual(state["notifications"]["channels"], ["telegram"])
        self.assertEqual(state["notifications"]["telegram"]["bot_token"], "bot-123")
        self.assertEqual(state["notifications"]["telegram"]["chat_id"], "")
        self.assertIn("envia /start", result.lower())

    def test_update_notification_settings_requires_topic_for_ntfy(self):
        state_path = TEST_RUNTIME_DIR / "session_notifications_invalid_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                session.update_notification_settings(
                    enabled=True,
                    windows_enabled=False,
                    ntfy_enabled=True,
                    ntfy_server="https://ntfy.sh",
                    ntfy_topic="",
                )

    def test_update_notification_settings_requires_bot_token_for_telegram(self):
        state_path = TEST_RUNTIME_DIR / "session_notifications_invalid_telegram_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                session.update_notification_settings(
                    enabled=True,
                    windows_enabled=False,
                    ntfy_enabled=False,
                    telegram_enabled=True,
                    telegram_bot_token="",
                    telegram_chat_id="",
                )

    def test_send_test_notification_attempts_telegram_link_before_failing(self):
        state_path = TEST_RUNTIME_DIR / "session_test_notification_telegram_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "try_link_telegram_chat", return_value=True) as link_mock:
                with patch.object(session, "send_notification", return_value=True) as send_mock:
                    result = session.send_test_notification()

        link_mock.assert_called_once()
        send_mock.assert_called_once()
        self.assertEqual(result, "Notificacion de prueba enviada.")
