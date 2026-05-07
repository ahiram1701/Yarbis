import inspect
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import pc_context_runtime
import yarbis_desktop

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class PcContextRuntimeTestCase(unittest.TestCase):
    def test_task_create_args_quote_python_and_script_paths(self):
        args = pc_context_runtime._task_create_args()

        self.assertIn("/Create", args)
        self.assertIn("/TN", args)
        self.assertIn(pc_context_runtime.TASK_NAME, args)
        task_command = args[args.index("/TR") + 1]
        self.assertIn('"', task_command)
        self.assertIn("pc_context_tray.py", task_command)
        self.assertIn(str(pc_context_runtime.HELPER_SCRIPT), task_command)

    def test_ensure_context_task_uses_schtasks_create(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="OK", stderr="")

        with patch.object(pc_context_runtime.os, "name", "nt"):
            with patch.object(pc_context_runtime, "_run_schtasks", return_value=completed) as run_mock:
                result = pc_context_runtime.ensure_context_task()

        self.assertIn("YarbisLocalContext", result)
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.args[0][1], "/Create")

    def test_start_context_helper_does_not_spawn_duplicate(self):
        with patch.object(pc_context_runtime, "_read_pid", return_value=1234):
            with patch.object(pc_context_runtime, "_pid_is_running", return_value=True):
                with patch.object(pc_context_runtime.subprocess, "Popen") as popen_mock:
                    result = pc_context_runtime.start_context_helper()

        self.assertIn("ya activo", result)
        popen_mock.assert_not_called()

    def test_stop_context_helper_writes_stop_file_for_running_helper(self):
        stop_path = TEST_RUNTIME_DIR / "pc_context_helper.stop"
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stop_path.unlink()
        except FileNotFoundError:
            pass

        with patch.object(pc_context_runtime, "HELPER_STOP_FILE", stop_path):
            with patch.object(pc_context_runtime, "_read_pid", return_value=1234):
                with patch.object(pc_context_runtime, "_pid_is_running", side_effect=[True, False]):
                    with patch.object(pc_context_runtime, "clear_helper_pid"):
                        with patch.object(pc_context_runtime, "write_helper_status"):
                            result = pc_context_runtime.stop_context_helper(timeout_seconds=1)

        self.assertIn("detenido", result)
        self.assertTrue(stop_path.exists())

    def test_desktop_close_does_not_stop_context_helper(self):
        source = inspect.getsource(yarbis_desktop.YarbisDesktop._on_close)

        self.assertNotIn("_stop_local_context_worker", source)
        self.assertNotIn("stop_context_helper", source)


if __name__ == "__main__":
    unittest.main()
