import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import memory
import self_knowledge
import tools

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


def _stub(doc):
    def f():
        pass
    f.__doc__ = doc
    return f


class CapabilitiesCatalogTestCase(unittest.TestCase):
    def test_catalog_derived_from_registry_and_grouped(self):
        fake_registry = {
            "save_note": _stub("Guarda una nota."),
            "list_notes": _stub("Lista notas."),
            "browser_open": _stub("Abre el navegador."),
            "desktop_click": _stub("Hace click."),
            "record_self_insight": _stub("Guarda un aprendizaje sobre mi mismo."),
            "coding_apply_proposal": _stub("Aplica una propuesta."),
            "evolution_status": _stub("Estado de autoevolucion."),
            "una_tool_rara": _stub("Sin area asignada."),
        }
        fake_agent = type("M", (), {"available_functions": fake_registry})
        with patch.dict("sys.modules", {"agent": fake_agent}):
            lines = self_knowledge._render_capabilities_catalog()
        text = "\n".join(lines)
        self.assertIn("Herramientas disponibles: 8", text)
        self.assertIn("Autoconocimiento", text)
        self.assertIn("record_self_insight", text)
        self.assertIn("Navegador y control de PC", text)
        # la tool sin area cae en "Otras"
        self.assertIn("una_tool_rara", text)

    def test_catalog_reflects_tool_additions_and_removals(self):
        base = {"save_note": _stub("Guarda una nota.")}
        with patch.dict("sys.modules", {"agent": type("M", (), {"available_functions": base})}):
            before = "\n".join(self_knowledge._render_capabilities_catalog())
        self.assertIn("Herramientas disponibles: 1", before)

        grown = dict(base)
        grown["nueva_tool"] = _stub("Nueva capacidad.")
        with patch.dict("sys.modules", {"agent": type("M", (), {"available_functions": grown})}):
            after = "\n".join(self_knowledge._render_capabilities_catalog())
        self.assertIn("Herramientas disponibles: 2", after)
        self.assertIn("nueva_tool", after)

    def test_catalog_falls_back_without_agent(self):
        with patch.dict("sys.modules", {"agent": None}):
            lines = self_knowledge._render_capabilities_catalog()
        # Con agent=None el import lanza y cae al fallback, sin romper.
        self.assertTrue(any("Capacidades base" in ln or "No pude derivar" in ln for ln in lines))

    def test_source_inventory_richer_cap(self):
        # El tope subio de 36 a 90: se listan mas archivos que antes.
        self.assertGreaterEqual(self_knowledge.MAX_SOURCE_FILES, 90)


class SelfInsightsNormalizationTestCase(unittest.TestCase):
    def test_default_state_has_empty_insights(self):
        self.assertEqual(memory.default_state()["self_knowledge"]["insights"], [])

    def test_normalize_filters_and_caps(self):
        raw = {
            "self_knowledge": {
                "summary": "x",
                "insights": [
                    {"text": "valido", "category": "limite"},
                    {"text": "  ", "category": "leccion"},          # vacio -> descartado
                    {"text": "cat mala", "category": "bogus"},       # categoria -> leccion
                    "no soy dict",                                    # descartado
                ],
            }
        }
        insights = memory.normalize_state(raw)["self_knowledge"]["insights"]
        self.assertEqual(len(insights), 2)
        self.assertEqual(insights[0]["category"], "limite")
        self.assertEqual(insights[1]["category"], "leccion")
        for item in insights:
            self.assertTrue(item["id"])

    def test_normalize_respects_max_count(self):
        many = [{"text": f"insight {i}", "category": "leccion"} for i in range(memory.MAX_SELF_KNOWLEDGE_INSIGHTS + 10)]
        insights = memory.normalize_state({"self_knowledge": {"insights": many}})["self_knowledge"]["insights"]
        self.assertEqual(len(insights), memory.MAX_SELF_KNOWLEDGE_INSIGHTS)


class SelfInsightToolsTestCase(unittest.TestCase):
    def _state_path(self):
        return TEST_RUNTIME_DIR / f"sk-{uuid4().hex[:8]}.json"

    def test_record_list_remove_and_overview(self):
        path = self._state_path()
        with patch.object(memory, "STATE_FILE", path):
            memory.save_state(memory.default_state())
            out = tools.record_self_insight("me cuesta estimar tiempos", "limite")
            self.assertIn("limite", out)
            listed = tools.list_self_insights()
            self.assertIn("me cuesta estimar tiempos", listed)

            state = memory.load_state()
            iid = state["self_knowledge"]["insights"][0]["id"]

            overview = tools.self_overview(refresh=True)
            self.assertIn("Herramientas disponibles", overview)  # capacidades derivadas
            self.assertIn("me cuesta estimar tiempos", overview)  # insight preservado y mostrado
            # refresh no borra insights
            self.assertEqual(len(memory.load_state()["self_knowledge"]["insights"]), 1)

            removed = tools.remove_self_insight(iid)
            self.assertIn("eliminado", removed)
            self.assertEqual(memory.load_state()["self_knowledge"]["insights"], [])

    def test_record_invalid_category_defaults_and_dedup(self):
        path = self._state_path()
        with patch.object(memory, "STATE_FILE", path):
            memory.save_state(memory.default_state())
            tools.record_self_insight("mismo texto", "bogus")
            tools.record_self_insight("mismo texto", "estrategia")  # dedup por texto, actualiza categoria
            insights = memory.load_state()["self_knowledge"]["insights"]
        self.assertEqual(len(insights), 1)
        self.assertEqual(insights[0]["category"], "estrategia")


if __name__ == "__main__":
    unittest.main()
