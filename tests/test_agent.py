import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent
import memory

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class AgentTestCase(unittest.TestCase):
    def test_run_one_cycle_persists_chat_errors(self):
        state_path = TEST_RUNTIME_DIR / "agent_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", side_effect=RuntimeError("fallo controlado")):
                agent.run_one_cycle(max_steps=1)

            state = memory.load_state()

        self.assertIn("No pude consultar Ollama", state["last_result"])

    def test_build_messages_includes_personal_context(self):
        state = memory.normalize_state({
            "goal": "Organizar la semana",
            "profile": {"name": "Ahiram", "preferences": ["local first"]},
            "tasks": [{"id": "task-1", "title": "Definir prioridades", "status": "pending"}],
            "notes": [{"id": "note-1", "title": "Rutina", "content": "Planificar cada lunes"}],
        })

        messages = agent.build_messages(state)

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("agente inteligente personal", messages[0]["content"])
        self.assertIn("Ahiram", messages[1]["content"])
        self.assertIn("Definir prioridades", messages[1]["content"])

    def test_run_one_cycle_persists_cycle_before_tools(self):
        state_path = TEST_RUNTIME_DIR / "agent_tool_cycle_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="agent_overview", arguments={})
        )
        tool_response = SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool_call])
        )
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Resultado final", tool_calls=[])
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", side_effect=[tool_response, final_response]):
                result = agent.run_one_cycle(max_steps=2)
                state = memory.load_state()

        tool_messages = [message for message in state["messages"] if message["role"] == "tool"]

        self.assertEqual(result["status"], "final")
        self.assertEqual(state["cycle_count"], 1)
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("Ciclos ejecutados: 1", tool_messages[0]["content"])

    def test_run_autonomous_session_stops_after_direct_response_without_tasks(self):
        state = memory.default_state()

        with patch.object(agent, "load_state", return_value=state):
            with patch.object(agent, "run_one_cycle", return_value={
                "status": "final",
                "content": "Aqui estan tus ideas",
                "used_tools": False,
                "looks_meta": False,
            }):
                completed_cycles = agent.run_autonomous_session(cycles=5)

        self.assertEqual(completed_cycles, 1)

    def test_run_autonomous_session_stops_when_waiting_for_user_input(self):
        initial_state = memory.default_state()
        waiting_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que nicho quieres trabajar?",
                "reason": "Hace falta contexto para seguir.",
            }
        })

        with patch.object(agent, "load_state", side_effect=[initial_state, initial_state, waiting_state]):
            with patch.object(agent, "run_one_cycle", return_value={
                "status": "final",
                "content": "Necesito un dato mas.",
                "used_tools": True,
                "looks_meta": False,
                "needs_user_input": True,
            }):
                completed_cycles = agent.run_autonomous_session(cycles=5)

        self.assertEqual(completed_cycles, 1)
