import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("requiere Windows (UI de escritorio tkinter)")

import tkinter as tk
from tkinter import ttk

import ui_theme


class UiThemeTestCase(unittest.TestCase):
    def test_use_bootstrap_theme_accepts_partial_success(self):
        class PartialSwitchStyle:
            def __init__(self):
                self.current = "darkly"

            def theme_use(self, theme=None):
                if theme is None:
                    return self.current
                self.current = theme
                raise RuntimeError("post-switch style rebuild failed")

        style = PartialSwitchStyle()

        self.assertTrue(ui_theme.use_bootstrap_theme(style, "light"))
        self.assertEqual(style.theme_use(), "litera")

    def test_configure_styles_after_scrollbar_exists(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk no disponible: {exc}")

        try:
            root.withdraw()
            style, _active = ui_theme.create_app_style(root, "dark")
            ttk.Scrollbar(root, style="Yarbis.Vertical.TScrollbar")

            ui_theme.configure_app_styles(style, ui_theme.THEMES["dark"])
        finally:
            root.destroy()

    def test_bootstrap_theme_can_toggle_after_custom_styles(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk no disponible: {exc}")

        try:
            root.withdraw()
            style, active = ui_theme.create_app_style(root, "dark")
            ttk.Scrollbar(root, style="Yarbis.Vertical.TScrollbar")
            ttk.Spinbox(root, style="Yarbis.TSpinbox")
            ui_theme.configure_app_styles(style, ui_theme.THEMES["dark"])

            if not active:
                self.skipTest("ttkbootstrap no disponible")

            self.assertTrue(ui_theme.use_bootstrap_theme(style, "light"))
            self.assertEqual(style.theme_use(), "litera")
            ui_theme.configure_app_styles(style, ui_theme.THEMES["light"])
            self.assertTrue(ui_theme.use_bootstrap_theme(style, "dark"))
            self.assertEqual(style.theme_use(), "darkly")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
