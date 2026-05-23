import http.client
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

            result = yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="2468")
            state = memory.load_state()

        self.assertIn("UI movil actualizada", result)
        self.assertTrue(state["service"]["mobile_ui"]["enabled"])
        self.assertEqual(state["service"]["mobile_ui"]["port"], 8787)
        self.assertTrue(state["service"]["mobile_ui"]["pin_hash"])
        self.assertTrue(state["service"]["mobile_ui"]["session_secret"])

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
        dialog.pin_var = SimpleNamespace(get=lambda: "")
        dialog._configured = False

        with patch("ui_settings_dialogs.messagebox.showwarning") as warning_mock:
            self.assertFalse(ServiceMobileUiDialog.validate(dialog))

        warning_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
