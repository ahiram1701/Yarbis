import unittest
from pathlib import Path
from unittest.mock import patch

import browser_automation
import computer_control
import memory
import tools

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class ComputerControlTestCase(unittest.TestCase):
    def setUp(self):
        TEST_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    def _state_path(self):
        return TEST_RUNTIME_DIR / f"cc_{id(self)}.json"

    def test_disabled_by_default_blocks_tools(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            self.assertIn("desactivado", tools.browser_open("https://x").lower())
            self.assertIn("desactivado", tools.desktop_click(1, 1).lower())

    def test_set_computer_control_enables_and_reports(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            out = tools.set_computer_control(enabled=True, browser_channel="chrome")
            self.assertIn("activado", out.lower())
            state = memory.load_state()["computer_control"]
            self.assertTrue(state["enabled"])
            self.assertEqual(state["settings"]["browser_channel"], "chrome")

    def test_os_control_toggle_blocks_desktop(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            tools.set_computer_control(enabled=True, os_control=False)
            self.assertIn("sistema operativo", tools.desktop_type("hola").lower())

    def test_desktop_click_sensitive_gate(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            tools.set_computer_control(enabled=True)
            blocked = tools.desktop_click(1, 1, label="Publicar")
            self.assertIn("BLOQUEADO", blocked)
            # Con confirm correcto no bloquea (pyautogui mockeado).
            with patch.object(computer_control, "click", return_value="ok"):
                allowed = tools.desktop_click(1, 1, label="Publicar", confirm="Publicar")
            self.assertEqual(allowed, "ok")

    def test_browser_act_passes_confirm_sensitive_from_state(self):
        captured = {}

        def fake_act(**kwargs):
            captured.update(kwargs)
            return "ok"

        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            tools.set_computer_control(enabled=True, confirm_sensitive=False)
            with patch.object(tools, "act_on_live_page", side_effect=fake_act):
                tools.browser_act('[{"action":"click","text":"Publicar"}]')
        self.assertFalse(captured["confirm_sensitive"])

    def test_confirm_satisfies_robust(self):
        self.assertTrue(browser_automation._confirm_satisfies("si", "Publicar"))
        self.assertTrue(browser_automation._confirm_satisfies("Publicar", "Publicar "))
        self.assertFalse(browser_automation._confirm_satisfies("", "Publicar"))
        self.assertFalse(browser_automation._confirm_satisfies("cancelar", "Publicar"))

    def test_sensitive_regex_matches_expected(self):
        for label in ("Publicar", "Pagar ahora", "Eliminar", "Compartir", "Enviar"):
            self.assertTrue(browser_automation._SENSITIVE_CLICK.search(label), label)
        for label in ("Hola mundo", "Comentar", "Me gusta"):
            self.assertFalse(browser_automation._SENSITIVE_CLICK.search(label), label)

    def test_resolve_locator_requires_reference(self):
        with self.assertRaises(ValueError):
            browser_automation._resolve_locator(object(), {"action": "click"})

    def test_normalize_computer_control_defaults(self):
        cc = memory.default_state()["computer_control"]
        self.assertFalse(cc["enabled"])
        self.assertEqual(cc["settings"]["browser_profile_mode"], "isolated")
        self.assertEqual(cc["settings"]["browser_user_data_dir"], "")
        self.assertEqual(cc["settings"]["browser_profile_directory"], "")
        norm = memory.normalize_state({"computer_control": {"enabled": True, "settings": {"browser_channel": "bogus", "os_control": False}}})
        cc2 = norm["computer_control"]
        self.assertTrue(cc2["enabled"])
        self.assertEqual(cc2["settings"]["browser_channel"], "msedge")
        self.assertFalse(cc2["settings"]["os_control"])

    def test_normalize_profile_mode_invalid_falls_back(self):
        norm = memory.normalize_state({"computer_control": {"settings": {
            "browser_profile_mode": "bogus",
            "browser_user_data_dir": "  C:/x/User Data  ",
            "browser_profile_directory": "  Profile 1  ",
        }}})
        s = norm["computer_control"]["settings"]
        self.assertEqual(s["browser_profile_mode"], "isolated")
        self.assertEqual(s["browser_user_data_dir"], "C:/x/User Data")
        self.assertEqual(s["browser_profile_directory"], "Profile 1")

    def test_set_computer_control_persists_profile(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            out = tools.set_computer_control(
                enabled=True,
                browser_channel="brave",
                browser_profile_mode="system",
                browser_profile_directory="Default",
            )
            self.assertIn("sistema", out.lower())
            s = memory.load_state()["computer_control"]["settings"]
            self.assertEqual(s["browser_profile_mode"], "system")
            self.assertEqual(s["browser_profile_directory"], "Default")

    def test_resolve_user_data_dir_isolated_system_custom(self):
        # isolated -> perfil propio de Yarbis
        _, label = browser_automation._resolve_user_data_dir("brave", "isolated", "")
        self.assertIn("aislado", label.lower())
        # custom override gana sobre el modo
        custom = str(TEST_RUNTIME_DIR / f"ud_{id(self)}")
        path, label = browser_automation._resolve_user_data_dir("brave", "system", custom)
        self.assertIn("personalizado", label.lower())
        self.assertTrue(Path(path).exists())
        # system con LOCALAPPDATA existente -> ruta del sistema
        import os
        fake_local = TEST_RUNTIME_DIR / f"local_{id(self)}"
        (fake_local / "BraveSoftware" / "Brave-Browser" / "User Data").mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(fake_local)}):
            path, label = browser_automation._resolve_user_data_dir("brave", "system", "")
        self.assertIn("perfil del sistema", label.lower())
        self.assertTrue(str(path).endswith("User Data"))

    def test_browser_open_passes_profile_settings(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            tools.set_computer_control(
                enabled=True,
                browser_channel="brave",
                browser_profile_mode="system",
                browser_profile_directory="Profile 1",
                browser_user_data_dir="C:/custom/UD",
            )
            with patch.object(tools, "open_persistent_browser", return_value="ok") as mock_open:
                tools.browser_open("https://www.facebook.com/")
            _, kwargs = mock_open.call_args
            self.assertEqual(kwargs["channel"], "brave")
            self.assertEqual(kwargs["profile_mode"], "system")
            self.assertEqual(kwargs["profile_directory"], "Profile 1")
            self.assertEqual(kwargs["user_data_dir"], "C:/custom/UD")


class BrowserSessionLaunchTestCase(unittest.TestCase):
    def test_process_session_id_is_int_or_none(self):
        sid = browser_automation._process_session_id()
        self.assertTrue(sid is None or isinstance(sid, int))
        # el proceso de tests es interactivo: no debe ser Session 0
        self.assertFalse(browser_automation._running_in_session0())

    def test_spawn_uses_popen_outside_session0(self):
        with patch.object(browser_automation, "_running_in_session0", return_value=False), \
             patch.object(browser_automation.subprocess, "Popen") as popen, \
             patch.object(browser_automation, "_launch_in_active_session") as user_launch:
            browser_automation._spawn_browser_process("x.exe", ["x.exe", "--a"], 0)
        self.assertTrue(popen.called)
        self.assertFalse(user_launch.called)

    def test_spawn_uses_user_session_in_session0(self):
        with patch.object(browser_automation, "_running_in_session0", return_value=True), \
             patch.object(browser_automation, "_launch_in_active_session", return_value=True) as user_launch, \
             patch.object(browser_automation.subprocess, "Popen") as popen:
            browser_automation._spawn_browser_process("x.exe", ["x.exe", "--a"], 0)
        self.assertTrue(user_launch.called)
        self.assertFalse(popen.called)  # no doble-lanzamiento

    def test_spawn_falls_back_to_popen_when_user_launch_fails(self):
        with patch.object(browser_automation, "_running_in_session0", return_value=True), \
             patch.object(browser_automation, "_launch_in_active_session", return_value=False), \
             patch.object(browser_automation.subprocess, "Popen") as popen:
            browser_automation._spawn_browser_process("x.exe", ["x.exe", "--a"], 0)
        self.assertTrue(popen.called)

    def test_launch_in_active_session_non_windows_returns_false(self):
        with patch.object(browser_automation.os, "name", "posix"):
            self.assertFalse(browser_automation._launch_in_active_session("x", ["x"]))


if __name__ == "__main__":
    unittest.main()
