import unittest
from unittest.mock import patch

import internet


class FakeResponse:
    def __init__(self, body: bytes, url: str, content_type: str = "text/html; charset=utf-8"):
        self._body = body
        self._url = url
        self.headers = {"Content-Type": content_type}

    def read(self, *_args, **_kwargs):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class InternetTestCase(unittest.TestCase):
    def test_validate_public_url_rejects_local_and_respects_allowed_domains(self):
        url, error = internet.validate_public_url("http://127.0.0.1:8000/demo")
        self.assertIsNone(url)
        self.assertIn("IPs privadas o locales", error)

        allowed_url, allowed_error = internet.validate_public_url(
            "https://docs.python.org/3/",
            allowed_domains=["python.org"],
        )
        self.assertEqual(allowed_url, "https://docs.python.org/3/")
        self.assertIsNone(allowed_error)

        blocked_url, blocked_error = internet.validate_public_url(
            "https://example.com/demo",
            allowed_domains=["python.org"],
        )
        self.assertIsNone(blocked_url)
        self.assertIn("dominio permitido", blocked_error)

    def test_search_web_parses_duckduckgo_results(self):
        html_body = """
        <html>
          <body>
            <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">
              Articulo de ejemplo
            </a>
            <div class="result__snippet">Resumen del articulo.</div>
            <a class="result__a" href="https://docs.python.org/3/library/unittest.html">
              unittest docs
            </a>
            <div class="result__snippet">Documentacion oficial de unittest.</div>
          </body>
        </html>
        """
        fake_response = FakeResponse(
            body=html_body.encode("utf-8"),
            url="https://html.duckduckgo.com/html/?q=yarbis",
        )

        with patch.object(internet.request, "urlopen", return_value=fake_response):
            results = internet.search_web("yarbis unittest", limit=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Articulo de ejemplo")
        self.assertEqual(results[0]["url"], "https://example.com/article")
        self.assertIn("Resumen del articulo", results[0]["snippet"])
        self.assertEqual(
            results[1]["url"],
            "https://docs.python.org/3/library/unittest.html",
        )

    def test_fetch_web_page_extracts_visible_text(self):
        html_body = """
        <html>
          <head>
            <title>Demo legible</title>
            <style>body { color: red; }</style>
          </head>
          <body>
            <main>
              <h1>Titulo visible</h1>
              <p>Primer parrafo.</p>
              <script>console.log("oculto")</script>
              <p>Segundo parrafo.</p>
            </main>
          </body>
        </html>
        """
        fake_response = FakeResponse(
            body=html_body.encode("utf-8"),
            url="https://example.com/demo",
        )

        with patch.object(internet.request, "urlopen", return_value=fake_response):
            page = internet.fetch_web_page("https://example.com/demo", max_page_chars=4_000)

        self.assertEqual(page["title"], "Demo legible")
        self.assertEqual(page["url"], "https://example.com/demo")
        self.assertIn("Titulo visible", page["content"])
        self.assertIn("Primer parrafo.", page["content"])
        self.assertIn("Segundo parrafo.", page["content"])
        self.assertNotIn("console.log", page["content"])

    def test_fetch_web_page_respects_max_page_chars(self):
        fake_response = FakeResponse(
            body=("inicio " + ("x" * 500)).encode("utf-8"),
            url="https://example.com/plain",
            content_type="text/plain; charset=utf-8",
        )

        with patch.object(internet.request, "urlopen", return_value=fake_response):
            page = internet.fetch_web_page("https://example.com/plain", max_page_chars=40)

        self.assertTrue(page["truncated"])
        self.assertLessEqual(len(page["content"]), 40)
        self.assertTrue(page["content"].startswith("inicio"))
