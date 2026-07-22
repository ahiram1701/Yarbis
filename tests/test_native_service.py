"""Fase C multi-SO: gestor de servicio nativo systemd (Linux) / launchd (macOS).

Todo esta MOCKEADO: `_run` nunca ejecuta systemctl/launchctl reales y `Path.home`
apunta a un temporal, asi que no se toca el sistema del que corre los tests.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import native_service


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class ManagerKindTestCase(unittest.TestCase):
    def test_kind_by_platform(self):
        with patch.object(native_service.platform, "system", return_value="Linux"):
            self.assertEqual(native_service.service_manager_kind(), "systemd")
        with patch.object(native_service.platform, "system", return_value="Darwin"):
            self.assertEqual(native_service.service_manager_kind(), "launchd")
        with patch.object(native_service.platform, "system", return_value="Windows"):
            self.assertEqual(native_service.service_manager_kind(), "none")

    def test_install_requires_a_native_manager(self):
        with patch.object(native_service.platform, "system", return_value="Windows"):
            with self.assertRaises(native_service.NativeServiceError):
                native_service.install_service()


class SystemdTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.calls = []

        def fake_run(args, timeout_seconds=30):
            self.calls.append(args)
            if "show" in args:
                return _cp(stdout="ActiveState=active\nMainPID=4242\nUnitFileState=enabled\nLoadState=loaded\n")
            return _cp(0)

        self._patches = [
            patch.object(native_service.platform, "system", return_value="Linux"),
            patch.object(native_service.shutil, "which", return_value="/usr/bin/systemctl"),
            patch.object(native_service.Path, "home", return_value=self.home),
            patch.object(native_service, "_run", side_effect=fake_run),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _joined(self):
        return [" ".join(c) for c in self.calls]

    def test_install_writes_unit_and_enables(self):
        msg = native_service.install_service(start_auto=True, instance_id="trader")
        unit = self.home / ".config" / "systemd" / "user" / "yarbis-trader.service"
        self.assertTrue(unit.exists())
        text = unit.read_text(encoding="utf-8")
        self.assertIn("YARBIS_INSTANCE=trader", text)
        self.assertIn("yarbis_service.py", text)
        self.assertIn("WantedBy=default.target", text)
        joined = self._joined()
        self.assertTrue(any("daemon-reload" in j for j in joined))
        self.assertTrue(any("enable yarbis-trader.service" in j for j in joined))
        self.assertIn("systemd", msg)

    def test_default_instance_unit_stem_is_yarbis(self):
        native_service.install_service(start_auto=False, instance_id="default")
        self.assertTrue((self.home / ".config" / "systemd" / "user" / "yarbis.service").exists())
        # Con start_auto=False no debe llamar enable.
        self.assertFalse(any("enable" in j for j in self._joined()))

    def test_status_parses_show_output(self):
        status = native_service.get_service_status("trader")
        self.assertTrue(status["installed"])
        self.assertTrue(status["running"])
        self.assertEqual(status["pid"], 4242)
        self.assertTrue(status["autostart_enabled"])
        self.assertEqual(status["manager"], "systemd")
        self.assertEqual(status["service_name"], "yarbis-trader.service")

    def test_stop_calls_systemctl_stop(self):
        # Instalado + activo (fake_run "show" -> active), stop debe llamar systemctl stop.
        native_service.stop_service("trader")
        self.assertTrue(any("stop yarbis-trader.service" in j for j in self._joined()))

    def test_remove_disables_stops_and_deletes_unit(self):
        unit = self.home / ".config" / "systemd" / "user" / "yarbis-trader.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("dummy", encoding="utf-8")
        native_service.remove_service("trader")
        self.assertFalse(unit.exists())
        joined = self._joined()
        self.assertTrue(any("disable yarbis-trader.service" in j for j in joined))


class LaunchdTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.calls = []

        def fake_run(args, timeout_seconds=30):
            self.calls.append(args)
            if args[:2] == ["launchctl", "list"]:
                return _cp(stdout='{\n\t"PID" = 777;\n\t"Label" = "org.yarbis.trader";\n}\n')
            return _cp(0)

        self._patches = [
            patch.object(native_service.platform, "system", return_value="Darwin"),
            patch.object(native_service.shutil, "which", return_value="/bin/launchctl"),
            patch.object(native_service.Path, "home", return_value=self.home),
            patch.object(native_service, "_run", side_effect=fake_run),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _joined(self):
        return [" ".join(c) for c in self.calls]

    def test_install_writes_plist_and_loads(self):
        msg = native_service.install_service(start_auto=True, instance_id="trader")
        plist = self.home / "Library" / "LaunchAgents" / "org.yarbis.trader.plist"
        self.assertTrue(plist.exists())
        text = plist.read_text(encoding="utf-8")
        self.assertIn("<key>Label</key>", text)
        self.assertIn("org.yarbis.trader", text)
        self.assertIn("YARBIS_INSTANCE", text)
        self.assertIn("<key>RunAtLoad</key>", text)
        self.assertIn("<true/>", text)
        self.assertTrue(any("load -w" in j for j in self._joined()))
        self.assertIn("launchd", msg)

    def test_status_parses_list_pid(self):
        status = native_service.get_service_status("trader")
        self.assertTrue(status["running"])
        self.assertEqual(status["pid"], 777)
        self.assertEqual(status["manager"], "launchd")
        self.assertEqual(status["service_name"], "org.yarbis.trader")

    def test_autostart_reads_runatload_from_plist(self):
        native_service.install_service(start_auto=False, instance_id="trader")
        plist = self.home / "Library" / "LaunchAgents" / "org.yarbis.trader.plist"
        self.assertIn("<false/>", plist.read_text(encoding="utf-8"))
        status = native_service.get_service_status("trader")
        self.assertFalse(status["autostart_enabled"])


class TermuxTestCase(unittest.TestCase):
    """Android/Termux: es Linux pero sin systemd (runit via termux-services)."""

    def setUp(self):
        self.prefix = Path(tempfile.mkdtemp())
        self.calls = []

        def fake_run(args, timeout_seconds=30):
            self.calls.append(args)
            if args[:2] == ["sv", "status"]:
                return _cp(stdout="run: yarbis-trader: (pid 4321) 10s\n")
            return _cp(0)

        self._patches = [
            patch.object(native_service.platform, "system", return_value="Linux"),
            patch.object(native_service, "_is_termux", return_value=True),
            patch.object(native_service, "_termux_prefix", return_value=self.prefix),
            patch.object(native_service, "_run", side_effect=fake_run),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _joined(self):
        return [" ".join(c) for c in self.calls]

    def test_manager_kind_is_termux_not_systemd(self):
        self.assertEqual(native_service.service_manager_kind(), native_service.MANAGER_TERMUX)
        self.assertTrue(native_service.service_available())

    def test_install_writes_runit_script_with_wake_lock(self):
        with patch.object(native_service, "_termux_has_runit", return_value=True), \
             patch.object(native_service.shutil, "which", return_value="/bin/sv-enable"):
            msg = native_service.install_service(start_auto=True, instance_id="trader")

        script = self.prefix / "var" / "service" / "yarbis-trader" / "run"
        self.assertTrue(script.exists())
        text = script.read_text(encoding="utf-8")
        self.assertIn("YARBIS_INSTANCE=trader", text)
        self.assertIn("termux-wake-lock", text)  # Android mata procesos sin wake lock
        self.assertIn("yarbis_service.py", text)
        self.assertTrue(any("sv-enable yarbis-trader" in j for j in self._joined()))
        self.assertIn("Termux", msg)

    def test_install_without_runit_explains_how_to_get_it(self):
        with patch.object(native_service, "_termux_has_runit", return_value=False):
            msg = native_service.install_service(start_auto=True, instance_id="trader")
        self.assertIn("termux-services", msg)
        self.assertTrue((self.prefix / "var" / "service" / "yarbis-trader" / "run").exists())

    def test_status_reports_running_from_sv(self):
        script = self.prefix / "var" / "service" / "yarbis-trader" / "run"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("dummy", encoding="utf-8")
        with patch.object(native_service, "_termux_has_runit", return_value=True), \
             patch.object(native_service, "_termux_running_pid", return_value=4321):
            status = native_service.get_service_status("trader")
        self.assertTrue(status["installed"])
        self.assertTrue(status["running"])
        self.assertEqual(status["pid"], 4321)
        self.assertEqual(status["manager"], native_service.MANAGER_TERMUX)

    def test_stop_uses_sv_down_when_runit_available(self):
        script = self.prefix / "var" / "service" / "yarbis-trader" / "run"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("dummy", encoding="utf-8")
        with patch.object(native_service, "_termux_has_runit", return_value=True), \
             patch.object(native_service, "_termux_running_pid", return_value=4321):
            native_service.stop_service("trader")
        self.assertTrue(any("sv down yarbis-trader" in j for j in self._joined()))


class FacadeTestCase(unittest.TestCase):
    def test_status_formats_native_off_windows(self):
        import tools

        native_status = {
            "installed": True, "running": True, "pid": 5, "autostart_enabled": True,
            "log_file": "/home/u/.yarbis_runtime/service.log", "manager": "systemd",
        }
        with patch.object(tools.os, "name", "posix"), \
             patch("native_service.service_manager_kind", return_value="systemd"), \
             patch("native_service.get_service_status", return_value=native_status):
            out = tools.background_service_status()
        self.assertIn("systemd", out)
        self.assertIn("activo", out)
        self.assertIn("PID 5", out)

    def test_status_uses_scm_on_windows(self):
        import tools
        import service_manager

        with patch.object(tools.os, "name", "nt"), \
             patch.object(service_manager, "get_service_status", return_value={"installed": False}):
            out = tools.background_service_status()
        self.assertIn("SCM", out)
        self.assertIn("no instalado", out)

    def test_install_tool_reports_backend_errors_without_raising(self):
        import tools

        with patch.object(tools.os, "name", "posix"), \
             patch("native_service.install_service", side_effect=native_service.NativeServiceError("boom")):
            out = tools.install_background_service(autostart=True)
        self.assertIn("No pude instalar", out)
        self.assertIn("boom", out)


if __name__ == "__main__":
    unittest.main()
