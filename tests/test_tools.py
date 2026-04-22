import unittest
from pathlib import Path
from unittest.mock import patch

import memory
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

    def test_state_tools_manage_profile_tasks_and_notes(self):
        state_path = TEST_RUNTIME_DIR / "tool_state.json"

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
            status_result = tools.update_task_status(
                task_id=task_id,
                status="done",
                result="Plan semanal definido",
            )
            overview = tools.agent_overview()
            done_tasks = tools.list_tasks(status="done")
            personal_notes = tools.list_notes(category="personal")

        self.assertIn("Perfil actualizado", profile_result)
        self.assertIn("Tarea creada", task_result)
        self.assertIn("Nota guardada", note_result)
        self.assertIn("Nuevo estado: done", status_result)
        self.assertIn("Ahiram", overview)
        self.assertIn("Plan semanal definido", done_tasks)
        self.assertIn("Rutina", personal_notes)

    def test_request_user_input_persists_pending_question(self):
        state_path = TEST_RUNTIME_DIR / "tool_pending_input_state.json"

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
