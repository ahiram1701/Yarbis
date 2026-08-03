"""Verificacion COMPLETA de la TUI: cada pestana y cada control.

Se pulsa TODOS los botones y se usan todos los campos, con los backends
mockeados, para comprobar que cada control llama a lo que debe. Especial
atencion a la pestana Ajustes (proveedor, control de PC, MCP, servicio, pulso).
"""

import unittest
import unittest.mock
from unittest.mock import patch

from textual.widgets import Button, DataTable, Input, RichLog, Select, Static

ROWS = [
    {"id": "default", "active": True, "waiting": False, "goal": "objetivo A", "last_result": ""},
    {"id": "asistente", "active": False, "waiting": True, "goal": "objetivo B", "last_result": ""},
]

# Todo lo que la TUI llama hacia el backend, para no tocar el estado real.
BACKENDS = {
    "agent_overview": "resumen",
    "background_service_status": "servicio: detenido",
    "install_background_service": "instalado",
    "start_background_service": "iniciado",
    "stop_background_service": "detenido",
    "remove_background_service": "quitado",
    "set_background_service_autostart": "autostart ok",
    "set_computer_control": "cc ok",
    "set_mcp_enabled": "mcp ok",
    "update_goal": "objetivo ok",
    "add_task": "tarea ok",
    "save_note": "nota ok",
    "send_yarbis_message": "mensaje ok",
    "answer_instance_for_user": "respondido",
    "update_service_proactive_settings": "pulso ok",
    "request_stop_current_operation": "detenido",
}

PULSE = {
    "enabled": True,
    "interval_seconds": 1800,
    "cycles": None,
    "start_delay_seconds": 60,
    "model": "",
}


class _TuiHarness:
    """Monta la TUI con TODO el backend mockeado."""

    def __init__(self):
        import yarbis_tui

        self.mod = yarbis_tui
        self.mocks = {}
        self._patches = [
            patch.object(yarbis_tui, "_instance_rows", return_value=ROWS),
            patch.object(yarbis_tui, "get_service_proactive_settings", return_value=dict(PULSE)),
        ]
        for name, result in BACKENDS.items():
            p = patch.object(yarbis_tui, name, return_value=result)
            self._patches.append(p)
        for p in self._patches:
            started = p.start()
            target = getattr(p, "attribute", None)
            if target:
                self.mocks[target] = started

    def stop(self):
        for p in self._patches:
            p.stop()


class TuiPanelsTestCase(unittest.IsolatedAsyncioTestCase):
    """Las 6 pestanas montan y pintan contenido sin reventar."""

    async def test_all_tabs_render(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            app = yarbis_tui.YarbisTUI()
            async with app.run_test() as pilot:
                await pilot.pause()
                for tab in ("tab-chat", "tab-estado", "tab-instancias",
                            "tab-contexto", "tab-ajustes", "tab-actividad"):
                    app.query_one("TabbedContent").active = tab
                    await pilot.pause()
                # Los paneles de texto tienen contenido (no quedaron en "Cargando…")
                for panel_id in ("estado", "contexto", "ajustes", "servicio"):
                    panel = app.query_one(f"#{panel_id}", Static)
                    self.assertNotIn("Cargando", str(panel.renderable), panel_id)
                self.assertIsInstance(app.query_one("#chat_log", RichLog), RichLog)
                self.assertEqual(app.query_one("#inst_table", DataTable).row_count, 2)
        finally:
            h.stop()


class TuiAjustesTestCase(unittest.IsolatedAsyncioTestCase):
    """La pestana Ajustes al completo: proveedor, toggles, servicio y pulso."""

    async def _mounted(self, harness):
        import yarbis_tui

        app = yarbis_tui.YarbisTUI()
        return app

    async def test_provider_save_calls_the_right_updater(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            for provider in ("ollama", "openrouter", "openai_compat", "puter"):
                with patch.dict(yarbis_tui._PROVIDER_UPDATERS,
                                {provider: (fake := unittest.mock.MagicMock(return_value="ok"))}):
                    app = yarbis_tui.YarbisTUI()
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        app.query_one("#set_provider", Select).value = provider
                        # Dejar que el cambio de proveedor precargue sus campos
                        # ANTES de escribir los propios (si no, los pisa).
                        await pilot.pause()
                        app.query_one("#set_model", Input).value = "mi-modelo"
                        app.query_one("#set_host", Input).value = "https://host"
                        app.query_one("#set_key", Input).value = "secreto"
                        app.query_one("#btn_provider", Button).press()
                        await pilot.pause()
                    fake.assert_called_once()
                    kwargs = fake.call_args.kwargs
                    self.assertEqual(kwargs["model"], "mi-modelo")
                    self.assertEqual(kwargs["host"], "https://host")
                    self.assertEqual(kwargs["api_key"], "secreto")
        finally:
            h.stop()

    async def test_provider_without_selection_warns_and_does_not_save(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            fake = unittest.mock.MagicMock()
            with patch.dict(yarbis_tui._PROVIDER_UPDATERS, {"ollama": fake}):
                app = yarbis_tui.YarbisTUI()
                async with app.run_test() as pilot:
                    await pilot.pause()
                    # El formulario se precarga con el proveedor activo; forzar
                    # el caso sin seleccion para comprobar que no guarda nada.
                    app.query_one("#set_provider", Select).value = Select.BLANK
                    await pilot.pause()
                    app.query_one("#btn_provider", Button).press()
                    await pilot.pause()
            fake.assert_not_called()
        finally:
            h.stop()

    async def test_provider_form_is_prefilled_with_current_config(self):
        """Sin precarga, tocar solo el host obligaba a reescribir el modelo
        (guardar con modelo vacio falla: 'no puede quedar vacio')."""
        import yarbis_tui

        state = {
            "model_provider": {
                "default": "puter",
                "puter": {"model": "gpt-4o-mini", "host": "https://llm.puter.work", "api_key": "s"},
                "ollama": {"model": "qwen3.5:2b", "host": "", "api_key": ""},
            }
        }
        h = _TuiHarness()
        try:
            with patch.object(yarbis_tui, "load_state", return_value=state):
                app = yarbis_tui.YarbisTUI()
                async with app.run_test() as pilot:
                    await pilot.pause()
                    self.assertEqual(app.query_one("#set_provider", Select).value, "puter")
                    self.assertEqual(app.query_one("#set_model", Input).value, "gpt-4o-mini")
                    self.assertEqual(app.query_one("#set_host", Input).value, "https://llm.puter.work")
                    # Cambiar de proveedor muestra SU configuracion.
                    app.query_one("#set_provider", Select).value = "ollama"
                    await pilot.pause()
                    self.assertEqual(app.query_one("#set_model", Input).value, "qwen3.5:2b")
        finally:
            h.stop()

    async def test_computer_control_and_mcp_toggles(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            app = yarbis_tui.YarbisTUI()
            async with app.run_test() as pilot:
                await pilot.pause()
                for button_id in ("btn_cc_on", "btn_cc_off", "btn_mcp_on", "btn_mcp_off"):
                    app.query_one(f"#{button_id}", Button).press()
                    await pilot.pause()

            cc = h.mocks["set_computer_control"]
            mcp = h.mocks["set_mcp_enabled"]
            self.assertEqual([c.args[0] for c in cc.call_args_list], [True, False])
            self.assertEqual([c.args[0] for c in mcp.call_args_list], [True, False])
        finally:
            h.stop()

    async def test_every_service_button(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            app = yarbis_tui.YarbisTUI()
            async with app.run_test() as pilot:
                await pilot.pause()
                for button_id in ("btn_svc_install", "btn_svc_start", "btn_svc_stop",
                                  "btn_svc_remove", "btn_svc_auto_on", "btn_svc_auto_off"):
                    app.query_one(f"#{button_id}", Button).press()
                    await pilot.pause()

            h.mocks["install_background_service"].assert_called_once()
            h.mocks["start_background_service"].assert_called_once()
            h.mocks["stop_background_service"].assert_called_once()
            h.mocks["remove_background_service"].assert_called_once()
            autostart = h.mocks["set_background_service_autostart"]
            self.assertEqual([c.args[0] for c in autostart.call_args_list], [True, False])
        finally:
            h.stop()

    async def test_pulse_form_loads_current_values(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            app = yarbis_tui.YarbisTUI()
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertEqual(app.query_one("#pulse_interval", Input).value, "1800")
                self.assertEqual(app.query_one("#pulse_delay", Input).value, "60")
                self.assertEqual(app.query_one("#pulse_cycles", Input).value, "")
                self.assertEqual(app.query_one("#pulse_enabled", Select).value, "1")
        finally:
            h.stop()

    async def test_pulse_save_sends_edited_values(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            app = yarbis_tui.YarbisTUI()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#pulse_interval", Input).value = "600"
                app.query_one("#pulse_cycles", Input).value = "3"
                app.query_one("#pulse_delay", Input).value = "15"
                app.query_one("#pulse_model", Input).value = "gpt-4o-mini"
                app.query_one("#pulse_enabled", Select).value = "0"
                app.query_one("#btn_pulse", Button).press()
                await pilot.pause()

            save = h.mocks["update_service_proactive_settings"]
            save.assert_called_once()
            args = save.call_args.args
            self.assertFalse(args[0])                 # desactivado
            self.assertEqual(args[1], "600")          # intervalo
            self.assertEqual(args[2], "3")            # ciclos
            self.assertEqual(args[3], "15")           # espera inicial
            self.assertEqual(args[4], "gpt-4o-mini")  # modelo
        finally:
            h.stop()


class TuiContextTabTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_goal_task_and_note_buttons(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            app = yarbis_tui.YarbisTUI()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#ctx_goal", Input).value = "nuevo objetivo"
                app.query_one("#btn_goal", Button).press()
                await pilot.pause()
                app.query_one("#ctx_task", Input).value = "nueva tarea"
                app.query_one("#btn_task", Button).press()
                await pilot.pause()
                app.query_one("#ctx_note_title", Input).value = "titulo"
                app.query_one("#ctx_note_body", Input).value = "cuerpo"
                app.query_one("#btn_note", Button).press()
                await pilot.pause()

            self.assertEqual(h.mocks["update_goal"].call_args.args[0], "nuevo objetivo")
            self.assertEqual(h.mocks["add_task"].call_args.args[0], "nueva tarea")
            self.assertEqual(h.mocks["save_note"].call_args.args[:2], ("titulo", "cuerpo"))
        finally:
            h.stop()


class TuiInstancesTabTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_and_message_buttons(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            app = yarbis_tui.YarbisTUI()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#btn_refresh_inst", Button).press()
                await pilot.pause()
                app.query_one("#inst_table", DataTable).move_cursor(row=1)
                app.query_one("#inst_msg", Input).value = "hola"
                app.query_one("#btn_send_msg", Button).press()
                await pilot.pause()

            self.assertEqual(h.mocks["send_yarbis_message"].call_args.args[0], "asistente")
        finally:
            h.stop()


class TuiTelegramMirrorTestCase(unittest.IsolatedAsyncioTestCase):
    """Lo que hablas por la TUI debe llegar a Telegram, como en las otras UIs.

    La TUI pasaba emit_notifications=False / mirror_telegram=False, asi que las
    respuestas nunca se espejaban al telefono.
    """

    async def test_chat_reply_does_not_disable_notifications(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            with patch.object(yarbis_tui, "submit_user_reply", return_value="ok") as reply:
                app = yarbis_tui.YarbisTUI()
                async with app.run_test() as pilot:
                    await pilot.pause()
                    chat = app.query_one("#chat_input", Input)
                    chat.value = "hola"
                    chat.focus()
                    await pilot.press("enter")
                    await app.workers.wait_for_complete()
                    await pilot.pause()
            reply.assert_called_once()
            self.assertNotEqual(reply.call_args.kwargs.get("emit_notifications"), False)
        finally:
            h.stop()

    async def test_cycle_and_auto_mirror_to_telegram(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            with patch.object(yarbis_tui, "run_cycle_with_output", return_value="ciclo") as cycle, \
                 patch.object(yarbis_tui, "run_auto_with_output", return_value="auto") as auto:
                app = yarbis_tui.YarbisTUI()
                async with app.run_test() as pilot:
                    await pilot.pause()
                    app.action_run_cycle()
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    app.action_run_auto()
                    await app.workers.wait_for_complete()
                    await pilot.pause()
            for mock in (cycle, auto):
                mock.assert_called_once()
                self.assertNotEqual(mock.call_args.kwargs.get("mirror_telegram"), False)
                self.assertNotEqual(mock.call_args.kwargs.get("emit_notifications"), False)
        finally:
            h.stop()


class TuiChatTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_stop_action_and_busy_guard(self):
        import yarbis_tui

        h = _TuiHarness()
        try:
            app = yarbis_tui.YarbisTUI()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_stop()
                await pilot.pause()
                # Con una operacion en curso, la entrada queda deshabilitada.
                app._set_busy(True, "ciclo")
                self.assertTrue(app.query_one("#chat_input", Input).disabled)
                app._set_busy(False)
                self.assertFalse(app.query_one("#chat_input", Input).disabled)
            h.mocks["request_stop_current_operation"].assert_called_once()
        finally:
            h.stop()


if __name__ == "__main__":
    unittest.main()
