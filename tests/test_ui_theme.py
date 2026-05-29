import unittest
import tkinter as tk
from tkinter import ttk

import ui_theme


class UiThemeTestCase(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
