import json
import io
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock, patch

import agent
import memory

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class AgentTestCase(unittest.TestCase):
    def test_run_one_cycle_does_not_advance_while_waiting_for_user_input(self):
        state_path = TEST_RUNTIME_DIR / "agent_waiting_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que tono quieres usar?",
                "reason": "Hace falta ese dato para seguir.",
            }
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(agent.client, "chat", side_effect=RuntimeError("no deberia llamarse")) as chat_mock:
                result = agent.run_one_cycle(max_steps=1)
                state = memory.load_state()

        self.assertEqual(chat_mock.call_count, 0)
        self.assertEqual(result["status"], "waiting_for_user_input")
        self.assertTrue(result["needs_user_input"])
        self.assertEqual(state["cycle_count"], 0)

    def test_run_one_cycle_persists_chat_errors(self):
        state_path = TEST_RUNTIME_DIR / "agent_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", side_effect=RuntimeError("fallo controlado")):
                result = agent.run_one_cycle(max_steps=1)

            state = memory.load_state()

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["used_tools"])
        self.assertIn("No pude consultar Ollama", state["last_result"])

    def test_run_one_cycle_explains_ollama_timeouts(self):
        state_path = TEST_RUNTIME_DIR / "agent_timeout_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", side_effect=TimeoutError("timed out")):
                result = agent.run_one_cycle(max_steps=1)

            state = memory.load_state()

        self.assertEqual(result["status"], "error")
        self.assertIn("timed out", result["content"])
        self.assertIn("YARBIS_OLLAMA_TIMEOUT_SECONDS", result["content"])
        self.assertIn(agent.MODEL, state["last_result"])

    def test_run_one_cycle_stops_when_stop_requested(self):
        state_path = TEST_RUNTIME_DIR / "agent_cancel_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "runtime": {
                "thinking": {
                    "active": True,
                    "label": "Ciclo",
                    "source": "pid:1234",
                    "started_at": "2026-05-01T12:00:00+00:00",
                    "operation_id": "ciclo-demo",
                },
                "stop_requested": {
                    "active": True,
                    "operation_id": "ciclo-demo",
                    "requested_at": "2026-05-01T12:00:01+00:00",
                    "source": "telegram",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(agent.client, "chat", side_effect=RuntimeError("no debe consultar")) as chat_mock:
                result = agent.run_one_cycle(max_steps=1)
            state = memory.load_state()

        chat_mock.assert_not_called()
        self.assertEqual(result["status"], "cancelled")
        self.assertIn("Operacion detenida", state["last_result"])

    def test_run_one_cycle_uses_persisted_ollama_model(self):
        state_path = TEST_RUNTIME_DIR / "agent_ollama_model_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Resultado final", tool_calls=[])
        )

        seeded_state = memory.normalize_state({
            "ollama": {
                "model": "llama3.2:3b",
                "timeout_seconds": agent._client_timeout_seconds,
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(agent.os.environ, {}, clear=True):
                with patch.object(agent.client, "chat", return_value=final_response) as chat_mock:
                    result = agent.run_one_cycle(max_steps=1)

        self.assertEqual(result["status"], "final")
        self.assertEqual(chat_mock.call_args.kwargs["model"], "llama3.2:3b")

    def test_run_one_cycle_can_override_model_for_single_operation(self):
        state_path = TEST_RUNTIME_DIR / "agent_model_override_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Resultado proactivo", tool_calls=[])
        )

        seeded_state = memory.normalize_state({
            "ollama": {
                "model": "llama3.2:3b",
                "timeout_seconds": agent._client_timeout_seconds,
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(agent.os.environ, {}, clear=True):
                with patch.object(agent.client, "chat", return_value=final_response) as chat_mock:
                    result = agent.run_one_cycle(max_steps=1, model_override="qwen3.5:0.8b")

        self.assertEqual(result["status"], "final")
        self.assertEqual(chat_mock.call_args.kwargs["model"], "qwen3.5:0.8b")

    def test_builds_cloud_ollama_client_with_authorization_header(self):
        with patch.dict(agent.os.environ, {"OLLAMA_API_KEY": "test-key"}, clear=False):
            with patch.object(agent, "Client", return_value=object()) as client_cls:
                agent._build_ollama_client("https://ollama.com/api", 30, "OLLAMA_API_KEY")

        client_cls.assert_called_once_with(
            timeout=30,
            host="https://ollama.com",
            headers={"Authorization": "Bearer test-key"},
        )

    def test_run_one_cycle_tries_fallback_model_after_primary_failure(self):
        state_path = TEST_RUNTIME_DIR / "agent_ollama_fallback_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Fallback respondio", tool_calls=[])
        )

        seeded_state = memory.normalize_state({
            "ollama": {
                "model": "modelo-local-roto:latest",
                "fallback_models": ["gpt-oss:120b-cloud"],
                "timeout_seconds": agent._client_timeout_seconds,
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(agent.os.environ, {}, clear=True):
                with patch.object(
                    agent.client,
                    "chat",
                    side_effect=[RuntimeError("modelo no disponible"), final_response],
                ) as chat_mock:
                    result = agent.run_one_cycle(max_steps=1)

        self.assertEqual(result["status"], "final")
        self.assertEqual(
            [call.kwargs["model"] for call in chat_mock.call_args_list],
            ["modelo-local-roto:latest", "gpt-oss:120b-cloud"],
        )

    def test_run_one_cycle_uses_openrouter_provider(self):
        state_path = TEST_RUNTIME_DIR / "agent_openrouter_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Respuesta OpenRouter", tool_calls=[])
        )
        fake_client = SimpleNamespace(chat=Mock(return_value=final_response))
        old_runtime = (
            agent.client,
            agent._client_signature,
            agent._client_timeout_seconds,
            agent.MODEL_PROVIDER,
            agent.MODEL,
        )

        seeded_state = memory.normalize_state({
            "model_provider": {
                "default": "openrouter",
                "openrouter": {
                    "model": "openai/gpt-demo",
                    "fallback_models": ["anthropic/claude-demo"],
                    "host": "https://openrouter.ai/api/v1",
                    "api_key": "stored-openrouter-key",
                    "api_key_env_var": "OPENROUTER_API_KEY",
                    "timeout_seconds": 900,
                },
            },
        })

        try:
            with patch.object(memory, "STATE_FILE", state_path):
                memory.save_state(seeded_state)
                with patch.dict(agent.os.environ, {}, clear=True):
                    with patch.object(agent, "_build_openrouter_client", return_value=fake_client) as build_mock:
                        result = agent.run_one_cycle(max_steps=1)
        finally:
            (
                agent.client,
                agent._client_signature,
                agent._client_timeout_seconds,
                agent.MODEL_PROVIDER,
                agent.MODEL,
            ) = old_runtime

        self.assertEqual(result["status"], "final")
        build_mock.assert_called_once_with(
            "https://openrouter.ai/api/v1",
            900,
            "OPENROUTER_API_KEY",
            "stored-openrouter-key",
        )
        self.assertEqual(fake_client.chat.call_args.kwargs["model"], "openai/gpt-demo")
        self.assertEqual(agent.MODEL_PROVIDER, old_runtime[3])

    def test_openrouter_client_normalizes_chat_completion_response_and_tool_calls(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "name": "add_task",
                                            "arguments": "{\"title\":\"Demo\"}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }).encode("utf-8")

        def sample_tool(title: str, done: bool = False):
            """Guarda una tarea."""
            return title

        with patch.dict(agent.os.environ, {}, clear=True):
            with patch.object(agent.request, "urlopen", return_value=FakeResponse()) as urlopen_mock:
                response = agent.OpenRouterClient(
                    "https://openrouter.ai/api/v1/chat/completions",
                    30,
                    "OPENROUTER_API_KEY",
                    api_key="stored-openrouter-key",
                ).chat(
                    model="openai/gpt-demo",
                    messages=[{"role": "user", "content": "hola"}],
                    tools=[sample_tool],
                )

        request_arg = urlopen_mock.call_args.args[0]
        payload = json.loads(request_arg.data.decode("utf-8"))
        self.assertEqual(request_arg.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request_arg.get_header("Authorization"), "Bearer stored-openrouter-key")
        self.assertEqual(payload["model"], "openai/gpt-demo")
        self.assertEqual(payload["tools"][0]["function"]["name"], "sample_tool")
        self.assertEqual(response.message.tool_calls[0].id, "call-1")
        self.assertEqual(response.message.tool_calls[0].function.name, "add_task")

    def test_openrouter_client_retries_http_429_once(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({
                    "choices": [
                        {"message": {"content": "ok", "tool_calls": []}}
                    ]
                }).encode("utf-8")

        error_body = json.dumps({
            "error": {"message": "Provider returned error"}
        }).encode("utf-8")
        http_error = HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            429,
            "Too Many Requests",
            {"Retry-After": "0"},
            io.BytesIO(error_body),
        )

        with patch.dict(agent.os.environ, {}, clear=True):
            with patch.object(agent.request, "urlopen", side_effect=[http_error, FakeResponse()]) as urlopen_mock:
                with patch.object(agent.time, "sleep") as sleep_mock:
                    response = agent.OpenRouterClient(
                        "https://openrouter.ai/api/v1",
                        30,
                        "OPENROUTER_API_KEY",
                        api_key="stored-openrouter-key",
                    ).chat(
                        model="openai/gpt-demo",
                        messages=[{"role": "user", "content": "hola"}],
                    )

        self.assertEqual(response.message.content, "ok")
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once()

    def test_run_one_cycle_tries_openrouter_fallback_after_rate_limit(self):
        state_path = TEST_RUNTIME_DIR / "agent_openrouter_fallback_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Fallback OpenRouter", tool_calls=[])
        )
        fake_client = SimpleNamespace(
            chat=Mock(side_effect=[
                agent.OpenRouterHTTPError(429, "Provider returned error"),
                final_response,
            ])
        )
        old_runtime = (
            agent.client,
            agent._client_signature,
            agent._client_timeout_seconds,
            agent.MODEL_PROVIDER,
            agent.MODEL,
        )
        seeded_state = memory.normalize_state({
            "model_provider": {
                "default": "openrouter",
                "openrouter": {
                    "model": "openai/rate-limited",
                    "fallback_models": ["anthropic/claude-demo"],
                    "host": "https://openrouter.ai/api/v1",
                    "api_key": "stored-openrouter-key",
                    "timeout_seconds": 900,
                },
            },
        })

        try:
            with patch.object(memory, "STATE_FILE", state_path):
                memory.save_state(seeded_state)
                with patch.dict(agent.os.environ, {}, clear=True):
                    with patch.object(agent, "_build_openrouter_client", return_value=fake_client):
                        result = agent.run_one_cycle(max_steps=1)
        finally:
            (
                agent.client,
                agent._client_signature,
                agent._client_timeout_seconds,
                agent.MODEL_PROVIDER,
                agent.MODEL,
            ) = old_runtime

        self.assertEqual(result["status"], "final")
        self.assertEqual(result["content"], "Fallback OpenRouter")
        self.assertEqual(
            [call.kwargs["model"] for call in fake_client.chat.call_args_list],
            ["openai/rate-limited", "anthropic/claude-demo"],
        )

    def test_run_one_cycle_explains_openrouter_rate_limit_without_raw_provider_error(self):
        state_path = TEST_RUNTIME_DIR / "agent_openrouter_429_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fake_client = SimpleNamespace(
            chat=Mock(side_effect=agent.OpenRouterHTTPError(429, "Provider returned error"))
        )
        old_runtime = (
            agent.client,
            agent._client_signature,
            agent._client_timeout_seconds,
            agent.MODEL_PROVIDER,
            agent.MODEL,
        )
        seeded_state = memory.normalize_state({
            "model_provider": {
                "default": "openrouter",
                "openrouter": {
                    "model": "openai/rate-limited",
                    "fallback_models": ["anthropic/rate-limited"],
                    "host": "https://openrouter.ai/api/v1",
                    "api_key": "stored-openrouter-key",
                    "timeout_seconds": 900,
                },
            },
        })

        try:
            with patch.object(memory, "STATE_FILE", state_path):
                memory.save_state(seeded_state)
                with patch.dict(agent.os.environ, {}, clear=True):
                    with patch.object(agent, "_build_openrouter_client", return_value=fake_client):
                        result = agent.run_one_cycle(max_steps=1)
                state = memory.load_state()
        finally:
            (
                agent.client,
                agent._client_signature,
                agent._client_timeout_seconds,
                agent.MODEL_PROVIDER,
                agent.MODEL,
            ) = old_runtime

        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 429", result["content"])
        self.assertIn("limite temporal", result["content"])
        self.assertIn("fallbacks", result["content"].lower())
        self.assertNotIn("Provider returned error", result["content"])
        self.assertNotIn("Provider returned error", state["last_result"])

    def test_build_messages_includes_personal_context(self):
        state = memory.normalize_state({
            "goal": "Organizar la semana",
            "profile": {"name": "Ahiram", "preferences": ["local first"]},
            "tasks": [{"id": "task-1", "title": "Definir prioridades", "status": "pending"}],
            "notes": [{"id": "note-1", "title": "Rutina", "content": "Planificar cada lunes"}],
            "internet": {"mode": "auto"},
            "self_knowledge": {
                "summary": "Codigo fuente:\n- agent.py\n\nEntorno actual:\nArquitectura: AMD64",
                "source_signature": "source-sig",
            },
        })

        messages = agent.build_messages(state)

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("agente inteligente personal", messages[0]["content"])
        self.assertIn("Yarbis eres tu, el asistente", messages[0]["content"])
        self.assertIn("resume con criterio", messages[0]["content"])
        self.assertIn("Tu nombre es Yarbis", messages[1]["content"])
        self.assertIn("Yarbis es el asistente, no el usuario", messages[1]["content"])
        self.assertIn("no uses Yarbis como nombre del usuario", messages[1]["content"])
        self.assertIn("Interfaz, Telegram y pulso proactivo", messages[1]["content"])
        self.assertIn("mismas herramientas del agente", messages[1]["content"])
        self.assertIn("state.json", messages[1]["content"])
        self.assertIn("respuestas completas", messages[1]["content"])
        self.assertIn("Todo aprendizaje estable", messages[1]["content"])
        self.assertIn("Ahiram", messages[1]["content"])
        self.assertIn("Definir prioridades", messages[1]["content"])
        self.assertIn("Internet: modo=auto", messages[1]["content"])
        self.assertIn("Autoconocimiento de Yarbis", messages[1]["content"])
        self.assertIn("Codigo fuente", messages[1]["content"])
        self.assertIn("AMD64", messages[0]["content"])
        self.assertIn("no procesador AMD", messages[0]["content"])

    def test_format_local_temporal_context_uses_fixed_local_time(self):
        fixed_now = datetime(2026, 5, 16, 3, 33, 19, tzinfo=timezone(timedelta(hours=-6)))

        temporal_context = agent._format_local_temporal_context(fixed_now)

        self.assertIn("Contexto temporal local:", temporal_context)
        self.assertIn("Fecha local: 2026-05-16", temporal_context)
        self.assertIn("Hora local: 03:33:19", temporal_context)
        self.assertIn("Dia local: sabado", temporal_context)
        self.assertIn("Zona horaria local: UTC-06:00", temporal_context)
        self.assertIn("Referencia UTC: 2026-05-16T09:33:19+00:00", temporal_context)
        self.assertIn("interpretar hoy, manana, ayer", temporal_context)

    def test_build_messages_includes_local_temporal_context(self):
        state = memory.normalize_state({"goal": "Responder con la hora correcta"})
        fixed_context = (
            "Contexto temporal local:\n"
            "- Fecha local: 2026-05-16\n"
            "- Hora local: 03:33:19\n"
            "- Dia local: sabado\n"
            "- Zona horaria local: UTC-06:00\n"
            "- Referencia UTC: 2026-05-16T09:33:19+00:00\n"
            "- Usa esta fecha y hora local para interpretar hoy, manana, ayer y horarios del usuario.\n"
            "- Los timestamps UTC del estado, eventos o autoconocimiento son solo referencias internas; "
            "no los trates como hora local del usuario."
        )

        with patch.object(agent, "_format_local_temporal_context", return_value=fixed_context):
            messages = agent.build_messages(state)

        self.assertIn("Contexto temporal local:", messages[1]["content"])
        self.assertIn("Fecha local: 2026-05-16", messages[1]["content"])
        self.assertIn("Zona horaria local: UTC-06:00", messages[1]["content"])
        self.assertIn("Contexto actual del agente:", messages[1]["content"])

    def test_run_one_cycle_does_not_address_unknown_user_as_yarbis(self):
        state_path = TEST_RUNTIME_DIR / "agent_identity_guard_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        final_response = SimpleNamespace(
            message=SimpleNamespace(
                content="Hola, Yarbis. En que puedo ayudarte hoy?",
                tool_calls=[],
            )
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", return_value=final_response):
                result = agent.run_one_cycle(max_steps=1)
                state = memory.load_state()

        self.assertEqual(result["status"], "final")
        self.assertEqual(result["content"], "Hola. En que puedo ayudarte hoy?")
        self.assertNotIn("Hola, Yarbis", state["last_result"])

    def test_identity_guard_preserves_explicit_yarbis_user_name(self):
        state = memory.normalize_state({
            "profile": {"name": "Yarbis"},
        })

        result = agent._sanitize_assistant_identity(
            "Hola, Yarbis. En que puedo ayudarte hoy?",
            state,
        )

        self.assertEqual(result, "Hola, Yarbis. En que puedo ayudarte hoy?")

    def test_identity_guard_handles_other_greetings(self):
        result = agent._sanitize_assistant_identity(
            "Buenos dias, Yarbis! Listo para ayudarte.",
            memory.default_state(),
        )

        self.assertEqual(result, "Buenos dias. Listo para ayudarte.")

    def test_web_tools_are_registered(self):
        self.assertIn("web_search", agent.available_functions)
        self.assertIn("fetch_web_page", agent.available_functions)
        self.assertIn("update_internet_settings", agent.available_functions)
        self.assertIn("self_overview", agent.available_functions)
        self.assertIn("get_note", agent.available_functions)
        self.assertIn("delete_note", agent.available_functions)
        self.assertIn("update_goal", agent.available_functions)
        self.assertIn("run_project_check", agent.available_functions)
        self.assertIn("run_system_command", agent.available_functions)
        self.assertIn("browser_automation", agent.available_functions)
        self.assertIn("create_calendar_event", agent.available_functions)
        self.assertIn("compose_email", agent.available_functions)
        self.assertIn("open_system_target", agent.available_functions)
        self.assertIn("create_memory_backup", agent.available_functions)
        self.assertIn("list_memory_backups", agent.available_functions)
        self.assertIn("inspect_memory_backup", agent.available_functions)
        self.assertIn("import_memory_backup", agent.available_functions)
        self.assertIn("social_accounts_overview", agent.available_functions)
        self.assertIn("start_social_oauth", agent.available_functions)
        self.assertIn("save_social_draft", agent.available_functions)
        self.assertIn("list_social_drafts", agent.available_functions)
        self.assertIn("prepare_social_publication", agent.available_functions)
        self.assertIn("confirm_social_publication", agent.available_functions)
        self.assertIn("open_assisted_social_post", agent.available_functions)

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

    def test_run_one_cycle_preserves_mutating_tool_side_effects(self):
        state_path = TEST_RUNTIME_DIR / "agent_mutating_tool_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        tool_call = SimpleNamespace(
            function=SimpleNamespace(
                name="add_task",
                arguments={
                    "title": "Pedir briefing",
                    "details": "Definir objetivo y audiencia",
                    "priority": "alta",
                },
            )
        )
        tool_response = SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool_call])
        )
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Tarea registrada", tool_calls=[])
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", side_effect=[tool_response, final_response]):
                result = agent.run_one_cycle(max_steps=2)
                state = memory.load_state()

        self.assertEqual(result["status"], "final")
        self.assertEqual(len(state["tasks"]), 1)
        self.assertEqual(state["tasks"][0]["title"], "Pedir briefing")
        self.assertEqual(state["tasks"][0]["priority"], "alta")

    def test_run_one_cycle_accepts_json_string_tool_arguments(self):
        state_path = TEST_RUNTIME_DIR / "agent_json_tool_args_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        tool_call = SimpleNamespace(
            function=SimpleNamespace(
                name="add_task",
                arguments=(
                    '{"title":"Pedir briefing","details":"Definir objetivo",'
                    '"priority":"alta"}'
                ),
            )
        )
        tool_response = SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool_call])
        )
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Tarea registrada", tool_calls=[])
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", side_effect=[tool_response, final_response]):
                result = agent.run_one_cycle(max_steps=2)
                state = memory.load_state()

        self.assertEqual(result["status"], "final")
        self.assertTrue(result["action_tools_used"])
        self.assertEqual(len(state["tasks"]), 1)
        self.assertEqual(state["tasks"][0]["title"], "Pedir briefing")

    def test_run_one_cycle_rejects_action_claim_after_read_only_tool(self):
        state_path = TEST_RUNTIME_DIR / "agent_read_only_action_claim_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "messages": [
                {"role": "user", "content": "Implementa una mejora pequena."},
            ],
        })
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="agent_overview", arguments={})
        )
        tool_response = SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool_call])
        )
        final_response = SimpleNamespace(
            message=SimpleNamespace(
                content="Implemente una mejora pequena en el codigo.",
                tool_calls=[],
            )
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(agent.client, "chat", side_effect=[tool_response, final_response]):
                result = agent.run_one_cycle(max_steps=2)

        self.assertEqual(result["status"], "final")
        self.assertFalse(result["action_tools_used"])
        self.assertIn("No complete una accion verificable", result["content"])
        self.assertNotIn("Implemente", result["content"])

    def test_run_one_cycle_stops_after_requesting_user_input(self):
        state_path = TEST_RUNTIME_DIR / "agent_request_input_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        tool_call = SimpleNamespace(
            function=SimpleNamespace(
                name="request_user_input",
                arguments={
                    "question": "Que nicho quieres trabajar?",
                    "reason": "Sin eso las ideas serian demasiado genericas.",
                    "missing_fields": "nicho",
                },
            )
        )
        tool_response = SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool_call])
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(
                agent.client,
                "chat",
                side_effect=[tool_response, RuntimeError("no deberia continuar")],
            ) as chat_mock:
                result = agent.run_one_cycle(max_steps=3)
                state = memory.load_state()

        self.assertEqual(chat_mock.call_count, 1)
        self.assertEqual(result["status"], "waiting_for_user_input")
        self.assertTrue(result["needs_user_input"])
        self.assertTrue(result["used_tools"])
        self.assertTrue(state["awaiting_user_input"]["pending"])
        self.assertEqual(
            state["awaiting_user_input"]["question"],
            "Que nicho quieres trabajar?",
        )

    def test_run_one_cycle_retries_menu_after_confirmed_action(self):
        state_path = TEST_RUNTIME_DIR / "agent_confirmed_action_retry_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "messages": [
                {
                    "role": "assistant",
                    "content": "Puedo optimizar la velocidad. Te gustaria que implemente esto ahora?",
                },
                {
                    "role": "user",
                    "content": (
                        "Si, hazlo\n\n"
                        "Contexto para Yarbis: respuesta afirmativa a la pregunta pendiente.\n"
                        "Interpretacion operativa: el usuario autorizo avanzar con la propuesta anterior."
                    ),
                },
            ],
        })
        menu_response = SimpleNamespace(
            message=SimpleNamespace(
                content=(
                    "Entendido. Puedo hacer varias cosas:\n\n"
                    "1. Analizar el codigo.\n"
                    "2. Correr tests.\n\n"
                    "Que prefieres que yo haga ahora?"
                ),
                tool_calls=[],
            )
        )
        tool_call = SimpleNamespace(
            function=SimpleNamespace(
                name="set_plan",
                arguments={"plan_text": "Analizar velocidad\nCorrer tests seguros"},
            )
        )
        tool_response = SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool_call])
        )
        final_response = SimpleNamespace(
            message=SimpleNamespace(content="Plan de optimizacion iniciado.", tool_calls=[])
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                agent.client,
                "chat",
                side_effect=[menu_response, tool_response, final_response],
            ) as chat_mock:
                result = agent.run_one_cycle(max_steps=3)
                state = memory.load_state()

        self.assertEqual(result["status"], "final")
        self.assertEqual(chat_mock.call_count, 3)
        self.assertFalse(state["awaiting_user_input"]["pending"])
        self.assertEqual(
            state["current_plan"],
            ["Analizar velocidad", "Correr tests seguros"],
        )
        self.assertTrue(any(
            agent.NON_ACTIONABLE_RETRY_MESSAGE in message.get("content", "")
            for message in state["messages"]
        ))

    def test_run_one_cycle_sanitizes_unsolicited_intro_without_retrying_it(self):
        state_path = TEST_RUNTIME_DIR / "agent_unsolicited_intro_retry_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "messages": [
                {"role": "user", "content": "Hola"},
            ],
        })
        intro_response = SimpleNamespace(
            message=SimpleNamespace(
                content=(
                    "Hola. Soy Yarbis, tu agente local optimizado.\n\n"
                    "Veo que el sistema operativo Windows 11 y la arquitectura "
                    "AMD Ryzen 7 con GPU estan listos.\n\n"
                    "Puedo:\n"
                    "1. Planificar tareas.\n"
                    "2. Ejecutar codigo.\n\n"
                    "Cuentame que necesitas hacer."
                ),
                tool_calls=[],
            )
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                agent.client,
                "chat",
                return_value=intro_response,
            ) as chat_mock:
                with patch.object(agent, "_print_output") as print_mock:
                    result = agent.run_one_cycle(max_steps=2)
                    state = memory.load_state()

        printed_text = "\n".join(call.args[0] for call in print_mock.call_args_list)

        self.assertEqual(result["status"], "final")
        self.assertEqual(chat_mock.call_count, 1)
        self.assertIn("sin inventar datos del equipo", result["content"])
        self.assertNotIn("Ryzen", printed_text)
        self.assertFalse(any(
            agent.NON_ACTIONABLE_RETRY_MESSAGE in message.get("content", "")
            for message in state["messages"]
        ))

    def test_run_one_cycle_replaces_unsolicited_hardware_when_retry_is_unavailable(self):
        state_path = TEST_RUNTIME_DIR / "agent_unsolicited_hardware_fallback_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "messages": [
                {"role": "user", "content": "Hola"},
            ],
        })
        intro_response = SimpleNamespace(
            message=SimpleNamespace(
                content=(
                    "Soy Yarbis. Tu Windows 11 con CPU AMD Ryzen 7 y GPU esta listo. "
                    "Como puedo ayudarte hoy?"
                ),
                tool_calls=[],
            )
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(agent.client, "chat", return_value=intro_response):
                with patch.object(agent, "_print_output") as print_mock:
                    result = agent.run_one_cycle(max_steps=1)
                    state = memory.load_state()

        printed_text = "\n".join(call.args[0] for call in print_mock.call_args_list)

        self.assertEqual(result["status"], "final")
        self.assertNotIn("Ryzen", result["content"])
        self.assertNotIn("Ryzen", printed_text)
        self.assertIn("sin inventar datos del equipo", state["last_result"])

    def test_run_one_cycle_marks_pending_input_when_questions_appear_in_list(self):
        state_path = TEST_RUNTIME_DIR / "agent_embedded_questions_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        final_response = SimpleNamespace(
            message=SimpleNamespace(
                content=(
                    "Voy a entender bien el objetivo. Para generar dinero con un agente autonomo, "
                    "primero necesito definir claramente el contexto de nuestro emprendimiento.\n\n"
                    "Necesito conocer:\n"
                    "- Que tipo de negocio o servicio deseas ofrecer?\n"
                    "- Cual es tu nicho o area de especialidad?\n"
                    "- Quien es tu cliente objetivo?\n\n"
                    "Dejame que defina estos puntos para comenzar a organizar el trabajo."
                ),
                tool_calls=[],
            )
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", return_value=final_response):
                result = agent.run_one_cycle(max_steps=1)
                state = memory.load_state()

        self.assertEqual(result["status"], "final")
        self.assertTrue(result["needs_user_input"])
        self.assertTrue(state["awaiting_user_input"]["pending"])
        self.assertEqual(
            state["awaiting_user_input"]["question"],
            "Que tipo de negocio o servicio deseas ofrecer?",
        )

    def test_run_one_cycle_marks_pending_input_when_waiting_for_instructions(self):
        state_path = TEST_RUNTIME_DIR / "agent_waiting_instructions_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        self.assertTrue(agent._looks_like_waiting_for_instructions("Quedo atento a tus indicaciones."))

        final_response = SimpleNamespace(
            message=SimpleNamespace(
                content=(
                    "Termine el ciclo actual sin encontrar una accion segura adicional. "
                    "Quedo a la espera de instrucciones del usuario."
                ),
                tool_calls=[],
            )
        )

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(agent.client, "chat", return_value=final_response):
                result = agent.run_one_cycle(max_steps=1)
                state = memory.load_state()

        self.assertEqual(result["status"], "final")
        self.assertTrue(result["needs_user_input"])
        self.assertTrue(state["awaiting_user_input"]["pending"])
        self.assertEqual(
            state["awaiting_user_input"]["question"],
            agent.WAITING_FOR_INSTRUCTIONS_QUESTION,
        )

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

    def test_run_autonomous_session_stops_after_chat_error(self):
        state = memory.default_state()

        with patch.object(agent, "load_state", return_value=state):
            with patch.object(agent, "run_one_cycle", return_value={
                "status": "error",
                "content": "No pude consultar Ollama en este ciclo: timed out",
                "used_tools": False,
                "looks_meta": False,
            }):
                completed_cycles = agent.run_autonomous_session(cycles=5)

        self.assertEqual(completed_cycles, 1)
