"""UI movil: navegacion de Config y control de servicio/pulso.

El usuario no encontraba como iniciar/detener el servicio ni configurar el pulso:
existian, pero enterrados en una Config de 14 secciones en scroll continuo, con
el servicio escondido bajo el titulo "Internet y servicio" (seccion 13 de 14).
Estos tests fijan la organizacion nueva y los huecos que se cerraron.
"""

import unittest
from unittest.mock import patch

import yarbis_mobile


class SettingsNavigationTestCase(unittest.TestCase):
    def setUp(self):
        self.html = yarbis_mobile._html_page()

    def test_config_has_group_navigation(self):
        self.assertIn("settings-groups", self.html)
        for group in ("operacion", "modelo", "canales", "sistema"):
            self.assertIn(f'data-group-btn="{group}"', self.html)

    def test_every_section_belongs_to_a_group(self):
        # Si una seccion se queda sin grupo, desaparece de la UI al filtrar.
        self.assertEqual(self.html.count('<section class="section" data-group='), 14)

    def test_service_and_pulse_live_in_operacion(self):
        for anchor in ('<h2>Servicio de fondo</h2>', '<h2>Pulso proactivo</h2>'):
            index = self.html.index(anchor)
            # La etiqueta de grupo va justo antes del <h2> de la seccion.
            preceding = self.html[max(0, index - 120):index]
            self.assertIn('data-group="operacion"', preceding, anchor)

    def test_service_is_its_own_section_not_buried_in_internet(self):
        self.assertIn("<h2>Servicio de fondo</h2>", self.html)
        self.assertIn("<h2>Internet</h2>", self.html)
        self.assertNotIn("<h2>Internet y servicio</h2>", self.html)

    def test_group_filter_is_wired(self):
        self.assertIn("function applySettingsGroup", self.html)
        self.assertIn("yarbisSettingsGroup", self.html)  # recuerda la eleccion


class ServiceControlsTestCase(unittest.TestCase):
    def setUp(self):
        self.html = yarbis_mobile._html_page()

    def test_all_service_actions_are_present(self):
        for action in ("install", "start", "stop", "autostart", "remove"):
            self.assertIn(f'data-action="service-{action}"', self.html)

    def test_install_action_was_the_missing_one(self):
        # Antes no existia: no se podia dejar el servicio instalado desde el movil.
        self.assertIn('data-action="service-install"', self.html)
        self.assertIn('await action("service_install"', self.html)

    def test_pulse_form_has_every_field(self):
        for field in ("pulseEnabled", "pulseInterval", "pulseCycles", "pulseDelay", "pulseModel"):
            self.assertIn(f'id="{field}"', self.html)
        self.assertIn('data-action="save-pulse"', self.html)

    def test_home_shows_pulse_and_links_to_settings(self):
        self.assertIn("Pulso proactivo</span>", self.html)
        self.assertIn('data-action="goto-operacion"', self.html)


class ServiceBackendRoutingTestCase(unittest.TestCase):
    """Las acciones deben ir por la fachada multiplataforma, no por SCM."""

    def _run(self, action, payload=None):
        return yarbis_mobile._execute_action(action, payload or {})

    def test_start_stop_remove_use_the_facade(self):
        with patch.object(yarbis_mobile, "start_background_service", return_value="iniciado") as start, \
             patch.object(yarbis_mobile, "stop_background_service", return_value="detenido") as stop, \
             patch.object(yarbis_mobile, "remove_background_service", return_value="quitado") as remove:
            self.assertEqual(self._run("service_start")["result"], "iniciado")
            self.assertEqual(self._run("service_stop")["result"], "detenido")
            self.assertEqual(self._run("service_remove")["result"], "quitado")
        start.assert_called_once()
        stop.assert_called_once()
        remove.assert_called_once()

    def test_install_without_account_uses_the_facade(self):
        with patch.object(yarbis_mobile, "install_background_service", return_value="instalado") as facade, \
             patch.object(yarbis_mobile, "install_service") as scm:
            result = self._run("service_install", {"start_auto": True})
        self.assertEqual(result["result"], "instalado")
        facade.assert_called_once_with(autostart=True)
        scm.assert_not_called()

    def test_install_with_windows_account_keeps_the_scm_path(self):
        # La cuenta/password solo existe en SCM: no se pierde esa capacidad.
        with patch.object(yarbis_mobile.os, "name", "nt"), \
             patch.object(yarbis_mobile, "install_service", return_value="instalado con cuenta") as scm, \
             patch.object(yarbis_mobile, "install_background_service") as facade:
            result = self._run("service_install", {"account_name": ".\\yarbis", "password": "x"})
        self.assertEqual(result["result"], "instalado con cuenta")
        scm.assert_called_once()
        facade.assert_not_called()

    def test_autostart_uses_the_facade(self):
        with patch.object(yarbis_mobile, "set_background_service_autostart", return_value="ok") as auto:
            self._run("service_autostart", {"enabled": True})
        auto.assert_called_once_with(True)


class StatePayloadTestCase(unittest.TestCase):
    def test_proactive_includes_start_delay(self):
        # Faltaba en el payload: el formulario siempre mostraba el default 60.
        import memory

        state = memory.default_state()
        state["service"]["proactive"]["start_delay_seconds"] = 123
        health = yarbis_mobile._mobile_health_status_from_state(state, {"running": True})
        self.assertEqual(health["proactive"]["start_delay_seconds"], 123)

    def test_provider_label_is_not_hardcoded_to_ollama(self):
        import memory

        state = memory.default_state()
        state["model_provider"]["default"] = "puter"
        health = yarbis_mobile._mobile_health_status_from_state(state, {"running": True})
        self.assertEqual(health["model_provider"]["label"], "Puter")


if __name__ == "__main__":
    unittest.main()
