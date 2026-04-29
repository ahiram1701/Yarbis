import subprocess
import unittest
from unittest.mock import call, patch

import service_manager


def completed(args=None, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=args or ["sc.exe"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def status(installed=True, running=False, pid=None, autostart=False, start_type="demand_start"):
    return {
        "installed": installed,
        "running": running,
        "pid": pid,
        "autostart_enabled": autostart,
        "start_type": start_type,
    }


class ServiceManagerTestCase(unittest.TestCase):
    def test_get_service_status_reports_missing_service(self):
        missing = completed(
            returncode=1060,
            stderr="[SC] OpenService FAILED 1060: The specified service does not exist.",
        )

        with patch.object(service_manager, "_run_sc", return_value=missing):
            result = service_manager.get_service_status()

        self.assertFalse(result["installed"])
        self.assertFalse(result["running"])
        self.assertEqual(result["state"], "not_installed")
        self.assertEqual(result["start_type"], "not_installed")

    def test_get_service_status_parses_running_service(self):
        query = completed(
            stdout=(
                "SERVICE_NAME: Yarbis\n"
                "        TYPE               : 10  WIN32_OWN_PROCESS\n"
                "        STATE              : 4  RUNNING\n"
                "        PID                : 4321\n"
            ),
        )
        config = completed(
            stdout=(
                "[SC] QueryServiceConfig SUCCESS\n"
                "        START_TYPE         : 2   AUTO_START\n"
            ),
        )

        with patch.object(service_manager, "_run_sc", side_effect=[query, config]):
            result = service_manager.get_service_status()

        self.assertTrue(result["installed"])
        self.assertTrue(result["running"])
        self.assertEqual(result["pid"], 4321)
        self.assertTrue(result["autostart_enabled"])
        self.assertEqual(result["start_type"], "auto_start")

    def test_get_service_status_parses_localized_spanish_sc_output(self):
        query = completed(
            stdout=(
                "NOMBRE_SERVICIO: Yarbis\n"
                "        TIPO               : 10  WIN32_OWN_PROCESS\n"
                "        ESTADO             : 4  RUNNING\n"
                "        PID                : 4321\n"
            ),
        )
        config = completed(
            stdout=(
                "[SC] QueryServiceConfig CORRECTO\n"
                "        TIPO_INICIO        : 3   DEMAND_START\n"
            ),
        )

        with patch.object(service_manager, "_run_sc", side_effect=[query, config]):
            result = service_manager.get_service_status()

        self.assertTrue(result["installed"])
        self.assertTrue(result["running"])
        self.assertEqual(result["pid"], 4321)
        self.assertFalse(result["autostart_enabled"])
        self.assertEqual(result["start_type"], "demand_start")

    def test_install_service_creates_scm_service(self):
        missing = completed(
            returncode=1060,
            stderr="[SC] OpenService FAILED 1060: The specified service does not exist.",
        )

        with patch.object(service_manager, "_ensure_service_host_built") as build_mock:
            with patch.object(
                service_manager,
                "_run_sc",
                side_effect=[missing, completed(), completed()],
            ) as sc_mock:
                result = service_manager.install_service(start_auto=True)

        build_mock.assert_called_once()
        self.assertIn("instalado en SCM", result)
        self.assertEqual(sc_mock.call_args_list[1].args[0][0], "create")
        self.assertIn("start=", sc_mock.call_args_list[1].args[0])
        self.assertIn("auto", sc_mock.call_args_list[1].args[0])

    def test_install_service_updates_existing_service(self):
        with patch.object(service_manager, "_ensure_service_host_built"):
            with patch.object(service_manager, "get_service_status", return_value=status(installed=True)):
                with patch.object(service_manager, "_run_sc", return_value=completed()) as sc_mock:
                    result = service_manager.install_service(start_auto=False)

        self.assertIn("Configuracion actualizada", result)
        sc_mock.assert_called_once()
        self.assertEqual(sc_mock.call_args.args[0][0], "config")
        self.assertIn("demand", sc_mock.call_args.args[0])

    def test_start_service_installs_when_missing_then_starts(self):
        with patch.object(
            service_manager,
            "get_service_status",
            side_effect=[
                status(installed=False),
                status(installed=True, running=False),
                status(installed=True, running=True, pid=777),
            ],
        ):
            with patch.object(service_manager, "install_service", return_value="instalado") as install_mock:
                with patch.object(service_manager, "_run_sc", return_value=completed()) as sc_mock:
                    result = service_manager.start_service()

        install_mock.assert_called_once_with(start_auto=False)
        sc_mock.assert_called_once_with(["start", service_manager.SERVICE_NAME], timeout_seconds=45)
        self.assertIn("PID 777", result)

    def test_stop_service_stops_running_service(self):
        with patch.object(
            service_manager,
            "get_service_status",
            side_effect=[
                status(installed=True, running=True, pid=888),
                status(installed=True, running=False),
            ],
        ):
            with patch.object(service_manager, "_run_sc", return_value=completed()) as sc_mock:
                result = service_manager.stop_service(timeout_seconds=1)

        sc_mock.assert_called_once_with(["stop", service_manager.SERVICE_NAME], timeout_seconds=45)
        self.assertIn("detenido por SCM", result)

    def test_remove_service_stops_then_deletes(self):
        with patch.object(
            service_manager,
            "get_service_status",
            side_effect=[
                status(installed=True, running=True, pid=999),
                status(installed=True, running=True, pid=999),
                status(installed=True, running=False),
            ],
        ):
            with patch.object(
                service_manager,
                "_run_sc",
                side_effect=[completed(), completed()],
            ) as sc_mock:
                result = service_manager.remove_service()

        self.assertIn("quitado de SCM", result)
        self.assertEqual(
            sc_mock.call_args_list,
            [
                call(["stop", service_manager.SERVICE_NAME], timeout_seconds=45),
                call(["delete", service_manager.SERVICE_NAME]),
            ],
        )

    def test_set_autostart_uses_scm_start_type(self):
        with patch.object(service_manager, "get_service_status", return_value=status(installed=True)):
            with patch.object(service_manager, "_run_sc", return_value=completed()) as sc_mock:
                result = service_manager.set_autostart_enabled(True)

        self.assertIn("automaticamente", result)
        sc_mock.assert_called_once_with(["config", service_manager.SERVICE_NAME, "start=", "auto"])

    def test_set_autostart_disabled_uses_demand_start_type(self):
        with patch.object(service_manager, "get_service_status", return_value=status(installed=True)):
            with patch.object(service_manager, "_run_sc", return_value=completed()) as sc_mock:
                result = service_manager.set_autostart_enabled(False)

        self.assertIn("inicio manual", result)
        sc_mock.assert_called_once_with(["config", service_manager.SERVICE_NAME, "start=", "demand"])

    def test_set_autostart_installs_missing_service_with_requested_start_type(self):
        with patch.object(service_manager, "get_service_status", return_value=status(installed=False)):
            with patch.object(service_manager, "install_service") as install_mock:
                result = service_manager.set_autostart_enabled(True)

        self.assertIn("arranque automatico", result)
        install_mock.assert_called_once_with(start_auto=True)

        with patch.object(service_manager, "get_service_status", return_value=status(installed=False)):
            with patch.object(service_manager, "install_service") as install_mock:
                result = service_manager.set_autostart_enabled(False)

        self.assertIn("arranque manual", result)
        install_mock.assert_called_once_with(start_auto=False)

    def test_set_autostart_rejects_non_windows(self):
        with patch.object(service_manager.os, "name", "posix"):
            with self.assertRaises(RuntimeError):
                service_manager.set_autostart_enabled(True)
