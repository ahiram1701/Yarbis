import io
import json
import unittest
from unittest.mock import patch

import agent
import memory


class ModelProviderNormalizationTestCase(unittest.TestCase):
    def test_default_state_has_new_providers(self):
        mp = memory.default_state()["model_provider"]
        self.assertIn("openai_compat", mp)
        self.assertIn("puter", mp)
        self.assertEqual(mp["openai_compat"]["api_key_env_var"], memory.DEFAULT_OPENAI_COMPAT_API_KEY_ENV_VAR)
        self.assertEqual(mp["puter"]["api_key_env_var"], memory.DEFAULT_PUTER_API_KEY_ENV_VAR)

    def test_valid_providers_includes_new(self):
        self.assertIn(memory.MODEL_PROVIDER_OPENAI_COMPAT, memory.VALID_MODEL_PROVIDERS)
        self.assertIn(memory.MODEL_PROVIDER_PUTER, memory.VALID_MODEL_PROVIDERS)

    def test_normalize_openai_compat_and_puter(self):
        n = memory.normalize_state({"model_provider": {
            "default": "openai_compat",
            "openai_compat": {"host": "https://api.groq.com/openai/v1/", "model": "llama-3.3-70b", "api_key": "k"},
            "puter": {"host": "https://x.puter.work/", "model": "gpt-4o-mini"},
        }})["model_provider"]
        self.assertEqual(n["default"], "openai_compat")
        self.assertEqual(n["openai_compat"]["host"], "https://api.groq.com/openai/v1")
        self.assertEqual(n["openai_compat"]["model"], "llama-3.3-70b")
        self.assertEqual(n["puter"]["host"], "https://x.puter.work")

    def test_invalid_default_provider_falls_back(self):
        n = memory.normalize_state({"model_provider": {"default": "bogus"}})["model_provider"]
        self.assertEqual(n["default"], memory.DEFAULT_MODEL_PROVIDER)


class ModelClientSelectionTestCase(unittest.TestCase):
    def _settings(self, provider, host=""):
        return {
            "provider": provider,
            "host": host,
            "timeout_seconds": 60,
            "api_key_env_var": "K",
            "api_key": "",
        }

    def test_build_client_per_provider(self):
        self.assertEqual(type(agent._build_model_client(self._settings("openrouter", "https://openrouter.ai/api/v1"))).__name__, "OpenRouterClient")
        self.assertEqual(type(agent._build_model_client(self._settings("openai_compat", "https://api.groq.com/openai/v1"))).__name__, "OpenRouterClient")
        self.assertEqual(type(agent._build_model_client(self._settings("puter", "https://x.puter.work"))).__name__, "PuterClient")
        self.assertEqual(type(agent._build_model_client(self._settings("ollama"))).__name__, "YarbisOllamaClient")

    def test_openai_compat_uses_configured_host(self):
        client = agent._build_model_client(self._settings("openai_compat", "https://api.deepseek.com"))
        self.assertEqual(client.host, "https://api.deepseek.com")

    def test_runtime_signature_distinguishes_providers(self):
        sig_or = agent._runtime_client_signature(self._settings("openrouter", "https://openrouter.ai/api/v1"))
        sig_puter = agent._runtime_client_signature(self._settings("puter", "https://x.puter.work"))
        self.assertEqual(sig_or[0], "openrouter")
        self.assertEqual(sig_puter[0], "puter")


class PuterClientTestCase(unittest.TestCase):
    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(self, payload):
        def _open(req, timeout=None):
            return self._FakeResp(json.dumps(payload).encode("utf-8"))
        return _open

    def test_chat_parses_content_and_tool_calls(self):
        payload = {"message": {"content": "Hola", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "save_note", "arguments": '{"text":"x"}'}}
        ]}}
        c = agent.PuterClient("https://x.puter.work", 60, "PUTER_WORKER_SECRET", api_key="secreto")
        with patch.object(agent.request, "urlopen", self._fake_urlopen(payload)):
            r = c.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(r.message.content, "Hola")
        self.assertEqual(r.message.tool_calls[0].function.name, "save_note")
        self.assertEqual(r.message.tool_calls[0].id, "call_1")

    def test_chat_handles_list_content(self):
        payload = {"message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}
        c = agent.PuterClient("https://x.puter.work", 60, "PUTER_WORKER_SECRET", api_key="s")
        with patch.object(agent.request, "urlopen", self._fake_urlopen(payload)):
            r = c.chat(model="m", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(r.message.content, "ab")

    def test_chat_requires_host_and_model(self):
        with self.assertRaises(RuntimeError):
            agent.PuterClient("", 60, "S").chat(model="m", messages=[{"role": "user", "content": "x"}])
        with self.assertRaises(RuntimeError):
            agent.PuterClient("https://x.puter.work", 60, "S").chat(model="", messages=[{"role": "user", "content": "x"}])

    def test_chat_raises_on_worker_error(self):
        c = agent.PuterClient("https://x.puter.work", 60, "S", api_key="s")
        with patch.object(agent.request, "urlopen", self._fake_urlopen({"error": "boom"})):
            with self.assertRaises(RuntimeError):
                c.chat(model="m", messages=[{"role": "user", "content": "x"}])


if __name__ == "__main__":
    unittest.main()
