"""Guarda anti-elision/truncado de archivos de codigo.

Nace de un incidente real: la autoedicion de Yarbis aplico una propuesta que
reescribio 14 de sus modulos con solo `...`, vaciandolos. `ast.parse` no lo
atrapa porque `...` es Python valido, y el servicio sobrevivio un tiempo con
bytecode viejo en cache, lo que hizo el fallo dificil de diagnosticar.
"""

import tempfile
import unittest
from pathlib import Path

import tools

REAL_MODULE = "\n".join(
    ['"""Modulo de ejemplo."""', "", "import os", "", ""]
    + [f"def funcion_{i}():\n    return os.getcwd() + '{i}'\n" for i in range(60)]
)


class ElisionDetectionTestCase(unittest.TestCase):
    def test_bare_ellipsis_is_elided(self):
        self.assertTrue(tools._python_body_is_elided("..."))

    def test_pass_and_docstring_only_are_elided(self):
        self.assertTrue(tools._python_body_is_elided("pass"))
        self.assertTrue(tools._python_body_is_elided('"""Solo docstring."""'))
        self.assertTrue(tools._python_body_is_elided(""))

    def test_real_code_is_not_elided(self):
        self.assertFalse(tools._python_body_is_elided("def f():\n    return 1\n"))

    def test_syntax_error_is_left_to_the_other_guard(self):
        self.assertFalse(tools._python_body_is_elided("def f(:\n"))

    def test_textual_elision_markers(self):
        self.assertTrue(tools._looks_like_elided_text("..."))
        self.assertTrue(tools._looks_like_elided_text("# resto del archivo sin cambios"))
        self.assertTrue(tools._looks_like_elided_text("// rest of the file unchanged"))
        self.assertFalse(tools._looks_like_elided_text("const x = 1;"))


class TruncationGuardTestCase(unittest.TestCase):
    def _reason(self, name, new, old, existed=True):
        return tools._code_truncation_block_reason(Path(name), new, old, existed)

    def test_blocks_the_real_disaster(self):
        reason = self._reason("tools.py", "...", REAL_MODULE)
        self.assertIsNotNone(reason)
        self.assertIn("BLOQUEADO", reason)
        self.assertIn("elision", reason)

    def test_blocks_drastic_shrink_even_with_real_code(self):
        shrunk = "import os\n\n\ndef solo_una():\n    return os.getcwd()\n"
        reason = self._reason("tools.py", shrunk, REAL_MODULE)
        self.assertIsNotNone(reason)
        self.assertIn("BLOQUEADO", reason)

    def test_allows_normal_edit(self):
        self.assertIsNone(self._reason("tools.py", REAL_MODULE + "\n# nota\n", REAL_MODULE))

    def test_allows_moderate_refactor(self):
        kept = REAL_MODULE[: int(len(REAL_MODULE) * 0.7)] + "\n"
        self.assertIsNone(self._reason("tools.py", kept, REAL_MODULE))

    def test_allows_new_file(self):
        self.assertIsNone(self._reason("nuevo.py", "...", "", existed=False))

    def test_allows_small_file_rewrite(self):
        self.assertIsNone(self._reason("mini.py", "x = 1\n", "y = 2\n" * 20))

    def test_does_not_guard_docs(self):
        # Resumir un README al 12% es legitimo; la guarda es solo para codigo.
        long_doc = "# Titulo\n" + ("linea de documentacion\n" * 400)
        self.assertIsNone(self._reason("README.md", "# Titulo\n", long_doc))

    def test_guards_other_code_suffixes(self):
        long_js = "// worker\n" + ("function f() { return 1; }\n" * 100)
        self.assertIsNotNone(self._reason("worker.js", "// resto igual\n", long_js))


class WritePathIntegrationTestCase(unittest.TestCase):
    """La guarda debe cortar la escritura REAL, no solo evaluar el texto."""

    def test_write_text_file_impl_refuses_to_empty_a_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modulo.py"
            target.write_text(REAL_MODULE, encoding="utf-8")

            result = tools._write_text_file_impl(str(target), "...", enforce_coding_guard=False)

            self.assertIn("BLOQUEADO", result)
            # Lo esencial: el archivo NO se toco.
            self.assertEqual(target.read_text(encoding="utf-8"), REAL_MODULE)

    def test_write_text_file_impl_allows_normal_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modulo.py"
            target.write_text(REAL_MODULE, encoding="utf-8")
            nuevo = REAL_MODULE + "\n\ndef extra():\n    return 'ok'\n"

            result = tools._write_text_file_impl(str(target), nuevo, enforce_coding_guard=False)

            self.assertNotIn("BLOQUEADO", result)
            self.assertIn("extra", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
