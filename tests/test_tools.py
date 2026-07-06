import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import memory
import tools

TEST_RUNTIME_ROOT = tools.WORKSPACE_ROOT / "tests_runtime" / "tools_case"


class ToolsTestCase(unittest.TestCase):
    def setUp(self):
        self.runtime_dir = TEST_RUNTIME_ROOT / f"{self._testMethodName}-{uuid4().hex[:8]}"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir = self.runtime_dir / ".yarbis_checkpoints"
        self.proposals_dir = self.runtime_dir / ".yarbis_runtime" / "coding_proposals"
        self.state_path = self.runtime_dir / "state.json"
        self.external_dir = Path(tempfile.gettempdir()) / f"yarbis-tools-{self._testMethodName}-{uuid4().hex[:8]}"
        self.external_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.external_dir.exists() and self.external_dir.name.startswith("yarbis-tools-"):
            shutil.rmtree(self.external_dir, ignore_errors=True)

    def test_set_timezone_valid_saves_and_invalid_rejected(self):
        with patch.object(memory, "STATE_FILE", self.state_path):
            memory.save_state(memory.default_state())
            ok = tools.set_timezone("America/Mexico_City")
            self.assertIn("America/Mexico_City", ok)
            self.assertEqual(memory.load_state()["profile"]["timezone"], "America/Mexico_City")

            bad = tools.set_timezone("Nowhere/Fake")
            self.assertIn("invalida", bad.lower())
            # No cambia la zona valida previa.
            self.assertEqual(memory.load_state()["profile"]["timezone"], "America/Mexico_City")

            cleared = tools.set_timezone("")
            self.assertIn("sistema", cleared.lower())
            self.assertEqual(memory.load_state()["profile"]["timezone"], "")

    def test_read_text_file_returns_large_content_complete(self):
        file_path = self.runtime_dir / "large.txt"
        content = "a" * (tools.MAX_READ_BYTES + 25)
        file_path.write_text(content, encoding="utf-8")

        result = tools.read_text_file(file_path.relative_to(tools.WORKSPACE_ROOT).as_posix())

        self.assertEqual(result, content)

    def test_read_text_file_can_return_bounded_content(self):
        file_path = self.runtime_dir / "bounded.txt"
        content = "a" * 200
        file_path.write_text(content, encoding="utf-8")

        result = tools.read_text_file(
            file_path.relative_to(tools.WORKSPACE_ROOT).as_posix(),
            max_bytes=50,
        )

        self.assertTrue(result.startswith("a" * 50))
        self.assertIn("truncado por max_bytes=50", result)
        self.assertNotEqual(result, content)

    def test_write_text_file_allows_paths_outside_workspace(self):
        file_path = self.external_dir / "outside.txt"

        with patch.object(tools, "CHECKPOINTS_DIR", self.checkpoints_dir):
            result = tools.write_text_file(str(file_path), "hola")

        self.assertIn("Archivo creado", result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "hola")

    def test_list_and_read_files_allow_paths_outside_workspace(self):
        file_path = self.external_dir / "outside-read.txt"
        file_path.write_text("hola externo", encoding="utf-8")

        listing = tools.list_files(str(self.external_dir))
        content = tools.read_text_file(str(file_path))

        self.assertIn(str(file_path), listing)
        self.assertEqual(content, "hola externo")

    def test_write_text_file_blocks_protected_state_file(self):
        result = tools.write_text_file("state.json", "{}")

        self.assertIn("Escritura bloqueada", result)

    def test_write_text_file_creates_checkpoint_and_diff_for_existing_file(self):
        file_path = self.runtime_dir / "self_edit.py"
        file_path.write_text("print('old')\n", encoding="utf-8")

        with patch.object(tools, "CHECKPOINTS_DIR", self.checkpoints_dir):
            result = tools.write_text_file(
                file_path.relative_to(tools.WORKSPACE_ROOT).as_posix(),
                "print('new')\n",
            )

        checkpoint_dirs = [item for item in self.checkpoints_dir.iterdir() if item.is_dir()]
        self.assertEqual(len(checkpoint_dirs), 1)
        metadata = json.loads((checkpoint_dirs[0] / "meta.json").read_text(encoding="utf-8"))

        self.assertIn("Checkpoint previo:", result)
        self.assertIn("Diff:", result)
        self.assertIn("-print('old')", result)
        self.assertIn("+print('new')", result)
        self.assertEqual(
            metadata["target_path"],
            file_path.relative_to(tools.WORKSPACE_ROOT).as_posix(),
        )
        self.assertTrue(metadata["existed_before"])

    def test_write_text_file_bounds_preview_and_diff(self):
        file_path = self.runtime_dir / "large_diff.txt"
        previous_content = "\n".join(f"old {index}" for index in range(tools.MAX_DIFF_LINES + 50))
        new_content = "\n".join(f"new {index}" for index in range(tools.MAX_DIFF_LINES + 50))
        file_path.write_text(previous_content, encoding="utf-8")

        with patch.object(tools, "CHECKPOINTS_DIR", self.checkpoints_dir):
            result = tools.write_text_file(
                file_path.relative_to(tools.WORKSPACE_ROOT).as_posix(),
                new_content,
            )

        preview = result.split("Vista previa:\n", 1)[1].split("\nDiff:\n", 1)[0]
        diff = result.split("\nDiff:\n", 1)[1].split("\nSiguiente paso recomendado:", 1)[0]
        self.assertLessEqual(len(preview), tools.MAX_WRITE_PREVIEW_CHARS + 40)
        self.assertIn("[truncado", preview)
        self.assertIn("diff truncado", diff)
        self.assertLessEqual(len(diff.splitlines()), tools.MAX_DIFF_LINES)

    def test_coding_set_workspace_persists_repo_path(self):
        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                result = tools.coding_set_workspace(str(self.external_dir))
                state = memory.load_state()

        self.assertIn("Workspace de codigo configurado", result)
        self.assertEqual(state["coding"]["workspace_path"], str(self.external_dir.resolve()))
        self.assertEqual(state["coding"]["mode"], "propose_first")

    def test_coding_set_workspace_rejects_missing_path(self):
        missing_path = self.external_dir / "missing"

        result = tools.coding_set_workspace(str(missing_path))

        self.assertIn("no existe", result)

    def test_coding_tools_reject_paths_outside_active_workspace(self):
        outside_file = self.runtime_dir / "outside.txt"
        outside_file.write_text("fuera", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_read_text_file(str(outside_file))

        self.assertIn("Ruta fuera del workspace de codigo activo", result)

    def test_coding_search_text_respects_workspace_and_limits_results(self):
        (self.external_dir / "app.py").write_text("alpha\nneedle here\nomega\n", encoding="utf-8")

        fake_result = subprocess.CompletedProcess(
            args=["rg"],
            returncode=0,
            stdout="app.py:2:1:needle here\n",
            stderr="",
        )

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                with patch.object(tools.subprocess, "run", return_value=fake_result) as run_mock:
                    result = tools.coding_search_text("needle", glob="*.py", max_results=5)

        self.assertIn("Busqueda coding", result)
        self.assertIn("needle here", result)
        self.assertEqual(run_mock.call_args.kwargs["cwd"], str(self.external_dir.resolve()))
        self.assertIn("--max-count", run_mock.call_args.args[0])
        self.assertIn("5", run_mock.call_args.args[0])

    def test_coding_read_text_range_returns_numbered_lines(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_read_text_range("app.py", start_line=2, line_count=2)

        self.assertIn("Lineas: 2-3 de 4", result)
        self.assertIn("2: two", result)
        self.assertIn("3: three", result)

    def test_coding_propose_text_file_creates_diff_without_modifying_file(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_propose_text_file("app.py", "print('new')\n", reason="actualizar salida")
                state = memory.load_state()
                detail = tools.coding_get_proposal(state["coding"]["pending_proposal_ids"][0])

        proposal_files = list(self.proposals_dir.glob("*.json"))
        proposal = json.loads(proposal_files[0].read_text(encoding="utf-8"))
        self.assertIn("Propuesta de coding creada", result)
        self.assertIn("Diff:", detail)
        self.assertIn("-print('old')", result)
        self.assertIn("+print('new')", result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "print('old')\n")
        self.assertEqual(proposal["schema_version"], 2)
        self.assertEqual(proposal["files"][0]["path"], "app.py")
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(state["coding"]["pending_proposal_ids"], [proposal["id"]])

    def test_coding_propose_changes_creates_multi_file_proposal(self):
        first_path = self.external_dir / "app.py"
        first_path.write_text("print('old')\n", encoding="utf-8")

        files_json = json.dumps([
            {"path": "app.py", "operation": "write", "content": "print('new')\n"},
            {"path": "new_module.py", "operation": "write", "content": "VALUE = 1\n"},
        ])

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_propose_changes(
                    title="Actualizar app",
                    files_json=files_json,
                    summary="Cambia app y agrega modulo.",
                    reason="demo",
                )

        proposal_id = next(line.split(":", 1)[1].strip() for line in result.splitlines() if line.startswith("Id:"))
        proposal = json.loads((self.proposals_dir / f"{proposal_id}.json").read_text(encoding="utf-8"))
        self.assertIn("Archivos: 2", result)
        self.assertEqual([item["path"] for item in proposal["files"]], ["app.py", "new_module.py"])
        self.assertFalse((self.external_dir / "new_module.py").exists())

    def test_coding_propose_edits_exact_replace_creates_proposal_without_mutating_file(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")
        edits_json = json.dumps([{
            "type": "exact_replace",
            "path": "app.py",
            "old_text": "old",
            "new_text": "new",
        }])

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_propose_edits("Editar app", edits_json)

        proposal_id = next(line.split(":", 1)[1].strip() for line in result.splitlines() if line.startswith("Id:"))
        proposal = json.loads((self.proposals_dir / f"{proposal_id}.json").read_text(encoding="utf-8"))
        self.assertIn("Propuesta de coding creada", result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "print('old')\n")
        self.assertEqual(proposal["files"][0]["proposed_content"], "print('new')\n")

    def test_coding_propose_edits_exact_replace_blocks_ambiguous_text(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("value = 1\nvalue = 2\n", encoding="utf-8")
        edits_json = json.dumps([{
            "type": "exact_replace",
            "path": "app.py",
            "old_text": "value",
            "new_text": "item",
        }])

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_propose_edits("Editar app", edits_json)

        self.assertIn("ambiguedad", result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "value = 1\nvalue = 2\n")

    def test_coding_propose_edits_line_range_creates_proposal(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
        edits_json = json.dumps([{
            "type": "line_range",
            "path": "app.py",
            "start_line": 2,
            "end_line": 2,
            "replacement": "TWO\n",
        }])

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_propose_edits("Editar rango", edits_json)

        proposal_id = next(line.split(":", 1)[1].strip() for line in result.splitlines() if line.startswith("Id:"))
        proposal = json.loads((self.proposals_dir / f"{proposal_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(proposal["files"][0]["proposed_content"], "one\nTWO\nthree\n")
        self.assertEqual(file_path.read_text(encoding="utf-8"), "one\ntwo\nthree\n")

    def test_coding_propose_edits_line_range_blocks_invalid_range(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("one\n", encoding="utf-8")
        edits_json = json.dumps([{
            "type": "line_range",
            "path": "app.py",
            "start_line": 3,
            "end_line": 3,
            "replacement": "three\n",
        }])

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_propose_edits("Editar rango", edits_json)

        self.assertIn("Rango fuera del archivo", result)

    def test_coding_apply_proposal_handles_multiple_files_and_delete(self):
        keep_path = self.external_dir / "app.py"
        delete_path = self.external_dir / "old.py"
        keep_path.write_text("print('old')\n", encoding="utf-8")
        delete_path.write_text("OLD = True\n", encoding="utf-8")
        files_json = json.dumps([
            {"path": "app.py", "operation": "write", "content": "print('new')\n"},
            {"path": "old.py", "operation": "delete"},
            {"path": "created.py", "operation": "write", "content": "CREATED = True\n"},
        ])

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                with patch.object(tools, "CHECKPOINTS_DIR", self.checkpoints_dir):
                    tools.coding_set_workspace(str(self.external_dir))
                    propose_result = tools.coding_propose_changes("Trabajo multi", files_json)
                    proposal_id = next(
                        line.split(":", 1)[1].strip()
                        for line in propose_result.splitlines()
                        if line.startswith("Id:")
                    )
                    apply_result = tools.coding_apply_proposal(proposal_id)

        self.assertIn("Archivos aplicados", apply_result)
        self.assertEqual(keep_path.read_text(encoding="utf-8"), "print('new')\n")
        self.assertFalse(delete_path.exists())
        self.assertEqual((self.external_dir / "created.py").read_text(encoding="utf-8"), "CREATED = True\n")
        checkpoint_dirs = [item for item in self.checkpoints_dir.iterdir() if item.is_dir()]
        self.assertEqual(len(checkpoint_dirs), 3)

    def test_coding_apply_proposal_preflight_blocks_if_any_file_changed(self):
        first_path = self.external_dir / "app.py"
        second_path = self.external_dir / "other.py"
        first_path.write_text("old app\n", encoding="utf-8")
        second_path.write_text("old other\n", encoding="utf-8")
        files_json = json.dumps([
            {"path": "app.py", "operation": "write", "content": "new app\n"},
            {"path": "other.py", "operation": "write", "content": "new other\n"},
        ])

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                with patch.object(tools, "CHECKPOINTS_DIR", self.checkpoints_dir):
                    tools.coding_set_workspace(str(self.external_dir))
                    propose_result = tools.coding_propose_changes("Trabajo multi", files_json)
                    proposal_id = next(
                        line.split(":", 1)[1].strip()
                        for line in propose_result.splitlines()
                        if line.startswith("Id:")
                    )
                    second_path.write_text("changed by user\n", encoding="utf-8")
                    apply_result = tools.coding_apply_proposal(proposal_id)

        self.assertIn("El archivo cambio", apply_result)
        self.assertEqual(first_path.read_text(encoding="utf-8"), "old app\n")
        self.assertEqual(second_path.read_text(encoding="utf-8"), "changed by user\n")
        self.assertFalse(self.checkpoints_dir.exists())

    def test_coding_apply_proposal_writes_file_with_checkpoint(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                with patch.object(tools, "CHECKPOINTS_DIR", self.checkpoints_dir):
                    tools.coding_set_workspace(str(self.external_dir))
                    propose_result = tools.coding_propose_text_file("app.py", "print('new')\n")
                    proposal_id = next(
                        line.split(":", 1)[1].strip()
                        for line in propose_result.splitlines()
                        if line.startswith("Id:")
                    )

                    apply_result = tools.coding_apply_proposal(proposal_id)
                    state = memory.load_state()

        proposal = json.loads((self.proposals_dir / f"{proposal_id}.json").read_text(encoding="utf-8"))
        checkpoint_dirs = [item for item in self.checkpoints_dir.iterdir() if item.is_dir()]
        self.assertIn("Propuesta aplicada", apply_result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "print('new')\n")
        self.assertEqual(proposal["status"], "applied")
        self.assertEqual(state["coding"]["pending_proposal_ids"], [])
        self.assertEqual(len(checkpoint_dirs), 1)

    def test_write_text_file_blocks_active_coding_workspace_in_propose_first_mode(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.write_text_file(str(file_path), "print('new')\n")

        self.assertIn("Escritura bloqueada", result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "print('old')\n")

    def test_coding_git_status_runs_inside_active_workspace(self):
        fake_result = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="## main\n M app.py\n",
            stderr="",
        )

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                with patch.object(tools.subprocess, "run", return_value=fake_result) as run_mock:
                    result = tools.coding_git_status()

        self.assertIn("Git status OK.", result)
        self.assertIn("M app.py", result)
        self.assertEqual(run_mock.call_args.kwargs["cwd"], str(self.external_dir.resolve()))
        self.assertFalse(run_mock.call_args.kwargs["shell"])

    def test_coding_git_status_reports_non_repo_failure(self):
        fake_result = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                with patch.object(tools.subprocess, "run", return_value=fake_result):
                    result = tools.coding_git_status()

        self.assertIn("Git status con fallos", result)
        self.assertIn("not a git repository", result)

    def test_coding_run_validation_executes_command_inside_active_workspace(self):
        fake_result = subprocess.CompletedProcess(
            args="python -m unittest",
            returncode=0,
            stdout="OK",
            stderr="",
        )

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                with patch.object(tools.subprocess, "run", return_value=fake_result) as run_mock:
                    result = tools.coding_run_validation("python -m unittest", timeout_seconds=5)

        self.assertIn("Validacion OK.", result)
        self.assertIn("OK", result)
        self.assertEqual(run_mock.call_args.kwargs["cwd"], str(self.external_dir.resolve()))
        self.assertTrue(run_mock.call_args.kwargs["shell"])

    def test_coding_detect_validation_command_saves_detected_command(self):
        scripts_dir = self.external_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "check.ps1").write_text("Write-Output OK\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_detect_validation_command()
                state = memory.load_state()

        self.assertIn("detectado y guardado", result)
        self.assertIn("scripts\\check.ps1", state["coding"]["validation_command"])

    def test_coding_run_validation_uses_saved_command_and_associates_proposal(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")
        fake_result = subprocess.CompletedProcess(
            args="pytest",
            returncode=0,
            stdout="OK",
            stderr="",
        )

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                tools.coding_update_validation_command("pytest")
                propose_result = tools.coding_propose_text_file("app.py", "print('new')\n")
                proposal_id = next(
                    line.split(":", 1)[1].strip()
                    for line in propose_result.splitlines()
                    if line.startswith("Id:")
                )
                with patch.object(tools.subprocess, "run", return_value=fake_result):
                    result = tools.coding_run_validation(proposal_id=proposal_id)
                state = memory.load_state()

        proposal = json.loads((self.proposals_dir / f"{proposal_id}.json").read_text(encoding="utf-8"))
        self.assertIn("Validacion OK.", result)
        self.assertEqual(state["coding"]["last_validation"]["command"], "pytest")
        self.assertEqual(proposal["validation"]["command"], "pytest")
        self.assertEqual(proposal["validation"]["exit_code"], 0)

    def test_coding_validation_plan_prefers_saved_command_and_lists_proposal_files(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                tools.coding_update_validation_command("pytest tests/test_app.py")
                propose_result = tools.coding_propose_text_file("app.py", "print('new')\n")
                proposal_id = next(
                    line.split(":", 1)[1].strip()
                    for line in propose_result.splitlines()
                    if line.startswith("Id:")
                )
                result = tools.coding_validation_plan(proposal_id)

        self.assertIn("Plan de validacion coding", result)
        self.assertIn("pytest tests/test_app.py", result)
        self.assertIn("Fuente: guardado", result)
        self.assertIn("app.py", result)

    def test_coding_validation_plan_uses_detected_command_when_missing_saved_command(self):
        scripts_dir = self.external_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "check.ps1").write_text("Write-Output OK\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                result = tools.coding_validation_plan()

        self.assertIn("Fuente: detectado", result)
        self.assertIn("scripts\\check.ps1", result)

    def test_coding_workflow_status_summarizes_git_validation_and_pending_proposals(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")
        fake_git = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="## main\n M app.py\n",
            stderr="",
        )

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                tools.coding_update_validation_command("pytest")
                tools.coding_propose_text_file("app.py", "print('new')\n", reason="demo")
                with patch.object(tools.subprocess, "run", return_value=fake_git):
                    result = tools.coding_workflow_status()

        self.assertIn("Estado de workflow coding", result)
        self.assertIn("Validacion guardada: pytest", result)
        self.assertIn("M app.py", result)
        self.assertIn("Propuestas", result)
        self.assertIn("proposal-", result)

    def test_coding_check_proposal_reports_preflight_ok_without_mutating_files(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                propose_result = tools.coding_propose_text_file("app.py", "print('new')\n")
                proposal_id = next(
                    line.split(":", 1)[1].strip()
                    for line in propose_result.splitlines()
                    if line.startswith("Id:")
                )
                result = tools.coding_check_proposal(proposal_id)

        self.assertIn("Preflight OK.", result)
        self.assertIn("app.py", result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "print('old')\n")

    def test_coding_check_proposal_reports_obsolete_when_file_changed(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                propose_result = tools.coding_propose_text_file("app.py", "print('new')\n")
                proposal_id = next(
                    line.split(":", 1)[1].strip()
                    for line in propose_result.splitlines()
                    if line.startswith("Id:")
                )
                file_path.write_text("changed by user\n", encoding="utf-8")
                result = tools.coding_check_proposal(proposal_id)

        self.assertIn("Preflight con fallos", result)
        self.assertIn("El archivo cambio", result)

    def test_coding_check_proposal_reports_wrong_workspace(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")
        other_dir = self.runtime_dir / "other_repo"
        other_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                tools.coding_set_workspace(str(self.external_dir))
                propose_result = tools.coding_propose_text_file("app.py", "print('new')\n")
                proposal_id = next(
                    line.split(":", 1)[1].strip()
                    for line in propose_result.splitlines()
                    if line.startswith("Id:")
                )
                tools.coding_set_workspace(str(other_dir))
                result = tools.coding_check_proposal(proposal_id)

        self.assertIn("Preflight con fallos", result)
        self.assertIn("pertenece a otro workspace", result)

    def test_coding_apply_and_validate_applies_then_records_validation(self):
        file_path = self.external_dir / "app.py"
        file_path.write_text("print('old')\n", encoding="utf-8")
        fake_result = subprocess.CompletedProcess(
            args="pytest",
            returncode=0,
            stdout="OK",
            stderr="",
        )

        with patch.object(memory, "STATE_FILE", self.state_path):
            with patch.object(tools, "CODING_PROPOSALS_DIR", self.proposals_dir):
                with patch.object(tools, "CHECKPOINTS_DIR", self.checkpoints_dir):
                    tools.coding_set_workspace(str(self.external_dir))
                    tools.coding_update_validation_command("pytest")
                    propose_result = tools.coding_propose_text_file("app.py", "print('new')\n")
                    proposal_id = next(
                        line.split(":", 1)[1].strip()
                        for line in propose_result.splitlines()
                        if line.startswith("Id:")
                    )
                    with patch.object(tools.subprocess, "run", return_value=fake_result):
                        result = tools.coding_apply_and_validate(proposal_id)

        proposal = json.loads((self.proposals_dir / f"{proposal_id}.json").read_text(encoding="utf-8"))
        self.assertIn("Aplicacion y validacion completadas", result)
        self.assertIn("Validacion OK.", result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "print('new')\n")
        self.assertEqual(proposal["status"], "applied")
        self.assertEqual(proposal["validation"]["exit_code"], 0)

    def test_restore_checkpoint_recovers_previous_content(self):
        file_path = self.runtime_dir / "recover_me.py"
        file_path.write_text("print('stable')\n", encoding="utf-8")

        with patch.object(tools, "CHECKPOINTS_DIR", self.checkpoints_dir):
            write_result = tools.write_text_file(
                file_path.relative_to(tools.WORKSPACE_ROOT).as_posix(),
                "print('broken')\n",
            )
            checkpoint_id = next(
                line.split(":", 1)[1].strip()
                for line in write_result.splitlines()
                if line.startswith("Checkpoint previo:")
            )

            restore_result = tools.restore_checkpoint(checkpoint_id)

        self.assertIn("Archivo restaurado", restore_result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "print('stable')\n")

    def test_run_project_tests_reports_success(self):
        fake_result = subprocess.CompletedProcess(
            args=["python", "-m", "unittest"],
            returncode=0,
            stdout="Ran 1 test\nOK",
            stderr="",
        )

        with patch.object(tools.subprocess, "run", return_value=fake_result) as run_mock:
            result = tools.run_project_tests()

        self.assertIn("Tests OK.", result)
        self.assertIn("Ran 1 test", result)
        self.assertEqual(run_mock.call_count, 1)

    def test_run_project_tests_bounds_long_output_and_keeps_tail(self):
        fake_result = subprocess.CompletedProcess(
            args=["python", "-m", "unittest"],
            returncode=1,
            stdout="inicio\n" + ("x" * (tools.MAX_TEST_OUTPUT_CHARS + 500)) + "\nFINAL_RELEVANTE",
            stderr="",
        )

        with patch.object(tools.subprocess, "run", return_value=fake_result):
            result = tools.run_project_tests()

        output = result.split("Salida:\n", 1)[1]
        self.assertLessEqual(len(output), tools.MAX_TEST_OUTPUT_CHARS)
        self.assertIn("[truncado", output)
        self.assertIn("FINAL_RELEVANTE", output)

    def test_run_project_check_runs_tests_and_service_build(self):
        fake_build = subprocess.CompletedProcess(
            args=["dotnet", "build"],
            returncode=0,
            stdout="Build succeeded",
            stderr="",
        )

        with patch.object(tools, "run_project_tests", return_value="Tests OK.\nSalida:\nOK") as tests_mock:
            with patch.object(tools.subprocess, "run", return_value=fake_build) as run_mock:
                result = tools.run_project_check()

        tests_mock.assert_called_once()
        run_mock.assert_called_once()
        self.assertIn("Validacion completa OK.", result)
        self.assertIn("Build .NET OK.", result)

    def test_run_system_command_executes_in_requested_directory(self):
        with patch.object(
            tools,
            "_run_command_process",
            return_value=(0, "hola\n", "", False),
        ) as run_mock:
            result = tools.run_system_command("echo hola", cwd=str(self.external_dir), timeout_seconds=5)

        self.assertIn("Comando del sistema completado", result)
        self.assertIn("hola", result)
        self.assertEqual(run_mock.call_args.kwargs["cwd"], self.external_dir.resolve())
        self.assertTrue(run_mock.call_args.kwargs["shell"])

    def test_run_system_command_reports_timeout_after_tree_cleanup(self):
        with patch.object(
            tools,
            "_run_command_process",
            return_value=(None, "salida parcial", "", True),
        ) as run_mock:
            result = tools.run_system_command("python tarea_larga.py", timeout_seconds=5)

        self.assertIn("excedio el timeout de 5 segundos", result)
        self.assertIn("salida parcial", result)
        self.assertTrue(run_mock.call_args.kwargs["shell"])

    def test_browser_automation_wrapper_passes_workspace(self):
        with patch.object(tools, "run_browser_automation", return_value="ok navegador") as browser_mock:
            result = tools.browser_automation(start_url="https://example.com")

        self.assertEqual(result, "ok navegador")
        browser_mock.assert_called_once()
        self.assertEqual(browser_mock.call_args.kwargs["workspace_root"], tools.WORKSPACE_ROOT)

    def test_calendar_email_and_open_system_tools(self):
        event_path = self.external_dir / "evento.ics"
        calendar_result = tools.create_calendar_event(
            title="Demo",
            start="2026-05-06T15:00:00",
            end="2026-05-06T16:00:00",
            attendees="a@example.com",
            output_path=str(event_path),
        )
        email_result = tools.compose_email(
            to="a@example.com",
            subject="Hola",
            body="Cuerpo",
            open_client=False,
        )
        target = self.external_dir / "abrir.txt"
        target.write_text("demo", encoding="utf-8")
        with patch.object(tools, "open_system_target_impl", return_value="abierto") as open_mock:
            open_result = tools.open_system_target(str(target))

        self.assertIn("Evento de calendario creado", calendar_result)
        self.assertIn("SUMMARY:Demo", event_path.read_text(encoding="utf-8"))
        self.assertIn("Borrador de correo preparado", email_result)
        self.assertIn("mailto:a@example.com", email_result)
        self.assertEqual(open_result, "abierto")
        open_mock.assert_called_once()

    def test_list_files_returns_relative_paths(self):
        nested_dir = self.runtime_dir / "docs"
        nested_dir.mkdir(parents=True, exist_ok=True)
        (nested_dir / "note.txt").write_text("hola", encoding="utf-8")

        result = tools.list_files(self.runtime_dir.relative_to(tools.WORKSPACE_ROOT).as_posix())

        self.assertIn(nested_dir.relative_to(tools.WORKSPACE_ROOT).as_posix(), result)

    def test_state_tools_manage_profile_tasks_and_notes(self):
        state_path = self.runtime_dir / "tool_state.json"

        with patch.object(memory, "STATE_FILE", state_path):
            profile_result = tools.update_profile(
                name="Ahiram",
                role="builder",
                preferences="local, rapido",
                constraints="sin nube",
            )
            task_result = tools.add_task(
                title="Preparar plan semanal",
                details="Definir tres prioridades concretas",
                priority="alta",
            )
            task_id = task_result.splitlines()[0].split()[-1].rstrip(".")
            note_result = tools.save_note(
                title="Rutina",
                content="Revisar pendientes cada manana",
                category="personal",
            )
            note_id = note_result.split("id ", 1)[1].split(":", 1)[0]
            full_note = tools.get_note(note_id)
            status_result = tools.update_task_status(
                task_id=task_id,
                status="done",
                result="Plan semanal definido",
            )
            goal_result = tools.update_goal("Lanzar asistente local")
            overview = tools.agent_overview()
            done_tasks = tools.list_tasks(status="done")
            personal_notes = tools.list_notes(category="personal")
            delete_note_result = tools.delete_note(note_id)
            notes_after_delete = tools.list_notes(category="personal")
            state = memory.load_state()

        self.assertIn("Perfil actualizado", profile_result)
        self.assertIn("Tarea creada", task_result)
        self.assertIn("Nota guardada", note_result)
        self.assertIn("Revisar pendientes cada manana", full_note)
        self.assertIn("Nuevo estado: done", status_result)
        self.assertIn("Objetivo actualizado", goal_result)
        self.assertIn("Lanzar asistente local", overview)
        self.assertEqual(state["goal"], "Lanzar asistente local")
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["current_plan"], [])
        self.assertFalse(state["awaiting_user_input"]["pending"])
        self.assertIn("No hay tareas", done_tasks)
        self.assertIn("Rutina", personal_notes)
        self.assertIn("Nota eliminada", delete_note_result)
        self.assertIn("No hay notas", notes_after_delete)

    def test_request_user_input_persists_pending_question(self):
        state_path = self.runtime_dir / "tool_pending_input_state.json"

        with patch.object(memory, "STATE_FILE", state_path):
            result = tools.request_user_input(
                question="Que nicho quieres trabajar en Facebook?",
                reason="No hay suficiente contexto para proponer ideas utiles.",
                missing_fields="nicho, audiencia",
            )
            state = memory.load_state()

        self.assertIn("Solicitud de informacion registrada", result)
        self.assertTrue(state["awaiting_user_input"]["pending"])
        self.assertEqual(
            state["awaiting_user_input"]["question"],
            "Que nicho quieres trabajar en Facebook?",
        )
        self.assertEqual(
            state["awaiting_user_input"]["fields"],
            ["nicho", "audiencia"],
        )

    def test_idea_project_tools_create_update_list_and_promote(self):
        state_path = self.runtime_dir / "tool_idea_project_state.json"

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.normalize_state({
                "tasks": [{
                    "id": "task-existing",
                    "title": "Definir brief",
                    "status": "pending",
                    "priority": "media",
                }]
            }))
            create_result = tools.create_idea_project(
                title="Servicio para negocios locales",
                kind="producto_negocio",
                summary="Automatizar seguimiento de clientes",
                creative_directions="CRM simple\nAsistente de WhatsApp",
                open_questions="precio, canal",
                next_steps="Definir brief\nHablar con 3 clientes",
            )
            state = memory.load_state()
            project_id = state["idea_projects"][0]["id"]
            update_result = tools.update_idea_project(
                project_id=project_id,
                selected_direction="CRM simple",
                status="planned",
            )
            list_result = tools.list_idea_projects(status="open")
            get_result = tools.get_idea_project(project_id)
            promote_result = tools.promote_idea_project_to_work(project_id, priority="alta")
            state = memory.load_state()

        self.assertIn("Proyecto de idea creado", create_result)
        self.assertIn("Proyecto de idea actualizado", update_result)
        self.assertIn("Servicio para negocios locales", list_result)
        self.assertIn("CRM simple", get_result)
        self.assertIn("Proyecto de idea activado", promote_result)
        self.assertEqual(state["idea_projects"][0]["status"], "active")
        self.assertEqual(state["current_plan"], ["Definir brief", "Hablar con 3 clientes"])
        self.assertEqual(
            [task["title"] for task in state["tasks"]],
            ["Definir brief", "Hablar con 3 clientes"],
        )
        self.assertEqual(state["tasks"][1]["priority"], "alta")

    def test_visual_board_tools_create_update_list_get_and_export(self):
        state_path = self.runtime_dir / "tool_visual_board_state.json"
        visual_dir = self.runtime_dir / ".yarbis_runtime" / "visual_boards"

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(tools, "VISUAL_BOARDS_DIR", visual_dir):
                memory.save_state(memory.default_state())
                create_project = tools.create_idea_project(
                    title="Servicio visual",
                    summary="Planear oferta",
                    creative_directions="CRM ligero\nAgenda asistida",
                    next_steps="Validar problema\nHacer demo",
                )
                state = memory.load_state()
                project_id = state["idea_projects"][0]["id"]

                invalid = tools.create_project_visual_board(project_id, "desconocido")
                create_board = tools.create_project_visual_board(project_id, "idea_canvas")
                state = memory.load_state()
                board_id = state["idea_projects"][0]["visual_boards"][0]["id"]
                list_result = tools.list_project_visual_boards(project_id)
                board = json.loads(tools.get_project_visual_board(project_id, board_id))
                board["nodes"][0]["x"] = 123
                board["nodes"][0]["text"] = "Problema actualizado"
                update_result = tools.update_project_visual_board(project_id, board_id, json.dumps(board))
                export_result = tools.export_project_visual_board(project_id, board_id, formats="html,svg,json")
                state = memory.load_state()

        self.assertIn("Proyecto de idea creado", create_project)
        self.assertIn("Tipo de board visual invalido", invalid)
        self.assertIn("Board visual creado", create_board)
        self.assertIn(board_id, list_result)
        self.assertIn("Board visual actualizado", update_result)
        self.assertEqual(state["idea_projects"][0]["visual_boards"][0]["nodes"][0]["x"], 123)
        self.assertEqual(
            state["idea_projects"][0]["visual_boards"][0]["nodes"][0]["text"],
            "Problema actualizado",
        )
        self.assertIn("Board visual exportado", export_result)
        export_paths = state["idea_projects"][0]["visual_boards"][0]["export_paths"]
        self.assertTrue(Path(export_paths["json"]).exists())
        self.assertTrue(Path(export_paths["svg"]).exists())
        self.assertTrue(Path(export_paths["html"]).exists())

    def test_update_internet_settings_persists_policy(self):
        state_path = self.runtime_dir / "tool_internet_state.json"

        with patch.object(memory, "STATE_FILE", state_path):
            result = tools.update_internet_settings(
                mode="off",
                allowed_domains="docs.python.org, example.com",
                blocked_domains="news.ycombinator.com",
                max_search_results=7,
                max_page_chars=8_000,
                request_timeout_seconds=15,
            )
            state = memory.load_state()

        self.assertIn("Configuracion de internet actualizada", result)
        self.assertEqual(state["internet"]["mode"], "off")
        self.assertEqual(state["internet"]["allowed_domains"], ["docs.python.org", "example.com"])
        self.assertEqual(state["internet"]["blocked_domains"], ["news.ycombinator.com"])
        self.assertEqual(state["internet"]["max_search_results"], 7)
        self.assertEqual(state["internet"]["max_page_chars"], 8_000)
        self.assertEqual(state["internet"]["request_timeout_seconds"], 15)

    def test_web_search_uses_persisted_settings(self):
        state_path = self.runtime_dir / "tool_web_search_state.json"

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.normalize_state({
                "internet": {
                    "mode": "auto",
                    "provider": "duckduckgo_html",
                    "max_search_results": 3,
                    "request_timeout_seconds": 12,
                    "allowed_domains": ["docs.python.org"],
                }
            }))
            with patch.object(tools, "search_public_web", return_value=[
                {
                    "title": "unittest docs",
                    "url": "https://docs.python.org/3/library/unittest.html",
                    "snippet": "Documentacion oficial",
                }
            ]) as search_mock:
                result = tools.web_search("python unittest", limit=9, reason="validar sintaxis actual")

        self.assertIn("Busqueda web completada", result)
        self.assertIn("unittest docs", result)
        self.assertIn("Documentacion oficial", result)
        search_mock.assert_called_once_with(
            query="python unittest",
            limit=3,
            timeout_seconds=12,
            provider="duckduckgo_html",
            allowed_domains=["docs.python.org"],
            blocked_domains=[],
        )

    def test_fetch_web_page_respects_internet_mode(self):
        state_path = self.runtime_dir / "tool_fetch_web_state.json"

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.normalize_state({
                "internet": {
                    "mode": "off",
                }
            }))
            result = tools.fetch_web_page("https://example.com/demo")

        self.assertIn("desactivada por politica", result)

    def test_self_overview_includes_code_and_runtime_context(self):
        state_path = self.runtime_dir / "tool_self_overview_state.json"

        with patch.object(memory, "STATE_FILE", state_path):
            result = tools.self_overview(refresh=True)
            state = memory.load_state()

        self.assertIn("Identidad:", result)
        self.assertIn("Codigo fuente:", result)
        self.assertIn("Sistema operativo:", result)
        self.assertIn("agent.py", result)
        self.assertIn("Identidad:", state["self_knowledge"]["summary"])
        self.assertTrue(state["self_knowledge"]["last_analyzed_at"])
        self.assertTrue(state["self_knowledge"]["source_signature"])
