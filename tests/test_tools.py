import json
import subprocess
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

    def test_read_text_file_truncates_large_content(self):
        file_path = self.runtime_dir / "large.txt"
        file_path.write_text("a" * (tools.MAX_READ_BYTES + 25), encoding="utf-8")

        result = tools.read_text_file(file_path.relative_to(tools.WORKSPACE_ROOT).as_posix())

        self.assertIn("Contenido truncado", result)
        self.assertIn("large.txt", result)

    def test_write_text_file_blocks_paths_outside_workspace(self):
        result = tools.write_text_file("../outside.txt", "hola")

        self.assertIn("Acceso denegado", result)

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
            overview = tools.agent_overview()
            done_tasks = tools.list_tasks(status="done")
            personal_notes = tools.list_notes(category="personal")
            delete_note_result = tools.delete_note(note_id)
            notes_after_delete = tools.list_notes(category="personal")

        self.assertIn("Perfil actualizado", profile_result)
        self.assertIn("Tarea creada", task_result)
        self.assertIn("Nota guardada", note_result)
        self.assertIn("Revisar pendientes cada manana", full_note)
        self.assertIn("Nuevo estado: done", status_result)
        self.assertIn("Ahiram", overview)
        self.assertIn("Plan semanal definido", done_tasks)
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
