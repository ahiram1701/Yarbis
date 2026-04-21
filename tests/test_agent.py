import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import memory

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class AgentTestCase(unittest.TestCase):
    def test_run_one_cycle_persists_chat_errors(self):
        state_path = TEST_RUNTIME_DIR / "agent_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(agent.client, "chat", side_effect=RuntimeError("fallo controlado")):
                agent.run_one_cycle(max_steps=1)

            state = memory.load_state()

        self.assertIn("No pude consultar Ollama", state["last_result"])
