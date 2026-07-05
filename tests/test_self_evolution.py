import os
import unittest
from unittest.mock import patch

import memory
import proactive_context
import yarbis_service


class EvolutionStateTestCase(unittest.TestCase):
    def test_default_state_evolution_is_safe_off(self):
        evo = memory.default_state()["evolution"]
        self.assertFalse(evo["enabled"])
        self.assertEqual(evo["interval_hours"], memory.DEFAULT_EVOLUTION_INTERVAL_HOURS)
        self.assertEqual(evo["max_pending"], memory.DEFAULT_EVOLUTION_MAX_PENDING)
        self.assertEqual(evo["dimensions"], list(memory.EVOLUTION_DIMENSIONS))
        self.assertEqual(evo["directives"], [])
        self.assertEqual(evo["directives_pending"], [])

    def test_normalize_clamps_and_filters(self):
        n = memory.normalize_state({"evolution": {
            "enabled": "true",
            "interval_hours": 99999,
            "max_pending": -5,
            "dimensions": "code, behavior, bogus",
            "directives": [{"text": "corre tests antes de aplicar", "reason": "seguridad"}],
            "directives_pending": "basura",
        }})["evolution"]
        self.assertTrue(n["enabled"])
        self.assertEqual(n["interval_hours"], 168)
        self.assertEqual(n["max_pending"], 1)
        self.assertEqual(n["dimensions"], ["code", "behavior"])
        self.assertEqual(len(n["directives"]), 1)
        self.assertTrue(n["directives"][0]["id"])
        self.assertEqual(n["directives_pending"], [])

    def test_normalize_drops_empty_directives(self):
        n = memory._normalize_evolution_directives([{"text": "  "}, {"reason": "x"}, {"text": "ok"}])
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]["text"], "ok")


class EvolutionTickTestCase(unittest.TestCase):
    def test_tick_message_instructs_propose_only(self):
        state = memory.default_state()
        state["evolution"]["max_pending"] = 3
        msg = proactive_context.build_self_evolution_tick_message(state)
        self.assertIn("coding_propose_changes", msg)
        self.assertIn("NUNCA apliques", msg)
        self.assertIn("3 o mas", msg)  # respeta max_pending
        self.assertTrue(msg.startswith(proactive_context.SELF_EVOLUTION_TICK_BASE_MESSAGE.split(":", 1)[0]))


class EvolutionSettingsTestCase(unittest.TestCase):
    def test_settings_default_disabled(self):
        with patch.object(yarbis_service, "load_state", return_value=memory.default_state()):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("YARBIS_EVOLUTION", None)
                s = yarbis_service.get_self_evolution_settings()
        self.assertFalse(s["enabled"])
        self.assertEqual(s["interval_seconds"], s["interval_hours"] * 3600)

    def test_env_enables_and_state_enables(self):
        with patch.object(yarbis_service, "load_state", return_value=memory.default_state()):
            with patch.dict(os.environ, {"YARBIS_EVOLUTION": "1"}):
                self.assertTrue(yarbis_service.get_self_evolution_settings()["enabled"])
        enabled_state = memory.default_state()
        enabled_state["evolution"]["enabled"] = True
        with patch.object(yarbis_service, "load_state", return_value=enabled_state):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("YARBIS_EVOLUTION", None)
                self.assertTrue(yarbis_service.get_self_evolution_settings()["enabled"])


class EvolutionGatingTestCase(unittest.TestCase):
    def test_run_self_evolution_disabled_short_circuits(self):
        with patch.object(yarbis_service, "get_self_evolution_settings", return_value={"enabled": False}):
            self.assertEqual(yarbis_service.run_self_evolution(), "Autoevolucion desactivada.")

    def test_count_pending_proposals(self):
        state = memory.default_state()
        state["coding"]["pending_proposal_ids"] = ["a", "b", "c"]
        self.assertEqual(yarbis_service._count_pending_coding_proposals(state), 3)

    def test_inline_skips_when_max_pending_reached(self):
        state = memory.default_state()
        state["coding"]["pending_proposal_ids"] = ["a", "b"]

        class _Lock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(yarbis_service, "session_operation_lock", return_value=_Lock()):
            with patch.object(yarbis_service, "has_pending_user_question", return_value=False):
                with patch.object(yarbis_service, "load_state", return_value=state):
                    with patch.object(yarbis_service, "run_auto_with_output") as run_auto:
                        out = yarbis_service._run_self_evolution_inline({"max_pending": 2, "cycles": 4})
        self.assertIn("ya hay 2 propuesta", out)
        run_auto.assert_not_called()


if __name__ == "__main__":
    unittest.main()
