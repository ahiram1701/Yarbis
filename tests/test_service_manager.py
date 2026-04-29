import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import service_manager

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime" / "service_manager"


class ServiceManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.runtime_dir = TEST_RUNTIME_DIR / self._testMethodName
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = self.runtime_dir / "service.pid"
        self.stop_file = self.runtime_dir / "service.stop"
        self.log_file = self.runtime_dir / "service.log"

    def _runtime_patches(self):
        return (
            patch.object(service_manager, "RUNTIME_DIR", self.runtime_dir),
            patch.object(service_manager, "PID_FILE", self.pid_file),
            patch.object(service_manager, "STOP_FILE", self.stop_file),
            patch.object(service_manager, "LOG_FILE", self.log_file),
        )

    def test_get_service_status_reports_running_and_autostart(self):
        self.pid_file.write_text("123", encoding="utf-8")

        patches = self._runtime_patches()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch.object(service_manager, "_is_process_alive", return_value=True):
                with patch.object(service_manager, "is_autostart_enabled", return_value=True):
                    status = service_manager.get_service_status()

        self.assertTrue(status["running"])
        self.assertEqual(status["pid"], 123)
        self.assertTrue(status["autostart_enabled"])

    def test_get_service_status_removes_stale_pid(self):
        self.pid_file.write_text("456", encoding="utf-8")

        patches = self._runtime_patches()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch.object(service_manager, "_is_process_alive", return_value=False):
                with patch.object(service_manager, "is_autostart_enabled", return_value=False):
                    with patch.object(service_manager, "_remove_file") as remove_mock:
                        status = service_manager.get_service_status()

        self.assertFalse(status["running"])
        remove_mock.assert_called_once_with(self.pid_file)

    def test_start_service_launches_background_process(self):
        fake_process = SimpleNamespace(pid=789, poll=lambda: None)

        patches = self._runtime_patches()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch.object(service_manager, "get_service_status", return_value={"running": False}):
                with patch.object(service_manager.subprocess, "Popen", return_value=fake_process) as popen_mock:
                    with patch.object(service_manager, "_is_process_alive", return_value=True):
                        result = service_manager.start_service()

        self.assertIn("Servicio de Yarbis iniciado", result)
        self.assertEqual(self.pid_file.read_text(encoding="utf-8"), "789")
        popen_mock.assert_called_once()

    def test_start_service_skips_spawn_when_already_running(self):
        with patch.object(
            service_manager,
            "get_service_status",
            return_value={"running": True, "pid": 111},
        ):
            with patch.object(service_manager.subprocess, "Popen") as popen_mock:
                result = service_manager.start_service()

        self.assertIn("ya esta activo", result)
        popen_mock.assert_not_called()

    def test_stop_service_requests_stop_and_cleans_runtime_files(self):
        self.pid_file.write_text("222", encoding="utf-8")

        patches = self._runtime_patches()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch.object(service_manager, "_is_process_alive", side_effect=[True, False]):
                with patch.object(service_manager, "_remove_file") as remove_mock:
                    result = service_manager.stop_service(timeout_seconds=1)

        self.assertIn("detenido", result)
        remove_mock.assert_any_call(self.pid_file)
        remove_mock.assert_any_call(self.stop_file)

    def test_set_autostart_rejects_non_windows(self):
        with patch.object(service_manager.os, "name", "posix"):
            with self.assertRaises(RuntimeError):
                service_manager.set_autostart_enabled(True)
