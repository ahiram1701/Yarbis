import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import memory
import yarbis_service

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class YarbisServiceTestCase(unittest.TestCase):
    def test_startup_stop_file_helpers_ignore_stale_file_until_updated(self):
        stop_file = Mock()
        stop_file.exists.return_value = True
        stop_file.stat.return_value = SimpleNamespace(st_mtime=10.0)

        with patch.object(yarbis_service, "STOP_FILE", stop_file):
            self.assertFalse(yarbis_service._stop_file_requests_current_run(10.0))

            stop_file.stat.return_value = SimpleNamespace(st_mtime=12.0)

            self.assertTrue(yarbis_service._stop_file_requests_current_run(10.0))

    def test_discard_startup_stop_file_removes_deletable_file(self):
        stop_file = Mock()

        with patch.object(yarbis_service, "STOP_FILE", stop_file):
            ignored_mtime = yarbis_service._discard_startup_stop_file()

        self.assertEqual(ignored_mtime, 0.0)
        stop_file.unlink.assert_called_once_with()

    def test_get_service_proactive_settings_sanitizes_environment(self):
        with patch.dict(
            yarbis_service.os.environ,
            {
                yarbis_service.ENV_SERVICE_PROACTIVE: "0",
                yarbis_service.ENV_SERVICE_PROACTIVE_INTERVAL_SECONDS: "10",
                yarbis_service.ENV_SERVICE_PROACTIVE_CYCLES: "9",
                yarbis_service.ENV_SERVICE_PROACTIVE_START_DELAY_SECONDS: "-5",
                yarbis_service.ENV_SERVICE_PROACTIVE_MODEL: "qwen3.5:0.8b",
            },
            clear=True,
        ):
            settings = yarbis_service.get_service_proactive_settings()

        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["interval_seconds"], 60)
        self.assertEqual(settings["cycles"], 5)
        self.assertEqual(settings["start_delay_seconds"], 0)
        self.assertEqual(settings["model"], "qwen3.5:0.8b")

    def test_run_proactive_pulse_skips_when_waiting_for_user(self):
        state_path = TEST_RUNTIME_DIR / "service_waiting_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que permiso tengo para avanzar?",
                "reason": "Hace falta una decision humana.",
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(
                    yarbis_service,
                    "run_auto_with_output",
                    side_effect=RuntimeError("no debe ejecutarse"),
                ) as auto_mock:
                    result = yarbis_service.run_proactive_pulse()
                    state = memory.load_state()

        self.assertIn("omitido", result)
        self.assertIn("Que permiso tengo para avanzar?", result)
        self.assertEqual(auto_mock.call_count, 0)
        self.assertTrue(state["awaiting_user_input"]["pending"])
        self.assertEqual(
            state["awaiting_user_input"]["question"],
            "Que permiso tengo para avanzar?",
        )
        self.assertEqual(
            state["awaiting_user_input"]["reason"],
            "Hace falta una decision humana.",
        )
        self.assertEqual(state["messages"], [])

    def test_run_proactive_pulse_does_not_start_operation_when_waiting_for_user(self):
        state_path = TEST_RUNTIME_DIR / "service_waiting_no_lock_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Confirmas que avance?",
                "reason": "Necesito autorizacion.",
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(
                    yarbis_service,
                    "session_operation_lock",
                    side_effect=RuntimeError("no debe tomar el candado"),
                ) as lock_mock:
                    result = yarbis_service.run_proactive_pulse()
                    state = memory.load_state()

        self.assertIn("Confirmas que avance?", result)
        lock_mock.assert_not_called()
        self.assertTrue(state["awaiting_user_input"]["pending"])
        self.assertEqual(state["awaiting_user_input"]["question"], "Confirmas que avance?")

    def test_run_proactive_pulse_skips_when_another_operation_is_running(self):
        state_path = TEST_RUNTIME_DIR / "service_busy_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(
                    yarbis_service,
                    "session_operation_lock",
                    side_effect=yarbis_service.SessionOperationBusy("ocupado"),
                ):
                    with patch.object(
                        yarbis_service,
                        "run_auto_with_output",
                        side_effect=RuntimeError("no debe ejecutarse"),
                    ) as auto_mock:
                        result = yarbis_service._run_proactive_pulse_inline(
                            {"cycles": 1},
                            "2026-05-06T12:00:00+00:00",
                        )
                        state = memory.load_state()

        self.assertIn("operacion de Yarbis en curso", result)
        self.assertEqual(auto_mock.call_count, 0)
        self.assertEqual(state["messages"], [])

    def test_run_proactive_pulse_appends_tick_and_runs_autonomy(self):
        state_path = TEST_RUNTIME_DIR / "service_proactive_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.dict(
                yarbis_service.os.environ,
                {yarbis_service.ENV_SERVICE_PROACTIVE_CYCLES: "2"},
                clear=True,
            ):
                with patch.object(
                    yarbis_service,
                    "run_auto_with_output",
                    return_value="Modo autonomo ejecutado por 2 ciclo(s).",
                ) as auto_mock:
                    with patch.object(yarbis_service, "notify_user_input_required") as notify_mock:
                        result = yarbis_service._run_proactive_pulse_inline(
                            {"cycles": 2},
                            "2026-05-06T12:00:00+00:00",
                        )
                        state = memory.load_state()

        self.assertIn("Modo autonomo", result)
        auto_mock.assert_called_once_with(cycles=2, emit_notifications=False)
        self.assertEqual(state["messages"][-1]["role"], "user")
        self.assertIn("Pulso proactivo 24/7", state["messages"][-1]["content"])
        notify_mock.assert_not_called()

    def test_run_proactive_pulse_uses_configured_model_override(self):
        state_path = TEST_RUNTIME_DIR / "service_proactive_model_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(
                    yarbis_service,
                    "run_auto_with_output",
                    return_value="Modo autonomo ejecutado con modelo ligero.",
                ) as auto_mock:
                    result = yarbis_service._run_proactive_pulse_inline(
                        {"cycles": 1, "model": "qwen3.5:0.8b"},
                        "2026-05-06T12:00:00+00:00",
                    )

        self.assertIn("modelo ligero", result)
        auto_mock.assert_called_once_with(
            cycles=1,
            emit_notifications=False,
            model_override="qwen3.5:0.8b",
        )

    def test_run_proactive_pulse_recovers_unanswered_user_message_first(self):
        state_path = TEST_RUNTIME_DIR / "service_recover_user_state.json"
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
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(
                    yarbis_service,
                    "recover_unanswered_user_message_with_output",
                    return_value="Yarbis:\nCiclo recuperado.",
                ) as recover_mock:
                    with patch.object(
                        yarbis_service,
                        "run_auto_with_output",
                        side_effect=RuntimeError("no debe ejecutarse"),
                    ):
                        result = yarbis_service._run_proactive_pulse_inline(
                            {"cycles": 1},
                            "2026-05-06T12:00:00+00:00",
                        )
                        state = memory.load_state()

        self.assertIn("Respuesta de usuario pendiente recuperada", result)
        self.assertIn("Ciclo recuperado", result)
        recover_mock.assert_called_once_with(
            emit_notifications=False,
            blocking=False,
        )
        self.assertNotIn("Pulso proactivo 24/7", state["messages"][-1]["content"])

    def test_run_proactive_pulse_sends_telegram_continuity_update(self):
        state_path = TEST_RUNTIME_DIR / "service_proactive_telegram_state.json"
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
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(
                    yarbis_service,
                    "run_auto_with_output",
                    return_value="Yarbis:\nAvance proactivo listo.",
                ):
                    with patch.object(
                        yarbis_service,
                        "send_telegram_message",
                        return_value=True,
                    ) as send_mock:
                        result = yarbis_service._run_proactive_pulse_inline(
                            {"cycles": 1},
                            "2026-05-06T12:00:00+00:00",
                        )

        self.assertIn("Avance proactivo listo", result)
        send_mock.assert_called_once()
        sent_text = send_mock.call_args.args[0]
        self.assertIn("Yarbis - Pulso proactivo", sent_text)
        self.assertIn("Continuidad:", sent_text)
        self.assertIn("Avance proactivo listo", sent_text)
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "123")

    def test_run_proactive_pulse_uses_isolated_timeout_wrapper(self):
        state_path = TEST_RUNTIME_DIR / "service_wrapper_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.normalize_state({
                "service": {
                    "proactive": {
                        "enabled": True,
                    },
                },
            }))
            with patch.dict(
                yarbis_service.os.environ,
                {yarbis_service.ENV_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS: "45"},
                clear=True,
            ):
                with patch.object(
                    yarbis_service,
                    "_run_proactive_pulse_with_timeout",
                    return_value="Pulso listo.",
                ) as wrapper_mock:
                    result = yarbis_service.run_proactive_pulse()

        self.assertEqual(result, "Pulso listo.")
        wrapper_mock.assert_called_once()
        self.assertEqual(wrapper_mock.call_args.args[0]["max_runtime_seconds"], 45)

    def test_timeout_wrapper_cleans_abandoned_proactive_tick(self):
        state_path = TEST_RUNTIME_DIR / "service_timeout_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        class FakeProcess:
            pid = 1234
            returncode = None

            def communicate(self, timeout=None):
                raise yarbis_service.subprocess.TimeoutExpired(["python"], timeout or 1)

            def poll(self):
                return None

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.normalize_state({
                "messages": [
                    {"role": "assistant", "content": "Previo."},
                    {
                        "role": "user",
                        "content": yarbis_service.PROACTIVE_TICK_BASE_MESSAGE + "\n\ncontexto",
                    },
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": "list_tasks", "arguments": {}}}],
                    },
                ],
            }))
            with patch.object(yarbis_service.subprocess, "Popen", return_value=FakeProcess()):
                with patch.object(yarbis_service, "_kill_process_tree") as kill_mock:
                    with patch.object(yarbis_service, "clear_abandoned_runtime_operation", return_value=True):
                        result = yarbis_service._run_proactive_pulse_with_timeout(
                            {"cycles": 1, "max_runtime_seconds": 15},
                            "2026-05-06T12:00:00+00:00",
                        )
            state = memory.load_state()

        self.assertIn("timeout duro", result)
        kill_mock.assert_called_once()
        self.assertEqual(state["messages"], [{"role": "assistant", "content": "Previo."}])

    def test_recover_unanswered_user_message_sends_telegram_update(self):
        state_path = TEST_RUNTIME_DIR / "service_recover_telegram_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "messages": [
                {"role": "assistant", "content": "Avance anterior."},
                {"role": "user", "content": "Mejora tu rendimiento"},
            ],
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
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(
                    yarbis_service,
                    "recover_unanswered_user_message_with_output",
                    return_value="Yarbis:\nCiclo recuperado.",
                ):
                    with patch.object(
                        yarbis_service,
                        "send_telegram_message",
                        return_value=True,
                    ) as send_mock:
                        result = yarbis_service._recover_unanswered_user_message()

        self.assertIn("Respuesta de usuario pendiente recuperada", result)
        send_mock.assert_called_once()
        sent_text = send_mock.call_args.args[0]
        self.assertIn("Yarbis - Respuesta recuperada", sent_text)
        self.assertIn("Ciclo recuperado", sent_text)
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "123")

    def test_run_proactive_pulse_notifies_when_autonomy_requests_input(self):
        state_path = TEST_RUNTIME_DIR / "service_proactive_pending_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        def fake_auto(cycles=None, emit_notifications=True):
            state = memory.load_state()
            state["awaiting_user_input"] = {
                "pending": True,
                "question": "Que prioridad quieres darme?",
                "reason": "Necesito decidir el siguiente paso.",
                "fields": ["prioridad"],
            }
            memory.save_state(state)
            return "Estoy esperando una respuesta del usuario."

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                with patch.object(yarbis_service, "run_auto_with_output", side_effect=fake_auto):
                    with patch.object(yarbis_service, "notify_user_input_required") as notify_mock:
                        yarbis_service._run_proactive_pulse_inline(
                            {"cycles": 1},
                            "2026-05-06T12:00:00+00:00",
                        )
                        state = memory.load_state()

        notify_mock.assert_called_once_with(
            "Que prioridad quieres darme?",
            "Necesito decidir el siguiente paso.",
        )
        self.assertTrue(state["awaiting_user_input"]["pending"])
        self.assertEqual(
            state["awaiting_user_input"]["question"],
            "Que prioridad quieres darme?",
        )

    def test_get_service_proactive_settings_uses_persisted_state(self):
        state_path = TEST_RUNTIME_DIR / "service_persisted_settings_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "service": {
                "proactive": {
                    "enabled": False,
                    "interval_seconds": 900,
                    "cycles": 3,
                    "start_delay_seconds": 120,
                    "model": "qwen3.5:0.8b",
                }
            }
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(yarbis_service.os.environ, {}, clear=True):
                settings = yarbis_service.get_service_proactive_settings()

        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["interval_seconds"], 900)
        self.assertEqual(settings["cycles"], 3)
        self.assertEqual(settings["start_delay_seconds"], 120)
        self.assertEqual(settings["model"], "qwen3.5:0.8b")


if __name__ == "__main__":
    unittest.main()
