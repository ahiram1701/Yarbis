import unittest
from unittest.mock import patch

import memory
import proactive_context as pc


class ExperienceSectionTestCase(unittest.TestCase):
    def test_experience_section_summarizes_outcomes(self):
        events = [
            {"type": "operation_finished", "label": "Responder al usuario"},
            {"type": "remote_job_failed", "label": "Pulso", "content": "timeout del modelo"},
            {"type": "yarbis_message_sent", "label": "irrelevante"},  # no es tipo de experiencia
            {"type": "proactive_pulse_timed_out", "label": "Pulso proactivo"},
        ]
        state = memory.default_state()
        state["last_result"] = "Organice las notas del proyecto."
        with patch.object(pc.activity, "read_recent_events", return_value=events):
            section = pc._render_experience_section(state)
        self.assertIn("Resultados recientes:", section)
        self.assertIn("operation_finished", section)
        self.assertIn("remote_job_failed", section)
        self.assertIn("timeout del modelo", section)
        self.assertNotIn("yarbis_message_sent", section)  # filtrado
        self.assertIn("Ultimo resultado: Organice", section)

    def test_experience_section_empty_is_safe(self):
        state = memory.default_state()
        with patch.object(pc.activity, "read_recent_events", return_value=[]):
            section = pc._render_experience_section(state)
        self.assertIn("Sin senales nuevas relevantes", section)

    def test_recent_user_corrections_ignores_ticks(self):
        tick_prefix = pc.PROACTIVE_TICK_BASE_MESSAGE.split(":", 1)[0]
        state = memory.default_state()
        state["messages"] = [
            {"role": "user", "content": tick_prefix + ": revisa objetivo..."},  # tick, se ignora
            {"role": "user", "content": "Prefiero respuestas cortas"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "No abras el navegador sin avisarme"},
        ]
        corrections = pc._recent_user_corrections(state)
        self.assertEqual(corrections, ["Prefiero respuestas cortas", "No abras el navegador sin avisarme"])


class PulseLearningTestCase(unittest.TestCase):
    def test_pulse_tick_includes_experience_and_learning_rules(self):
        state = memory.default_state()
        state["messages"] = [{"role": "user", "content": "Se mas directo"}]
        with patch.object(pc.activity, "read_recent_events", return_value=[]):
            msg = pc.build_proactive_tick_message(state)
        self.assertIn("Experiencia reciente:", msg)
        self.assertIn("Aprendizaje continuo", msg)
        self.assertIn("save_note", msg)
        self.assertIn("evolution_propose_directive", msg)
        self.assertIn("NO cambies tu conducta base sin aprobacion", msg)
        self.assertIn("Se mas directo", msg)  # correccion visible


if __name__ == "__main__":
    unittest.main()
