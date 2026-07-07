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
        norm = memory.normalize_state({"computer_control": {"enabled": True, "settings": {"browser_channel": "bogus", "os_control": False}}})
        cc2 = norm["computer_control"]
        self.assertTrue(cc2["enabled"])
        self.assertEqual(cc2["settings"]["browser_channel"], "msedge")
        self.assertFalse(cc2["settings"]["os_control"])


if __name__ == "__main__":
    unittest.main()
