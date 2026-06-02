import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import memory
import yarbis_bus
import yarbis_desktop
import yarbis_instance

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class _FakeVar:
    def __init__(self, value=None):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class _FakeWidget:
    def __init__(self):
        self.options = {}
        self.content = ""

    def configure(self, **kwargs):
        self.options.update(kwargs)

    config = configure

    def cget(self, key):
        return self.options.get(key, "normal")

    def delete(self, *_args):
        self.content = ""

    def insert(self, _index, content):
        self.content += str(content)

    def yview(self):
        return (0.0, 1.0)

    def see(self, *_args):
        pass

    def yview_moveto(self, *_args):
        pass

    def focus_set(self):
        pass


class _DesktopStub:
    def __init__(self):
        self.activities = []
        self.refreshed = False

    def _append_activity(self, title: str, body: str):
        self.activities.append((title, body))

    def refresh_state_view(self):
        self.refreshed = True

    def _edit_notifications(self):
        raise AssertionError("No debe abrir notificaciones en esta prueba.")

    def _toggle_service(self):
        raise AssertionError("No debe instalar servicio en esta prueba.")

    def _start_background_job(self, *_args, **_kwargs):
        raise AssertionError("No debe ejecutar ciclo en esta prueba.")


def _service_status(**overrides):
    status = {
        "installed": False,
        "running": False,
        "autostart_enabled": False,
        "start_type": "not_installed",
        "pid": None,
        "account_name": "",
    }
    status.update(overrides)
    return status


def _desktop_app_stub(service_status=None):
    app = object.__new__(yarbis_desktop.YarbisDesktop)
    app.goal_var = _FakeVar()
    app.cycles_var = _FakeVar()
    app.pending_var = _FakeVar()
    app.thinking_var = _FakeVar()
    app.theme_var = _FakeVar()
    app.ollama_var = _FakeVar()
    app.coding_var = _FakeVar()
    app.health_var = _FakeVar()
    app.readiness_var = _FakeVar()
    app.status_var = _FakeVar()
    app.instance_chip_var = _FakeVar()
    app.instance_warning_var = _FakeVar()
    app.message_target_var = _FakeVar()
    app.message_timeout_var = _FakeVar("120")
    app.service_var = _FakeVar()
    app.service_button_text = _FakeVar()
    app.service_autostart_var = _FakeVar(False)
    app.service_autostart_text = _FakeVar()
    app.send_button = _FakeWidget()
    app.summary_text = _FakeWidget()
    app.activity_text = _FakeWidget()
    app.reply_text = _FakeWidget()
    app.service_toggle_button = _FakeWidget()
    app.service_autostart_check = _FakeWidget()
    app.remove_service_button = _FakeWidget()
    app._result_queue = yarbis_desktop.queue.Queue()
    app._busy = False
    app._busy_sources = set()
    app._action_buttons = []
    app._view_has_pending_question = False
    app._last_summary_text = ""
    app._last_result_text = ""
    app._last_result_widgets = [_FakeWidget()]
    app._last_activity_text = ""
    app._last_activity_signature = None
    app._last_status_refresh_at = time.monotonic()
    app._status_refresh_in_flight = False
    app._status_refresh_pending_force = False
    app._status_refresh_pending_context_helper = False
    app._status_refresh_pending_context_task = False
    app._cached_health_status = {"service": service_status or _service_status()}
    app._cached_readiness_status = {"items": []}
    app._cached_context_helper_status = {"state": "desactivado"}
    app._last_state_signature = None
    app._cached_state = None
    app._last_state_file_signature = None
    app._instance_rows = []
    app._message_rows = {}
    app._archived_rows = {}
    app._local_telegram_polling = False
    app._closing = False
    app._style_service_autostart_toggle = lambda: None
    app._sync_runtime_thinking = lambda _state=None: False
    app._refresh_activity_view = lambda *args, **kwargs: None
    app._set_text = lambda widget, content: widget.configure(content=content)
    return app


class YarbisDesktopTestCase(unittest.TestCase):
    def test_instance_overview_rows_include_selector_columns_and_warnings(self):
        root = TEST_RUNTIME_DIR / f"desktop_instances_overview-{uuid4().hex[:8]}"
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            yarbis_instance.create_instance("alpha", display_name="Alpha")
            yarbis_instance.create_instance("beta", display_name="Beta")
            for instance_id in ("alpha", "beta"):
                state_path = yarbis_instance.state_file(instance_id)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    json.dumps({
                        "notifications": {
                            "telegram": {"bot_token": "same-token"},
                        },
                        "service": {
                            "mobile_ui": {"port": 9001 if instance_id == "alpha" else 9002},
                        },
                    }),
                    encoding="utf-8",
                )
                pid_path = yarbis_instance.runtime_dir(instance_id) / "service.pid"
                pid_path.parent.mkdir(parents=True, exist_ok=True)
                pid_path.write_text(str(os.getpid()), encoding="utf-8")

            rows, warnings = yarbis_desktop._instance_overview_rows()

        alpha = [row for row in rows if row["id"] == "alpha"][0]
        self.assertEqual(alpha["display_name"], "Alpha")
        self.assertEqual(alpha["active_text"], "activa")
        self.assertEqual(alpha["service_name"], "Yarbis-alpha")
        self.assertEqual(alpha["mobile_port"], 9001)
        self.assertIn("pending_messages", alpha)
        self.assertTrue(any("Telegram duplicado" in warning for warning in warnings))

    def test_instance_archive_enabled_blocks_default_current_and_active(self):
        self.assertFalse(yarbis_desktop._instance_archive_enabled({"id": "default", "active": False}, "default"))
        self.assertFalse(yarbis_desktop._instance_archive_enabled({"id": "worker", "active": False}, "worker"))
        self.assertFalse(yarbis_desktop._instance_archive_enabled({"id": "worker", "active": True}, "default"))
        self.assertTrue(yarbis_desktop._instance_archive_enabled({"id": "worker", "active": False}, "default"))

    def test_instance_message_ui_helper_queues_offline_target(self):
        root = TEST_RUNTIME_DIR / f"desktop_message_queue-{uuid4().hex[:8]}"
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                result = yarbis_desktop.YarbisDesktop._send_instance_message_for_ui(
                    "beta",
                    "hola beta",
                    0,
                )
                messages = yarbis_bus.list_instance_messages(instance_id="alpha")

        self.assertIn("Mensaje en cola", result)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["to_instance"], "beta")

    def test_first_run_setup_can_store_direct_ollama_api_key(self):
        state_path = TEST_RUNTIME_DIR / "desktop_first_run_ollama_key_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        settings = {
            "goal": "Usar Yarbis con Ollama Cloud",
            "name": "",
            "role": "",
            "provider": memory.MODEL_PROVIDER_OLLAMA,
            "model": "gpt-oss:120b",
            "fallback_models": "qwen3.5:2b",
            "timeout_seconds": "1200",
            "host": "https://ollama.com",
            "api_key": "ollama-secret",
            "api_key_env_var": "OLLAMA_API_KEY",
            "run_first_cycle": False,
            "open_notifications": False,
            "install_service": False,
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(yarbis_desktop, "readiness_status", return_value={}):
                app = _DesktopStub()
                yarbis_desktop.YarbisDesktop._apply_first_run_setup(app, settings)
            state = memory.load_state()

        self.assertEqual(state["model_provider"]["default"], memory.MODEL_PROVIDER_OLLAMA)
        self.assertEqual(state["ollama"]["model"], "gpt-oss:120b")
        self.assertEqual(state["ollama"]["fallback_models"], ["qwen3.5:2b"])
        self.assertEqual(state["ollama"]["host"], "https://ollama.com")
        self.assertEqual(state["ollama"]["api_key"], "ollama-secret")
        self.assertEqual(state["model_provider"]["ollama"]["api_key"], "ollama-secret")
        self.assertTrue(any("API key: guardada" in body for _title, body in app.activities))

    def test_first_run_setup_can_select_openrouter_and_store_direct_api_key(self):
        state_path = TEST_RUNTIME_DIR / "desktop_first_run_openrouter_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        settings = {
            "goal": "Usar Yarbis con OpenRouter",
            "name": "Ahiram",
            "role": "Usuario local",
            "provider": memory.MODEL_PROVIDER_OPENROUTER,
            "model": "openai/gpt-demo",
            "fallback_models": "anthropic/claude-demo",
            "timeout_seconds": "1200",
            "host": "https://openrouter.ai/api/v1",
            "api_key": "openrouter-secret",
            "api_key_env_var": "OPENROUTER_API_KEY",
            "run_first_cycle": False,
            "open_notifications": False,
            "install_service": False,
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(yarbis_desktop, "readiness_status", return_value={}):
                app = _DesktopStub()
                yarbis_desktop.YarbisDesktop._apply_first_run_setup(app, settings)
            state = memory.load_state()

        self.assertEqual(state["goal"], "Usar Yarbis con OpenRouter")
        self.assertEqual(state["model_provider"]["default"], memory.MODEL_PROVIDER_OPENROUTER)
        self.assertEqual(state["model_provider"]["openrouter"]["model"], "openai/gpt-demo")
        self.assertEqual(
            state["model_provider"]["openrouter"]["fallback_models"],
            ["anthropic/claude-demo"],
        )
        self.assertEqual(state["model_provider"]["openrouter"]["api_key"], "openrouter-secret")
        self.assertEqual(state["model_provider"]["openrouter"]["api_key_env_var"], "OPENROUTER_API_KEY")
        self.assertTrue(app.refreshed)
        self.assertTrue(any("OpenRouter" in body for _title, body in app.activities))

    def test_poll_runtime_events_queues_remote_started_from_service(self):
        events_path = TEST_RUNTIME_DIR / "desktop_remote_events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            json.dumps(
                {
                    "type": "remote_job_started",
                    "label": "Respuesta",
                    "status_text": "Estoy pensando: Respuesta...",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        app = object.__new__(yarbis_desktop.YarbisDesktop)
        app._result_queue = yarbis_desktop.queue.Queue()
        app._runtime_events_position = 0
        app._local_telegram_polling = False

        with patch.object(yarbis_desktop.activity, "EVENTS_FILE", events_path):
            yarbis_desktop.YarbisDesktop._poll_runtime_events(app)

        self.assertEqual(app._runtime_events_position, events_path.stat().st_size)
        self.assertEqual(
            app._result_queue.get_nowait(),
            ("remote_start", "Respuesta", "Estoy pensando: Respuesta..."),
        )

    def test_poll_runtime_events_queues_proactive_pulse_started_from_service(self):
        events_path = TEST_RUNTIME_DIR / "desktop_proactive_events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            json.dumps(
                {
                    "type": "operation_started",
                    "label": "Pulso proactivo",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        app = object.__new__(yarbis_desktop.YarbisDesktop)
        app._result_queue = yarbis_desktop.queue.Queue()
        app._runtime_events_position = 0
        app._local_telegram_polling = False

        with patch.object(yarbis_desktop.activity, "EVENTS_FILE", events_path):
            yarbis_desktop.YarbisDesktop._poll_runtime_events(app)

        self.assertEqual(
            app._result_queue.get_nowait(),
            ("runtime_start", "Pulso proactivo", "Estoy pensando: Pulso proactivo..."),
        )

    def test_desktop_session_operations_keep_notifications_enabled(self):
        script = yarbis_desktop._DESKTOP_SESSION_OPERATION_SCRIPT

        self.assertIn("run_cycle_with_output()", script)
        self.assertIn("run_auto_with_output(cycles=payload.get(\"cycles\"))", script)
        self.assertIn("submit_user_reply(str(payload.get(\"reply_text\", \"\")))", script)
        self.assertNotIn("emit_notifications=False", script)

    def test_launch_update_process_runs_external_updater_without_elevation(self):
        with patch.object(yarbis_desktop.subprocess, "Popen") as popen_mock:
            yarbis_desktop._launch_update_process(needs_admin=False)

        popen_mock.assert_called_once()
        args = popen_mock.call_args.args[0]
        kwargs = popen_mock.call_args.kwargs
        command = args[-1]

        self.assertEqual(
            args[:5],
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"],
        )
        self.assertIn("Start-Process -FilePath 'powershell.exe'", command)
        self.assertIn(str(yarbis_desktop._UPDATE_SCRIPT), command)
        self.assertIn("-RestartDesktop", command)
        self.assertNotIn("-Verb RunAs", command)
        self.assertEqual(kwargs["cwd"], str(yarbis_desktop._WORKSPACE_ROOT))
        self.assertIs(kwargs["stdin"], yarbis_desktop.subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], yarbis_desktop.subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], yarbis_desktop.subprocess.DEVNULL)

    def test_launch_update_process_requests_elevation_when_service_is_installed(self):
        with patch.object(yarbis_desktop.subprocess, "Popen") as popen_mock:
            yarbis_desktop._launch_update_process(needs_admin=True)

        command = popen_mock.call_args.args[0][-1]
        self.assertIn("-Verb RunAs", command)

    def test_refresh_state_view_uses_cached_status_without_blocking_checks(self):
        app = _desktop_app_stub()
        state = memory.default_state()
        state["goal"] = "Responder rapido"

        with patch.object(yarbis_desktop, "load_state", return_value=state):
            with patch.object(yarbis_desktop, "health_status", side_effect=AssertionError("bloqueante")):
                with patch.object(yarbis_desktop, "readiness_status", side_effect=AssertionError("bloqueante")):
                    with patch.object(
                        yarbis_desktop,
                        "get_context_helper_status",
                        side_effect=AssertionError("bloqueante"),
                    ):
                        yarbis_desktop.YarbisDesktop.refresh_state_view(app, force_heavy=False)

        self.assertEqual(app.goal_var.get(), "Responder rapido")
        self.assertEqual(app.service_button_text.get(), "Instalar e iniciar")

    def test_refresh_state_view_shows_last_result_in_response_panel(self):
        app = _desktop_app_stub()
        state = memory.default_state()
        state["last_result"] = "Respuesta visible de Yarbis."

        with patch.object(yarbis_desktop, "load_state", return_value=state):
            with patch.object(yarbis_desktop.YarbisDesktop, "_request_status_refresh", return_value=False):
                yarbis_desktop.YarbisDesktop.refresh_state_view(app, force_heavy=False)

        self.assertIn("Respuesta visible de Yarbis.", app._last_result_widgets[0].options["content"])

    def test_status_refresh_does_not_start_overlapping_workers(self):
        app = _desktop_app_stub()
        app._cached_health_status = None
        app._cached_readiness_status = None
        created_threads = []

        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.target = kwargs["target"]
                created_threads.append(self)

            def start(self):
                pass

        with patch.object(yarbis_desktop.threading, "Thread", FakeThread):
            self.assertTrue(yarbis_desktop.YarbisDesktop._request_status_refresh(app, force=True))
            self.assertFalse(yarbis_desktop.YarbisDesktop._request_status_refresh(app, force=True))

        self.assertEqual(len(created_threads), 1)
        self.assertTrue(app._status_refresh_in_flight)
        self.assertTrue(app._status_refresh_pending_force)

    def test_refresh_state_view_reuses_cached_state_when_file_unchanged(self):
        app = _desktop_app_stub()
        state = memory.default_state()
        state["goal"] = "cache rapido"
        app._cached_state = state
        app._last_state_file_signature = ("state.json", 123, 456)

        with patch.object(
            yarbis_desktop.YarbisDesktop,
            "_state_file_signature",
            return_value=("state.json", 123, 456),
        ):
            with patch.object(yarbis_desktop, "load_state", side_effect=AssertionError("no debe recargar")):
                yarbis_desktop.YarbisDesktop.refresh_state_view(
                    app,
                    force_heavy=False,
                    force_state_reload=False,
                )

        self.assertEqual(app.goal_var.get(), "cache rapido")

    def test_apply_status_snapshot_updates_cache_and_refreshes_view(self):
        app = _desktop_app_stub()
        refreshed = []
        activities = []
        app.refresh_state_view = lambda force_heavy=True, **_kwargs: refreshed.append(force_heavy)
        app._append_activity = lambda title, body: activities.append((title, body))
        snapshot = {
            "health": {"service": _service_status(installed=True, running=True)},
            "readiness": {"items": []},
            "context_helper": {"state": "activo"},
            "events": [("Contexto local", "Helper iniciado.")],
        }

        yarbis_desktop.YarbisDesktop._apply_status_snapshot(app, snapshot)

        self.assertTrue(app._cached_health_status["service"]["running"])
        self.assertEqual(app._cached_context_helper_status["state"], "activo")
        self.assertEqual(activities, [("Contexto local", "Helper iniciado.")])
        self.assertEqual(refreshed, [False])

    def test_service_toggle_uses_cached_status_and_background_job(self):
        app = _desktop_app_stub(_service_status(installed=True, running=True))
        started = []
        app._start_background_job = lambda *args: started.append(args)

        with patch.object(yarbis_desktop, "get_service_status", side_effect=AssertionError("bloqueante")):
            yarbis_desktop.YarbisDesktop._toggle_service(app)

        self.assertEqual(started[0][0], "Servicio")
        self.assertIs(started[0][1], yarbis_desktop.stop_service)

    def test_service_autostart_runs_as_background_job(self):
        app = _desktop_app_stub(_service_status(installed=True, running=False))
        app.service_autostart_var.set(True)
        started = []
        app._start_background_job = lambda *args: started.append(args)

        with patch.object(yarbis_desktop, "get_service_status", side_effect=AssertionError("bloqueante")):
            yarbis_desktop.YarbisDesktop._toggle_service_autostart(app)

        self.assertEqual(started[0][0], "Servicio")
        self.assertIs(started[0][1], yarbis_desktop.set_autostart_enabled)
        self.assertTrue(started[0][2])


if __name__ == "__main__":
    unittest.main()
