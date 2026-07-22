import unittest
from unittest.mock import patch



class TuiSmokeTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_app_mounts_and_has_tabs(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        async with app.run_test() as pilot:
            # las pestañas clave existen
            for wid in ("#chat_log", "#estado", "#instancias", "#contexto", "#ajustes", "#actividad"):
                self.assertTrue(app.query(wid), f"falta el panel {wid}")
            # el input de chat existe
            self.assertTrue(app.query("#chat_input"))
            await pilot.pause()

    async def test_sending_a_message_calls_submit_user_reply(self):
        import yarbis_tui
        from textual.widgets import Input

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "submit_user_reply", return_value="respuesta de prueba") as mock_reply:
            async with app.run_test() as pilot:
                chat = app.query_one("#chat_input", Input)
                chat.value = "hola yarbis"
                chat.focus()
                await pilot.press("enter")
                # el worker corre en un hilo; esperar a que termine
                await app.workers.wait_for_complete()
                await pilot.pause()
        mock_reply.assert_called_once()
        self.assertEqual(mock_reply.call_args[0][0], "hola yarbis")

    async def test_refresh_actions_do_not_crash(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        async with app.run_test() as pilot:
            app.action_refresh()  # estado + instancias + contexto + ajustes + actividad
            await pilot.pause()
            # el panel de estado quedo con contenido (no vacio)
            from textual.widgets import Static
            estado = app.query_one("#estado", Static)
            self.assertTrue(str(estado.renderable))


class TuiHelpersTestCase(unittest.TestCase):
    def test_preselect_instance_from_argv(self):
        import yarbis_tui
        with patch.object(yarbis_tui.sys, "argv", ["yarbis_tui.py", "--instance", "dev"]):
            import os
            with patch.dict(os.environ, {}, clear=False):
                yarbis_tui._preselect_instance()
                self.assertEqual(os.environ.get("YARBIS_INSTANCE"), "dev")

    def test_render_health_is_string(self):
        import yarbis_tui
        self.assertIsInstance(yarbis_tui._render_health(), str)


if __name__ == "__main__":
    unittest.main()
