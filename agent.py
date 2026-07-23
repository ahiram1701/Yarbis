import json
import inspect
import itertools
import os
import re
import sys
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib import error as urllib_error, request
from urllib.parse import urlparse

try:
    from ollama import Client as OllamaClient

    OLLAMA_AVAILABLE = True
except ImportError:
    # El paquete `ollama` es la UNICA dependencia de terceros del core. Hacerlo
    # opcional permite correr Yarbis con solo la stdlib usando un proveedor en la
    # nube (openai_compat / puter / openrouter usan urllib), que es lo que hace
    # viable Android/Termux, una Raspberry o un VPS minimo donde ese paquete y
    # sus dependencias no se pueden instalar.
    OLLAMA_AVAILABLE = False

    class OllamaClient:  # type: ignore[no-redef]
        """Stub: falla con un mensaje claro solo si se intenta usar Ollama."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "El proveedor 'ollama' necesita el paquete `ollama` "
                "(pip install ollama). En dispositivos limitados usa un proveedor "
                "en la nube: openai_compat, puter u openrouter (solo stdlib)."
            )


import mcp_client
from intent_text import (
    looks_like_affirmative_action_reply as _looks_like_affirmative_action_reply,
    normalize_intent_text as _normalize_intent_text,
)
from memory import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_API_KEY_ENV_VAR,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_OPENROUTER_API_KEY_ENV_VAR,
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
    DEFAULT_OPENAI_COMPAT_API_KEY_ENV_VAR,
    DEFAULT_OPENAI_COMPAT_HOST,
    DEFAULT_OPENAI_COMPAT_MODEL,
    DEFAULT_OPENAI_COMPAT_TIMEOUT_SECONDS,
    DEFAULT_PUTER_API_KEY_ENV_VAR,
    DEFAULT_PUTER_HOST,
    DEFAULT_PUTER_MODEL,
    DEFAULT_PUTER_TIMEOUT_SECONDS,
    MAX_OLLAMA_TIMEOUT_SECONDS,
    MIN_OLLAMA_TIMEOUT_SECONDS,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    MODEL_PROVIDER_OPENAI_COMPAT,
    MODEL_PROVIDER_PUTER,
    VALID_MODEL_PROVIDERS,
    load_state,
    normalize_cycle_count,
    render_state_summary,
    sanitize_unicode_text,
    state_transaction,
)
from tools import (
    add_task,
    agent_overview,
    browser_automation,
    browser_open,
    browser_observe,
    browser_act,
    set_computer_control,
    set_mcp_enabled,
    mcp_add_server,
    mcp_remove_server,
    mcp_list_servers,
    mcp_connect,
    mcp_refresh_tools,
    mcp_list_tools,
    mcp_call_tool,
    set_social_confirmation,
    desktop_look,
    desktop_screen_size,
    desktop_click,
    desktop_move,
    desktop_type,
    desktop_press,
    coding_apply_and_validate,
    coding_apply_proposal,
    coding_check_proposal,
    coding_detect_validation_command,
    coding_discard_proposal,
    coding_git_diff,
    coding_git_status,
    coding_get_proposal,
    coding_list_files,
    coding_list_proposals,
    coding_propose_edits,
    coding_propose_changes,
    coding_propose_text_file,
    coding_read_text_file,
    coding_read_text_range,
    coding_run_validation,
    coding_search_text,
    coding_set_workspace,
    coding_update_validation_command,
    coding_validation_plan,
    coding_workflow_status,
    coding_workspace_overview,
    compose_email,
    create_memory_backup,
    create_calendar_event,
    create_idea_project,
    create_project_visual_board,
    delete_note,
    export_project_visual_board,
    fetch_web_page,
    get_idea_project,
    get_project_visual_board,
    get_note,
    import_memory_backup,
    list_idea_projects,
    list_yarbis_instances,
    list_files,
    list_checkpoints,
    list_memory_backups,
    list_self_code_changes,
    mark_self_code_changes_versioned,
    list_notes,
    list_project_visual_boards,
    list_tasks,
    memory_protection_status,
    open_system_target,
    read_text_file,
    request_user_input,
    restore_checkpoint,
    run_project_check,
    run_project_tests,
    run_system_command,
    save_note,
    set_plan,
    self_overview,
    record_self_insight,
    list_self_insights,
    remove_self_insight,
    send_yarbis_message,
    answer_instance_for_user,
    list_pending_user_questions,
    confirm_social_publication,
    inspect_memory_backup,
    list_recent_media,
    list_social_drafts,
    list_social_publications,
    open_assisted_social_post,
    prepare_social_publication,
    save_social_draft,
    social_accounts_overview,
    start_social_oauth,
    promote_idea_project_to_work,
    update_goal,
    update_idea_project,
    update_internet_settings,
    update_memory_protection_settings,
    update_project_visual_board,
    update_profile,
    set_timezone,
    update_task_status,
    verify_memory_backups,
    web_search,
    write_text_file,
    read_yarbis_messages,
    evolution_status,
    evolution_set_enabled,
    evolution_set_interval,
    evolution_propose_directive,
    evolution_list_pending,
    evolution_list_directives,
    evolution_apply_directive,
    evolution_discard_directive,
    evolution_propose_goal,
    evolution_propose_memory,
    evolution_list_suggestions,
    evolution_apply_suggestion,
    evolution_discard_suggestion,
    analyze_image,
    vision_status,
    vision_set_model,
    background_service_status,
    install_background_service,
    start_background_service,
    stop_background_service,
    remove_background_service,
    set_background_service_autostart,
    device_profile_overview,
    device_adaptation_suggestions,
    probe_device,
    memory_search,
    memory_consolidate,
    memory_audit,
    mesh_create_network,
    mesh_configure_relay,
    mesh_status,
    mesh_deploy_help,
    mesh_enroll,
    mesh_list_nodes,
    mesh_send,
    mesh_delegate,
    mesh_leave,
    shortcuts_create_token,
    shortcuts_revoke_token,
    shortcuts_status,
)


def _json_object_from_ollama_tool_arguments(raw_arguments: str) -> dict:
    cleaned = raw_arguments.strip()
    if not cleaned:
        return {}

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ollama devolvio argumentos de tool como JSON invalido: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("Ollama devolvio argumentos de tool que no son un objeto JSON.")
    return dict(parsed)


def _normalize_ollama_response_tool_arguments(data) -> None:
    if not isinstance(data, dict):
        return

    message = data.get("message")
    if not isinstance(message, dict):
        return

    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return

    for raw_tool_call in tool_calls:
        if not isinstance(raw_tool_call, dict):
            continue
        function = raw_tool_call.get("function")
        if not isinstance(function, dict):
            continue
        raw_arguments = function.get("arguments")
        if isinstance(raw_arguments, str):
            function["arguments"] = _json_object_from_ollama_tool_arguments(raw_arguments)


def _sanitize_model_payload(value):
    if isinstance(value, str):
        return sanitize_unicode_text(value)
    if isinstance(value, dict):
        return {
            sanitize_unicode_text(key) if isinstance(key, str) else key: _sanitize_model_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_model_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_model_payload(item) for item in value]
    return value


class YarbisOllamaClient(OllamaClient):
    def _request(self, cls, *args, stream: bool = False, **kwargs):
        if "json" in kwargs:
            kwargs = dict(kwargs)
            kwargs["json"] = _sanitize_model_payload(kwargs["json"])

        if stream:
            return super()._request(cls, *args, stream=stream, **kwargs)

        data = self._request_raw(*args, **kwargs).json()
        _normalize_ollama_response_tool_arguments(data)
        return cls(**data)


Client = YarbisOllamaClient

DEFAULT_MODEL = DEFAULT_OLLAMA_MODEL
DEFAULT_EMPTY_RESPONSE_RETRIES = 2
DEFAULT_OPENROUTER_HTTP_RETRIES = 1
DEFAULT_OPENROUTER_RETRY_DELAY_SECONDS = 1.0
WAITING_FOR_INSTRUCTIONS_QUESTION = "Que instruccion quieres que siga ahora?"
NON_ACTIONABLE_RETRY_MESSAGE = (
    "El ultimo mensaje del usuario ya autoriza avanzar con la propuesta anterior. "
    "Si tu respuesta anterior fue una presentacion generica, un menu de capacidades "
    "o menciono hardware/sistema sin que el usuario lo pidiera, descartala. Ejecuta "
    "el siguiente paso util usando herramientas cuando aplique: revisa el estado, "
    "crea plan o tareas, lee archivos o corre pruebas seguras. No vuelvas a pedir "
    "que elija entre opciones generales salvo que falte un dato privado, una decision "
    "real o un archivo concreto. No afirmes CPU, GPU, arquitectura, RAM o sistema "
    "operativo salvo que el usuario lo pida; AMD64 es una arquitectura, no una marca "
    "de procesador."
)
MAX_NON_ACTIONABLE_RETRIES = 1
DEFAULT_AUTO_RUN_SAFETY_CYCLES = 5
AUTONOMOUS_FINAL_STATUSES = {"final", "empty", "error"}
AUTONOMOUS_LOOP_CLOSING_PHRASES = (
    "silencio total",
    "no tengo nada mas que anadir",
    "no tengo absolutamente nada mas que anadir",
    "no hay nada mas que decir",
    "no tengo nada mas que decir",
    "corto aqui",
    "caso cerrado",
    "no mas respuestas",
    "no voy a anadir",
)
_CANCEL_WATCH_INTERVAL_SECONDS = 0.25
_SPANISH_WEEKDAYS = (
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
)


def _format_utc_offset(moment: datetime) -> str:
    offset = moment.utcoffset()
    if offset is None:
        return "UTC"

    total_minutes = int(offset.total_seconds() / 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _resolve_user_timezone(timezone_str: str):
    cleaned = str(timezone_str or "").strip()
    if not cleaned:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(cleaned)
    except Exception:
        return None


def _format_local_temporal_context(now: datetime | None = None, timezone_str: str = "") -> str:
    tz = _resolve_user_timezone(timezone_str)
    if tz is not None:
        local_now = (now or datetime.now(tz=tz)).astimezone(tz)
    else:
        local_now = (now or datetime.now()).astimezone()
    weekday = _SPANISH_WEEKDAYS[local_now.weekday()]
    utc_now = local_now.astimezone(timezone.utc)
    tz_name = local_now.tzname()
    timezone_text = _format_utc_offset(local_now)
    if tz_name:
        timezone_text = f"{timezone_text} ({tz_name})"

    return (
        "Contexto temporal local:\n"
        f"- Fecha local: {local_now.date().isoformat()}\n"
        f"- Hora local: {local_now.strftime('%H:%M:%S')}\n"
        f"- Dia local: {weekday}\n"
        f"- Zona horaria local: {timezone_text}\n"
        f"- Referencia UTC: {utc_now.isoformat(timespec='seconds')}\n"
        "- Usa esta fecha y hora local para interpretar hoy, manana, ayer y horarios del usuario.\n"
        "- Los timestamps UTC del estado, eventos o autoconocimiento son solo referencias internas; "
        "no los trates como hora local del usuario."
    )


def _get_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    try:
        return int(raw_value)
    except ValueError:
        return default


def _get_env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    try:
        return float(raw_value)
    except ValueError:
        return default


MODEL = os.getenv("YARBIS_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
MODEL_PROVIDER = os.getenv("YARBIS_MODEL_PROVIDER", MODEL_PROVIDER_OLLAMA).strip().lower()
if MODEL_PROVIDER not in VALID_MODEL_PROVIDERS:
    MODEL_PROVIDER = MODEL_PROVIDER_OLLAMA
OLLAMA_FALLBACK_MODELS = []
OLLAMA_HOST = os.getenv("YARBIS_OLLAMA_HOST", DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST
OLLAMA_API_KEY_ENV_VAR = (
    os.getenv("YARBIS_OLLAMA_API_KEY_ENV_VAR", DEFAULT_OLLAMA_API_KEY_ENV_VAR).strip()
    or DEFAULT_OLLAMA_API_KEY_ENV_VAR
)
OLLAMA_API_KEY = ""
OPENROUTER_FALLBACK_MODELS = []
OPENROUTER_HOST = os.getenv("YARBIS_OPENROUTER_HOST", DEFAULT_OPENROUTER_HOST).strip() or DEFAULT_OPENROUTER_HOST
OPENROUTER_API_KEY = ""
OPENROUTER_API_KEY_ENV_VAR = (
    os.getenv("YARBIS_OPENROUTER_API_KEY_ENV_VAR", DEFAULT_OPENROUTER_API_KEY_ENV_VAR).strip()
    or DEFAULT_OPENROUTER_API_KEY_ENV_VAR
)
OLLAMA_TIMEOUT_SECONDS = _get_env_int(
    "YARBIS_OLLAMA_TIMEOUT_SECONDS",
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
)
OLLAMA_TIMEOUT_SECONDS = max(
    MIN_OLLAMA_TIMEOUT_SECONDS,
    min(MAX_OLLAMA_TIMEOUT_SECONDS, OLLAMA_TIMEOUT_SECONDS),
)
OPENROUTER_TIMEOUT_SECONDS = _get_env_int(
    "YARBIS_OPENROUTER_TIMEOUT_SECONDS",
    DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
)
OPENROUTER_TIMEOUT_SECONDS = max(
    MIN_OLLAMA_TIMEOUT_SECONDS,
    min(MAX_OLLAMA_TIMEOUT_SECONDS, OPENROUTER_TIMEOUT_SECONDS),
)
EMPTY_RESPONSE_RETRIES = max(
    0,
    _get_env_int("YARBIS_EMPTY_RESPONSE_RETRIES", DEFAULT_EMPTY_RESPONSE_RETRIES),
)
OPENROUTER_HTTP_RETRIES = max(
    0,
    min(3, _get_env_int("YARBIS_OPENROUTER_HTTP_RETRIES", DEFAULT_OPENROUTER_HTTP_RETRIES)),
)
OPENROUTER_RETRY_DELAY_SECONDS = max(
    0.0,
    min(
        10.0,
        _get_env_float(
            "YARBIS_OPENROUTER_RETRY_DELAY_SECONDS",
            DEFAULT_OPENROUTER_RETRY_DELAY_SECONDS,
        ),
    ),
)
OPENROUTER_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


def _env_list(name: str) -> list[str]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return []
    return [
        item.strip()
        for item in re.split(r"[,;\n]+", raw_value)
        if item.strip()
    ]


def _normalize_host(host: str) -> str:
    cleaned = str(host).strip()
    if cleaned.endswith("/api"):
        cleaned = cleaned[:-4].rstrip("/")
    if cleaned.endswith("/chat/completions"):
        cleaned = cleaned[: -len("/chat/completions")].rstrip("/")
    return cleaned.rstrip("/")


def _host_uses_ollama_cloud(host: str) -> bool:
    hostname = urlparse(str(host).strip()).hostname or ""
    return hostname.lower().endswith("ollama.com")


def _ollama_api_key(host: str, api_key_env_var: str, configured_api_key: str = "") -> tuple[str, str]:
    cleaned_host = _normalize_host(host)
    cleaned_env_var = str(api_key_env_var).strip() or DEFAULT_OLLAMA_API_KEY_ENV_VAR
    _uses_cloud = _host_uses_ollama_cloud(cleaned_host) or str(MODEL).strip().lower().endswith(":cloud")
    if not _uses_cloud:
        return "", cleaned_env_var
    direct_key = os.getenv("YARBIS_OLLAMA_API_KEY", "").strip()
    if direct_key:
        return direct_key, "YARBIS_OLLAMA_API_KEY"
    saved_key = str(configured_api_key).strip()
    if saved_key:
        return saved_key, "API key guardada en Yarbis"
    return os.getenv(cleaned_env_var, "").strip(), cleaned_env_var


def _ollama_client_signature(
    host: str,
    timeout_seconds: int,
    api_key_env_var: str,
    api_key: str = "",
) -> tuple:
    cleaned_host = _normalize_host(host)
    cleaned_env_var = str(api_key_env_var).strip() or DEFAULT_OLLAMA_API_KEY_ENV_VAR
    resolved_api_key, key_source = _ollama_api_key(cleaned_host, cleaned_env_var, api_key)
    return cleaned_host, int(timeout_seconds), cleaned_env_var, key_source, resolved_api_key


def _build_ollama_client(host: str, timeout_seconds: int, api_key_env_var: str, api_key: str = ""):
    cleaned_host, _timeout_seconds, cleaned_env_var, key_source, resolved_api_key = _ollama_client_signature(
        host,
        timeout_seconds,
        api_key_env_var,
        api_key,
    )
    kwargs = {"timeout": timeout_seconds}
    if cleaned_host:
        kwargs["host"] = cleaned_host
    if resolved_api_key:
        kwargs["headers"] = {"Authorization": f"Bearer {resolved_api_key}"}
    return Client(**kwargs)


def _normalize_openrouter_host(host: str) -> str:
    cleaned = str(host).strip() or DEFAULT_OPENROUTER_HOST
    if cleaned.endswith("/chat/completions"):
        cleaned = cleaned[: -len("/chat/completions")].rstrip("/")
    return cleaned.rstrip("/")


def _normalize_worker_host(host: str) -> str:
    """URL del Worker de Puter: se usa tal cual (solo quita la barra final)."""
    return str(host or "").strip().rstrip("/")


_PROVIDER_LABELS = {
    MODEL_PROVIDER_OLLAMA: "Ollama",
    MODEL_PROVIDER_OPENROUTER: "OpenRouter",
    MODEL_PROVIDER_OPENAI_COMPAT: "OpenAI-compatible",
    MODEL_PROVIDER_PUTER: "Puter",
}


def _openrouter_api_key(api_key_env_var: str, configured_api_key: str = "") -> tuple[str, str]:
    direct_key = os.getenv("YARBIS_OPENROUTER_API_KEY", "").strip()
    if direct_key:
        return direct_key, "YARBIS_OPENROUTER_API_KEY"
    saved_key = str(configured_api_key).strip()
    if saved_key:
        return saved_key, "API key guardada en Yarbis"
    cleaned_env_var = str(api_key_env_var).strip() or DEFAULT_OPENROUTER_API_KEY_ENV_VAR
    return os.getenv(cleaned_env_var, "").strip(), cleaned_env_var


def _openrouter_client_signature(
    host: str,
    timeout_seconds: int,
    api_key_env_var: str,
    api_key: str = "",
) -> tuple:
    cleaned_host = _normalize_openrouter_host(host)
    resolved_api_key, key_source = _openrouter_api_key(api_key_env_var, api_key)
    return cleaned_host, int(timeout_seconds), str(api_key_env_var).strip(), key_source, resolved_api_key


def _json_type_for_annotation(annotation) -> dict:
    if annotation in {bool, "bool"}:
        return {"type": "boolean"}
    if annotation in {int, "int"}:
        return {"type": "integer"}
    if annotation in {float, "float"}:
        return {"type": "number"}
    if annotation in {dict, "dict"}:
        return {"type": "object"}
    if annotation in {list, tuple, set, "list", "tuple", "set"}:
        return {"type": "array", "items": {"type": "string"}}

    origin = getattr(annotation, "__origin__", None)
    if origin in {list, tuple, set}:
        return {"type": "array", "items": {"type": "string"}}
    if origin is dict:
        return {"type": "object"}
    return {"type": "string"}


def _openrouter_tool_schema(tool) -> dict:
    # Las tools MCP ya llegan como schema dict en formato function-calling.
    if isinstance(tool, dict):
        return tool
    name = getattr(tool, "__name__", "tool")
    doc = inspect.getdoc(tool) or ""
    description = doc.splitlines()[0].strip() if doc else name
    properties = {}
    required = []
    try:
        signature = inspect.signature(tool)
    except (TypeError, ValueError):
        signature = None

    if signature is not None:
        for parameter_name, parameter in signature.parameters.items():
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            properties[parameter_name] = _json_type_for_annotation(parameter.annotation)
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter_name)

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _openrouter_tools(tools) -> list[dict]:
    return [_openrouter_tool_schema(tool) for tool in (tools or [])]


def _tool_call_field(tool_call, field_name: str, default=""):
    if isinstance(tool_call, dict):
        return tool_call.get(field_name, default)
    return getattr(tool_call, field_name, default)


def _tool_call_function_field(tool_call, field_name: str, default=""):
    function = _tool_call_field(tool_call, "function", {})
    if isinstance(function, dict):
        return function.get(field_name, default)
    return getattr(function, field_name, default)


def _openrouter_messages(messages: list[dict]) -> list[dict]:
    converted = []
    fallback_call_index = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user")).strip()
        content = str(message.get("content", ""))
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id", "")).strip()
            if tool_call_id:
                converted.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content,
                })
            else:
                tool_name = str(message.get("tool_name", "tool")).strip() or "tool"
                converted.append({
                    "role": "user",
                    "content": f"Resultado previo de {tool_name}: {content}",
                })
            continue

        if role not in {"system", "user", "assistant"}:
            role = "user"

        item = {"role": role, "content": content}
        if role == "assistant" and message.get("tool_calls"):
            tool_calls = []
            for raw_tool_call in message.get("tool_calls") or []:
                call_id = str(_tool_call_field(raw_tool_call, "id", "")).strip()
                if not call_id:
                    fallback_call_index += 1
                    call_id = f"call_{fallback_call_index}"
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": str(_tool_call_function_field(raw_tool_call, "name", "")).strip(),
                        "arguments": _tool_call_function_field(raw_tool_call, "arguments", "{}"),
                    },
                })
            if tool_calls:
                item["tool_calls"] = tool_calls
        converted.append(item)
    return converted


def _openrouter_tool_calls(raw_tool_calls) -> list:
    normalized = []
    for raw_tool_call in raw_tool_calls or []:
        if not isinstance(raw_tool_call, dict):
            continue
        function = raw_tool_call.get("function", {})
        if not isinstance(function, dict):
            function = {}
        normalized.append(SimpleNamespace(
            id=str(raw_tool_call.get("id", "")).strip(),
            function=SimpleNamespace(
                name=str(function.get("name", "")).strip(),
                arguments=function.get("arguments", "{}"),
            ),
        ))
    return normalized


class OpenRouterHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str, raw_message: str = ""):
        self.status_code = int(status_code)
        self.raw_message = str(raw_message or message).strip()
        self.message = str(message).strip() or self.raw_message or "error HTTP"
        super().__init__(self._render_message())

    def _render_message(self) -> str:
        if self.status_code == 429:
            return "OpenRouter HTTP 429: limite temporal del proveedor o modelo"
        return f"OpenRouter HTTP {self.status_code}: {self.message}"


class OpenRouterRateLimitError(RuntimeError):
    def __init__(self, models: list[str] | None = None):
        self.models = [str(model).strip() for model in (models or []) if str(model).strip()]
        suffix = f" Modelos intentados: {', '.join(self.models)}." if self.models else ""
        super().__init__("OpenRouter aplico un limite temporal del proveedor o modelo." + suffix)


def _openrouter_retry_after_seconds(exc: urllib_error.HTTPError, attempt: int) -> float:
    retry_after = ""
    headers = getattr(exc, "headers", None)
    if headers is not None:
        try:
            retry_after = str(headers.get("Retry-After", "")).strip()
        except Exception:
            retry_after = ""
    if retry_after:
        try:
            return max(0.0, min(10.0, float(retry_after)))
        except ValueError:
            pass
    return min(10.0, OPENROUTER_RETRY_DELAY_SECONDS * max(1, attempt + 1))


class OpenRouterClient:
    def __init__(self, host: str, timeout_seconds: int, api_key_env_var: str, api_key: str = ""):
        self.host = _normalize_openrouter_host(host)
        self.timeout_seconds = int(timeout_seconds)
        self.api_key_env_var = str(api_key_env_var).strip() or DEFAULT_OPENROUTER_API_KEY_ENV_VAR
        self.api_key = str(api_key).strip()

    def chat(self, **kwargs):
        api_key, key_source = _openrouter_api_key(self.api_key_env_var, self.api_key)
        if not api_key:
            raise RuntimeError(f"Falta API key de OpenRouter. Define `{key_source}`.")

        model = str(kwargs.get("model", "")).strip()
        if not model:
            raise RuntimeError("No hay modelo OpenRouter configurado.")

        payload = {
            "model": model,
            "messages": _openrouter_messages(kwargs.get("messages", [])),
        }
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = _openrouter_tools(tools)

        payload = _sanitize_model_payload(payload)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.host}/chat/completions"
        http_request = request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        response_body = ""
        for attempt in range(OPENROUTER_HTTP_RETRIES + 1):
            try:
                with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                    response_body = response.read().decode("utf-8", errors="replace")
                break
            except urllib_error.HTTPError as exc:
                try:
                    error_body = exc.read().decode("utf-8", errors="replace")
                finally:
                    close_error = getattr(exc, "close", None)
                    if callable(close_error):
                        close_error()
                openrouter_error = _openrouter_http_error(exc.code, error_body)
                if (
                    openrouter_error.status_code in OPENROUTER_TRANSIENT_HTTP_CODES
                    and attempt < OPENROUTER_HTTP_RETRIES
                ):
                    time.sleep(_openrouter_retry_after_seconds(exc, attempt))
                    continue
                raise openrouter_error from exc
            except urllib_error.URLError as exc:
                raise RuntimeError(f"OpenRouter no respondio: {exc}") from exc

        data = json.loads(response_body)
        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices:
            raise RuntimeError("OpenRouter devolvio una respuesta sin choices.")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        if not isinstance(message, dict):
            message = {}
        return SimpleNamespace(
            message=SimpleNamespace(
                content=message.get("content") or "",
                tool_calls=_openrouter_tool_calls(message.get("tool_calls", [])),
            )
        )


def _openrouter_error_message(error_body: str) -> str:
    try:
        data = json.loads(error_body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message", "")).strip()
    return str(data.get("message", "")).strip()


def _openrouter_http_error(status_code: int, error_body: str) -> OpenRouterHTTPError:
    raw_message = _openrouter_error_message(error_body) or str(error_body).strip()
    message = raw_message
    if int(status_code) == 429 and "provider returned error" in raw_message.lower():
        message = "limite temporal del proveedor/modelo solicitado"
    return OpenRouterHTTPError(status_code, message, raw_message=raw_message)


def _build_openrouter_client(host: str, timeout_seconds: int, api_key_env_var: str, api_key: str = ""):
    return OpenRouterClient(host, timeout_seconds, api_key_env_var, api_key=api_key)


# User-Agent de navegador para atravesar el Cloudflare que fronta los Workers de
# Puter (bloquea "Python-urllib" con error 1010).
PUTER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class PuterClient:
    """Cliente para el proveedor Puter via un Worker pasarela (puter.js).

    Yarbis (Python) -> HTTPS -> Worker de Puter -> puter.ai.chat (500+ modelos).
    El Worker recibe {model, messages, tools} en formato OpenAI y responde
    {message:{content, tool_calls}}. `host` es la URL del Worker; `api_key` es el
    secreto compartido que valida el Worker (evita que otros gasten los creditos
    Puter del usuario).
    """

    def __init__(self, host: str, timeout_seconds: int, api_key_env_var: str, api_key: str = ""):
        self.host = _normalize_worker_host(host)
        self.timeout_seconds = int(timeout_seconds)
        self.api_key_env_var = str(api_key_env_var).strip() or DEFAULT_PUTER_API_KEY_ENV_VAR
        self.api_key = str(api_key).strip()

    def _secret(self) -> str:
        env_secret = os.getenv(self.api_key_env_var, "").strip() if self.api_key_env_var else ""
        return env_secret or self.api_key

    def chat(self, **kwargs):
        if not self.host:
            raise RuntimeError("Falta la URL del Worker de Puter. Configura el host del proveedor puter.")
        model = str(kwargs.get("model", "")).strip()
        if not model:
            raise RuntimeError("No hay modelo Puter configurado.")

        payload = {
            "model": model,
            "messages": _openrouter_messages(kwargs.get("messages", [])),
        }
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = _openrouter_tools(tools)
        payload = _sanitize_model_payload(payload)

        headers = {
            "Content-Type": "application/json",
            # Los Workers de Puter estan detras de Cloudflare, que bloquea el
            # User-Agent por defecto de urllib ("Python-urllib") con error 1010.
            # Un User-Agent de navegador evita ese bloqueo.
            "User-Agent": PUTER_USER_AGENT,
        }
        secret = self._secret()
        if secret:
            headers["X-Puter-Secret"] = secret

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = request.Request(self.host, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                error_body = ""
            raise RuntimeError(f"Puter Worker HTTP {exc.code}: {error_body[:300]}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"El Worker de Puter no respondio: {exc}") from exc

        data = json.loads(response_body)
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Puter devolvio error: {str(data.get('error'))[:300]}")
        message = data.get("message", {}) if isinstance(data, dict) else {}
        if not isinstance(message, dict):
            message = {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        return SimpleNamespace(
            message=SimpleNamespace(
                content=content or "",
                tool_calls=_openrouter_tool_calls(message.get("tool_calls", [])),
            )
        )


def _puter_client_signature(host: str, timeout_seconds: int, api_key_env_var: str, api_key: str = "") -> tuple:
    cleaned_host = _normalize_worker_host(host)
    env_secret = os.getenv(str(api_key_env_var).strip(), "").strip() if str(api_key_env_var).strip() else ""
    secret = env_secret or str(api_key).strip()
    return cleaned_host, int(timeout_seconds), str(api_key_env_var).strip(), secret


def _build_puter_client(host: str, timeout_seconds: int, api_key_env_var: str, api_key: str = ""):
    return PuterClient(host, timeout_seconds, api_key_env_var, api_key=api_key)


try:
    client = _build_ollama_client(
        OLLAMA_HOST,
        OLLAMA_TIMEOUT_SECONDS,
        OLLAMA_API_KEY_ENV_VAR,
        OLLAMA_API_KEY,
    )
except RuntimeError:
    # Sin el paquete `ollama` no hay cliente por defecto: el proveedor
    # configurado (openai_compat/puter/openrouter, solo stdlib) construye el
    # suyo en la primera resolucion de runtime.
    client = None
_client_signature = (
    MODEL_PROVIDER_OLLAMA,
    *_ollama_client_signature(
        OLLAMA_HOST,
        OLLAMA_TIMEOUT_SECONDS,
        OLLAMA_API_KEY_ENV_VAR,
        OLLAMA_API_KEY,
    ),
)
_client_timeout_seconds = OLLAMA_TIMEOUT_SECONDS
_client_lock = threading.RLock()
tool_definitions = [
    agent_overview,
    update_profile,
    set_timezone,
    update_internet_settings,
    request_user_input,
    save_note,
    list_notes,
    get_note,
    delete_note,
    create_idea_project,
    list_idea_projects,
    get_idea_project,
    update_idea_project,
    promote_idea_project_to_work,
    create_project_visual_board,
    list_project_visual_boards,
    get_project_visual_board,
    update_project_visual_board,
    export_project_visual_board,
    add_task,
    list_tasks,
    update_task_status,
    update_goal,
    set_plan,
    create_memory_backup,
    list_memory_backups,
    inspect_memory_backup,
    import_memory_backup,
    memory_protection_status,
    update_memory_protection_settings,
    verify_memory_backups,
    list_yarbis_instances,
    send_yarbis_message,
    answer_instance_for_user,
    list_pending_user_questions,
    read_yarbis_messages,
    coding_set_workspace,
    coding_workspace_overview,
    coding_workflow_status,
    coding_list_files,
    coding_read_text_file,
    coding_read_text_range,
    coding_search_text,
    coding_propose_changes,
    coding_propose_edits,
    coding_propose_text_file,
    coding_list_proposals,
    coding_get_proposal,
    coding_check_proposal,
    coding_apply_proposal,
    coding_apply_and_validate,
    coding_discard_proposal,
    coding_git_status,
    coding_git_diff,
    coding_detect_validation_command,
    coding_update_validation_command,
    coding_validation_plan,
    coding_run_validation,
    list_files,
    read_text_file,
    write_text_file,
    list_checkpoints,
    restore_checkpoint,
    list_self_code_changes,
    mark_self_code_changes_versioned,
    run_project_tests,
    run_project_check,
    run_system_command,
    web_search,
    fetch_web_page,
    browser_automation,
    browser_open,
    browser_observe,
    browser_act,
    set_computer_control,
    set_mcp_enabled,
    mcp_add_server,
    mcp_remove_server,
    mcp_list_servers,
    mcp_connect,
    mcp_refresh_tools,
    mcp_list_tools,
    mcp_call_tool,
    set_social_confirmation,
    desktop_look,
    desktop_screen_size,
    desktop_click,
    desktop_move,
    desktop_type,
    desktop_press,
    create_calendar_event,
    compose_email,
    open_system_target,
    self_overview,
    record_self_insight,
    list_self_insights,
    remove_self_insight,
    social_accounts_overview,
    start_social_oauth,
    save_social_draft,
    list_social_drafts,
    list_recent_media,
    list_social_publications,
    prepare_social_publication,
    confirm_social_publication,
    open_assisted_social_post,
    evolution_status,
    evolution_set_enabled,
    evolution_set_interval,
    evolution_propose_directive,
    evolution_list_pending,
    evolution_list_directives,
    evolution_apply_directive,
    evolution_discard_directive,
    evolution_propose_goal,
    evolution_propose_memory,
    evolution_list_suggestions,
    evolution_apply_suggestion,
    evolution_discard_suggestion,
    analyze_image,
    vision_status,
    vision_set_model,
    background_service_status,
    install_background_service,
    start_background_service,
    stop_background_service,
    remove_background_service,
    set_background_service_autostart,
    device_profile_overview,
    device_adaptation_suggestions,
    probe_device,
    memory_search,
    memory_consolidate,
    memory_audit,
    mesh_create_network,
    mesh_configure_relay,
    mesh_status,
    mesh_deploy_help,
    mesh_enroll,
    mesh_list_nodes,
    mesh_send,
    mesh_delegate,
    mesh_leave,
    shortcuts_create_token,
    shortcuts_revoke_token,
    shortcuts_status,
]

available_functions = {
    "agent_overview": agent_overview,
    "update_profile": update_profile,
    "set_timezone": set_timezone,
    "update_internet_settings": update_internet_settings,
    "request_user_input": request_user_input,
    "save_note": save_note,
    "list_notes": list_notes,
    "get_note": get_note,
    "delete_note": delete_note,
    "create_idea_project": create_idea_project,
    "list_idea_projects": list_idea_projects,
    "get_idea_project": get_idea_project,
    "update_idea_project": update_idea_project,
    "promote_idea_project_to_work": promote_idea_project_to_work,
    "create_project_visual_board": create_project_visual_board,
    "list_project_visual_boards": list_project_visual_boards,
    "get_project_visual_board": get_project_visual_board,
    "update_project_visual_board": update_project_visual_board,
    "export_project_visual_board": export_project_visual_board,
    "add_task": add_task,
    "list_tasks": list_tasks,
    "update_task_status": update_task_status,
    "update_goal": update_goal,
    "set_plan": set_plan,
    "create_memory_backup": create_memory_backup,
    "list_memory_backups": list_memory_backups,
    "inspect_memory_backup": inspect_memory_backup,
    "import_memory_backup": import_memory_backup,
    "memory_protection_status": memory_protection_status,
    "update_memory_protection_settings": update_memory_protection_settings,
    "verify_memory_backups": verify_memory_backups,
    "list_yarbis_instances": list_yarbis_instances,
    "send_yarbis_message": send_yarbis_message,
    "answer_instance_for_user": answer_instance_for_user,
    "list_pending_user_questions": list_pending_user_questions,
    "read_yarbis_messages": read_yarbis_messages,
    "coding_set_workspace": coding_set_workspace,
    "coding_workspace_overview": coding_workspace_overview,
    "coding_workflow_status": coding_workflow_status,
    "coding_list_files": coding_list_files,
    "coding_read_text_file": coding_read_text_file,
    "coding_read_text_range": coding_read_text_range,
    "coding_search_text": coding_search_text,
    "coding_propose_changes": coding_propose_changes,
    "coding_propose_edits": coding_propose_edits,
    "coding_propose_text_file": coding_propose_text_file,
    "coding_list_proposals": coding_list_proposals,
    "coding_get_proposal": coding_get_proposal,
    "coding_check_proposal": coding_check_proposal,
    "coding_apply_proposal": coding_apply_proposal,
    "coding_apply_and_validate": coding_apply_and_validate,
    "coding_discard_proposal": coding_discard_proposal,
    "coding_git_status": coding_git_status,
    "coding_git_diff": coding_git_diff,
    "coding_detect_validation_command": coding_detect_validation_command,
    "coding_update_validation_command": coding_update_validation_command,
    "coding_validation_plan": coding_validation_plan,
    "coding_run_validation": coding_run_validation,
    "list_files": list_files,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
    "list_checkpoints": list_checkpoints,
    "restore_checkpoint": restore_checkpoint,
    "list_self_code_changes": list_self_code_changes,
    "mark_self_code_changes_versioned": mark_self_code_changes_versioned,
    "run_project_tests": run_project_tests,
    "run_project_check": run_project_check,
    "run_system_command": run_system_command,
    "web_search": web_search,
    "fetch_web_page": fetch_web_page,
    "browser_automation": browser_automation,
    "browser_open": browser_open,
    "browser_observe": browser_observe,
    "browser_act": browser_act,
    "set_computer_control": set_computer_control,
    "set_mcp_enabled": set_mcp_enabled,
    "mcp_add_server": mcp_add_server,
    "mcp_remove_server": mcp_remove_server,
    "mcp_list_servers": mcp_list_servers,
    "mcp_connect": mcp_connect,
    "mcp_refresh_tools": mcp_refresh_tools,
    "mcp_list_tools": mcp_list_tools,
    "mcp_call_tool": mcp_call_tool,
    "set_social_confirmation": set_social_confirmation,
    "desktop_look": desktop_look,
    "desktop_screen_size": desktop_screen_size,
    "desktop_click": desktop_click,
    "desktop_move": desktop_move,
    "desktop_type": desktop_type,
    "desktop_press": desktop_press,
    "create_calendar_event": create_calendar_event,
    "compose_email": compose_email,
    "open_system_target": open_system_target,
    "self_overview": self_overview,
    "record_self_insight": record_self_insight,
    "list_self_insights": list_self_insights,
    "remove_self_insight": remove_self_insight,
    "social_accounts_overview": social_accounts_overview,
    "start_social_oauth": start_social_oauth,
    "save_social_draft": save_social_draft,
    "list_social_drafts": list_social_drafts,
    "list_recent_media": list_recent_media,
    "list_social_publications": list_social_publications,
    "prepare_social_publication": prepare_social_publication,
    "confirm_social_publication": confirm_social_publication,
    "open_assisted_social_post": open_assisted_social_post,
    "evolution_status": evolution_status,
    "evolution_set_enabled": evolution_set_enabled,
    "evolution_set_interval": evolution_set_interval,
    "evolution_propose_directive": evolution_propose_directive,
    "evolution_list_pending": evolution_list_pending,
    "evolution_list_directives": evolution_list_directives,
    "evolution_apply_directive": evolution_apply_directive,
    "evolution_discard_directive": evolution_discard_directive,
    "evolution_propose_goal": evolution_propose_goal,
    "evolution_propose_memory": evolution_propose_memory,
    "evolution_list_suggestions": evolution_list_suggestions,
    "evolution_apply_suggestion": evolution_apply_suggestion,
    "evolution_discard_suggestion": evolution_discard_suggestion,
    "analyze_image": analyze_image,
    "vision_status": vision_status,
    "vision_set_model": vision_set_model,
    "background_service_status": background_service_status,
    "install_background_service": install_background_service,
    "start_background_service": start_background_service,
    "stop_background_service": stop_background_service,
    "remove_background_service": remove_background_service,
    "set_background_service_autostart": set_background_service_autostart,
    "device_profile_overview": device_profile_overview,
    "device_adaptation_suggestions": device_adaptation_suggestions,
    "probe_device": probe_device,
    "memory_search": memory_search,
    "memory_consolidate": memory_consolidate,
    "memory_audit": memory_audit,
    "mesh_create_network": mesh_create_network,
    "mesh_configure_relay": mesh_configure_relay,
    "mesh_status": mesh_status,
    "mesh_deploy_help": mesh_deploy_help,
    "mesh_enroll": mesh_enroll,
    "mesh_list_nodes": mesh_list_nodes,
    "mesh_send": mesh_send,
    "mesh_delegate": mesh_delegate,
    "mesh_leave": mesh_leave,
    "shortcuts_create_token": shortcuts_create_token,
    "shortcuts_revoke_token": shortcuts_revoke_token,
    "shortcuts_status": shortcuts_status,
}

PROACTIVE_SAFE_TOOL_NAMES = {
    "agent_overview",
    "update_profile",
    "set_timezone",
    "request_user_input",
    "save_note",
    "list_notes",
    "get_note",
    "delete_note",
    "create_idea_project",
    "list_idea_projects",
    "get_idea_project",
    "update_idea_project",
    "promote_idea_project_to_work",
    "create_project_visual_board",
    "list_project_visual_boards",
    "get_project_visual_board",
    "update_project_visual_board",
    "add_task",
    "list_tasks",
    "update_task_status",
    "set_plan",
    "create_memory_backup",
    "list_memory_backups",
    "inspect_memory_backup",
    "memory_protection_status",
    "verify_memory_backups",
    "list_yarbis_instances",
    "send_yarbis_message",
    "list_pending_user_questions",
    "read_yarbis_messages",
    "self_overview",
    "list_self_insights",
    "record_self_insight",
    "mcp_list_servers",
    "mcp_list_tools",
    "coding_workspace_overview",
    "coding_workflow_status",
    "coding_list_files",
    "coding_read_text_file",
    "coding_read_text_range",
    "coding_search_text",
    "coding_list_proposals",
    "coding_get_proposal",
    "coding_check_proposal",
    "coding_git_status",
    "coding_git_diff",
    "coding_validation_plan",
    "coding_propose_changes",
    "coding_propose_edits",
    "coding_propose_text_file",
    "coding_detect_validation_command",
    "evolution_status",
    "evolution_propose_directive",
    "evolution_list_pending",
    "evolution_list_directives",
    "evolution_propose_goal",
    "evolution_propose_memory",
    "evolution_list_suggestions",
    "list_self_code_changes",
    "analyze_image",
    "vision_status",
    "background_service_status",
    "device_profile_overview",
    "device_adaptation_suggestions",
    "shortcuts_status",
    "probe_device",
    "memory_search",
    "memory_audit",
    "mesh_status",
    "mesh_list_nodes",
    "social_accounts_overview",
    "save_social_draft",
    "list_social_drafts",
    "list_recent_media",
    "list_social_publications",
    "prepare_social_publication",
}

ACTION_PROOF_TOOL_NAMES = {
    "update_profile",
    "set_timezone",
    "update_internet_settings",
    "update_memory_protection_settings",
    "request_user_input",
    "save_note",
    "delete_note",
    "create_idea_project",
    "update_idea_project",
    "promote_idea_project_to_work",
    "create_project_visual_board",
    "update_project_visual_board",
    "export_project_visual_board",
    "add_task",
    "update_task_status",
    "update_goal",
    "set_plan",
    "send_yarbis_message",
    "answer_instance_for_user",
    "create_memory_backup",
    "import_memory_backup",
    "coding_set_workspace",
    "coding_propose_changes",
    "coding_propose_edits",
    "coding_propose_text_file",
    "coding_apply_proposal",
    "coding_apply_and_validate",
    "coding_discard_proposal",
    "coding_detect_validation_command",
    "coding_update_validation_command",
    "coding_run_validation",
    "write_text_file",
    "restore_checkpoint",
    "run_project_tests",
    "run_project_check",
    "run_system_command",
    "browser_automation",
    "browser_open",
    "browser_act",
    "set_computer_control",
    "set_social_confirmation",
    "desktop_click",
    "desktop_move",
    "desktop_type",
    "desktop_press",
    "create_calendar_event",
    "compose_email",
    "open_system_target",
    "start_social_oauth",
    "save_social_draft",
    "prepare_social_publication",
    "confirm_social_publication",
    "open_assisted_social_post",
}

TOOL_FAILURE_PREFIXES = (
    "acceso denegado",
    "argumentos de tool",
    "comando del sistema con fallos",
    "comando invalido",
    "contenido demasiado grande",
    "debes indicar",
    "el comando excedio",
    "error ",
    "error:",
    "la ruta no existe",
    "modo de internet invalido",
    "no encontre",
    "no existe",
    "no es ",
    "no pude",
    "proveedor de busqueda invalido",
    "solo puedo",
    "tool no encontrada",
)


def _proactive_safe_mode() -> bool:
    return os.getenv("YARBIS_PROACTIVE_SAFE_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _mcp_tool_specs() -> list:
    """Schemas de tools MCP conectadas, solo si la capacidad esta activada."""
    try:
        if not load_state().get("mcp", {}).get("enabled"):
            return []
        return mcp_client.tool_specs()
    except Exception:
        return []


def _current_tool_definitions() -> list:
    if not _proactive_safe_mode():
        # Las tools MCP externas no participan del pulso proactivo (seguridad).
        return tool_definitions + _mcp_tool_specs()
    return [
        tool
        for tool in tool_definitions
        if getattr(tool, "__name__", "") in PROACTIVE_SAFE_TOOL_NAMES
    ]


def _normalize_timeout_seconds(value, default: int = DEFAULT_OLLAMA_TIMEOUT_SECONDS) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    return max(MIN_OLLAMA_TIMEOUT_SECONDS, min(MAX_OLLAMA_TIMEOUT_SECONDS, parsed))


def _close_ollama_client(ollama_client) -> None:
    raw_client = getattr(ollama_client, "_client", None)
    close = getattr(raw_client, "close", None)
    if callable(close):
        close()


def _recall_query_from_state(state: dict) -> str:
    """Consulta para el recall: ultimo mensaje real del usuario + objetivo."""
    goal = str(state.get("goal", "")).strip()
    messages = state.get("messages", []) if isinstance(state, dict) else []
    last_user = ""
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                content = str(message.get("content", "")).strip()
                # Ignora los ticks proactivos/autoevolucion: no son la intencion real.
                if content and not content.startswith("Pulso proactivo") and not content.startswith("Autoevolucion"):
                    last_user = content[:500]
                    break
    return f"{last_user} {goal}".strip()


def _render_recall_block(state: dict) -> str:
    """Recuerdos relevantes al turno (notas/insights/ideas), por relevancia.

    Reemplaza el volcado de "notas recientes": recupera lo pertinente a lo que el
    usuario esta pidiendo ahora. Best-effort: nunca rompe el armado del prompt.
    """
    try:
        import memory_recall

        query = _recall_query_from_state(state)
        if not query:
            return ""
        items = memory_recall.recall(state, query, k=5)
        return memory_recall.render_recall_block(items)
    except Exception:
        return ""


def _render_device_line() -> str:
    """Capacidades reales del equipo donde corre Yarbis.

    Va al prompt para que el agente planee dentro de lo posible y no proponga
    acciones que este dispositivo no puede hacer (control de escritorio sin
    pantalla, voz sin audio, navegador en Android/Termux).
    """
    try:
        import device_profile

        return device_profile.render_profile_summary()
    except Exception:
        return ""


def _close_model_client(model_client) -> None:
    try:
        close = getattr(model_client, "close", None)
        if callable(close):
            close()
            return
        _close_ollama_client(model_client)
    except Exception:
        pass


def _runtime_client_signature(settings: dict) -> tuple:
    provider = settings.get("provider", MODEL_PROVIDER_OLLAMA)
    if provider in (MODEL_PROVIDER_OPENROUTER, MODEL_PROVIDER_OPENAI_COMPAT):
        return (
            provider,
            *_openrouter_client_signature(
                settings.get("host", DEFAULT_OPENROUTER_HOST),
                settings.get("timeout_seconds", DEFAULT_OPENROUTER_TIMEOUT_SECONDS),
                settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR),
                settings.get("api_key", ""),
            ),
        )
    if provider == MODEL_PROVIDER_PUTER:
        return (
            provider,
            *_puter_client_signature(
                settings.get("host", DEFAULT_PUTER_HOST),
                settings.get("timeout_seconds", DEFAULT_PUTER_TIMEOUT_SECONDS),
                settings.get("api_key_env_var", DEFAULT_PUTER_API_KEY_ENV_VAR),
                settings.get("api_key", ""),
            ),
        )
    return (
        MODEL_PROVIDER_OLLAMA,
        *_ollama_client_signature(
            settings.get("host", DEFAULT_OLLAMA_HOST),
            settings.get("timeout_seconds", DEFAULT_OLLAMA_TIMEOUT_SECONDS),
            settings.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR),
            settings.get("api_key", ""),
        ),
    )


def _build_model_client(settings: dict):
    provider = settings.get("provider")
    if provider in (MODEL_PROVIDER_OPENROUTER, MODEL_PROVIDER_OPENAI_COMPAT):
        # openai_compat reutiliza el cliente OpenAI-compatible con su propio host.
        return _build_openrouter_client(
            settings.get("host", DEFAULT_OPENROUTER_HOST),
            settings.get("timeout_seconds", DEFAULT_OPENROUTER_TIMEOUT_SECONDS),
            settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR),
            settings.get("api_key", ""),
        )
    if provider == MODEL_PROVIDER_PUTER:
        return _build_puter_client(
            settings.get("host", DEFAULT_PUTER_HOST),
            settings.get("timeout_seconds", DEFAULT_PUTER_TIMEOUT_SECONDS),
            settings.get("api_key_env_var", DEFAULT_PUTER_API_KEY_ENV_VAR),
            settings.get("api_key", ""),
        )
    return _build_ollama_client(
        settings.get("host", DEFAULT_OLLAMA_HOST),
        settings.get("timeout_seconds", DEFAULT_OLLAMA_TIMEOUT_SECONDS),
        settings.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR),
        settings.get("api_key", ""),
    )


def cancel_active_ollama_request() -> bool:
    global client, _client_signature, _client_timeout_seconds

    with _client_lock:
        old_client = client
        _close_model_client(old_client)
        settings = {
            "provider": MODEL_PROVIDER,
            "host": OPENROUTER_HOST if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER else OLLAMA_HOST,
            "timeout_seconds": (
                OPENROUTER_TIMEOUT_SECONDS
                if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER
                else OLLAMA_TIMEOUT_SECONDS
            ),
            "api_key": (
                OPENROUTER_API_KEY
                if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER
                else OLLAMA_API_KEY
            ),
            "api_key_env_var": (
                OPENROUTER_API_KEY_ENV_VAR
                if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER
                else OLLAMA_API_KEY_ENV_VAR
            ),
        }
        client = _build_model_client(settings)
        _client_signature = _runtime_client_signature(settings)
        _client_timeout_seconds = settings["timeout_seconds"]
        return True


def _normalize_tool_arguments(raw_arguments) -> tuple[dict | None, str | None]:
    if raw_arguments is None:
        return {}, None

    if isinstance(raw_arguments, Mapping):
        return dict(raw_arguments), None

    if hasattr(raw_arguments, "model_dump"):
        dumped = raw_arguments.model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped), None

    if isinstance(raw_arguments, str):
        cleaned = raw_arguments.strip()
        if not cleaned:
            return {}, None
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            return None, f"Argumentos de tool no son JSON valido: {exc}"
        if parsed is None:
            return {}, None
        if isinstance(parsed, Mapping):
            return dict(parsed), None
        return None, "Argumentos de tool deben ser un objeto JSON."

    return None, (
        "Argumentos de tool invalidos: "
        f"se esperaba un objeto, no {type(raw_arguments).__name__}."
    )


def _tool_output_looks_successful(output) -> bool:
    normalized = _normalize_intent_text(str(output))
    if not normalized:
        return False

    return not any(
        normalized.startswith(prefix)
        for prefix in TOOL_FAILURE_PREFIXES
    )


def _tool_call_proves_action(tool_name: str, output) -> bool:
    return (
        str(tool_name).strip() in ACTION_PROOF_TOOL_NAMES
        and _tool_output_looks_successful(output)
    )


def _provider_settings_from_state(state: dict, provider: str) -> dict:
    model_provider = state.get("model_provider", {}) if isinstance(state, dict) else {}
    if not isinstance(model_provider, dict):
        model_provider = {}
    if provider == MODEL_PROVIDER_OLLAMA:
        settings = model_provider.get("ollama", state.get("ollama", {}))
        return settings if isinstance(settings, dict) else {}
    settings = model_provider.get(provider, {})
    return settings if isinstance(settings, dict) else {}


def _default_provider_from_state(state: dict) -> str:
    model_provider = state.get("model_provider", {}) if isinstance(state, dict) else {}
    if not isinstance(model_provider, dict):
        model_provider = {}
    provider = str(model_provider.get("default", MODEL_PROVIDER_OLLAMA)).strip().lower()
    if provider not in VALID_MODEL_PROVIDERS:
        provider = MODEL_PROVIDER_OLLAMA
    env_provider = os.getenv("YARBIS_MODEL_PROVIDER", "").strip().lower()
    if env_provider in VALID_MODEL_PROVIDERS:
        provider = env_provider
    return provider


def _resolve_model_runtime_settings(
    state=None,
    model_override: str | None = None,
    provider_override: str | None = None,
) -> dict:
    if state is None:
        state = load_state()

    provider = _default_provider_from_state(state if isinstance(state, dict) else {})
    cleaned_provider_override = str(provider_override or "").strip().lower()
    if cleaned_provider_override:
        provider = cleaned_provider_override if cleaned_provider_override in VALID_MODEL_PROVIDERS else provider

    provider_settings = _provider_settings_from_state(
        state if isinstance(state, dict) else {},
        provider,
    )

    if provider == MODEL_PROVIDER_OPENROUTER:
        default_model = DEFAULT_OPENROUTER_MODEL
        default_host = DEFAULT_OPENROUTER_HOST
        default_api_key_env_var = DEFAULT_OPENROUTER_API_KEY_ENV_VAR
        default_api_key = ""
        default_timeout = DEFAULT_OPENROUTER_TIMEOUT_SECONDS
        fallback_env_name = "YARBIS_OPENROUTER_FALLBACK_MODELS"
        host_env_name = "YARBIS_OPENROUTER_HOST"
        api_key_env_name = "YARBIS_OPENROUTER_API_KEY_ENV_VAR"
        timeout_env_name = "YARBIS_OPENROUTER_TIMEOUT_SECONDS"
        host_normalizer = _normalize_openrouter_host
    elif provider == MODEL_PROVIDER_OPENAI_COMPAT:
        default_model = DEFAULT_OPENAI_COMPAT_MODEL
        default_host = DEFAULT_OPENAI_COMPAT_HOST
        default_api_key_env_var = DEFAULT_OPENAI_COMPAT_API_KEY_ENV_VAR
        default_api_key = ""
        default_timeout = DEFAULT_OPENAI_COMPAT_TIMEOUT_SECONDS
        fallback_env_name = "YARBIS_OPENAI_COMPAT_FALLBACK_MODELS"
        host_env_name = "YARBIS_OPENAI_COMPAT_HOST"
        api_key_env_name = "YARBIS_OPENAI_COMPAT_API_KEY_ENV_VAR"
        timeout_env_name = "YARBIS_OPENAI_COMPAT_TIMEOUT_SECONDS"
        host_normalizer = _normalize_openrouter_host
    elif provider == MODEL_PROVIDER_PUTER:
        default_model = DEFAULT_PUTER_MODEL
        default_host = DEFAULT_PUTER_HOST
        default_api_key_env_var = DEFAULT_PUTER_API_KEY_ENV_VAR
        default_api_key = ""
        default_timeout = DEFAULT_PUTER_TIMEOUT_SECONDS
        fallback_env_name = "YARBIS_PUTER_FALLBACK_MODELS"
        host_env_name = "YARBIS_PUTER_HOST"
        api_key_env_name = "YARBIS_PUTER_API_KEY_ENV_VAR"
        timeout_env_name = "YARBIS_PUTER_TIMEOUT_SECONDS"
        host_normalizer = _normalize_worker_host
    else:
        provider = MODEL_PROVIDER_OLLAMA
        default_model = DEFAULT_MODEL
        default_host = DEFAULT_OLLAMA_HOST
        default_api_key_env_var = DEFAULT_OLLAMA_API_KEY_ENV_VAR
        default_api_key = ""
        default_timeout = DEFAULT_OLLAMA_TIMEOUT_SECONDS
        fallback_env_name = "YARBIS_OLLAMA_FALLBACK_MODELS"
        host_env_name = "YARBIS_OLLAMA_HOST"
        api_key_env_name = "YARBIS_OLLAMA_API_KEY_ENV_VAR"
        timeout_env_name = "YARBIS_OLLAMA_TIMEOUT_SECONDS"
        host_normalizer = _normalize_host

    model = str(provider_settings.get("model", default_model)).strip()
    if not model and default_model:
        model = default_model
    fallback_models = [
        str(candidate).strip()
        for candidate in provider_settings.get("fallback_models", [])
        if str(candidate).strip()
    ]
    host = host_normalizer(provider_settings.get("host", default_host))
    api_key_env_var = (
        str(provider_settings.get("api_key_env_var", default_api_key_env_var)).strip()
        or default_api_key_env_var
    )
    api_key = str(provider_settings.get("api_key", default_api_key)).strip()
    timeout_seconds = _normalize_timeout_seconds(
        provider_settings.get("timeout_seconds", default_timeout),
        default=default_timeout,
    )

    env_model = os.getenv("YARBIS_MODEL", "").strip()
    if env_model:
        model = env_model
    provider_model_env = f"YARBIS_{provider.upper()}_MODEL"
    env_provider_model = os.getenv(provider_model_env, "").strip()
    if env_provider_model:
        model = env_provider_model

    env_fallback_models = _env_list(fallback_env_name)
    if env_fallback_models:
        fallback_models = env_fallback_models

    env_host = os.getenv(host_env_name, "").strip()
    if env_host:
        host = host_normalizer(env_host)

    env_api_key_env_var = os.getenv(api_key_env_name, "").strip()
    if env_api_key_env_var:
        api_key_env_var = env_api_key_env_var

    env_timeout = os.getenv(timeout_env_name, "").strip()
    if env_timeout:
        timeout_seconds = _normalize_timeout_seconds(env_timeout, timeout_seconds)

    cleaned_model_override = str(model_override or "").strip()
    if cleaned_model_override:
        model = cleaned_model_override

    model_candidates = []
    for candidate in [model, *fallback_models]:
        if candidate and candidate not in model_candidates:
            model_candidates.append(candidate)

    return {
        "provider": provider,
        "provider_label": _PROVIDER_LABELS.get(provider, "Ollama"),
        "model": model_candidates[0] if model_candidates else model,
        "fallback_models": model_candidates[1:],
        "models": model_candidates,
        "host": host,
        "api_key": api_key,
        "api_key_env_var": api_key_env_var,
        "timeout_seconds": timeout_seconds,
    }


def _resolve_ollama_runtime_settings(state=None, model_override: str | None = None) -> dict:
    return _resolve_model_runtime_settings(
        state,
        model_override=model_override,
        provider_override=MODEL_PROVIDER_OLLAMA,
    )


def _apply_model_runtime_settings(
    state=None,
    model_override: str | None = None,
    provider_override: str | None = None,
):
    global MODEL, MODEL_PROVIDER
    global OLLAMA_FALLBACK_MODELS, OLLAMA_HOST, OLLAMA_API_KEY, OLLAMA_API_KEY_ENV_VAR, OLLAMA_TIMEOUT_SECONDS
    global OPENROUTER_FALLBACK_MODELS, OPENROUTER_HOST, OPENROUTER_API_KEY
    global OPENROUTER_API_KEY_ENV_VAR, OPENROUTER_TIMEOUT_SECONDS
    global client, _client_signature, _client_timeout_seconds

    settings = _resolve_model_runtime_settings(
        state,
        model_override=model_override,
        provider_override=provider_override,
    )
    provider = settings["provider"]
    MODEL_PROVIDER = provider
    MODEL = settings["model"]
    if provider == MODEL_PROVIDER_OPENROUTER:
        OPENROUTER_FALLBACK_MODELS = settings["fallback_models"]
        OPENROUTER_HOST = settings["host"]
        OPENROUTER_API_KEY = settings.get("api_key", "")
        OPENROUTER_API_KEY_ENV_VAR = settings["api_key_env_var"]
        OPENROUTER_TIMEOUT_SECONDS = settings["timeout_seconds"]
    else:
        OLLAMA_FALLBACK_MODELS = settings["fallback_models"]
        OLLAMA_HOST = settings["host"]
        OLLAMA_API_KEY = settings.get("api_key", "")
        OLLAMA_API_KEY_ENV_VAR = settings["api_key_env_var"]
        OLLAMA_TIMEOUT_SECONDS = settings["timeout_seconds"]

    with _client_lock:
        signature = _runtime_client_signature(settings)
        if _client_signature != signature:
            _close_model_client(client)
            client = _build_model_client(settings)
            _client_signature = signature
            _client_timeout_seconds = settings["timeout_seconds"]
        current_client = client

    return settings, current_client


def _apply_ollama_runtime_settings(state=None, model_override: str | None = None):
    return _apply_model_runtime_settings(
        state,
        model_override=model_override,
        provider_override=MODEL_PROVIDER_OLLAMA,
    )


SYSTEM_PROMPT = """
Eres Yarbis, el agente inteligente personal, autonomo, local, proactivo y todologo practico del usuario.
Tu trabajo es avanzar paso a paso hacia el objetivo del usuario, organizar el trabajo para que siga progresando entre ciclos y cuidar continuidad 24/7 cuando el servicio de fondo este activo.

Reglas:
- Se util, preciso y orientado a acciones.
- Usa herramientas cuando sea necesario.
- No inventes resultados de herramientas.
- No afirmes que creaste, modificaste, ejecutaste, apagaste, instalaste, borraste o probaste algo salvo que una herramienta haya devuelto evidencia de esa accion en este ciclo. Si no hay evidencia, dilo como pendiente o como limitacion.
- Trabaja en pasos pequenos y claros.
- Eres un agente que aprende de su propia experiencia. Aprende y adaptate al usuario de forma continua: reflexiona sobre como te fue (resultados, fallos, tus correcciones) y guarda contexto personal estable con `update_profile` y hallazgos/aprendizajes utiles con `save_note`. Estas mejoras de memoria las aplicas por ti mismo cuando son estables y de bajo riesgo.
- Cuando notes una forma mejor y estable de comportarte (una regla o estrategia), proponla con `evolution_propose_directive` y espera aprobacion: no cambies tu conducta base sin que el usuario apruebe. Las directrices aprobadas ya guian tu comportamiento.
- Si detectas un siguiente paso util, conviertelo en plan, tarea o accion concreta. No crees tareas duplicadas.
- Persigue mejora continua: revisa tu autoconocimiento, identifica limitaciones reales y propone o ejecuta mejoras pequenas cuando ayuden al objetivo.
- No prometas capacidades que no tienes. Tu autonomia depende de Ollama, del servicio activo, permisos, herramientas disponibles, politica de internet y contexto del usuario.
- Puedes usar herramientas de filesystem fuera del workspace, comandos del sistema, navegador real, calendario y correo cuando la tarea lo requiera. Hazlo con rutas/comandos concretos y reporta la evidencia devuelta por la tool.
- Si la mejor salida del ciclo es texto util para el usuario, entregalo directamente en este ciclo.
- No cortes respuestas con marcadores como "truncado". Si hay demasiado material para responder bien, resume con criterio: conserva conclusiones, decisiones, pasos accionables y detalles que el usuario necesita; indica que estas resumiendo por volumen y donde queda el detalle completo cuando exista.
- Una respuesta resumida debe seguir siendo completa para su proposito: no dejes ideas partidas, datos clave fuera ni preguntas pendientes escondidas.
- No respondas con metacomentarios como "voy a empezar", "ahora me enfoco", "mi objetivo es" o "trabajare paso a paso" si todavia no has dado un resultado util.
- Yarbis eres tu, el asistente. No llames "Yarbis" al usuario salvo que el perfil indique explicitamente que ese es su nombre; si no conoces su nombre, hablale directamente en segunda persona.
- Si el usuario solo saluda, responde al usuario sin renombrarlo: nunca empieces con "Hola, Yarbis" salvo que el perfil diga explicitamente que el usuario se llama Yarbis.
- Si el usuario pide una accion directa o responde afirmativamente a una pregunta tuya ("si", "hazlo", "adelante", "procede"), interpreta eso como permiso para avanzar. No respondas con menus de opciones ni pidas otra confirmacion general.
- Si el objetivo aun no esta aterrizado, crea un plan corto con `set_plan` y tareas concretas con `add_task`.
- Si el usuario pide cambiar o reemplazar el objetivo principal de forma explicita, usa `update_goal`. Durante un pulso proactivo, tambien puedes actualizarlo si el estado deja claro que ese es el siguiente paso correcto; si no es claro, pide confirmacion.
- Si el usuario pide mejorar tu rendimiento o velocidad, empieza con acciones verificables: revisa estado/autoconocimiento, crea plan/tareas, inspecciona codigo o configuracion relevante y corre tests seguros cuando aplique.
- Si falta un dato clave para avanzar bien (por ejemplo nicho, audiencia, tono, archivo exacto, formato o criterio de exito), no lo inventes.
- Si falta informacion publica, verificable o reciente, prioriza `web_search`, `fetch_web_page` o `browser_automation` segun haga falta antes de preguntarle al usuario.
- Usa `request_user_input` solo cuando falte contexto privado, preferencias, decisiones, archivos concretos o criterios que el usuario debe definir.
- Para ideas de producto, negocio o proyectos personales, trabaja como taller de ideas: primero abre 2-4 direcciones creativas viables, explicita supuestos, riesgos, preguntas abiertas y criterios de exito; despues ayuda a elegir una direccion y convertirla en plan.
- Guarda ideas importantes con `create_idea_project` o actualizalas con `update_idea_project`; consulta `list_idea_projects` y `get_idea_project` antes de duplicar una idea existente.
- Usa `promote_idea_project_to_work` cuando el usuario ya quiera ejecutar una idea: activa el proyecto, copia sus proximos pasos al plan y crea tareas sin duplicar.
- Si una idea esta borrosa pero puedes avanzar con supuestos razonables, crea o actualiza el proyecto con esos supuestos y deja preguntas abiertas; si falta una decision privada critica, pregunta una sola cosa concreta y detente.
- Para creacion de contenido en redes sociales, aterriza nicho, audiencia, objetivo, plataforma, tono, oferta/CTA y restricciones de marca antes de producir piezas definitivas. Puedes crear briefs, calendarios, drafts, captions, guiones, hashtags y publicaciones pendientes con las tools sociales.
- Publicar en Facebook de CUALQUIER tipo (incluido perfil personal): si `computer_control.enabled` esta activo, hazlo TODO por el navegador propio de Yarbis, sin depender de cuentas conectadas ni de la API. Flujo: `browser_open("https://www.facebook.com")` (si no hay sesion iniciada, pide al usuario loguearse una vez en esa ventana), `browser_observe` para ver los elementos clicables (por `ref`), y `browser_act` para hacer clic en "Crear publicacion", escribir el texto, adjuntar la imagen de `media_inbox` si aplica y clicar "Publicar". Clica por `ref` o `text`. Si `computer_control.enabled` esta apagado, pide activarlo con `set_computer_control(enabled=True)`.
- Confirmacion del clic sensible (Publicar/Pagar/Enviar/Eliminar): depende del ajuste. Si `computer_control.settings.confirm_sensitive` esta activo, pide el visto bueno al usuario y reintenta `browser_act` con `confirm="si"` (o el texto del boton); si esta desactivado, publica directamente sin pausar. El navegador es visible.
- La API social (`confirm_social_publication` con `PUBLICAR <id>`) es una opcion secundaria solo si hay cuentas conectadas (Facebook Pages, Instagram profesional, LinkedIn) y el usuario la prefiere; respeta `social.settings.require_confirmation` (si esta desactivado, no exige la frase). Puedes cambiarlo con `set_social_confirmation`.
- Control del SO por coordenadas (`desktop_look`, `desktop_click`, `desktop_type`, `desktop_press`) es best-effort para apps sin navegador: mira la pantalla con `desktop_look` antes de clicar. Nunca uses control de PC durante pulsos proactivos autonomos. Prefiere siempre el navegador (DOM) sobre el control por coordenadas.
- Respeta la politica de internet visible en el estado. Si el usuario pide cambiarla, usa `update_internet_settings`.
- Cuando necesites una respuesta del usuario, usa `request_user_input` con una sola pregunta clara y concreta, explica brevemente por que falta ese dato y detente. No sigas produciendo contenido que dependa de esa respuesta.
- Cuando el usuario este en una conversacion por voz, responde con frases naturales y accionables: evita listas largas si no hacen falta, deja claro el siguiente paso y formula una sola pregunta facil de contestar en voz.
- En cualquier canal, comunica con tono calido y breve por defecto: di primero el resultado util, despues el siguiente paso; omite ruido de herramientas, ciclos o diagnosticos internos salvo que el usuario los pida o expliquen un error accionable.
- No uses el autoconocimiento como saludo ni como relleno. No te presentes con listas de capacidades salvo que el usuario pregunte que puedes hacer.
- No menciones sistema operativo, CPU, GPU, RAM, arquitectura o hardware salvo que el usuario lo pida o la tarea lo requiera. Si lo mencionas, copia valores verificados literalmente desde herramientas/autoconocimiento; nunca infieras marca o modelo. `AMD64` significa arquitectura x86_64, no procesador AMD.
- Manten las tareas sincronizadas: usa `update_task_status` para moverlas a `in_progress`, `blocked` o `done`.
- Si una tarea queda frenada por falta de informacion del usuario, marcalo con `update_task_status(..., status="blocked", result="...")`.
- Para consultar o eliminar notas persistentes, usa `list_notes`, `get_note` y `delete_note`. Para recordar algo por RELEVANCIA (no por recencia) usa `memory_search`. Al guardar con `save_note`, marca `importance` (0-3) y `tags` cuando aporte, y `pinned=True` para hechos que nunca deben perderse; una nota importante o fijada no la desaloja una trivial. Si notas notas duplicadas, usa `memory_consolidate`.
- Tienes autoconocimiento local: identidad, capacidades (derivadas del registro real de herramientas, siempre al dia), mapa de codigo fuente, sistema operativo y hardware. Si necesitas refrescarlo o verlo completo, usa `self_overview`.
- Puedes conectarte a servidores MCP externos (capacidad `mcp`, apagada por defecto). Si esta activada y hay servidores conectados, sus herramientas aparecen con el nombre `mcp__<servidor>__<herramienta>` y las usas como cualquier otra tool cuando aporten. Para administrarlos: `set_mcp_enabled`, `mcp_add_server`, `mcp_connect`, `mcp_list_servers`, `mcp_list_tools`. Conectar servidores MCP arbitrarios ejecuta comandos locales o llama endpoints: hazlo solo a peticion del usuario.
- Ademas tienes un self-model APRENDIDO sobre ti mismo (fortalezas, limites recurrentes, estrategias, lecciones), distinto de las notas sobre el usuario. Cuando descubras algo estable sobre ti —sobre todo tras un fallo o una correccion en un ciclo proactivo— guardalo con `record_self_insight(text, category)` (fortaleza/limite/estrategia/leccion). Revisalo con `list_self_insights` y depuralo con `remove_self_insight`. No dupliques ni guardes trivialidades.
- El pulso proactivo puede incluir un snapshot de contexto local de la PC: presencia/idle, proceso en primer plano si esta permitido, salud del sistema y cambios recientes del workspace. Usalo solo como senal auxiliar; no lo trates como certeza absoluta ni reveles detalles sensibles si no aportan.
- El pulso proactivo del servicio corre con acceso completo a las herramientas disponibles del agente cuando el objetivo lo requiera. Si una accion depende de datos que el contexto local no entrega, obtenlos con herramientas disponibles o pide contexto al usuario.
- El servicio administrado por SCM solo inicia, detiene o registra el proceso de fondo. No digas que SCM impide usar herramientas, ver notas, actualizar tareas o ejecutar ciclos; esas acciones dependen del servicio activo, permisos del proceso y herramientas disponibles. Instalar, quitar o reconfigurar el servicio puede requerir administrador.
- Antes de actuar a ciegas, revisa el estado con `agent_overview`, `list_tasks` o `list_notes`.
- Antes de razonar sobre tu propio codigo con detalle, usa `self_overview`, `list_files` o `read_text_file` segun haga falta.
- Antes de editar archivos de codigo, lee primero el archivo actual con `read_text_file`.
- `write_text_file` crea un checkpoint automatico y devuelve un diff. Puede trabajar fuera del workspace; manten los cambios pequenos, enfocados y bien entendidos.
- Para tareas de coding en un repositorio local, usa las tools `coding_*`: configura el workspace con `coding_set_workspace`, orientate con `coding_workflow_status`, busca con `coding_search_text`, lee solo rangos relevantes con `coding_read_text_range`, inspecciona archivos completos con `coding_read_text_file` solo cuando haga falta, revisa Git con `coding_git_status`/`coding_git_diff` y genera cambios localizados preferentemente con `coding_propose_edits`. Usa `coding_propose_changes` para archivos nuevos, reemplazos completos o refactors amplios.
- El modo de coding por defecto es `propose_first`: no modifiques archivos del workspace de codigo activo con `write_text_file`; crea una propuesta multiarchivo con titulo, resumen, motivo y diffs, y espera aprobacion explicita del usuario antes de llamar `coding_apply_proposal`.
- Usa `coding_validation_plan`, `coding_detect_validation_command` o `coding_update_validation_command` para preparar validacion del repo. Antes de aplicar una propuesta, usa `coding_check_proposal` para confirmar que sigue vigente. Cuando el usuario apruebe una propuesta concreta o diga que la apliques identificando el cambio, usa `coding_apply_proposal` y despues `coding_run_validation(proposal_id=...)`, o usa `coding_apply_and_validate` si quiere ambas acciones juntas; reporta evidencia.
- Si una propuesta queda obsoleta o el usuario la rechaza, usa `coding_discard_proposal`.
- Usa `run_system_command` para comandos arbitrarios del sistema cuando una tarea lo necesite. Usa `open_system_target`, `compose_email` y `create_calendar_event` para integraciones locales con apps del sistema.
- Despues de modificar codigo o tests, ejecuta `run_project_tests`; antes de cerrar cambios grandes, usa `run_project_check` para tests Python y build .NET.
- Si un cambio rompe algo, revisa `list_checkpoints` y usa `restore_checkpoint` para volver al estado anterior.
- Cada cambio que apliques a tu PROPIO codigo fuente queda registrado automaticamente en una bitacora de auto-cambios. El usuario la revisa con `list_self_code_changes` para versionar esos cambios en git; no viven en el repositorio hasta entonces. Cuando ya esten versionados, se archiva con `mark_self_code_changes_versioned`.
- Otras instancias de Yarbis pueden quedar PAUSADAS esperando una respuesta del usuario. Cuando el usuario te pida "responde/desbloquea por mi a las instancias que me esperan" (o a una en concreto), usa `list_pending_user_questions` para ver quien esta esperando y su pregunta, y luego `answer_instance_for_user(instancia, respuesta)` en cada una, redactando la respuesta con lo que sabes del usuario. Eso desbloquea a la instancia (limpia su pausa y retoma). Las acciones sensibles de esa instancia siguen pidiendo su propia confirmacion.
- Despues de cada accion, evalua el siguiente mejor paso.
- Si una tarea ya quedo resuelta, dilo claramente y deja evidencia en el estado.
- Responde en espanol.
"""


def _print_output(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text)


def _render_learned_directives(state) -> str:
    evolution = state.get("evolution", {}) if isinstance(state, dict) else {}
    if not isinstance(evolution, dict):
        return ""
    directives = evolution.get("directives", [])
    if not isinstance(directives, list):
        return ""
    lines = []
    for item in directives:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if text:
            lines.append(f"- {text}")
        if len(lines) >= 40:
            break
    if not lines:
        return ""
    return (
        "Directrices aprendidas (aprobadas por el usuario; siguelas salvo que el "
        "usuario indique lo contrario):\n" + "\n".join(lines)
    )


def _render_messages_transcript(messages: list) -> str:
    """Convierte turnos crudos en un transcripto compacto para resumir."""
    lines = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "")).strip()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        who = {"user": "Usuario", "assistant": "Yarbis", "tool": "Herramienta"}.get(role, role or "?")
        lines.append(f"{who}: {content[:1500]}")
    return "\n".join(lines)


def _summarize_conversation(old_messages: list, previous_summary: str) -> str:
    """Resumen incremental de turnos viejos, via una llamada toolless al modelo.

    Best-effort: cualquier fallo devuelve "" y el llamador NO poda (no se pierde
    historial). No usa herramientas ni recursion.
    """
    from memory import MAX_CONVERSATION_SUMMARY_CHARS

    transcript = _render_messages_transcript(old_messages)
    if not transcript.strip():
        return ""

    instruction = (
        "Eres el modulo de memoria de Yarbis. Resume de forma fiel y compacta la "
        "siguiente parte ANTIGUA de una conversacion, para conservar continuidad de "
        "largo plazo. Integra el resumen previo si lo hay, sin repetir. Conserva: "
        "hechos y preferencias del usuario, decisiones tomadas, compromisos y "
        "pendientes, y el hilo del objetivo. Omite saludos y relleno. Escribe en "
        "espanol, en prosa breve con vinetas cuando ayude. Devuelve SOLO el resumen."
    )
    prev = f"\n\nResumen previo:\n{previous_summary}" if str(previous_summary).strip() else ""
    user_content = f"{instruction}{prev}\n\nConversacion antigua a resumir:\n{transcript}"

    try:
        _settings, model_client = _apply_model_runtime_settings(load_state())
        response = model_client.chat(
            model=_settings["model"],
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception:
        return ""

    try:
        content = response.message.content
    except AttributeError:
        content = ""
    return str(content or "").strip()[:MAX_CONVERSATION_SUMMARY_CHARS]


def maybe_compact_history() -> bool:
    """Si el historial supera el umbral, resume lo viejo y poda los turnos crudos.

    Se llama al inicio de cada ciclo (choke point unico). Barato cuando no hay que
    hacer nada (solo mide longitud). Nunca rompe el ciclo: ante un fallo del
    resumen, deja el historial intacto.
    """
    from memory import (
        HISTORY_COMPACT_THRESHOLD,
        HISTORY_KEEP_RECENT,
        MAX_CONVERSATION_SUMMARY_CHARS,
    )

    state = load_state()
    messages = state.get("messages", [])
    if not isinstance(messages, list) or len(messages) <= HISTORY_COMPACT_THRESHOLD:
        return False

    old = messages[:-HISTORY_KEEP_RECENT]
    if not old:
        return False

    previous = str(state.get("conversation_summary", {}).get("text", "")).strip()
    summary = _summarize_conversation(old, previous)
    if not summary:
        return False  # sin resumen valido no se poda: no se pierde historial

    prior_count = 0
    try:
        prior_count = int(state.get("conversation_summary", {}).get("summarized_count", 0) or 0)
    except (TypeError, ValueError):
        prior_count = 0

    def mutate(mutable_state):
        mutable_state["conversation_summary"] = {
            "text": summary[:MAX_CONVERSATION_SUMMARY_CHARS],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summarized_count": prior_count + len(old),
        }
        # Poda: conserva solo los recientes literales.
        current = mutable_state.get("messages", [])
        if isinstance(current, list) and len(current) > HISTORY_KEEP_RECENT:
            mutable_state["messages"] = current[-HISTORY_KEEP_RECENT:]

    state_transaction("compact_history", mutate)
    return True


def build_messages(state):
    user_name = str(state.get("profile", {}).get("name", "")).strip()
    user_line = (
        f"- El usuario actual es {user_name}."
        if user_name
        else "- El usuario actual no tiene nombre definido en el perfil."
    )
    memory_contract = (
        "Identidad y memoria compartida:\n"
        "- Tu nombre es Yarbis; Yarbis es el asistente, no el usuario.\n"
        f"{user_line}\n"
        "- Cuando saludes, no uses Yarbis como nombre del usuario salvo que el perfil lo diga explicitamente.\n"
        "- Interfaz, Telegram y pulso proactivo leen y escriben la misma memoria persistente en state.json.\n"
        "- El pulso proactivo ejecuta las mismas herramientas del agente que la interfaz y Telegram.\n"
        "- El contexto local observado de la PC vive en un snapshot separado y solo debe usarse como senal prudente para sugerencias o siguientes pasos.\n"
        "- El estado guarda respuestas completas; cuando haya demasiado volumen, resume con criterio en la respuesta en vez de cortar texto.\n"
        "- Todo aprendizaje estable debe guardarse en perfil, notas, tareas o plan con herramientas.\n"
        "- Antes de asumir que olvidaste algo, revisa perfil, notas, tareas, plan y autoconocimiento."
    )
    state_summary = render_state_summary(
        state,
        task_limit=10,
        note_limit=0,  # las notas entran por relevancia (recall), no por recencia
        include_runtime=False,
        include_last_result=False,
    )
    recall_block = _render_recall_block(state)
    conversation_summary = str(state.get("conversation_summary", {}).get("text", "")).strip()
    self_knowledge = state.get("self_knowledge", {})
    if not isinstance(self_knowledge, dict):
        self_knowledge = {}
    self_summary = str(self_knowledge.get("summary", "")).strip()
    if not self_summary:
        self_summary = (
            "Pendiente de autoanalisis. Usa `self` para refrescar identidad, "
            "codigo fuente, sistema operativo y hardware."
        )
    insights = self_knowledge.get("insights", [])
    if isinstance(insights, list) and insights:
        insight_lines = ["", "Autoconocimiento aprendido (sobre mi mismo):"]
        for item in insights[:20]:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category", "leccion"))
            text = str(item.get("text", "")).strip()
            if text:
                insight_lines.append(f"- [{category}] {text}")
        self_summary = self_summary + "\n" + "\n".join(insight_lines)
    user_timezone = str(state.get("profile", {}).get("timezone", "")).strip()
    temporal_context = _format_local_temporal_context(timezone_str=user_timezone)
    learned_directives = _render_learned_directives(state)

    second_system = (
        f"{memory_contract}\n\n"
        f"{temporal_context}\n\n"
        f"Contexto actual del agente:\n{state_summary}\n\n"
        f"Autoconocimiento de Yarbis:\n{self_summary}"
    )
    device_line = _render_device_line()
    if device_line:
        second_system += f"\n\n{device_line}"
    if conversation_summary:
        second_system += (
            "\n\nResumen de la conversacion previa (turnos antiguos ya compactados; "
            f"usalo como memoria de largo plazo del hilo):\n{conversation_summary}"
        )
    if recall_block:
        second_system += f"\n\n{recall_block}"
    if learned_directives:
        second_system += f"\n\n{learned_directives}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "system", "content": second_system},
    ]

    messages.extend(state["messages"][-20:])
    return messages


def _record_assistant_message(state, content: str):
    awaiting_user_input = state.get("awaiting_user_input", {})

    def mutate(current_state):
        if awaiting_user_input.get("pending") and awaiting_user_input.get("question"):
            current_state["awaiting_user_input"] = awaiting_user_input
        current_state["messages"].append({
            "role": "assistant",
            "content": content,
        })
        current_state["last_result"] = content

    state_transaction("record_assistant_message", mutate)


def _user_is_named_yarbis(state) -> bool:
    user_name = str(state.get("profile", {}).get("name", "")).strip()
    return _normalize_intent_text(user_name) == "yarbis"


def _strip_yarbis_addressee_from_greeting_line(line: str) -> str:
    match = re.match(
        r"^(\s*(?:hola|buenas|buenos d.as|buenas tardes|buenas noches))\s*,?\s+yarbis\b(.*)$",
        str(line),
        flags=re.IGNORECASE,
    )
    if not match:
        return line

    greeting = match.group(1).strip()
    tail = match.group(2).strip()
    normalized_tail = _normalize_intent_text(tail)
    if not normalized_tail:
        return f"{greeting.capitalize()}."

    if tail[:1] in {".", ",", ":", ";", "!", "?", "¡", "¿"}:
        tail = tail[1:].strip()
    while tail and not tail[0].isalnum() and tail[0] not in {"¿", "¡"}:
        tail = tail[1:].strip()

    if not tail:
        return f"{greeting.capitalize()}."
    if tail[0] in {"¿", "¡"}:
        return f"{greeting.capitalize()}. {tail}"
    return f"{greeting.capitalize()}. {tail[:1].upper()}{tail[1:]}"


def _sanitize_assistant_identity(text: str, state) -> str:
    rendered = str(text).strip()
    if not rendered or _user_is_named_yarbis(state):
        return rendered

    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        lines[index] = _strip_yarbis_addressee_from_greeting_line(line)
        break

    return "\n".join(lines).strip()


def _exception_chain(exc: Exception):
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_timeout_error(exc: Exception) -> bool:
    for chained in _exception_chain(exc):
        error_type = type(chained).__name__.lower()
        error_text = str(chained).strip().lower()
        if "timeout" in error_type or "timed out" in error_text or "timeout" in error_text:
            return True
    return False


def _openrouter_rate_limit_error(exc: Exception):
    for chained in _exception_chain(exc):
        if isinstance(chained, OpenRouterRateLimitError):
            return chained
        if isinstance(chained, OpenRouterHTTPError) and chained.status_code == 429:
            return chained
        if "openrouter http 429" in str(chained).strip().lower():
            return chained
    return None


def _format_chat_error(exc: Exception) -> str:
    provider_label = "OpenRouter" if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER else "Ollama"
    host_value = OPENROUTER_HOST if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER else OLLAMA_HOST
    timeout_value = (
        OPENROUTER_TIMEOUT_SECONDS
        if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER
        else OLLAMA_TIMEOUT_SECONDS
    )
    fallback_models = (
        OPENROUTER_FALLBACK_MODELS
        if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER
        else OLLAMA_FALLBACK_MODELS
    )
    error_text = f"No pude consultar {provider_label} en este ciclo: {exc}"
    host_text = host_value or "local"
    fallback_text = f" Fallbacks configurados: {', '.join(fallback_models)}." if fallback_models else ""
    cloud_hint = ""
    if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER:
        _api_key, key_source = _openrouter_api_key(OPENROUTER_API_KEY_ENV_VAR, OPENROUTER_API_KEY)
        if not _api_key:
            cloud_hint = f"\nDefine `{key_source}` para usar OpenRouter."
        rate_limit_error = _openrouter_rate_limit_error(exc)
        if rate_limit_error is not None:
            models = getattr(rate_limit_error, "models", None) or [MODEL]
            model_text = ", ".join(models) if models else MODEL
            fallback_hint = (
                "\nTambien puedes configurar fallbacks de OpenRouter desde Modelo y timeout "
                "o con `/openrouter fallback modelo1, modelo2`."
                if not fallback_models
                else ""
            )
            return (
                "OpenRouter aplico un limite temporal del proveedor/modelo (HTTP 429).\n"
                f"Host OpenRouter: {host_text}.\n"
                f"Modelo(s) intentado(s): {model_text}."
                f"{fallback_text}\n"
                "Ya hice el reintento breve configurado. Para reducir que se repita, "
                "espera unos minutos, cambia a un modelo con mas capacidad/rate limit, "
                "agrega fallbacks de OpenRouter o vuelve temporalmente a Ollama."
                f"{fallback_hint}{cloud_hint}"
            )
    elif _host_uses_ollama_cloud(OLLAMA_HOST):
        _api_key, key_source = _ollama_api_key(OLLAMA_HOST, OLLAMA_API_KEY_ENV_VAR, OLLAMA_API_KEY)
        if not _api_key:
            cloud_hint = (
                f"\nPara Ollama Cloud directo, pega una API key en la app, define `{key_source}` "
                "o cambia el host a local y usa `ollama signin`."
            )
    if not _is_timeout_error(exc):
        return f"{error_text}\nHost {provider_label}: {host_text}.{fallback_text}{cloud_hint}"

    if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER:
        return (
            f"{error_text}\n\n"
            "Diagnostico: OpenRouter no respondio dentro del tiempo configurado "
            f"({timeout_value}s) usando el modelo {MODEL} en {host_text}."
            f"{fallback_text}\n"
            "Para resolverlo, verifica la conexion, la API key, el host de OpenRouter "
            "y aumenta el timeout desde la app, Telegram (`/timeout`) o con "
            "`YARBIS_OPENROUTER_TIMEOUT_SECONDS`."
            f"{cloud_hint}"
        )

    return (
        f"{error_text}\n\n"
        "Diagnostico: Ollama no respondio dentro del tiempo configurado "
        f"({timeout_value}s) usando el modelo {MODEL} en {host_text}."
        f"{fallback_text}\n"
        "Para resolverlo, verifica que Ollama este activo, calienta el modelo con "
        f"`ollama run {MODEL}`, aumenta el timeout desde la app, Telegram "
        "(`/timeout`) o con `YARBIS_OLLAMA_TIMEOUT_SECONDS`, o usa un modelo mas "
        "ligero desde la app, Telegram (`/modelo`) o con `YARBIS_MODEL`."
        f"{cloud_hint}"
    )


def _handle_chat_error(state, exc: Exception):
    error_text = _format_chat_error(exc)
    _print_output(f"\nYarbis:\n{error_text}")
    _record_assistant_message(state, error_text)
    return {
        "status": "error",
        "content": error_text,
        "used_tools": False,
        "looks_meta": False,
        "needs_user_input": False,
        "error_type": type(exc).__name__,
    }


def _handle_empty_response(state, model_candidates=None):
    tried_models = ", ".join(model_candidates) if model_candidates else "modelo configurado"
    error_text = (
        f"El modelo devolvio una respuesta vacia en este ciclo "
        f"(modelos intentados: {tried_models})."
    )
    _print_output(f"\nYarbis:\n{error_text}")
    _record_assistant_message(state, error_text)
    return error_text


def _build_waiting_for_user_input_result(state, used_tools: bool = False) -> dict:
    question = state["awaiting_user_input"]["question"]
    message = "Estoy esperando una respuesta del usuario antes de continuar."
    if question:
        message = f"{message}\nPregunta pendiente: {question}"

    _print_output(f"\nYarbis:\n{message}")
    return {
        "status": "waiting_for_user_input",
        "content": message,
        "used_tools": used_tools,
        "looks_meta": False,
        "needs_user_input": True,
    }


def _stop_requested_for_operation(state=None, operation_id: str = "") -> bool:
    if state is None:
        state = load_state()

    runtime = state.get("runtime", {}) if isinstance(state, dict) else {}
    if not isinstance(runtime, dict):
        return False

    stop_requested = runtime.get("stop_requested", {})
    if not isinstance(stop_requested, dict) or not stop_requested.get("active"):
        return False

    requested_operation_id = str(stop_requested.get("operation_id", "")).strip()
    if operation_id and requested_operation_id and requested_operation_id != operation_id:
        return False

    thinking = runtime.get("thinking", {})
    thinking_operation_id = ""
    if isinstance(thinking, dict):
        thinking_operation_id = str(thinking.get("operation_id", "")).strip()

    if requested_operation_id and thinking_operation_id and requested_operation_id != thinking_operation_id:
        return False

    return True


def _current_runtime_operation_id(state=None) -> str:
    if state is None:
        state = load_state()

    runtime = state.get("runtime", {}) if isinstance(state, dict) else {}
    if not isinstance(runtime, dict):
        return ""

    thinking = runtime.get("thinking", {})
    if not isinstance(thinking, dict):
        return ""
    return str(thinking.get("operation_id", "")).strip()


def _cancel_watchdog(operation_id: str, stop_event: threading.Event):
    while not stop_event.wait(_CANCEL_WATCH_INTERVAL_SECONDS):
        try:
            if _stop_requested_for_operation(operation_id=operation_id):
                cancel_active_ollama_request()
                return
        except Exception:
            continue


def _chat_with_cancel_watch(ollama_client, operation_id: str, **kwargs):
    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_cancel_watchdog,
        args=(operation_id, stop_event),
        name="yarbis-ollama-cancel-watchdog",
        daemon=True,
    )
    watcher.start()
    try:
        return ollama_client.chat(**kwargs)
    finally:
        stop_event.set()


def _chat_with_model_candidates(
    ollama_client,
    operation_id: str,
    model_candidates: list[str],
    **kwargs,
):
    errors = []
    caught_exceptions = []
    last_exc = None
    for model_name in model_candidates:
        try:
            return _chat_with_cancel_watch(
                ollama_client,
                operation_id=operation_id,
                model=model_name,
                **kwargs,
            )
        except Exception as exc:
            if _stop_requested_for_operation(operation_id=operation_id):
                raise
            last_exc = exc
            caught_exceptions.append(exc)
            errors.append(f"{model_name}: {exc}")

    if (
        MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER
        and errors
        and len(errors) == len(caught_exceptions)
        and all(_openrouter_rate_limit_error(exc) is not None for exc in caught_exceptions)
    ):
        raise OpenRouterRateLimitError(model_candidates)
    if len(errors) > 1:
        raise RuntimeError("Todos los modelos configurados fallaron: " + " | ".join(errors))
    if last_exc is not None:
        raise last_exc
    provider_label = "OpenRouter" if MODEL_PROVIDER == MODEL_PROVIDER_OPENROUTER else "Ollama"
    raise RuntimeError(f"No hay modelos {provider_label} configurados.")


def _handle_stop_requested(state, used_tools: bool = False, action_tools_used: bool = False) -> dict:
    final_text = "Operacion detenida por solicitud del usuario."
    _print_output(f"\nYarbis:\n{final_text}")
    _record_assistant_message(state, final_text)
    return {
        "status": "cancelled",
        "content": final_text,
        "used_tools": used_tools,
        "action_tools_used": action_tools_used,
        "looks_meta": False,
        "needs_user_input": False,
        "cancelled": True,
    }


def _looks_like_meta_response(text: str) -> bool:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return False

    meta_phrases = (
        "voy a empezar",
        "ahora me estoy enfocando",
        "ahora me enfoco",
        "mi objetivo es",
        "trabajar paso a paso",
        "trabajare paso a paso",
        "primer paso",
        "segundo ciclo",
        "primero creare",
    )
    matches = sum(phrase in normalized for phrase in meta_phrases)
    return len(normalized) < 500 and matches >= 2


def _last_message_content(state, role: str) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, dict) and message.get("role") == role:
            return str(message.get("content", "")).strip()
    return ""


def _last_user_authorized_action(state) -> bool:
    last_user_message = _last_message_content(state, "user")
    normalized = _normalize_intent_text(last_user_message)
    return (
        "usuario autorizo avanzar" in normalized
        or _looks_like_affirmative_action_reply(last_user_message)
    )


def _last_user_requested_action(state) -> bool:
    normalized = _normalize_intent_text(_last_message_content(state, "user"))
    if not normalized:
        return False

    action_phrases = (
        "mejora",
        "optimiza",
        "arregla",
        "corrige",
        "implementa",
        "ejecuta",
        "haz ",
        "crea",
        "actualiza",
        "analiza",
        "revisa",
    )
    return any(phrase in normalized for phrase in action_phrases)


def _numbered_option_count(text: str) -> int:
    return len(re.findall(r"(?m)^\s*\d+[\.\)]\s+", str(text)))


def _looks_like_choice_menu(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    asks_for_choice = any(
        phrase in normalized
        for phrase in (
            "que prefieres",
            "cual prefieres",
            "elige una",
            "elige opcion",
            "elige una opcion",
            "opciones",
        )
    )
    return asks_for_choice and _numbered_option_count(text) >= 2


def _looks_like_generic_help_prompt(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "en que puedo ayudarte hoy",
            "como puedo ayudarte hoy",
            "que puedo hacer por ti",
            "cuentame que necesitas hacer",
            "que necesitas mejorar o resolver hoy",
            "cual es lo que necesitas mejorar o resolver hoy",
        )
    )


def _looks_like_deferred_action_confirmation(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "te gustaria que implemente",
            "quieres que implemente",
            "deseas que implemente",
            "confirmas que avance",
            "lo implemento ahora",
            "lo hago ahora",
        )
    )


def _looks_like_unverified_action_claim(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "he realizado",
            "he implementado",
            "implemente",
            "actualice",
            "modifique",
            "reduje",
            "ejecute",
            "he creado",
        )
    )


def _last_user_asked_for_identity_or_capabilities(state) -> bool:
    normalized = _normalize_intent_text(_last_message_content(state, "user"))
    if not normalized:
        return False

    return any(
        phrase in normalized
        for phrase in (
            "quien eres",
            "que eres",
            "presentate",
            "que puedes hacer",
            "como me puedes ayudar",
            "cuales son tus capacidades",
            "lista tus capacidades",
        )
    )


def _has_normalized_phrase(normalized: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized) is not None


def _last_user_asked_about_environment(state) -> bool:
    normalized = _normalize_intent_text(_last_message_content(state, "user"))
    if not normalized:
        return False

    environment_terms = (
        "sistema operativo",
        "hardware",
        "equipo",
        "maquina",
        "procesador",
        "cpu",
        "gpu",
        "ram",
        "arquitectura",
        "windows",
        "amd",
        "ryzen",
        "intel",
        "autoconocimiento",
        "self overview",
        "entorno local",
    )
    return any(_has_normalized_phrase(normalized, term) for term in environment_terms)


def _looks_like_unsolicited_self_intro_or_capabilities(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    intro_phrases = (
        "soy yarbis",
        "tu agente local",
        "agente local optimizado",
        "como tu agente local",
    )
    if any(phrase in normalized for phrase in intro_phrases):
        return True

    has_capability_menu = (
        "puedo" in normalized
        and (
            _numbered_option_count(text) >= 2
            or re.search(r"(?mi)^\s*puedo\s*:", str(text)) is not None
        )
    )
    return bool(has_capability_menu)


def _looks_like_environment_claim(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    environment_terms = (
        "sistema operativo",
        "windows 11",
        "windows 10",
        "arquitectura",
        "amd64",
        "x86 64",
        "cpu",
        "gpu",
        "ram",
        "procesador",
        "ryzen",
        "genuineintel",
        "intel64",
    )
    return any(_has_normalized_phrase(normalized, term) for term in environment_terms)


def _strip_unsolicited_environment_claims(text: str) -> str:
    kept_lines = []
    for line in str(text).splitlines():
        if not _looks_like_environment_claim(line):
            kept_lines.append(line)
            continue

        sentences = re.split(r"(?<=[.!?])\s+", line.strip())
        kept_sentences = [
            sentence.strip()
            for sentence in sentences
            if sentence.strip() and not _looks_like_environment_claim(sentence)
        ]
        if kept_sentences:
            kept_lines.append(" ".join(kept_sentences))

    rendered = "\n".join(kept_lines).strip()
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered


def _contextual_recovery_message(state) -> str:
    open_tasks = [
        task
        for task in state.get("tasks", [])
        if isinstance(task, dict) and task.get("status") in {"pending", "in_progress", "blocked"}
    ]
    if open_tasks:
        task = open_tasks[0]
        title = str(task.get("title", "")).strip() or "la siguiente tarea"
        return f"Tengo una tarea abierta: {title}. Dime si quieres que continue con esa o ajusto el rumbo."

    goal = str(state.get("goal", "")).strip()
    if goal:
        return (
            f"Objetivo activo: {goal}. Puedo avanzar desde ahi; dime si quieres que ejecute "
            "un ciclo, cree un plan o atienda una accion concreta."
        )

    return "Dime la accion concreta que quieres que ejecute y avanzo con el contexto disponible."


def _looks_like_non_actionable_prompt(text: str) -> bool:
    return (
        _looks_like_choice_menu(text)
        or _looks_like_generic_help_prompt(text)
        or _looks_like_deferred_action_confirmation(text)
    )


def _should_reject_non_actionable_final(
    state,
    text: str,
    used_tools: bool,
    action_tools_used: bool = False,
) -> bool:
    if used_tools and action_tools_used:
        return False

    if used_tools:
        if not (_last_user_authorized_action(state) or _last_user_requested_action(state)):
            return False
        return (
            _looks_like_non_actionable_prompt(text)
            or _looks_like_unverified_action_claim(text)
        )

    if (
        _looks_like_unsolicited_self_intro_or_capabilities(text)
        and not _last_user_asked_for_identity_or_capabilities(state)
    ):
        return True

    if _looks_like_environment_claim(text) and not _last_user_asked_about_environment(state):
        return True

    if not (_last_user_authorized_action(state) or _last_user_requested_action(state)):
        return False

    return (
        _looks_like_non_actionable_prompt(text)
        or _looks_like_unverified_action_claim(text)
    )


def _should_retry_rejected_final(
    state,
    text: str,
    used_tools: bool,
    action_tools_used: bool = False,
) -> bool:
    if not _should_reject_non_actionable_final(
        state,
        text,
        used_tools=used_tools,
        action_tools_used=action_tools_used,
    ):
        return False

    if (
        not used_tools
        and (
            _looks_like_unsolicited_self_intro_or_capabilities(text)
            or _looks_like_environment_claim(text)
        )
        and not (
            _last_user_authorized_action(state)
            or _last_user_requested_action(state)
        )
    ):
        return False

    return True


def _safe_rejected_final_message(state, text: str) -> str:
    if _looks_like_environment_claim(text) and not _last_user_asked_about_environment(state):
        sanitized = _strip_unsolicited_environment_claims(text)
        if (
            sanitized
            and not _looks_like_environment_claim(sanitized)
            and not _looks_like_non_actionable_prompt(sanitized)
            and not _looks_like_unsolicited_self_intro_or_capabilities(sanitized)
        ):
            return sanitized
        return _contextual_recovery_message(state)

    if _last_user_authorized_action(state) or _last_user_requested_action(state):
        return (
            "No complete una accion verificable en este ciclo. Necesito una "
            "instruccion mas concreta o un dato faltante para avanzar bien."
        )

    return "Dime que quieres que haga y avanzare sin presentarme ni listar capacidades."


def _looks_like_waiting_for_instructions(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    negated_phrases = (
        "no espero instrucciones",
        "no estoy a la espera",
        "no quedo a la espera",
        "sin esperar instrucciones",
        "sin esperar indicaciones",
    )
    if any(phrase in normalized for phrase in negated_phrases):
        return False

    return bool(re.search(
        (
            r"\b(?:a la espera|en espera|esperando|pendiente|atento|"
            r"listo para recibir|listo para tus)\s+"
            r"(?:de\s+|a\s+)?(?:tus\s+|nuevas\s+|mas\s+|las\s+)?"
            r"(?:instrucciones|indicaciones|ordenes)\b"
        ),
        normalized,
    ))


def _has_open_tasks(state) -> bool:
    return any(task["status"] in {"pending", "in_progress", "blocked"} for task in state["tasks"])


def _is_waiting_for_user_input(state) -> bool:
    awaiting_user_input = state.get("awaiting_user_input", {})
    return bool(
        awaiting_user_input.get("pending")
        and str(awaiting_user_input.get("question", "")).strip()
    )


def _extract_user_input_request(text: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in str(text).split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return ""

    interactive_phrases = (
        "deseas",
        "prefieres",
        "quieres que",
        "puedes decirme",
        "necesito que me digas",
        "necesito saber",
        "para continuar",
        "para seguir",
        "antes de continuar",
        "antes de seguir",
        "comparteme",
        "confirmame",
        "confirma",
        "que",
        "qué",
        "cual",
        "cuál",
        "cuales",
        "cuáles",
        "quien",
        "quién",
        "como",
        "cómo",
        "donde",
        "dónde",
        "cuando",
        "cuándo",
        "cuanto",
        "cuánto",
    )

    candidates = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        for line in lines:
            candidate = re.sub(r"^[-*•\d\.\)\s]+", "", line).strip()
            if candidate:
                candidates.append(candidate)
        candidates.append(paragraph)

    for candidate in candidates:
        if "?" not in candidate:
            continue

        normalized = " ".join(candidate.lower().split())
        if any(phrase in normalized for phrase in interactive_phrases):
            return candidate

    if _looks_like_waiting_for_instructions(text):
        return WAITING_FOR_INSTRUCTIONS_QUESTION

    return ""


def run_one_cycle(max_steps=None, model_override: str | None = None):
    state = load_state()
    if _stop_requested_for_operation(state):
        return _handle_stop_requested(state)

    if _is_waiting_for_user_input(state):
        return _build_waiting_for_user_input_result(state)

    # Compacta el historial largo antes de construir el contexto: resume y poda
    # los turnos viejos para no perder el hilo ni inflar state.json. Best-effort.
    try:
        if maybe_compact_history():
            state = load_state()
    except Exception:
        pass

    state_transaction(
        "run_one_cycle_increment",
        lambda current_state: current_state.__setitem__(
            "cycle_count",
            current_state["cycle_count"] + 1,
        ),
        create_backup=False,
    )
    state = load_state()

    if max_steps is None:
        max_steps = state["autonomy"]["max_steps_per_cycle"]

    model_settings, model_client = _apply_model_runtime_settings(
        state,
        model_override=model_override,
    )
    model_candidates = model_settings["models"]
    operation_id = _current_runtime_operation_id(state)

    print(f"\n=== CICLO {state['cycle_count']} ===")
    used_tools = False
    action_tools_used = False
    empty_response_retries = 0
    non_actionable_retries = 0

    try:
        max_steps = int(max_steps) if max_steps is not None else None
    except (TypeError, ValueError):
        max_steps = None
    if max_steps is not None and max_steps <= 0:
        max_steps = None

    step_numbers = itertools.count(1) if max_steps is None else range(1, max_steps + 1)

    for step in step_numbers:
        print(f"\n--- Paso {step} ---")

        state = load_state()
        if _stop_requested_for_operation(state, operation_id=operation_id):
            return _handle_stop_requested(state, used_tools=used_tools, action_tools_used=action_tools_used)

        try:
            response = _chat_with_model_candidates(
                model_client,
                operation_id=operation_id,
                model_candidates=model_candidates,
                messages=build_messages(state),
                tools=_current_tool_definitions(),
                think=False,
            )
        except Exception as exc:
            latest_state = load_state()
            if _stop_requested_for_operation(latest_state, operation_id=operation_id):
                return _handle_stop_requested(
                    latest_state,
                    used_tools=used_tools,
                    action_tools_used=action_tools_used,
                )
            return _handle_chat_error(state, exc)

        state = load_state()
        if _stop_requested_for_operation(state, operation_id=operation_id):
            return _handle_stop_requested(state, used_tools=used_tools, action_tools_used=action_tools_used)

        assistant_message = response.message
        assistant_content = assistant_message.content or ""

        if assistant_message.tool_calls:
            used_tools = True
            print("Decidi usar herramientas.")

            assistant_tool_calls = []
            for index, tool_call in enumerate(assistant_message.tool_calls, start=1):
                tool_call_id = str(getattr(tool_call, "id", "")).strip()
                if not tool_call_id:
                    tool_call_id = f"call_{state['cycle_count']}_{step}_{index}"
                assistant_tool_calls.append({
                    "id": tool_call_id,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                })

            assistant_tool_message = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": assistant_tool_calls,
            }
            state_transaction(
                "record_assistant_tool_calls",
                lambda current_state: current_state["messages"].append(assistant_tool_message),
            )
            state = load_state()

            for index, tool_call in enumerate(assistant_message.tool_calls, start=1):
                state = load_state()
                if _stop_requested_for_operation(state, operation_id=operation_id):
                    return _handle_stop_requested(
                        state,
                        used_tools=used_tools,
                        action_tools_used=action_tools_used,
                    )

                tool_name = tool_call.function.name
                raw_tool_args = tool_call.function.arguments
                tool_call_id = str(getattr(tool_call, "id", "")).strip()
                if not tool_call_id:
                    tool_call_id = f"call_{state['cycle_count']}_{step}_{index}"
                tool_args, tool_args_error = _normalize_tool_arguments(raw_tool_args)

                print(f"\n> Ejecutando tool: {tool_name}")
                print(f"> Argumentos: {raw_tool_args}")

                is_mcp_tool = str(tool_name).startswith("mcp__")
                function_to_call = available_functions.get(tool_name)
                if _proactive_safe_mode() and tool_name not in PROACTIVE_SAFE_TOOL_NAMES:
                    tool_output = (
                        "Tool no permitida durante el pulso proactivo seguro: "
                        f"{tool_name}. Registra una tarea o pide confirmacion para ejecutarla fuera del pulso."
                    )
                elif is_mcp_tool:
                    if tool_args_error:
                        tool_output = tool_args_error
                    else:
                        try:
                            tool_output = mcp_client.call_qualified(tool_name, tool_args)
                        except Exception as exc:
                            tool_output = f"Error ejecutando {tool_name}: {exc}"
                        else:
                            action_tools_used = True
                elif not function_to_call:
                    tool_output = f"Tool no encontrada: {tool_name}"
                elif tool_args_error:
                    tool_output = tool_args_error
                else:
                    try:
                        tool_output = function_to_call(**tool_args)
                    except Exception as exc:
                        tool_output = f"Error ejecutando {tool_name}: {exc}"
                    else:
                        if _tool_call_proves_action(tool_name, tool_output):
                            action_tools_used = True

                _print_output(f"> Resultado:\n{tool_output}")

                def record_tool_output(current_state):
                    current_state["messages"].append({
                        "role": "tool",
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "content": str(tool_output),
                    })
                    current_state["last_result"] = str(tool_output)

                state_transaction("record_tool_output", record_tool_output)
                state = load_state()
                if _stop_requested_for_operation(state, operation_id=operation_id):
                    return _handle_stop_requested(
                        state,
                        used_tools=used_tools,
                        action_tools_used=action_tools_used,
                    )

            if _is_waiting_for_user_input(state):
                return _build_waiting_for_user_input_result(state, used_tools=used_tools)
            continue

        final_text = assistant_content.strip()
        latest_state = load_state()
        if _stop_requested_for_operation(latest_state, operation_id=operation_id):
            return _handle_stop_requested(
                latest_state,
                used_tools=used_tools,
                action_tools_used=action_tools_used,
            )
        if not final_text:
            if empty_response_retries < EMPTY_RESPONSE_RETRIES:
                empty_response_retries += 1
                # Rotar modelos: en el primer retry, intentar con el siguiente modelo fallback
                if empty_response_retries == 1 and len(model_candidates) > 1:
                    rotated = model_candidates[1:] + [model_candidates[0]]
                    model_settings = {**model_settings, "models": rotated}
                    model_candidates = rotated
                    print(f"Respuesta vacia del modelo. Reintentando con fallback: {rotated[0]}")
                else:
                    print(f"Respuesta vacia del modelo. Reintento {empty_response_retries}/{EMPTY_RESPONSE_RETRIES}.")
                retry_message = {
                    "role": "user",
                    "content": (
                        "Tu respuesta anterior llego vacia. Responde ahora con una salida util, "
                        "concreta y final para este ciclo. Si ya ejecutaste herramientas, "
                        "resume los resultados obtenidos y da el siguiente paso."
                    ),
                }
                state_transaction(
                    "record_empty_response_retry",
                    lambda current_state: current_state["messages"].append(retry_message),
                )
                state = load_state()
                continue

            final_text = _handle_empty_response(state, model_candidates=model_candidates)
            return {
                "status": "empty",
                "content": final_text,
                "used_tools": used_tools,
                "action_tools_used": action_tools_used,
                "looks_meta": False,
            }

        non_actionable_final = _should_reject_non_actionable_final(
            state,
            final_text,
            used_tools=used_tools,
            action_tools_used=action_tools_used,
        )
        if (
            non_actionable_final
            and _should_retry_rejected_final(
                state,
                final_text,
                used_tools=used_tools,
                action_tools_used=action_tools_used,
            )
            and non_actionable_retries < MAX_NON_ACTIONABLE_RETRIES
            and (max_steps is None or step < max_steps)
        ):
            non_actionable_retries += 1
            def record_non_actionable_retry(current_state):
                current_state["messages"].append({
                    "role": "assistant",
                    "content": final_text,
                })
                current_state["messages"].append({
                    "role": "user",
                    "content": NON_ACTIONABLE_RETRY_MESSAGE,
                })

            state_transaction("record_non_actionable_retry", record_non_actionable_retry)
            state = load_state()
            continue

        if non_actionable_final:
            final_text = _safe_rejected_final_message(state, final_text)

        final_text = _sanitize_assistant_identity(final_text, state)
        _print_output(f"\nYarbis:\n{final_text}")

        pending_question = ""
        if not (non_actionable_final and _looks_like_non_actionable_prompt(final_text)):
            pending_question = _extract_user_input_request(final_text)
        if pending_question and not _is_waiting_for_user_input(state):
            state["awaiting_user_input"] = {
                "pending": True,
                "question": pending_question,
                "reason": "Yarbis necesita una respuesta del usuario para continuar.",
                "fields": [],
            }
        _record_assistant_message(state, final_text)
        return {
            "status": "final",
            "content": final_text,
            "used_tools": used_tools,
            "action_tools_used": action_tools_used,
            "looks_meta": _looks_like_meta_response(final_text),
            "needs_user_input": _is_waiting_for_user_input(load_state()),
        }

    final_text = f"Se alcanzo el maximo de pasos ({max_steps}) sin una respuesta final."
    _print_output(f"\nYarbis:\n{final_text}")
    _record_assistant_message(state, final_text)
    return {
        "status": "max_steps",
        "content": final_text,
        "used_tools": used_tools,
        "action_tools_used": action_tools_used,
        "looks_meta": False,
    }


def _autonomous_response_signature(text: object) -> str:
    normalized = _normalize_intent_text(str(text or ""))
    if not normalized:
        return ""
    words = normalized.split()
    return " ".join(words[:80])


def _looks_like_autonomous_closure_loop(text: object) -> bool:
    normalized = _normalize_intent_text(str(text or ""))
    if not normalized:
        return False
    return any(phrase in normalized for phrase in AUTONOMOUS_LOOP_CLOSING_PHRASES)


def run_autonomous_session(cycles=None, model_override: str | None = None):
    state = load_state()
    using_safety_cycle_limit = False
    if cycles is None:
        cycle_limit = normalize_cycle_count(state["autonomy"]["auto_cycles_default"])
        if cycle_limit is None:
            cycle_limit = DEFAULT_AUTO_RUN_SAFETY_CYCLES
            using_safety_cycle_limit = True
    else:
        cycle_limit = normalize_cycle_count(cycles, default=0)

    completed_cycles = 0
    saw_tasks = bool(state["tasks"])
    previous_final_signature = ""
    repeated_final_signatures = 0
    stop_reason = ""

    while cycle_limit is None or completed_cycles < cycle_limit:
        current_state = load_state()
        if _stop_requested_for_operation(current_state):
            print("\nYarbis: operacion detenida por solicitud del usuario.")
            stop_reason = "cancelled"
            break

        if _is_waiting_for_user_input(current_state):
            print("\nYarbis: estoy esperando una respuesta del usuario antes de continuar.")
            print(f"Pregunta pendiente: {current_state['awaiting_user_input']['question']}")
            stop_reason = "waiting_for_user"
            break

        cycle_result = run_one_cycle(model_override=model_override)
        if not isinstance(cycle_result, dict):
            cycle_result = {
                "status": "error",
                "content": str(cycle_result),
                "used_tools": False,
                "action_tools_used": False,
                "looks_meta": False,
                "needs_user_input": False,
            }
        completed_cycles += 1

        updated_state = load_state()
        saw_tasks = saw_tasks or bool(updated_state["tasks"])
        if cycle_result.get("status") == "cancelled" or _stop_requested_for_operation(updated_state):
            print("\nYarbis: operacion detenida por solicitud del usuario.")
            stop_reason = "cancelled"
            break

        if cycle_result.get("needs_user_input") or _is_waiting_for_user_input(updated_state):
            print("\nYarbis: falta informacion del usuario. Deteniendo modo autonomo.")
            stop_reason = "waiting_for_user"
            break

        if saw_tasks and not _has_open_tasks(updated_state):
            print("\nYarbis: no quedan tareas abiertas. Deteniendo modo autonomo.")
            stop_reason = "tasks_done"
            break

        cycle_status = str(cycle_result.get("status", "")).strip()
        cycle_content = str(cycle_result.get("content", ""))
        action_tools_used = bool(cycle_result.get("action_tools_used"))
        final_signature = (
            _autonomous_response_signature(cycle_content)
            if cycle_status in AUTONOMOUS_FINAL_STATUSES
            else ""
        )
        if final_signature and final_signature == previous_final_signature:
            repeated_final_signatures += 1
        elif final_signature:
            previous_final_signature = final_signature
            repeated_final_signatures = 1
        else:
            previous_final_signature = ""
            repeated_final_signatures = 0

        if cycle_status in AUTONOMOUS_FINAL_STATUSES and _looks_like_autonomous_closure_loop(cycle_content):
            print("\nYarbis: respuesta repetitiva de cierre detectada. Deteniendo modo autonomo para evitar bucles.")
            stop_reason = "repetitive_closure"
            break

        if repeated_final_signatures >= 2:
            print("\nYarbis: respuesta final repetida detectada. Deteniendo modo autonomo para evitar bucles.")
            stop_reason = "repeated_final"
            break

        if cycle_status in AUTONOMOUS_FINAL_STATUSES and not action_tools_used:
            print("\nYarbis: no hubo acciones nuevas verificables. Deteniendo modo autonomo para evitar bucles.")
            stop_reason = "no_action"
            break

        if (
            not _has_open_tasks(updated_state)
            and cycle_status in AUTONOMOUS_FINAL_STATUSES
            and not cycle_result["used_tools"]
        ):
            print("\nYarbis: no hay tareas abiertas y este ciclo ya cerro sin seguimiento adicional.")
            stop_reason = "no_open_tasks"
            break

        if cycle_result["looks_meta"] and not _has_open_tasks(updated_state):
            print("\nYarbis: el modelo se quedo describiendo el proceso sin abrir tareas. Deteniendo modo autonomo.")
            stop_reason = "meta"
            break

    if (
        not stop_reason
        and using_safety_cycle_limit
        and cycle_limit is not None
        and completed_cycles >= cycle_limit
    ):
        print(
            "\nYarbis: limite seguro de "
            f"{cycle_limit} ciclo(s) alcanzado. Deteniendo modo autonomo para evitar bucles."
        )

    return completed_cycles
