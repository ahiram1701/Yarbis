"""Fase 2 de ubicuidad: relay de la malla + cliente.

Levanta un relay HTTP local en Python que refleja el protocolo de
yarbis_mesh_worker.js (KV en memoria), y prueba el cliente mesh_relay contra el.
No importa modulos de GUI, asi que corre tambien en el CI de Linux. Ningun test
toca Puter ni la red externa.
"""

import json
import socket
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import mesh_relay

SECRET = "secreto-de-red-de-prueba-1234567890"

# KV en memoria compartido por el handler (un solo relay por test case).
_STORE: dict = {"nodes": {}, "messages": []}


class _RelayHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        return

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        return self.headers.get("X-Yarbis-Net") == SECRET

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "service": "yarbis-mesh-relay"})
            return
        if not self._auth_ok():
            self._json(401, {"ok": False, "error": "secreto de red invalido"})
            return
        if parsed.path == "/mesh/roster":
            self._json(200, {"ok": True, "nodes": list(_STORE["nodes"].values())})
            return
        if parsed.path == "/mesh/poll":
            node = parse_qs(parsed.query).get("node", [""])[0]
            mine = [m for m in _STORE["messages"] if m.get("to_node") == node]
            _STORE["messages"] = [m for m in _STORE["messages"] if m.get("to_node") != node]
            self._json(200, {"ok": True, "messages": mine})
            return
        self._json(404, {"ok": False, "error": "ruta desconocida"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self._auth_ok():
            self._json(401, {"ok": False, "error": "secreto de red invalido"})
            return
        body = self._read_json()
        if parsed.path == "/mesh/enroll":
            node_id = str(body.get("node_id", "")).strip()
            if not node_id:
                self._json(400, {"ok": False, "error": "node_id requerido"})
                return
            _STORE["nodes"][node_id] = {
                "node_id": node_id,
                "name": body.get("name", node_id),
                "capabilities": body.get("capabilities", {}),
            }
            self._json(200, {"ok": True, "node_id": node_id})
            return
        if parsed.path == "/mesh/send":
            if not str(body.get("to_node", "")).strip():
                self._json(400, {"ok": False, "error": "to_node requerido"})
                return
            _STORE["messages"].append(body)
            self._json(200, {"ok": True, "id": "msg-1"})
            return
        if parsed.path == "/mesh/leave":
            _STORE["nodes"].pop(str(body.get("node_id", "")).strip(), None)
            self._json(200, {"ok": True, "node_id": body.get("node_id", "")})
            return
        self._json(404, {"ok": False, "error": "ruta desconocida"})


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MeshRelayClientTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.server = HTTPServer(("127.0.0.1", cls.port), _RelayHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        _STORE["nodes"].clear()
        _STORE["messages"].clear()

    def test_health(self):
        self.assertTrue(mesh_relay.health(self.url, SECRET))

    def test_wrong_secret_is_rejected(self):
        with self.assertRaises(mesh_relay.MeshRelayError):
            mesh_relay.roster(self.url, "secreto-malo")

    def test_enroll_appears_in_roster(self):
        mesh_relay.enroll(self.url, SECRET, "nodeA", "Portatil", {"gui": True})
        nodes = mesh_relay.roster(self.url, SECRET)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["node_id"], "nodeA")
        self.assertTrue(nodes[0]["capabilities"]["gui"])

    def test_send_and_poll_delivers_once(self):
        env = mesh_relay.make_envelope("nodeA", "nodeB", "direct", "hola B")
        mesh_relay.send(self.url, SECRET, env)
        got = mesh_relay.poll(self.url, SECRET, "nodeB")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["content"], "hola B")
        # Entregar es borrar: un segundo poll ya no lo trae.
        self.assertEqual(mesh_relay.poll(self.url, SECRET, "nodeB"), [])

    def test_poll_drops_messages_with_bad_signature(self):
        # Un mensaje sin firma valida (relay comprometido) se descarta en el cliente.
        forged = mesh_relay.make_envelope("nodeX", "nodeB", "direct", "malicioso")
        forged["sig"] = "0" * 64
        _STORE["messages"].append(forged)
        self.assertEqual(mesh_relay.poll(self.url, SECRET, "nodeB"), [])

    def test_leave_removes_node(self):
        mesh_relay.enroll(self.url, SECRET, "nodeA", "A", {})
        mesh_relay.leave(self.url, SECRET, "nodeA")
        self.assertEqual(mesh_relay.roster(self.url, SECRET), [])


class MeshToolsTestCase(unittest.TestCase):
    def test_create_network_keeps_secret_out_of_state(self):
        import tools

        captured = {}

        def fake_transaction(_label, mutate):
            state = {"mesh": {}}
            mutate(state)
            captured.update(state["mesh"])

        with unittest.mock.patch.object(tools, "state_transaction", side_effect=fake_transaction), \
             unittest.mock.patch.object(tools, "save_secret", return_value="ref-xyz") as save:
            out = tools.mesh_create_network("mi-red")

        secret_line = [ln for ln in out.splitlines() if ln.startswith("Secreto de red: ")][0]
        secret = secret_line.split("Secreto de red: ", 1)[1].strip()
        self.assertTrue(secret)
        # El secreto va a credential_store; en el estado solo queda la referencia.
        save.assert_called_once()
        self.assertEqual(captured["secret_ref"], "ref-xyz")
        self.assertNotIn(secret, json.dumps(captured))
        self.assertEqual(captured["network_name"], "mi-red")

    def test_deploy_help_injects_secret(self):
        import tools

        with unittest.mock.patch.object(tools, "load_state", return_value={"mesh": {"secret_ref": "r"}}), \
             unittest.mock.patch.object(tools, "load_secret", return_value="EL-SECRETO"):
            out = tools.mesh_deploy_help()
        self.assertIn("EL-SECRETO", out)
        self.assertNotIn("__YARBIS_NET_SECRET__", out)


if __name__ == "__main__":
    unittest.main()
