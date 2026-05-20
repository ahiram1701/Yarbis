import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import activity
import memory
import session

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class SessionTestCase(unittest.TestCase):
    def test_factory_reset_yarbis_clears_state_activity_and_operation_lock(self):
        state_path = TEST_RUNTIME_DIR / "session_factory_reset_state.json"
        activity_path = TEST_RUNTIME_DIR / "session_factory_reset_activity.log"
        events_path = TEST_RUNTIME_DIR / "session_factory_reset_events.jsonl"
        operation_lock_path = TEST_RUNTIME_DIR / "session_factory_reset_session.lock"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "goal": "Objetivo viejo",
            "messages": [{"role": "user", "content": "hola"}],
            "notes": [{"title": "Dato", "content": "privado"}],
            "tasks": [{"title": "Pendiente"}],
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                    "last_update_id": 99,
                },
            },
            "self_knowledge": {
                "last_analyzed_at": "2026-05-06T00:00:00+00:00",
                "summary": "Autoconocimiento viejo",
            },
        })
        activity_path.write_text("actividad previa", encoding="utf-8")
        events_path.write_text('{"type":"viejo"}\n', encoding="utf-8")
        operation_lock_path.write_text("pid=123\nlabel=Ciclo\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(activity, "ACTIVITY_LOG_FILE", activity_path):
                with patch.object(activity, "EVENTS_FILE", events_path):
                    with patch.object(session, "_clear_factory_runtime_artifacts") as clear_runtime_mock:
                        with patch.object(session, "OPERATION_LOCK_FILE", operation_lock_path):
                            memory.save_state(seeded_state)
                            result = session.factory_reset_yarbis()
                            state = memory.load_state()

        self.assertIn("reiniciado de fabrica", result)
        self.assertEqual(state["goal"], "")
        self.assertEqual(state["messages"], [])
        self.assertEqual(state["notes"], [])
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["self_knowledge"]["summary"], "")
        self.assertEqual(state["notifications"]["channels"], ["windows"])
        self.assertEqual(state["notifications"]["telegram"]["bot_token"], "")
        self.assertEqual(state["notifications"]["telegram"]["chat_id"], "")
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 0)
        self.assertEqual(activity_path.read_text(encoding="utf-8"), "")
        self.assertEqual(events_path.read_text(encoding="utf-8"), "")
        self.assertEqual(operation_lock_path.read_text(encoding="utf-8"), " ")
        clear_runtime_mock.assert_called_once_with()

    def test_first_run_clean_slate_clears_stale_activity(self):
        state_path = TEST_RUNTIME_DIR / "session_first_run_clean_state.json"
        activity_path = TEST_RUNTIME_DIR / "session_first_run_activity.log"
        events_path = TEST_RUNTIME_DIR / "session_first_run_events.jsonl"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        activity_path.write_text("actividad vieja", encoding="utf-8")
        events_path.write_text('{"type":"telegram_event"}\n', encoding="utf-8")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(activity, "ACTIVITY_LOG_FILE", activity_path):
                with patch.object(activity, "EVENTS_FILE", events_path):
                    memory.save_state(memory.default_state())
                    result = session.clear_activity_for_first_run_if_needed()

        self.assertTrue(result)
        self.assertEqual(activity_path.read_text(encoding="utf-8"), "")
        self.assertEqual(events_path.read_text(encoding="utf-8"), "")

    def test_first_run_activity_cleanup_keeps_existing_work_history(self):
        state_path = TEST_RUNTIME_DIR / "session_first_run_keep_state.json"
        activity_path = TEST_RUNTIME_DIR / "session_first_run_keep_activity.log"
        events_path = TEST_RUNTIME_DIR / "session_first_run_keep_events.jsonl"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        activity_path.write_text("actividad importante", encoding="utf-8")
        events_path.write_text('{"type":"telegram_event"}\n', encoding="utf-8")

        seeded_state = memory.normalize_state({
            "goal": "Objetivo activo",
            "messages": [{"role": "user", "content": "hola"}],
        })

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(activity, "ACTIVITY_LOG_FILE", activity_path):
                with patch.object(activity, "EVENTS_FILE", events_path):
                    memory.save_state(seeded_state)
                    result = session.clear_activity_for_first_run_if_needed()

        self.assertFalse(result)
        self.assertEqual(activity_path.read_text(encoding="utf-8"), "actividad importante")
        self.assertEqual(events_path.read_text(encoding="utf-8"), '{"type":"telegram_event"}\n')

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
                with patch.object(session, "get_cached_source_signature", return_value="source-sig"):
                    result = session.run_startup_self_analysis(force=True, background=False)
                state = memory.load_state()

        self.assertIn("Autoanalisis inicial completado", result)
        summary_mock.assert_called_once_with(refresh=True)
        self.assertIn("Nombre: Yarbis", state["self_knowledge"]["summary"])
        self.assertTrue(state["self_knowledge"]["last_analyzed_at"])
        self.assertEqual(state["self_knowledge"]["source_signature"], "source-sig")

    def test_run_startup_self_analysis_reuses_fresh_cached_summary(self):
        state_path = TEST_RUNTIME_DIR / "session_self_analysis_cached_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "self_knowledge": {
                "last_analyzed_at": datetime.now(timezone.utc).isoformat(),
                "summary": "Autoconocimiento cacheado",
                "source_signature": "source-sig",
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                session,
                "render_self_knowledge_summary",
                side_effect=RuntimeError("no debe refrescar"),
            ):
                result = session.run_startup_self_analysis()
                state = memory.load_state()

        self.assertIn("reutilizado desde cache", result)
        self.assertEqual(state["self_knowledge"]["summary"], "Autoconocimiento cacheado")

    def test_update_ollama_settings_persists_model_and_timeout(self):
        state_path = TEST_RUNTIME_DIR / "session_ollama_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_ollama_settings(
                "llama3.2:3b",
                900,
                host="https://ollama.com/api",
                fallback_models="gpt-oss:120b-cloud, qwen3.5:0.8b",
                api_key_env_var="OLLAMA_API_KEY",
            )
            state = memory.load_state()

        self.assertIn("Configuracion de Ollama actualizada", result)
        self.assertEqual(state["ollama"]["model"], "llama3.2:3b")
        self.assertEqual(
            state["ollama"]["fallback_models"],
            ["gpt-oss:120b-cloud", "qwen3.5:0.8b"],
        )
        self.assertEqual(state["ollama"]["host"], "https://ollama.com")
        self.assertEqual(state["ollama"]["api_key_env_var"], "OLLAMA_API_KEY")
        self.assertEqual(state["ollama"]["timeout_seconds"], 900)
        self.assertEqual(state["model_provider"]["default"], "ollama")
        self.assertEqual(state["model_provider"]["ollama"], state["ollama"])

    def test_update_openrouter_settings_persists_default_provider(self):
        state_path = TEST_RUNTIME_DIR / "session_openrouter_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_openrouter_settings(
                "openai/gpt-demo",
                1200,
                host="https://openrouter.ai/api/v1/chat/completions",
                fallback_models="anthropic/claude-demo",
                api_key_env_var="OPENROUTER_API_KEY",
            )
            state = memory.load_state()

        self.assertIn("Configuracion de OpenRouter actualizada", result)
        self.assertEqual(state["model_provider"]["default"], "openrouter")
        self.assertEqual(state["model_provider"]["openrouter"]["model"], "openai/gpt-demo")
        self.assertEqual(
            state["model_provider"]["openrouter"]["fallback_models"],
            ["anthropic/claude-demo"],
        )
        self.assertEqual(
            state["model_provider"]["openrouter"]["host"],
            "https://openrouter.ai/api/v1",
        )
        self.assertEqual(state["ollama"]["model"], memory.DEFAULT_OLLAMA_MODEL)

    def test_update_model_provider_persists_without_losing_provider_settings(self):
        state_path = TEST_RUNTIME_DIR / "session_provider_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            session.update_openrouter_settings("openai/gpt-demo", 900)
            result = session.update_model_provider("ollama")
            state = memory.load_state()

        self.assertIn("Ollama", result)
        self.assertEqual(state["model_provider"]["default"], "ollama")
        self.assertEqual(state["model_provider"]["openrouter"]["model"], "openai/gpt-demo")

    def test_agent_wrappers_load_agent_lazily(self):
        class FakeAgent:
            @staticmethod
            def run_one_cycle(**kwargs):
                return {"name": "cycle", "kwargs": kwargs}

            @staticmethod
            def run_autonomous_session(**kwargs):
                return 3

            @staticmethod
            def cancel_active_ollama_request():
                return True

        with patch.object(session, "_load_agent", return_value=FakeAgent) as load_mock:
            self.assertEqual(session.run_one_cycle(max_steps=1)["kwargs"]["max_steps"], 1)
            self.assertEqual(session.run_autonomous_session(cycles=3), 3)
            self.assertTrue(session.cancel_active_ollama_request())

        self.assertEqual(load_mock.call_count, 3)

    def test_update_ollama_settings_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            session.update_ollama_settings("", 900)

        with self.assertRaises(ValueError):
            session.update_ollama_settings("llama3.2:3b", "lento")

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
                with patch.object(session, "get_cached_source_signature", return_value="source-sig"):
                    with patch.object(session, "run_cycle_with_output", side_effect=RuntimeError("no debe llamarse")):
                        result = session.submit_user_reply("hazte un autoanálisis")
                        state = memory.load_state()

        self.assertIn("Autoanalisis inicial completado", result)
        self.assertIn("Nombre: Yarbis", result)
        summary_mock.assert_called_once_with(refresh=True)
        self.assertIn("agent.py", state["self_knowledge"]["summary"])
        self.assertEqual(state["self_knowledge"]["source_signature"], "source-sig")
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

    def test_submit_user_reply_expands_affirmative_pending_reply_for_autonomy(self):
        state_path = TEST_RUNTIME_DIR / "session_affirmative_reply_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Te gustaria que implemente esto ahora?",
                "reason": "Necesitaba confirmacion para editar.",
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "run_auto_with_output", return_value="Modo autonomo ejecutado."):
                result = session.submit_user_reply("Si, hazlo")
                state = memory.load_state()

        self.assertIn("Retomando el modo autonomo", result)
        self.assertFalse(state["awaiting_user_input"]["pending"])
        self.assertIn("Si, hazlo", state["messages"][-1]["content"])
        self.assertIn("usuario autorizo avanzar", state["messages"][-1]["content"])
        self.assertIn("Te gustaria que implemente esto ahora?", state["messages"][-1]["content"])

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

    def test_run_cycle_with_output_mirrors_local_response_to_telegram(self):
        state_path = TEST_RUNTIME_DIR / "session_telegram_mirror_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                session,
                "_capture_operation_output",
                return_value=("Yarbis:\nAvance listo.", {"status": "final"}),
            ):
                with patch.object(session, "send_telegram_operation_reply", return_value=True) as send_mock:
                    result = session.run_cycle_with_output()

        self.assertEqual(result, "Yarbis:\nAvance listo.")
        send_mock.assert_called_once_with("Ciclo", "Yarbis:\nAvance listo.")

    def test_run_cycle_with_output_skips_telegram_mirror_when_notifications_are_disabled(self):
        state_path = TEST_RUNTIME_DIR / "session_telegram_mirror_disabled_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(
                session,
                "_capture_operation_output",
                return_value=("Yarbis:\nAvance listo.", {"status": "final"}),
            ):
                with patch.object(session, "send_telegram_operation_reply", return_value=True) as send_mock:
                    result = session.run_cycle_with_output(emit_notifications=False)

        self.assertEqual(result, "Yarbis:\nAvance listo.")
        send_mock.assert_not_called()

    def test_submit_user_reply_mirrors_combined_local_response_once(self):
        state_path = TEST_RUNTIME_DIR / "session_reply_telegram_mirror_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "run_cycle_with_output", return_value="Ciclo ejecutado.") as cycle_mock:
                with patch.object(session, "send_telegram_operation_reply", return_value=True) as send_mock:
                    result = session.submit_user_reply("Te comparto mas contexto")

        cycle_mock.assert_called_once_with(
            emit_notifications=True,
            mirror_telegram=False,
        )
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], "Respuesta")
        self.assertIn("Respuesta guardada", send_mock.call_args.args[1])
        self.assertIn("Ciclo ejecutado.", send_mock.call_args.args[1])
        self.assertEqual(result, send_mock.call_args.args[1])

    def test_run_cycle_marks_runtime_thinking_while_running(self):
        state_path = TEST_RUNTIME_DIR / "session_thinking_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        def fake_capture(_func):
            state = memory.load_state()
            thinking = state["runtime"]["thinking"]
            self.assertTrue(thinking["active"])
            self.assertEqual(thinking["label"], "Ciclo")
            self.assertTrue(thinking["started_at"])
            self.assertTrue(thinking["operation_id"].startswith("ciclo-"))
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

    def test_clear_abandoned_runtime_operation_clears_dead_pid(self):
        state_path = TEST_RUNTIME_DIR / "session_abandoned_runtime_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        seeded_state = memory.normalize_state({
            "runtime": {
                "thinking": {
                    "active": True,
                    "label": "Pulso proactivo",
                    "source": "pid:999999",
                    "started_at": "2026-05-06T12:00:00+00:00",
                    "operation_id": "pulso-demo",
                },
                "stop_requested": {
                    "active": True,
                    "operation_id": "pulso-demo",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "_pid_is_running", return_value=False):
                result = session.clear_abandoned_runtime_operation()
            state = memory.load_state()

        self.assertTrue(result)
        self.assertFalse(state["runtime"]["thinking"]["active"])
        self.assertFalse(state["runtime"]["stop_requested"]["active"])

    def test_clear_abandoned_runtime_operation_keeps_live_pid(self):
        state_path = TEST_RUNTIME_DIR / "session_live_runtime_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        seeded_state = memory.normalize_state({
            "runtime": {
                "thinking": {
                    "active": True,
                    "label": "Ciclo",
                    "source": "pid:1234",
                    "operation_id": "ciclo-demo",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "_pid_is_running", return_value=True):
                result = session.clear_abandoned_runtime_operation()
            state = memory.load_state()

        self.assertFalse(result)
        self.assertTrue(state["runtime"]["thinking"]["active"])

    def test_request_stop_current_operation_marks_runtime_and_cancels_ollama(self):
        state_path = TEST_RUNTIME_DIR / "session_stop_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "runtime": {
                "thinking": {
                    "active": True,
                    "label": "Ciclo",
                    "source": "pid:1234",
                    "started_at": "2026-05-01T12:00:00+00:00",
                    "operation_id": "ciclo-demo",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(session, "cancel_active_ollama_request", return_value=True) as cancel_mock:
                result = session.request_stop_current_operation(source="telegram")
            state = memory.load_state()

        self.assertIn("Solicitud de parada enviada", result)
        cancel_mock.assert_called_once()
        stop_requested = state["runtime"]["stop_requested"]
        self.assertTrue(stop_requested["active"])
        self.assertEqual(stop_requested["operation_id"], "ciclo-demo")
        self.assertEqual(stop_requested["source"], "telegram")

    def test_request_stop_current_operation_does_not_create_memory_backup(self):
        base = TEST_RUNTIME_DIR / "session_stop_no_backup"
        state_path = base / "state.json"
        lock_path = base / "state.lock"
        backups_dir = base / ".yarbis_memory_backups"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "runtime": {
                "thinking": {
                    "active": True,
                    "label": "Ciclo",
                    "source": "pid:1234",
                    "started_at": "2026-05-01T12:00:00+00:00",
                    "operation_id": "ciclo-demo",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                memory.save_state(seeded_state)
                initial_count = len(list(backups_dir.glob("*.json")))
                with patch.object(session, "cancel_active_ollama_request", return_value=True):
                    session.request_stop_current_operation(source="telegram")
                after_count = len(list(backups_dir.glob("*.json")))

        self.assertEqual(after_count, initial_count)

    def test_request_stop_current_operation_reports_idle_state(self):
        state_path = TEST_RUNTIME_DIR / "session_stop_idle_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(session, "cancel_active_ollama_request") as cancel_mock:
                result = session.request_stop_current_operation(source="desktop")

        self.assertEqual(result, "Yarbis no esta pensando ahora.")
        cancel_mock.assert_not_called()

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
                model="qwen3.5:0.8b",
            )
            settings = session.get_service_proactive_settings()

        self.assertIn("Pulso proactivo actualizado", result)
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["interval_seconds"], 900)
        self.assertEqual(settings["cycles"], 2)
        self.assertEqual(settings["start_delay_seconds"], 30)
        self.assertEqual(settings["model"], "qwen3.5:0.8b")

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

    def test_update_local_context_settings_persists_configuration(self):
        state_path = TEST_RUNTIME_DIR / "session_local_context_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = session.update_local_context_settings(
                enabled=True,
                mode="detailed",
                sample_interval_seconds=15,
                max_snapshot_age_seconds=120,
                include_window_title=True,
                include_process_name=False,
                include_workspace_changes=True,
                include_system_health=False,
            )
            settings = session.get_local_context_settings()

        self.assertIn("Contexto local actualizado", result)
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["mode"], "detailed")
        self.assertEqual(settings["sample_interval_seconds"], 15)
        self.assertEqual(settings["max_snapshot_age_seconds"], 120)
        self.assertTrue(settings["include_window_title"])
        self.assertFalse(settings["include_process_name"])
        self.assertTrue(settings["include_workspace_changes"])
        self.assertFalse(settings["include_system_health"])

    def test_update_local_context_settings_rejects_invalid_values(self):
        state_path = TEST_RUNTIME_DIR / "session_local_context_invalid_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                session.update_local_context_settings(
                    enabled=True,
                    mode="unknown",
                    sample_interval_seconds=30,
                    max_snapshot_age_seconds=180,
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
