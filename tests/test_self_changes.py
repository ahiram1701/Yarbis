import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import self_changes
import tools

TEST_RUNTIME_ROOT = tools.WORKSPACE_ROOT / "tests_runtime" / "self_changes_case"


class SelfChangesModuleTestCase(unittest.TestCase):
    def setUp(self):
        self.runtime_dir = TEST_RUNTIME_ROOT / f"{self._testMethodName}-{uuid4().hex[:8]}"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.runtime_dir / "self_changes.jsonl"

    def tearDown(self):
        shutil.rmtree(self.runtime_dir, ignore_errors=True)

    def test_is_own_source_path_accepts_workspace_source(self):
        self.assertTrue(self_changes.is_own_source_path(self_changes.WORKSPACE_ROOT / "tools.py"))
        self.assertTrue(self_changes.is_own_source_path(self_changes.WORKSPACE_ROOT / "tests" / "test_memory.py"))
        self.assertTrue(self_changes.is_own_source_path(self_changes.WORKSPACE_ROOT / "service_host" / "Program.cs"))

    def test_is_own_source_path_rejects_runtime_and_external(self):
        root = self_changes.WORKSPACE_ROOT
        rejected = [
            root / ".yarbis_instances" / "asistente" / "state.json",
            root / ".yarbis_runtime" / "cosa.json",
            root / ".yarbis_checkpoints" / "x" / "before.txt",
            root / ".yarbis_memory_backups" / "b.json",
            root / ".venv" / "Scripts" / "python.exe",
            root / "state.json",
            root / "state.json.tmp-123",
            root / "service.log",
            root / "tests_runtime" / "caso" / "a.py",
            root / "service_host" / "bin" / "x.dll",
            root / "service_host" / "obj" / "x.dll",
            self_changes.WORKSPACE_ROOT.parent / "otra" / "carpeta" / "tools.py",
        ]
        for path in rejected:
            self.assertFalse(self_changes.is_own_source_path(path), f"no debio aceptar: {path}")

    def test_record_and_load_roundtrip(self):
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            self_changes.record_change(
                file_path=self_changes.WORKSPACE_ROOT / "agent.py",
                action="write",
                reason="mejora de reintentos",
                checkpoint_id="chk-1",
                diff_preview="+nueva linea",
            )
            entries = self_changes.load_changes()

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["file"], "agent.py")
        self.assertEqual(entry["action"], "write")
        self.assertEqual(entry["reason"], "mejora de reintentos")
        self.assertEqual(entry["checkpoint_id"], "chk-1")
        self.assertEqual(entry["diff_preview"], "+nueva linea")
        self.assertTrue(entry["timestamp"])
        self.assertTrue(entry["instance"])

    def test_record_ignores_non_source_paths(self):
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            self_changes.record_change(
                file_path=self_changes.WORKSPACE_ROOT / ".yarbis_runtime" / "x.json",
                action="write",
            )
            self_changes.record_change(file_path=self_changes.WORKSPACE_ROOT.parent / "fuera" / "del" / "workspace.py", action="write")
            self.assertEqual(self_changes.load_changes(), [])
        self.assertFalse(self.log_path.exists())

    def test_load_tolerates_corrupt_lines_and_bom(self):
        payload = json.dumps({"file": "tools.py", "action": "write"})
        self.log_path.write_text(
            "﻿" + payload + "\nbasura-no-json\n" + json.dumps({"file": "agent.py", "action": "write"}) + "\n",
            encoding="utf-8",
        )
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            entries = self_changes.load_changes()
        self.assertEqual([e["file"] for e in entries], ["tools.py", "agent.py"])

    def test_load_limit_returns_most_recent(self):
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            for index in range(5):
                self_changes.record_change(
                    file_path=self_changes.WORKSPACE_ROOT / f"mod{index}.py",
                    action="write",
                )
            entries = self_changes.load_changes(limit=2)
        self.assertEqual([e["file"] for e in entries], ["mod3.py", "mod4.py"])

    def test_mark_all_versioned_archives_and_clears(self):
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path), patch.object(
            self_changes, "WORKSPACE_ROOT", self.runtime_dir
        ):
            self_changes.record_change(
                file_path=Path(self_changes.WORKSPACE_ROOT) / "x.py",
                action="write",
            )
            # WORKSPACE_ROOT parcheado: record_change valida contra el parcheado.
            entries_before = self_changes.load_changes()
            self.assertEqual(len(entries_before), 1)

            archived = self_changes.mark_all_versioned(note="commit abc123")
            self.assertEqual(archived, 1)
            self.assertEqual(self_changes.load_changes(), [])

            archives = list(Path(self.runtime_dir).glob(".yarbis_self_changes.versioned-*.jsonl"))
            self.assertEqual(len(archives), 1)
            archived_entry = json.loads(archives[0].read_text(encoding="utf-8").strip())
            self.assertEqual(archived_entry["versioned_note"], "commit abc123")
            self.assertTrue(archived_entry["versioned_at"])

    def test_mark_all_versioned_empty_log(self):
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            self.assertEqual(self_changes.mark_all_versioned(), 0)


class SelfChangesToolsTestCase(unittest.TestCase):
    def setUp(self):
        self.runtime_dir = TEST_RUNTIME_ROOT / f"{self._testMethodName}-{uuid4().hex[:8]}"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.runtime_dir / "self_changes.jsonl"

    def tearDown(self):
        shutil.rmtree(self.runtime_dir, ignore_errors=True)

    def test_list_self_code_changes_empty(self):
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            result = tools.list_self_code_changes()
        self.assertIn("No hay auto-cambios", result)

    def test_list_self_code_changes_shows_entries(self):
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            self_changes.record_change(
                file_path=self_changes.WORKSPACE_ROOT / "agent.py",
                action="write",
                reason="rotacion de modelos",
                checkpoint_id="chk-9",
                diff_preview="+rotated = ...",
            )
            summary = tools.list_self_code_changes()
            with_diff = tools.list_self_code_changes(include_diff=True)

        self.assertIn("agent.py", summary)
        self.assertIn("rotacion de modelos", summary)
        self.assertIn("chk-9", summary)
        self.assertIn("versionarlos", summary)
        self.assertNotIn("+rotated", summary)
        self.assertIn("+rotated", with_diff)

    def test_write_text_file_records_self_change_for_own_source(self):
        # Escribir dentro de tests_runtime NO debe registrar (es ruta ignorada):
        target = self.runtime_dir / "nuevo.py"
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            result = tools._write_text_file_impl(
                str(target.relative_to(tools.WORKSPACE_ROOT).as_posix()),
                "print('hola')\n",
                enforce_coding_guard=False,
            )
            self.assertIn("Archivo creado", result)
            self.assertEqual(self_changes.load_changes(), [])

    def test_write_text_file_records_when_path_is_own_source(self):
        # Parcheamos el filtro para tratar tests_runtime como fuente propia y
        # verificar el hook sin tocar codigo real.
        target = self.runtime_dir / "hooked.py"
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path), patch.object(
            self_changes, "is_own_source_path", return_value=True
        ):
            result = tools._write_text_file_impl(
                str(target.relative_to(tools.WORKSPACE_ROOT).as_posix()),
                "print('hook')\n",
                enforce_coding_guard=False,
            )
        self.assertIn("Archivo creado", result)
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path):
            entries = self_changes.load_changes()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "create")
        self.assertEqual(entries[0]["reason"], "write_text_file")
        self.assertTrue(entries[0]["checkpoint_id"])
        self.assertIn("hook", entries[0]["diff_preview"])

    def test_mark_self_code_changes_versioned_tool(self):
        with patch.object(self_changes, "SELF_CHANGES_FILE", self.log_path), patch.object(
            self_changes, "WORKSPACE_ROOT", self.runtime_dir
        ):
            self_changes.record_change(file_path=Path(self.runtime_dir) / "y.py", action="write")
            result = tools.mark_self_code_changes_versioned(note="commit fff000")
            empty = tools.mark_self_code_changes_versioned()

        self.assertIn("1 auto-cambio", result)
        self.assertIn("vacia", empty)


if __name__ == "__main__":
    unittest.main()
