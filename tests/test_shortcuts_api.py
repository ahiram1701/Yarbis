"""API de Atajos de iOS (Shortcuts): autenticacion por token y endpoints.

Levanta el handler real en un puerto libre (mismo patron que test_mobile_pwa) y
no importa modulos de GUI, asi que tambien corre en el CI de Linux. Las
operaciones del agente estan mockeadas: ningun test ejecuta un ciclo real.
"""

import http.client
import json
import socket
import threading
import unittest
from http.server import HTTPServer
from unittest.mock import patch

import yarbis_mobile


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


TOKEN = "token-de-prueba-para-atajos-1234567890"


class _ShortcutServer:
    """Servidor con un token configurado (o sin el, para probar el 401)."""

    def __init__(self, configured: bool = True):
        self.configured = configured

    def __enter__(self):
        if self.configured:
            token_hash, token_salt = yarbis_mobile.hash_shortcut_token(TOKEN)
        else:
            token_hash, token_salt = "", ""
        self._patch = patch.object(
            yarbis_mobile,
            "get_mobile_ui_settings",
            return_value={
                "enabled": True,
                "port": 8787,
                "shortcut_token_hash": token_hash,
                "shortcut_token_salt": token_salt,
                "tailscale_serve_target": "",
            },
        )
        self._patch.start()
        self.port = _free_port()
        self.server = HTTPServer(("127.0.0.1", self.port), yarbis_mobile.MobileRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._patch.stop()
        return False

    def request(self, method: str, path: str, body: dict | None = None, token: str | None = TOKEN):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {"Content-Type": "application/json"}
            if token is not None:
                headers["Authorization"] = f"Bearer {token}"
            payload = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            try:
                return response.status, json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return response.status, {}
        finally:
            conn.close()


class ShortcutAuthTestCase(unittest.TestCase):
    def test_missing_token_is_unauthorized(self):
        with _ShortcutServer() as srv:
            status, body = srv.request("POST", "/api/shortcut/ask", {"text": "hola"}, token=None)
        self.assertEqual(status, 401)
        self.assertFalse(body["ok"])

    def test_wrong_token_is_unauthorized(self):
        with _ShortcutServer() as srv:
            status, body = srv.request("POST", "/api/shortcut/ask", {"text": "hola"}, token="otro")
        self.assertEqual(status, 401)
        self.assertFalse(body["ok"])

    def test_disabled_when_no_token_configured(self):
        # Apagado por defecto: aunque manden un token valido-looking, 401.
        with _ShortcutServer(configured=False) as srv:
            status, body = srv.request("POST", "/api/shortcut/ask", {"text": "hola"})
        self.assertEqual(status, 401)
        self.assertIn("no esta configurado", body["error"].lower())


class ShortcutAskTestCase(unittest.TestCase):
    def test_ask_waits_and_returns_reply(self):
        job = {"id": "job-1", "status": "finished", "result": "Listo, ya lo hice."}
        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "_start_job", return_value=job), \
             patch.object(yarbis_mobile, "_get_job", return_value=job):
            status, body = srv.request("POST", "/api/shortcut/ask", {"text": "que hago hoy"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["reply"], "Listo, ya lo hice.")

    def test_ask_without_waiting_queues_immediately(self):
        job = {"id": "job-2", "status": "running", "result": ""}
        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "_start_job", return_value=job) as start, \
             patch.object(yarbis_mobile, "_get_job", return_value=job):
            status, body = srv.request(
                "POST", "/api/shortcut/ask", {"text": "recuerdame algo", "esperar": "false"}
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["queued"])
        self.assertEqual(body["job_id"], "job-2")
        start.assert_called_once()

    def test_ask_pending_keeps_the_job_running(self):
        # Si se agota el tope de espera NO se cancela: sigue y avisa despues.
        job = {"id": "job-3", "status": "running", "result": ""}
        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "_start_job", return_value=job), \
             patch.object(yarbis_mobile, "_get_job", return_value=job):
            status, body = srv.request(
                "POST", "/api/shortcut/ask", {"text": "algo largo", "wait_seconds": 1}
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["pending"])
        self.assertEqual(body["job_id"], "job-3")

    def test_ask_reports_failure(self):
        job = {"id": "job-4", "status": "failed", "error": "algo trono"}
        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "_start_job", return_value=job), \
             patch.object(yarbis_mobile, "_get_job", return_value=job):
            status, body = srv.request("POST", "/api/shortcut/ask", {"text": "x"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("algo trono", body["error"])

    def test_ask_requires_text(self):
        with _ShortcutServer() as srv:
            status, body = srv.request("POST", "/api/shortcut/ask", {})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])


class ShortcutCaptureTestCase(unittest.TestCase):
    def test_note_uses_first_line_as_title_when_missing(self):
        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "save_note_text", return_value="nota guardada") as save:
            status, body = srv.request(
                "POST", "/api/shortcut/note", {"text": "Comprar pan\ny leche"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], "nota guardada")
        self.assertEqual(save.call_args[0][0], "Comprar pan")

    def test_task_uses_add_task_text(self):
        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "add_task_text", return_value="tarea creada") as add:
            status, body = srv.request(
                "POST", "/api/shortcut/task", {"text": "Llamar al banco", "prioridad": "alta"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], "tarea creada")
        self.assertEqual(add.call_args[0][0], "Llamar al banco")
        self.assertEqual(add.call_args[0][2], "alta")

    def test_status_returns_text(self):
        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "_shortcut_status_text", return_value="todo bien"):
            status, body = srv.request("GET", "/api/shortcut/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "todo bien")

    def test_image_routes_to_vision(self):
        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "_analyze_mobile_image", return_value="es un gato") as vision:
            status, body = srv.request(
                "POST", "/api/shortcut/image", {"image_b64": "AAAA", "question": "que es"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], "es un gato")
        vision.assert_called_once()

    def test_busy_agent_returns_conflict(self):
        from session import SessionOperationBusy

        with _ShortcutServer() as srv, \
             patch.object(yarbis_mobile, "_start_job", side_effect=SessionOperationBusy("ocupado")):
            status, body = srv.request("POST", "/api/shortcut/ask", {"text": "hola"})
        self.assertEqual(status, 409)
        self.assertFalse(body["ok"])


class ShortcutTokenStorageTestCase(unittest.TestCase):
    def test_token_is_stored_hashed_only(self):
        import tools

        captured = {}

        def fake_transaction(_label, mutate):
            state = {"ui": {"mobile_ui": {}}}
            mutate(state)
            captured.update(state["ui"]["mobile_ui"])

        with patch.object(tools, "state_transaction", side_effect=fake_transaction), \
             patch.object(tools, "load_state", return_value={"ui": {"mobile_ui": {"port": 8787}}}):
            output = tools.shortcuts_create_token()

        # El token viaja al usuario una sola vez, pero al estado solo va el hash.
        token_line = [ln for ln in output.splitlines() if ln.startswith("Token: ")][0]
        token = token_line.split("Token: ", 1)[1].strip()
        self.assertTrue(token)
        self.assertNotIn(token, json.dumps(captured))
        self.assertTrue(captured["shortcut_token_hash"])
        self.assertTrue(captured["shortcut_token_salt"])
        # Y el hash guardado si valida ese token.
        self.assertTrue(
            yarbis_mobile.verify_shortcut_token(
                token, captured["shortcut_token_hash"], captured["shortcut_token_salt"]
            )
        )

    def test_revoke_clears_the_token(self):
        import tools

        captured = {}

        def fake_transaction(_label, mutate):
            state = {"ui": {"mobile_ui": {"shortcut_token_hash": "x", "shortcut_token_salt": "y"}}}
            mutate(state)
            captured.update(state["ui"]["mobile_ui"])

        with patch.object(tools, "state_transaction", side_effect=fake_transaction):
            tools.shortcuts_revoke_token()
        self.assertEqual(captured["shortcut_token_hash"], "")
        self.assertEqual(captured["shortcut_token_salt"], "")


if __name__ == "__main__":
    unittest.main()
