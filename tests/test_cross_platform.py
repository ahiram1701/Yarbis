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


class GracefulDegradationTestCase(unittest.TestCase):
    def test_power_shutdown_degrades_off_windows(self):
        with patch.object(power.sys, "platform", "linux"):
            msg = power.request_system_shutdown()
        self.assertIsInstance(msg, str)
        self.assertTrue(msg)  # devuelve un mensaje, no crashea

    def test_pc_context_idle_none_off_windows(self):
        with patch.object(pc_context.platform, "system", return_value="Linux"):
            self.assertIsNone(pc_context._get_idle_seconds())

    def test_pc_context_foreground_empty_off_windows(self):
        with patch.object(pc_context.platform, "system", return_value="Darwin"):
            ctx = pc_context._get_foreground_context({})
        self.assertIsInstance(ctx, dict)


class CredentialStoreFallbackTestCase(unittest.TestCase):
    def test_base64_roundtrip_without_dpapi(self):
        # Fuera de Windows (sin DPAPI) el store usa base64 y hace round-trip.
        with patch.object(credential_store, "_dpapi_available", return_value=False):
            method, payload = credential_store._protect_secret("un-secreto-123")
            self.assertEqual(method, "base64")
            self.assertEqual(credential_store._unprotect_secret(method, payload), "un-secreto-123")


if __name__ == "__main__":
    unittest.main()
