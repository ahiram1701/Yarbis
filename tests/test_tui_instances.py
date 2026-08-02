"""TUI multi-instancia: selector, panel de instancias, servicio y pulso.

Todo mockeado: ningun test cambia de instancia de verdad ni toca un servicio.
"""

import unittest
from unittest.mock import patch

from textual.widgets import Button, DataTable, Input, Select

ROWS = [
    {"id": "default", "active": True, "waiting": False, "goal": "objetivo A", "last_result": ""},
    {"id": "asistente", "active": False, "waiting": True, "goal": "objetivo B", "last_result": ""},
]


class TuiInstancePanelTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_table_lists_instances_with_status(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS):
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.query_one("#inst_table", DataTable)
                self.assertEqual(table.row_count, 2)
                rendered = " ".join(
                    str(cell) for row in table.get_row_range(slice(0, 2)) for cell in row
                ) if hasattr(table, "get_row_range") else ""
                if rendered:
                    self.assertIn("asistente", rendered)

    async def test_selector_is_populated(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS):
            async with app.run_test() as pilot:
                await pilot.pause()
                options = [str(value) for _label, value in app.query_one("#inst_select", Select)._options]
                self.assertIn("asistente", options)


class TuiSwitchInstanceTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_switching_rebinds_and_updates_subtitle(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS), \
             patch.object(yarbis_tui.instance_binding, "rebind", return_value="asistente") as rebind:
            async with app.run_test() as pilot:
                await pilot.pause()
                changed = app.switch_to_instance("asistente")
                await pilot.pause()

        self.assertTrue(changed)
        rebind.assert_called_once_with("asistente")
        self.assertEqual(app.instance_id, "asistente")
        self.assertIn("asistente", app.sub_title)

    async def test_switching_is_refused_while_busy(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS), \
             patch.object(yarbis_tui.instance_binding, "rebind") as rebind:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.busy = True
                changed = app.switch_to_instance("asistente")
                await pilot.pause()

        self.assertFalse(changed)
        rebind.assert_not_called()  # nunca se cambia en medio de una operacion

    async def test_switching_to_the_same_instance_is_a_noop(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS), \
             patch.object(yarbis_tui.instance_binding, "rebind") as rebind:
            async with app.run_test() as pilot:
                await pilot.pause()
                same = app.instance_id
                self.assertFalse(app.switch_to_instance(same))
        rebind.assert_not_called()


class TuiRowActionsTestCase(unittest.IsolatedAsyncioTestCase):
    async def _press(self, app, pilot, button_id: str):
        app.query_one(f"#{button_id}", Button).press()
        await pilot.pause()

    async def test_answer_uses_the_selected_row(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS), \
             patch.object(yarbis_tui, "answer_instance_for_user", return_value="ok") as answer:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#inst_table", DataTable).move_cursor(row=1)  # asistente
                app.query_one("#inst_msg", Input).value = "sí, adelante"
                await self._press(app, pilot, "btn_unblock")

        answer.assert_called_once()
        self.assertEqual(answer.call_args[0][0], "asistente")
        self.assertEqual(answer.call_args[0][1], "sí, adelante")

    async def test_broadcast_only_targets_waiting_instances(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS), \
             patch.object(yarbis_tui, "answer_instance_for_user", return_value="ok") as answer:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#inst_msg", Input).value = "respuesta para todas"
                await self._press(app, pilot, "btn_broadcast")

        # Solo 'asistente' esperaba respuesta.
        self.assertEqual(answer.call_count, 1)
        self.assertEqual(answer.call_args[0][0], "asistente")


class TuiServiceAndPulseTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_service_buttons_call_the_cross_platform_facade(self):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS), \
             patch.object(yarbis_tui, "background_service_status", return_value="estado"), \
             patch.object(yarbis_tui, "start_background_service", return_value="iniciado") as start, \
             patch.object(yarbis_tui, "install_background_service", return_value="instalado") as install:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#btn_svc_install", Button).press()
                await pilot.pause()
                app.query_one("#btn_svc_start", Button).press()
                await pilot.pause()

        install.assert_called_once()
        start.assert_called_once()

    async def test_saving_pulse_passes_the_form_values(self):
        import yarbis_tui

        current = {"enabled": True, "interval_seconds": 1800, "cycles": None, "start_delay_seconds": 60, "model": ""}
        app = yarbis_tui.YarbisTUI()
        with patch.object(yarbis_tui, "_instance_rows", return_value=ROWS), \
             patch.object(yarbis_tui, "background_service_status", return_value="estado"), \
             patch.object(yarbis_tui, "get_service_proactive_settings", return_value=current), \
             patch.object(yarbis_tui, "update_service_proactive_settings", return_value="guardado") as save:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#pulse_interval", Input).value = "900"
                app.query_one("#pulse_delay", Input).value = "30"
                app.query_one("#pulse_model", Input).value = "gpt-4o-mini"
                app.query_one("#btn_pulse", Button).press()
                await pilot.pause()

        save.assert_called_once()
        args = save.call_args[0]
        self.assertEqual(args[1], "900")          # intervalo
        self.assertIsNone(args[2])                 # ciclos vacio = hasta terminar
        self.assertEqual(args[3], "30")            # espera inicial
        self.assertEqual(args[4], "gpt-4o-mini")   # modelo


if __name__ == "__main__":
    unittest.main()
