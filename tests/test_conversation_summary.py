"""Memoria profesional — compactacion del historial (resumen rodante).

El modelo esta mockeado: ningun test hace una llamada real. Verifica que los
turnos viejos se resumen y podan, que los recientes se conservan, y que un fallo
del resumen NO pierde historial.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent
import memory


def _messages(n):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turno {i}"}
        for i in range(n)
    ]


def _fake_client(summary_text):
    return SimpleNamespace(
        chat=lambda **kwargs: SimpleNamespace(message=SimpleNamespace(content=summary_text))
    )


class CompactionTestCase(unittest.TestCase):
    def _run_compaction(self, state, summary_text="RESUMEN de lo viejo"):
        def fake_apply(_s):
            return {"model": "x"}, _fake_client(summary_text)

        def fake_txn(_label, mutate, **_kw):
            mutate(state)

        with patch.object(agent, "load_state", return_value=state), \
             patch.object(agent, "_apply_model_runtime_settings", side_effect=fake_apply), \
             patch.object(agent, "state_transaction", side_effect=fake_txn):
            return agent.maybe_compact_history()

    def test_below_threshold_does_nothing(self):
        state = {"messages": _messages(40), "conversation_summary": {"text": "", "summarized_count": 0}}
        changed = self._run_compaction(state)
        self.assertFalse(changed)
        self.assertEqual(len(state["messages"]), 40)

    def test_above_threshold_summarizes_and_prunes(self):
        state = {"messages": _messages(70), "conversation_summary": {"text": "", "summarized_count": 0}}
        changed = self._run_compaction(state, "RESUMEN: 70 turnos")
        self.assertTrue(changed)
        self.assertEqual(len(state["messages"]), memory.HISTORY_KEEP_RECENT)
        # Se conservan los MAS recientes literales.
        self.assertEqual(state["messages"][-1]["content"], "turno 69")
        self.assertEqual(state["conversation_summary"]["text"], "RESUMEN: 70 turnos")
        self.assertEqual(state["conversation_summary"]["summarized_count"], 70 - memory.HISTORY_KEEP_RECENT)
        self.assertTrue(state["conversation_summary"]["updated_at"])

    def test_summary_is_incremental(self):
        state = {
            "messages": _messages(70),
            "conversation_summary": {"text": "resumen viejo", "summarized_count": 5},
        }
        captured = {}

        def fake_apply(_s):
            def chat(**kwargs):
                captured["messages"] = kwargs.get("messages")
                return SimpleNamespace(message=SimpleNamespace(content="resumen nuevo integrado"))
            return {"model": "x"}, SimpleNamespace(chat=chat)

        with patch.object(agent, "load_state", return_value=state), \
             patch.object(agent, "_apply_model_runtime_settings", side_effect=fake_apply), \
             patch.object(agent, "state_transaction", side_effect=lambda label, mut, **k: mut(state)):
            agent.maybe_compact_history()

        # El resumen previo se le paso al modelo para integrarlo.
        self.assertIn("resumen viejo", captured["messages"][0]["content"])
        self.assertEqual(state["conversation_summary"]["summarized_count"], 5 + (70 - memory.HISTORY_KEEP_RECENT))

    def test_failed_summary_keeps_history_intact(self):
        state = {"messages": _messages(70), "conversation_summary": {"text": "", "summarized_count": 0}}
        # El modelo devuelve vacio -> NO se debe podar.
        changed = self._run_compaction(state, summary_text="")
        self.assertFalse(changed)
        self.assertEqual(len(state["messages"]), 70)

    def test_model_error_does_not_raise_or_prune(self):
        state = {"messages": _messages(70), "conversation_summary": {"text": "", "summarized_count": 0}}

        def boom(_s):
            raise RuntimeError("modelo caido")

        with patch.object(agent, "load_state", return_value=state), \
             patch.object(agent, "_apply_model_runtime_settings", side_effect=boom), \
             patch.object(agent, "state_transaction", side_effect=lambda label, mut, **k: mut(state)):
            changed = agent.maybe_compact_history()
        self.assertFalse(changed)
        self.assertEqual(len(state["messages"]), 70)


class PromptInjectionTestCase(unittest.TestCase):
    def test_summary_is_injected_into_prompt(self):
        state = memory.default_state()
        state["conversation_summary"] = {"text": "El usuario prefiere respuestas cortas.", "updated_at": "", "summarized_count": 10}
        state["messages"] = [{"role": "user", "content": "hola"}]
        second_system = agent.build_messages(state)[1]["content"]
        self.assertIn("Resumen de la conversacion previa", second_system)
        self.assertIn("respuestas cortas", second_system)


class NormalizationTestCase(unittest.TestCase):
    def test_old_state_gets_summary_block(self):
        normalized = memory.normalize_state({"messages": []})
        self.assertIn("conversation_summary", normalized)
        self.assertEqual(normalized["conversation_summary"]["text"], "")
        self.assertEqual(normalized["conversation_summary"]["summarized_count"], 0)

    def test_dead_max_messages_is_gone(self):
        self.assertFalse(hasattr(memory, "MAX_MESSAGES"))


if __name__ == "__main__":
    unittest.main()
