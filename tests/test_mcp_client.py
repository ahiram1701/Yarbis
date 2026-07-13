import sys
import unittest
from pathlib import Path

import mcp_client

FIXTURE = str(Path(__file__).resolve().parent / "_fixtures" / "mcp_echo_server.py")


class McpClientStdioTestCase(unittest.TestCase):
    def tearDown(self):
        mcp_client.disconnect_all()

    def _connect(self, name="stub"):
        cfg = {"name": name, "transport": "stdio", "command": sys.executable, "args": [FIXTURE]}
        return mcp_client.connect(cfg, timeout=15)

    def test_connect_lists_and_calls_tool(self):
        tools = self._connect()
        self.assertEqual([t["name"] for t in tools], ["echo"])
        self.assertTrue(mcp_client.is_connected("stub"))

        specs = mcp_client.tool_specs()
        names = [s["function"]["name"] for s in specs]
        self.assertIn("mcp__stub__echo", names)
        # el inputSchema del servidor se usa tal cual como parameters
        echo_spec = next(s for s in specs if s["function"]["name"] == "mcp__stub__echo")
        self.assertEqual(echo_spec["function"]["parameters"]["required"], ["text"])

        out = mcp_client.call_qualified("mcp__stub__echo", {"text": "hola"})
        self.assertEqual(out, "echo: hola")

    def test_resolve_qualified_and_call_tool(self):
        self._connect()
        server, tool = mcp_client.resolve_qualified("mcp__stub__echo")
        self.assertEqual((server, tool), ("stub", "echo"))
        self.assertEqual(mcp_client.call_tool("stub", "echo", {"text": "x"}), "echo: x")

    def test_disconnect_clears_specs(self):
        self._connect()
        self.assertTrue(mcp_client.tool_specs())
        self.assertTrue(mcp_client.disconnect("stub"))
        self.assertFalse(mcp_client.is_connected("stub"))
        self.assertEqual(mcp_client.tool_specs(), [])
        self.assertEqual(mcp_client.resolve_qualified("mcp__stub__echo"), ("", ""))

    def test_connect_bad_command_raises(self):
        cfg = {"name": "bad", "transport": "stdio", "command": "definitely-not-a-real-binary-xyz", "args": []}
        with self.assertRaises(mcp_client.MCPError):
            mcp_client.connect(cfg, timeout=5)

    def test_unknown_transport_raises(self):
        with self.assertRaises(mcp_client.MCPError):
            mcp_client.connect({"name": "z", "transport": "carrier-pigeon"}, timeout=5)


class McpResultRenderTestCase(unittest.TestCase):
    def test_render_text_blocks(self):
        result = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        self.assertEqual(mcp_client._render_tool_result(result), "a\nb")

    def test_render_error_flag(self):
        result = {"isError": True, "content": [{"type": "text", "text": "boom"}]}
        self.assertIn("error", mcp_client._render_tool_result(result).lower())

    def test_render_structured_fallback(self):
        result = {"content": [], "structuredContent": {"k": 1}}
        self.assertIn("\"k\"", mcp_client._render_tool_result(result))


class McpHttpSseParseTestCase(unittest.TestCase):
    def test_extract_jsonrpc_from_sse(self):
        class R:
            headers = {"content-type": "text/event-stream"}
            text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
        msg = mcp_client._extract_jsonrpc(R(), 1)
        self.assertEqual(msg["result"], {"ok": True})

    def test_extract_jsonrpc_from_json(self):
        class R:
            headers = {"content-type": "application/json"}
            text = '{"jsonrpc":"2.0","id":2,"result":{"v":5}}'
        msg = mcp_client._extract_jsonrpc(R(), 2)
        self.assertEqual(msg["result"], {"v": 5})


if __name__ == "__main__":
    unittest.main()
