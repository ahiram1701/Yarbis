import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import agent
import mcp_client
import memory
import tools

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"
FIXTURE = str(Path(__file__).resolve().parent / "_fixtures" / "mcp_echo_server.py")


class McpStateNormalizationTestCase(unittest.TestCase):
    def test_default_off(self):
        self.assertEqual(memory.default_state()["mcp"], {"enabled": False, "servers": []})

    def test_normalize_sanitizes_and_dedups(self):
        norm = memory.normalize_state({"mcp": {"enabled": True, "servers": [
            {"name": "File System!!", "transport": "stdio", "command": "npx"},
            {"name": "File System!!", "transport": "stdio", "command": "otro"},  # dup
            {"name": "remote", "transport": "http", "url": "https://x"},
            {"name": "weird", "transport": "carrier"},  # transporte -> stdio
        ]}})["mcp"]
        self.assertTrue(norm["enabled"])
        names = [s["name"] for s in norm["servers"]]
        self.assertEqual(names, ["file-system", "remote", "weird"])
        self.assertEqual(norm["servers"][2]["transport"], "stdio")


class McpToolsTestCase(unittest.TestCase):
    def _state_path(self):
        return TEST_RUNTIME_DIR / f"mcp-{uuid4().hex[:8]}.json"

    def tearDown(self):
        mcp_client.disconnect_all()

    def test_gate_blocks_connect_when_disabled(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            tools.mcp_add_server("stub", "stdio", command=sys.executable, args=FIXTURE)
            out = tools.mcp_connect("stub")
        self.assertIn("desactivada", out.lower())

    def test_add_connect_and_dispatch(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            tools.set_mcp_enabled(True)
            tools.mcp_add_server("stub", "stdio", command=sys.executable, args=FIXTURE)
            connected = tools.mcp_connect("stub")
            self.assertIn("mcp__stub__echo", connected)

            # aparece en las definiciones que se pasan al modelo
            defs = agent._current_tool_definitions()
            mcp_names = [d["function"]["name"] for d in defs if isinstance(d, dict) and d.get("function", {}).get("name", "").startswith("mcp__")]
            self.assertIn("mcp__stub__echo", mcp_names)

            listed = tools.mcp_list_tools()
            self.assertIn("mcp__stub__echo", listed)

        # el dispatch de un nombre mcp__ se rutea al cliente
        self.assertEqual(mcp_client.call_qualified("mcp__stub__echo", {"text": "z"}), "echo: z")

    def test_disabled_hides_mcp_tools_from_definitions(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            tools.set_mcp_enabled(True)
            tools.mcp_add_server("stub", "stdio", command=sys.executable, args=FIXTURE)
            tools.mcp_connect("stub")
            tools.set_mcp_enabled(False)
            defs = agent._current_tool_definitions()
        mcp_names = [d for d in defs if isinstance(d, dict) and d.get("function", {}).get("name", "").startswith("mcp__")]
        self.assertEqual(mcp_names, [])

    def test_remove_server(self):
        with patch.object(memory, "STATE_FILE", self._state_path()):
            memory.save_state(memory.default_state())
            tools.mcp_add_server("stub", "stdio", command=sys.executable, args=FIXTURE)
            self.assertIn("stub", tools.mcp_list_servers())
            self.assertIn("eliminado", tools.mcp_remove_server("stub"))
            self.assertNotIn("- stub", tools.mcp_list_servers())


class McpOpenRouterSchemaTestCase(unittest.TestCase):
    def test_dict_schema_passthrough(self):
        spec = {"type": "function", "function": {"name": "mcp__x__y", "description": "d", "parameters": {"type": "object"}}}
        self.assertEqual(agent._openrouter_tool_schema(spec), spec)


if __name__ == "__main__":
    unittest.main()
