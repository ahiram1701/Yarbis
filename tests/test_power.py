import subprocess
import unittest
from unittest.mock import patch

import power


class PowerTestCase(unittest.TestCase):
    def test_request_system_shutdown_uses_windows_shutdown_exe(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch.object(power.sys, "platform", "win32"):
            with patch.object(power, "_shutdown_executable", return_value="shutdown.exe"):
                with patch.object(power, "_run_shutdown_command", return_value=completed) as run_mock:
                    result = power.request_system_shutdown(delay_seconds=120)

        self.assertIn("Apagado programado", result)
        run_mock.assert_called_once_with([
            "shutdown.exe",
            "/s",
            "/t",
            "120",
            "/c",
            power.SHUTDOWN_COMMENT,
        ])

    def test_request_system_restart_uses_windows_shutdown_exe(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch.object(power.sys, "platform", "win32"):
            with patch.object(power, "_shutdown_executable", return_value="shutdown.exe"):
                with patch.object(power, "_run_shutdown_command", return_value=completed) as run_mock:
                    result = power.request_system_restart(delay_seconds=180)

        self.assertIn("Reinicio programado", result)
        run_mock.assert_called_once_with([
            "shutdown.exe",
            "/r",
            "/t",
            "180",
            "/c",
            power.RESTART_COMMENT,
        ])

    def test_request_system_shutdown_uses_posix_shutdown_off_windows(self):
        # Fuera de Windows usa `shutdown -h`. MOCK de la ejecucion: nunca corre de verdad.
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.object(power.sys, "platform", "linux"):
            with patch.object(power, "_run_shutdown_command", return_value=completed) as run_mock:
                result = power.request_system_shutdown(delay_seconds=60)
        run_mock.assert_called_once_with(["shutdown", "-h", "+1"])
        self.assertIsInstance(result, str)

    def test_cancel_system_shutdown_aborts_pending_shutdown(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch.object(power.sys, "platform", "win32"):
            with patch.object(power, "_shutdown_executable", return_value="shutdown.exe"):
                with patch.object(power, "_run_shutdown_command", return_value=completed) as run_mock:
                    result = power.cancel_system_shutdown()

        self.assertEqual(result, "Apagado cancelado.")
        run_mock.assert_called_once_with(["shutdown.exe", "/a"])

    def test_cancel_system_shutdown_can_label_restart(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch.object(power.sys, "platform", "win32"):
            with patch.object(power, "_shutdown_executable", return_value="shutdown.exe"):
                with patch.object(power, "_run_shutdown_command", return_value=completed):
                    result = power.cancel_system_shutdown(action_label="reinicio")

        self.assertEqual(result, "Reinicio cancelado.")

    def test_cancel_system_shutdown_treats_1116_as_no_pending_action(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1116,
            stdout="",
            stderr=(
                "No se puede anular el apagado del sistema porque "
                "no se estaba apagando.(1116)"
            ),
        )

        with patch.object(power.sys, "platform", "win32"):
            with patch.object(power, "_shutdown_executable", return_value="shutdown.exe"):
                with patch.object(power, "_run_shutdown_command", return_value=completed):
                    result = power.cancel_system_shutdown(action_label="reinicio")

        self.assertEqual(result, "No habia ningun reinicio programado.")

    def test_cancel_system_shutdown_reports_timeout_without_raising(self):
        with patch.object(power.sys, "platform", "win32"):
            with patch.object(power, "_shutdown_executable", return_value="shutdown.exe"):
                with patch.object(
                    power,
                    "_run_shutdown_command",
                    side_effect=subprocess.TimeoutExpired(["shutdown.exe", "/a"], 10),
                ):
                    result = power.cancel_system_shutdown()

        self.assertIn("no respondio a tiempo", result)

    def test_request_system_shutdown_reports_invalid_delay_without_raising(self):
        with patch.object(power.sys, "platform", "win32"):
            result = power.request_system_shutdown(delay_seconds="cinco")

        self.assertIn("tiempo indicado no es valido", result)

    def test_request_system_restart_reports_invalid_delay_without_raising(self):
        with patch.object(power.sys, "platform", "win32"):
            result = power.request_system_restart(delay_seconds="cinco")

        self.assertIn("tiempo indicado no es valido", result)
