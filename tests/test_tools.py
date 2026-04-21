import unittest
from pathlib import Path

import tools

TEST_RUNTIME_DIR = tools.WORKSPACE_ROOT / "tests_runtime" / "tools_case"


class ToolsTestCase(unittest.TestCase):
    def setUp(self):
        TEST_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    def test_read_text_file_truncates_large_content(self):
        file_path = TEST_RUNTIME_DIR / "large.txt"
        file_path.write_text("a" * (tools.MAX_READ_BYTES + 25), encoding="utf-8")

        result = tools.read_text_file(file_path.relative_to(tools.WORKSPACE_ROOT).as_posix())

        self.assertIn("Contenido truncado", result)
        self.assertIn("large.txt", result)

    def test_write_text_file_blocks_paths_outside_workspace(self):
        result = tools.write_text_file("../outside.txt", "hola")

        self.assertIn("Acceso denegado", result)

    def test_list_files_returns_relative_paths(self):
        nested_dir = TEST_RUNTIME_DIR / "docs"
        nested_dir.mkdir(parents=True, exist_ok=True)
        (nested_dir / "note.txt").write_text("hola", encoding="utf-8")

        result = tools.list_files(TEST_RUNTIME_DIR.relative_to(tools.WORKSPACE_ROOT).as_posix())

        self.assertIn("tests_runtime/tools_case/docs", result)
