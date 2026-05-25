import http.client
import base64
import json
import socket
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import memory
import yarbis_mobile
from ui_settings_dialogs import ServiceMobileUiDialog

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class YarbisMobileTestCase(unittest.TestCase):
    def tearDown(self):
        yarbis_mobile.stop_mobile_ui_servers()
        yarbis_mobile._MOBILE_VALUE_CACHE.clear()

    def _service_status(self):
        return {
            "service_name": "Yarbis",
            "display_name": "Yarbis",
            "installed": True,
            "running": True,
            "state": "running",
            "pid": 123,
            "autostart_enabled": True,
            "start_type": "auto_start",
            "account_name": "LocalSystem",
            "log_file": "",
            "service_binary": "",
        }

    def test_pin_hash_and_session_cookie_round_trip(self):
        pin_hash, pin_salt = yarbis_mobile.hash_mobile_pin("1234")
        self.assertTrue(yarbis_mobile.verify_mobile_pin("1234", pin_hash, pin_salt))
        self.assertFalse(yarbis_mobile.verify_mobile_pin("9999", pin_hash, pin_salt))

        cookie, csrf = yarbis_mobile.create_session_cookie("secret")
        session = yarbis_mobile.verify_session_cookie(cookie, "secret")

        self.assertEqual(session["csrf"], csrf)
        self.assertIsNone(yarbis_mobile.verify_session_cookie(cookie, "other-secret"))

    def test_update_mobile_ui_settings_requires_pin_when_enabling(self):
        state_path = TEST_RUNTIME_DIR / "mobile_requires_pin_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="")

            result = yarbis_mobile.update_mobile_ui_settings(
                enabled=True,
                port=8787,
                pin="2468",
                job_timeout_seconds=3600,
            )
            state = memory.load_state()

        self.assertIn("UI movil actualizada", result)
        self.assertTrue(state["service"]["mobile_ui"]["enabled"])
        self.assertEqual(state["service"]["mobile_ui"]["port"], 8787)
        self.assertEqual(state["service"]["mobile_ui"]["job_timeout_seconds"], 3600)
        self.assertTrue(state["service"]["mobile_ui"]["pin_hash"])
        self.assertTrue(state["service"]["mobile_ui"]["session_secret"])

    def test_update_mobile_ui_settings_rejects_invalid_job_timeout(self):
        state_path = TEST_RUNTIME_DIR / "mobile_invalid_timeout_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                yarbis_mobile.update_mobile_ui_settings(
                    enabled=True,
                    port=8787,
                    pin="2468",
                    job_timeout_seconds=10,
                )

    def test_http_api_requires_auth_login_and_csrf(self):
        state_path = TEST_RUNTIME_DIR / "mobile_http_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/state")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                body = json.dumps({"pin": "1357"})
                conn.request("POST", "/api/login", body=body, headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                login_payload = json.loads(response.read().decode("utf-8"))
                cookie = response.getheader("Set-Cookie")
                self.assertEqual(response.status, 200)
                self.assertTrue(login_payload["csrf"])
                self.assertIn(yarbis_mobile.MOBILE_COOKIE_NAME, cookie)

                conn.request("POST", "/api/action", body=json.dumps({"action": "stop_operation"}), headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                })
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                with patch.object(yarbis_mobile, "_execute_action", return_value={"result": "ok"}):
                    conn.request("POST", "/api/action", body=json.dumps({
                        "csrf": login_payload["csrf"],
                        "action": "stop_operation",
                    }), headers={
                        "Content-Type": "application/json",
                        "Cookie": cookie,
                        "X-CSRF-Token": login_payload["csrf"],
                    })
                    response = conn.getresponse()
                    action_payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(action_payload["result"], "ok")
            finally:
                server.shutdown()
                server.server_close()

    def test_http_api_state_view_query_uses_partial_state(self):
        state_path = TEST_RUNTIME_DIR / "mobile_http_view_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    "/api/login",
                    body=json.dumps({"pin": "1357"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                response.read()
                cookie = response.getheader("Set-Cookie")

                with patch.object(yarbis_mobile, "_public_state", return_value={"view": "context"}) as state_mock:
                    conn.request("GET", "/api/state?view=context", headers={"Cookie": cookie})
                    response = conn.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["state"], {"view": "context"})
                state_mock.assert_called_once_with("context")
            finally:
                server.shutdown()
                server.server_close()

    def test_http_api_voice_transcribe_requires_auth_and_csrf(self):
        state_path = TEST_RUNTIME_DIR / "mobile_voice_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                body = json.dumps({
                    "audio_b64": base64.b64encode(b"webm").decode("ascii"),
                    "mime_type": "audio/webm",
                })
                conn.request("POST", "/api/voice/transcribe", body=body, headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                conn.request(
                    "POST",
                    "/api/login",
                    body=json.dumps({"pin": "1357"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                login_payload = json.loads(response.read().decode("utf-8"))
                cookie = response.getheader("Set-Cookie")

                conn.request("POST", "/api/voice/transcribe", body=body, headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                })
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                with patch.object(
                    yarbis_mobile.yarbis_voice,
                    "transcribe_audio_bytes",
                    return_value="texto dictado",
                ) as transcribe_mock:
                    conn.request("POST", "/api/voice/transcribe", body=json.dumps({
                        "csrf": login_payload["csrf"],
                        "audio_b64": base64.b64encode(b"webm").decode("ascii"),
                        "mime_type": "audio/webm",
                    }), headers={
                        "Content-Type": "application/json",
                        "Cookie": cookie,
                        "X-CSRF-Token": login_payload["csrf"],
                    })
                    response = conn.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["text"], "texto dictado")
                transcribe_mock.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()

    def test_public_state_default_is_lightweight_and_loads_state_once(self):
        seeded_state = memory.default_state()
        seeded_state["messages"] = [
            {"role": "assistant", "content": "x" * 4000}
            for _ in range(100)
        ]
        seeded_state["last_result"] = "r" * (yarbis_mobile.MOBILE_LAST_RESULT_CHARS + 20)

        with patch.object(yarbis_mobile, "load_state", return_value=memory.normalize_state(seeded_state)) as load_mock:
            with patch.object(yarbis_mobile, "get_service_status", return_value=self._service_status()):
                with patch.object(yarbis_mobile, "detect_tailscale_ipv4", return_value=""):
                    payload = yarbis_mobile._public_state()

        self.assertEqual(load_mock.call_count, 1)
        self.assertIn("jobs", payload)
        self.assertIn("health_text", payload)
        self.assertNotIn("messages", payload)
        self.assertNotIn("activity_text", payload)
        self.assertNotIn("summary_text", payload)
        self.assertNotIn("memory_protection_status", payload)
        self.assertLess(len(payload["last_result"]), yarbis_mobile.MOBILE_LAST_RESULT_CHARS + 100)

    def test_public_state_views_are_lazy(self):
        seeded_state = memory.normalize_state({
            "goal": "demo movil",
            "profile": {"name": "Ana"},
            "notes": [{"id": "note-1", "title": "Nota", "content": "Contenido"}],
            "tasks": [{"id": "task-1", "title": "Tarea", "status": "pending"}],
            "coding": {"workspace_path": r"C:\DEV\demo", "pending_proposal_ids": ["prop-1"]},
        })

        with patch.object(yarbis_mobile, "load_state", return_value=seeded_state):
            with patch.object(yarbis_mobile, "get_service_status", return_value=self._service_status()):
                with patch.object(yarbis_mobile, "detect_tailscale_ipv4", return_value=""):
                    context_payload = yarbis_mobile._public_state("context")
                    settings_payload = yarbis_mobile._public_state("settings")
                    activity_payload = yarbis_mobile._public_state("activity")

        self.assertEqual(context_payload["profile"]["name"], "Ana")
        self.assertIn("prop-1", context_payload["coding"]["proposals_text"])
        self.assertIn("model_provider", settings_payload)
        self.assertIn("memory_protection_status", settings_payload)
        self.assertIn("summary_text", activity_payload)
        self.assertIn("activity_text", activity_payload)

    def test_mobile_activity_history_uses_small_lazy_limits(self):
        seeded_state = memory.default_state()
        event = {
            "timestamp": "2026-05-23T00:00:00+00:00",
            "type": "remote_job_finished",
            "label": "Respuesta",
            "content": "ok",
        }

        with patch.object(yarbis_mobile.activity, "activity_history_signature", return_value=((1, 1), (2, 2))):
            with patch.object(yarbis_mobile.activity, "read_activity_log", return_value="historial") as log_mock:
                with patch.object(yarbis_mobile.activity, "read_recent_events", return_value=[event]) as events_mock:
                    rendered = yarbis_mobile._mobile_activity_history(seeded_state)

        log_mock.assert_called_once_with(max_bytes=yarbis_mobile.MOBILE_ACTIVITY_MAX_BYTES)
        events_mock.assert_called_once_with(limit=yarbis_mobile.MOBILE_ACTIVITY_EVENT_LIMIT)
        self.assertIn("historial", rendered)
        self.assertIn("Respuesta remoto finalizado", rendered)

    def test_mobile_job_timeout_sends_notification(self):
        sent = []
        notified = threading.Event()

        def fake_notification(title, body):
            sent.append((title, body))
            notified.set()
            return True

        with patch.object(
            yarbis_mobile,
            "_session_operation_subprocess",
            side_effect=yarbis_mobile.MobileJobTimeoutError(120),
        ):
            with patch.object(yarbis_mobile, "send_notification", side_effect=fake_notification):
                job = yarbis_mobile._start_job("Respuesta", "submit_user_reply", {"reply_text": "hola"})
                self.assertTrue(notified.wait(timeout=2))

        stored = yarbis_mobile._get_job(job["id"])
        self.assertEqual(stored["status"], "failed")
        self.assertIn("excedio el timeout movil", sent[0][1])
        self.assertIn("120 segundos", sent[0][1])

    def test_mobile_html_uses_lazy_views_and_slow_auto_refresh(self):
        html = yarbis_mobile._html_page()

        self.assertIn("refreshInFlight", html)
        self.assertIn("statePath(name)", html)
        self.assertIn("currentTab === \"activity\"", html)
        self.assertIn("mobileJobTimeout", html)
        self.assertIn("}, 15000);", html)

    def test_ensure_mobile_ui_servers_starts_localhost_when_tailscale_missing(self):
        state_path = TEST_RUNTIME_DIR / "mobile_server_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        port = _free_port()

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=port, pin="2468")
            with patch.object(yarbis_mobile, "detect_tailscale_ipv4", return_value=""):
                result = yarbis_mobile.ensure_mobile_ui_servers()
                status = yarbis_mobile.public_mobile_ui_status()
                last_bind_error = memory.load_state()["service"]["mobile_ui"]["last_bind_error"]

        self.assertIn("UI movil activa", result)
        self.assertEqual(status["active_urls"], [f"http://127.0.0.1:{port}"])
        self.assertIn("localhost", last_bind_error)

    def test_service_mobile_dialog_rejects_enabled_without_pin(self):
        dialog = object.__new__(ServiceMobileUiDialog)
        dialog.enabled_var = SimpleNamespace(get=lambda: True)
        dialog.port_var = SimpleNamespace(get=lambda: "8787")
        dialog.timeout_var = SimpleNamespace(get=lambda: "1800")
        dialog.pin_var = SimpleNamespace(get=lambda: "")
        dialog._configured = False

        with patch("ui_settings_dialogs.messagebox.showwarning") as warning_mock:
            self.assertFalse(ServiceMobileUiDialog.validate(dialog))

        warning_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
