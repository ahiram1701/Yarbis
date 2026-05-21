import unittest
from pathlib import Path
from unittest.mock import patch

import memory
import yarbis_desktop

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class _DesktopStub:
    def __init__(self):
        self.activities = []
        self.refreshed = False

    def _append_activity(self, title: str, body: str):
        self.activities.append((title, body))

    def refresh_state_view(self):
        self.refreshed = True

    def _edit_notifications(self):
        raise AssertionError("No debe abrir notificaciones en esta prueba.")

    def _toggle_service(self):
        raise AssertionError("No debe instalar servicio en esta prueba.")

    def _start_background_job(self, *_args, **_kwargs):
        raise AssertionError("No debe ejecutar ciclo en esta prueba.")


class YarbisDesktopTestCase(unittest.TestCase):
    def test_first_run_setup_can_store_direct_ollama_api_key(self):
        state_path = TEST_RUNTIME_DIR / "desktop_first_run_ollama_key_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        settings = {
            "goal": "Usar Yarbis con Ollama Cloud",
            "name": "",
            "role": "",
            "provider": memory.MODEL_PROVIDER_OLLAMA,
            "model": "gpt-oss:120b",
            "fallback_models": "qwen3.5:2b",
            "timeout_seconds": "1200",
            "host": "https://ollama.com",
            "api_key": "ollama-secret",
            "api_key_env_var": "OLLAMA_API_KEY",
            "run_first_cycle": False,
            "open_notifications": False,
            "install_service": False,
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(yarbis_desktop, "readiness_status", return_value={}):
                app = _DesktopStub()
                yarbis_desktop.YarbisDesktop._apply_first_run_setup(app, settings)
            state = memory.load_state()

        self.assertEqual(state["model_provider"]["default"], memory.MODEL_PROVIDER_OLLAMA)
        self.assertEqual(state["ollama"]["model"], "gpt-oss:120b")
        self.assertEqual(state["ollama"]["fallback_models"], ["qwen3.5:2b"])
        self.assertEqual(state["ollama"]["host"], "https://ollama.com")
        self.assertEqual(state["ollama"]["api_key"], "ollama-secret")
        self.assertEqual(state["model_provider"]["ollama"]["api_key"], "ollama-secret")
        self.assertTrue(any("API key: guardada" in body for _title, body in app.activities))

    def test_first_run_setup_can_select_openrouter_and_store_direct_api_key(self):
        state_path = TEST_RUNTIME_DIR / "desktop_first_run_openrouter_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        settings = {
            "goal": "Usar Yarbis con OpenRouter",
            "name": "Ahiram",
            "role": "Usuario local",
            "provider": memory.MODEL_PROVIDER_OPENROUTER,
            "model": "openai/gpt-demo",
            "fallback_models": "anthropic/claude-demo",
            "timeout_seconds": "1200",
            "host": "https://openrouter.ai/api/v1",
            "api_key": "openrouter-secret",
            "api_key_env_var": "OPENROUTER_API_KEY",
            "run_first_cycle": False,
            "open_notifications": False,
            "install_service": False,
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(yarbis_desktop, "readiness_status", return_value={}):
                app = _DesktopStub()
                yarbis_desktop.YarbisDesktop._apply_first_run_setup(app, settings)
            state = memory.load_state()

        self.assertEqual(state["goal"], "Usar Yarbis con OpenRouter")
        self.assertEqual(state["model_provider"]["default"], memory.MODEL_PROVIDER_OPENROUTER)
        self.assertEqual(state["model_provider"]["openrouter"]["model"], "openai/gpt-demo")
        self.assertEqual(
            state["model_provider"]["openrouter"]["fallback_models"],
            ["anthropic/claude-demo"],
        )
        self.assertEqual(state["model_provider"]["openrouter"]["api_key"], "openrouter-secret")
        self.assertEqual(state["model_provider"]["openrouter"]["api_key_env_var"], "OPENROUTER_API_KEY")
        self.assertTrue(app.refreshed)
        self.assertTrue(any("OpenRouter" in body for _title, body in app.activities))


if __name__ == "__main__":
    unittest.main()
