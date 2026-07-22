"""PWA de la UI movil: manifest, service worker e iconos.

Levanta el handler real en un puerto libre y pide los recursos SIN sesion, que
es como los pide el navegador antes de autenticar. No importa modulos de GUI, asi
que tambien corre en el CI de Linux.
"""

import http.client
import json
import socket
import threading
import unittest
from http.server import HTTPServer

import yarbis_mobile


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PwaAssetsTestCase(unittest.TestCase):
    def test_manifest_is_installable(self):
        manifest = json.loads(yarbis_mobile._pwa_manifest())
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        purposes = {icon.get("purpose") for icon in manifest["icons"]}
        self.assertIn("maskable", purposes)  # Android recorta el icono

    def test_service_worker_never_caches_api_or_html(self):
        sw = yarbis_mobile._pwa_service_worker()
        # El estado del agente jamas debe servirse desde cache.
        self.assertIn('url.pathname.startsWith("/api/")', sw)
        self.assertIn('url.pathname === "/"', sw)
        self.assertIn("SHELL_ASSETS.includes(url.pathname)", sw)

    def test_html_declares_pwa_and_registers_worker(self):
        html = yarbis_mobile._html_page()
        for needle in (
            'rel="manifest"',
            'name="theme-color"',
            'name="apple-mobile-web-app-capable"',
            'rel="apple-touch-icon"',
            'serviceWorker.register("/sw.js")',
        ):
            self.assertIn(needle, html)

    def test_icons_exist_as_static_files(self):
        # Se commitean como PNG estaticos: el host que sirve la UI (Termux, VPS)
        # no necesita Pillow para generarlos.
        for name in ("icon-192.png", "icon-512.png"):
            path = yarbis_mobile.PWA_ICONS_DIR / name
            self.assertTrue(path.exists(), f"falta {name}")
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))


class PwaRoutesTestCase(unittest.TestCase):
    """Los recursos PWA se sirven sin sesion y con el content-type correcto."""

    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server = HTTPServer(("127.0.0.1", cls.port), yarbis_mobile.MobileRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def _get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.getheader("Content-Type") or "", response.read()
        finally:
            conn.close()

    def test_manifest_route_without_session(self):
        status, content_type, body = self._get("/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertIn("application/manifest+json", content_type)
        self.assertEqual(json.loads(body.decode("utf-8"))["short_name"], "Yarbis")

    def test_service_worker_route_without_session(self):
        status, content_type, body = self._get("/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", content_type)
        self.assertIn(b"SHELL_CACHE", body)

    def test_icon_routes_without_session(self):
        for name in ("icon-192.png", "icon-512.png"):
            status, content_type, body = self._get(f"/icons/{name}")
            self.assertEqual(status, 200, name)
            self.assertEqual(content_type, "image/png")
            self.assertTrue(body.startswith(b"\x89PNG"))

    def test_unknown_icon_is_not_found(self):
        status, _content_type, _body = self._get("/icons/otro.png")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
