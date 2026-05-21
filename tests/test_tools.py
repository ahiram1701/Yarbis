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
        self.assertEqual(proposal["relative_path"], "app.py")
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(state["coding"]["pending_proposal_ids"], [proposal["id"]])

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
