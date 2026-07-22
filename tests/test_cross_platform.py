"""Fase A del multi-SO: verifica que el core no se rompe fuera de Windows y que
las capas por-SO enrutan al backend correcto. En Windows todo se comporta igual.
"""

import unittest
from unittest.mock import patch

import notifications
import power
import pc_context
import credential_store


class LocalNotificationRoutingTestCase(unittest.TestCase):
    def test_routes_to_windows_backend_on_win32(self):
        with patch.object(notifications.sys, "platform", "win32"), \
             patch.object(notifications, "_send_windows_notification", return_value=True) as win, \
             patch.object(notifications, "_send_macos_notification") as mac, \
             patch.object(notifications, "_send_linux_notification") as lin:
            self.assertTrue(notifications._send_local_desktop_notification("t", "b"))
        win.assert_called_once()
        mac.assert_not_called()
        lin.assert_not_called()

    def test_routes_to_macos_backend_on_darwin(self):
        with patch.object(notifications.sys, "platform", "darwin"), \
             patch.object(notifications, "_send_macos_notification", return_value=True) as mac, \
             patch.object(notifications, "_send_windows_notification") as win:
            self.assertTrue(notifications._send_local_desktop_notification("t", "b"))
        mac.assert_called_once()
        win.assert_not_called()

    def test_routes_to_linux_backend_on_linux(self):
        with patch.object(notifications.sys, "platform", "linux"), \
             patch.object(notifications, "_send_linux_notification", return_value=True) as lin, \
             patch.object(notifications, "_send_windows_notification") as win:
            self.assertTrue(notifications._send_local_desktop_notification("t", "b"))
        lin.assert_called_once()
        win.assert_not_called()

    def test_linux_backend_builds_notify_send_command(self):
        with patch.object(notifications.shutil, "which", return_value="/usr/bin/notify-send"), \
             patch.object(notifications.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(notifications._send_linux_notification("Hola", "Mundo"))
        self.assertEqual(run.call_args[0][0][0], "notify-send")

    def test_macos_backend_builds_osascript_command(self):
        with patch.object(notifications.shutil, "which", return_value="/usr/bin/osascript"), \
             patch.object(notifications.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(notifications._send_macos_notification("T", "B"))
        self.assertEqual(run.call_args[0][0][0], "osascript")

    def test_backends_return_false_when_tool_missing(self):
        with patch.object(notifications.shutil, "which", return_value=None):
            self.assertFalse(notifications._send_linux_notification("t", "b"))
            self.assertFalse(notifications._send_macos_notification("t", "b"))

    def test_unknown_platform_returns_false(self):
        with patch.object(notifications.sys, "platform", "freebsd"):
            self.assertFalse(notifications._send_local_desktop_notification("t", "b"))


class PowerPerOsTestCase(unittest.TestCase):
    def test_command_windows_unchanged(self):
        with patch.object(power.sys, "platform", "win32"):
            cmd = power._power_command("shutdown", 60)
            rcmd = power._power_command("restart", 0)
        self.assertIn("/s", cmd)
        self.assertIn("/t", cmd)
        self.assertIn("/r", rcmd)

    def test_command_posix_uses_shutdown(self):
        with patch.object(power.sys, "platform", "linux"):
            self.assertEqual(power._power_command("shutdown", 60), ["shutdown", "-h", "+1"])
            self.assertEqual(power._power_command("shutdown", 0), ["shutdown", "-h", "now"])
            self.assertEqual(power._power_command("restart", 120), ["shutdown", "-r", "+2"])

    def test_request_shutdown_runs_posix_command_without_executing(self):
        # MOCK de la ejecucion: nunca se corre shutdown de verdad en el test.
        from types import SimpleNamespace
        captured = {}

        def fake_run(cmd):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(power.sys, "platform", "linux"), \
             patch.object(power, "_run_shutdown_command", side_effect=fake_run):
            msg = power.request_system_shutdown(0)
        self.assertEqual(captured["cmd"], ["shutdown", "-h", "now"])
        self.assertIsInstance(msg, str)


class PcContextPerOsTestCase(unittest.TestCase):
    def _run_ok(self, stdout):
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    def test_linux_idle_parses_xprintidle_ms(self):
        with patch.object(pc_context.shutil, "which", return_value="/usr/bin/xprintidle"), \
             patch.object(pc_context.subprocess, "run", return_value=self._run_ok("5000\n")):
            self.assertEqual(pc_context._linux_idle_seconds(), 5)

    def test_linux_idle_none_without_tool(self):
        with patch.object(pc_context.shutil, "which", return_value=None):
            self.assertIsNone(pc_context._linux_idle_seconds())

    def test_macos_idle_parses_ioreg_hididletime(self):
        ioreg = '  "HIDIdleTime" = 3000000000\n  "HIDIdleTime" = 9000000000\n'
        with patch.object(pc_context.shutil, "which", return_value="/usr/sbin/ioreg"), \
             patch.object(pc_context.subprocess, "run", return_value=self._run_ok(ioreg)):
            self.assertEqual(pc_context._macos_idle_seconds(), 3)  # el menor -> mas reciente

    def test_linux_foreground_uses_xdotool(self):
        settings = {"include_process_name": True, "include_window_title": True}
        outputs = {"getactivewindow": "12345", "getwindowname": "Mi Ventana", "getwindowpid": "0"}

        def fake_run(args, **kw):
            from types import SimpleNamespace
            key = args[1] if len(args) > 1 else ""
            return SimpleNamespace(returncode=0, stdout=outputs.get(key, ""), stderr="")

        with patch.object(pc_context.shutil, "which", return_value="/usr/bin/xdotool"), \
             patch.object(pc_context.subprocess, "run", side_effect=fake_run):
            ctx = pc_context._linux_foreground({"available": False, "process_name": "", "title": ""}, settings)
        self.assertTrue(ctx["available"])
        self.assertEqual(ctx["title"], "Mi Ventana")

    def test_macos_foreground_uses_osascript(self):
        settings = {"include_process_name": True, "include_window_title": True}
        with patch.object(pc_context.shutil, "which", return_value="/usr/bin/osascript"), \
             patch.object(pc_context.subprocess, "run", return_value=self._run_ok("Safari\n")):
            ctx = pc_context._macos_foreground({"available": False, "process_name": "", "title": ""}, settings)
        self.assertTrue(ctx["available"])
        self.assertEqual(ctx["process_name"], "Safari")


class GracefulDegradationTestCase(unittest.TestCase):
    def test_pc_context_idle_none_off_windows_without_tools(self):
        with patch.object(pc_context.platform, "system", return_value="Linux"), \
             patch.object(pc_context.shutil, "which", return_value=None):
            self.assertIsNone(pc_context._get_idle_seconds())

    def test_pc_context_foreground_empty_off_windows_without_tools(self):
        with patch.object(pc_context.platform, "system", return_value="Darwin"), \
             patch.object(pc_context.shutil, "which", return_value=None):
            ctx = pc_context._get_foreground_context({"include_process_name": True, "include_window_title": True})
        self.assertIsInstance(ctx, dict)
        self.assertFalse(ctx["available"])


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, ref, value):
        self.store[(service, ref)] = value

    def get_password(self, service, ref):
        return self.store.get((service, ref))

    def delete_password(self, service, ref):
        self.store.pop((service, ref), None)


class CredentialStoreBackendsTestCase(unittest.TestCase):
    def test_windows_uses_dpapi_unchanged(self):
        with patch.object(credential_store, "_dpapi_available", return_value=True), \
             patch.object(credential_store, "_dpapi_protect", return_value=b"prot"):
            method, _ = credential_store._store_secret("cred-x", "s")
        self.assertEqual(method, "dpapi")

    def test_non_windows_uses_keyring_and_keeps_secret_out_of_file(self):
        fake = _FakeKeyring()
        with patch.object(credential_store, "_dpapi_available", return_value=False), \
             patch.object(credential_store, "_keyring_module", return_value=fake):
            ref = credential_store.save_secret("secreto-nube", kind="mcp")
            record_text = credential_store._credential_path(ref).read_text(encoding="utf-8")
            self.assertNotIn("secreto-nube", record_text)  # el secreto vive en el keyring
            self.assertEqual(credential_store.load_secret(ref), "secreto-nube")
            self.assertTrue(credential_store.delete_secret(ref))
            self.assertEqual(fake.store, {})  # delete limpia el keyring

    def test_non_windows_falls_back_to_base64_without_keyring(self):
        with patch.object(credential_store, "_dpapi_available", return_value=False), \
             patch.object(credential_store, "_keyring_module", return_value=None):
            method, payload = credential_store._store_secret("cred-y", "un-secreto")
            self.assertEqual(method, "base64")
            self.assertEqual(credential_store._unprotect_secret(method, payload), "un-secreto")

    def test_keyring_set_failure_falls_back_to_base64(self):
        class Boom(_FakeKeyring):
            def set_password(self, *a):
                raise RuntimeError("no dbus")
        with patch.object(credential_store, "_dpapi_available", return_value=False), \
             patch.object(credential_store, "_keyring_module", return_value=Boom()):
            method, payload = credential_store._store_secret("cred-z", "sec")
            self.assertEqual(method, "base64")
            self.assertEqual(credential_store._unprotect_secret(method, payload), "sec")


if __name__ == "__main__":
    unittest.main()
