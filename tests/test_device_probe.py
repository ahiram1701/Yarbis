"""Fase 1 de ubicuidad: sonda de comportamiento + clase 'unknown' + supervisor
portable. Todo mockeado: no se lanzan procesos reales ni se toca la red de verdad.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import device_profile
import native_service


class ProbeTestCase(unittest.TestCase):
    def test_write_files_probe(self):
        self.assertTrue(device_profile._probe_write_files())

    def test_spawn_process_probe_success(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with patch.object(device_profile.subprocess, "run", return_value=completed):
            self.assertTrue(device_profile._probe_spawn_process())

    def test_spawn_process_probe_failure(self):
        with patch.object(device_profile.subprocess, "run", side_effect=OSError("no exec")):
            self.assertFalse(device_profile._probe_spawn_process())

    def test_network_probe_true_when_socket_connects(self):
        import socket

        class _FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with patch.object(socket, "create_connection", return_value=_FakeConn()):
            self.assertTrue(device_profile._probe_network())

    def test_network_probe_false_when_blocked(self):
        import socket

        with patch.object(socket, "create_connection", side_effect=OSError("blocked")):
            self.assertFalse(device_profile._probe_network())

    def test_probe_capabilities_shape(self):
        with patch.object(device_profile, "_probe_write_files", return_value=True), \
             patch.object(device_profile, "_probe_spawn_process", return_value=True), \
             patch.object(device_profile, "_probe_network", return_value=False), \
             patch.object(device_profile, "_service_manager_label", return_value="portable"):
            probes = device_profile.probe_capabilities()
        self.assertTrue(probes["can_write_files"])
        self.assertFalse(probes["can_network"])
        self.assertEqual(probes["service_manager"], "portable")
        summary = device_profile.render_probe_summary(probes)
        self.assertIn("archivos:si", summary)
        self.assertIn("red:no", summary)


class UnknownOsTestCase(unittest.TestCase):
    def _profile_for(self, system: str, display: bool):
        patches = [
            patch.object(device_profile.platform, "system", return_value=system),
            patch.object(device_profile, "is_termux", return_value=False),
            patch.object(device_profile, "is_container", return_value=False),
            patch.object(device_profile, "is_wsl", return_value=False),
            patch.object(device_profile, "detect_virtualization", return_value=""),
            patch.object(device_profile, "sbc_model", return_value=""),
            patch.object(device_profile, "has_display", return_value=display),
            patch.object(device_profile, "_total_memory_bytes", return_value=8 * 1024**3),
            patch.object(device_profile, "_power_status", return_value={"available": False}),
            patch.object(device_profile, "_memory_status", return_value={}),
            patch.object(device_profile, "_has_gpu", return_value=False),
        ]
        for p in patches:
            p.start()
        device_profile._CACHE["profile"] = None
        try:
            return device_profile.get_profile(refresh=True)
        finally:
            for p in patches:
                p.stop()
            device_profile._CACHE["profile"] = None

    def test_unrecognized_os_with_display_is_unknown_not_windows(self):
        profile = self._profile_for("Plan9", display=True)
        self.assertEqual(profile["device_class"], device_profile.CLASS_UNKNOWN)
        self.assertFalse(profile["known_system"])
        # Aun sin reconocer el SO, las capacidades salen de senales reales.
        self.assertTrue(profile["capabilities"][device_profile.CAP_GUI])

    def test_known_os_still_workstation(self):
        profile = self._profile_for("Windows", display=True)
        self.assertEqual(profile["device_class"], device_profile.CLASS_WORKSTATION)
        self.assertTrue(profile["known_system"])


class PortableSupervisorTestCase(unittest.TestCase):
    def setUp(self):
        self.runtime = Path(tempfile.mkdtemp())
        self._patches = [
            patch.object(native_service.platform, "system", return_value="Plan9"),
            patch.object(native_service.os, "name", "posix"),
            patch.object(native_service.yarbis_instance, "runtime_dir", return_value=self.runtime),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_unknown_os_uses_portable_manager(self):
        self.assertEqual(native_service.service_manager_kind(), native_service.MANAGER_PORTABLE)
        self.assertTrue(native_service.service_available())

    def test_install_writes_marker(self):
        msg = native_service.install_service(start_auto=False, instance_id="default")
        self.assertTrue((self.runtime / "portable_service.json").exists())
        self.assertIn("portable", msg.lower())

    def test_start_spawns_detached_process(self):
        with patch.object(native_service.subprocess, "Popen") as popen, \
             patch.object(native_service, "_portable_running_pid", return_value=None):
            native_service.start_service(instance_id="default")
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertIn(str(native_service.SERVICE_SCRIPT), args[0])
        self.assertEqual(kwargs["env"]["YARBIS_INSTANCE"], "default")

    def test_status_reports_running_from_pid(self):
        (self.runtime / "portable_service.json").write_text("{}", encoding="utf-8")
        with patch.object(native_service, "_portable_running_pid", return_value=999):
            status = native_service.get_service_status("default")
        self.assertTrue(status["running"])
        self.assertEqual(status["pid"], 999)
        self.assertEqual(status["manager"], native_service.MANAGER_PORTABLE)

    def test_autostart_explains_it_is_manual(self):
        (self.runtime / "portable_service.json").write_text("{}", encoding="utf-8")
        out = native_service.set_autostart_enabled(True, instance_id="default")
        self.assertIn("arranque automatico", out.lower())


class ProbeToolTestCase(unittest.TestCase):
    def test_probe_device_tool_runs(self):
        import tools

        out = tools.probe_device()
        self.assertIn("Dispositivo:", out)
        self.assertIn("Sonda del entorno", out)


if __name__ == "__main__":
    unittest.main()
