import json
import inspect
import os
import re
import sys
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib import error as urllib_error, request
from urllib.parse import urlparse

from ollama import Client

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
    MAX_OLLAMA_TIMEOUT_SECONDS,
    MIN_OLLAMA_TIMEOUT_SECONDS,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    VALID_MODEL_PROVIDERS,
    load_state,
    render_state_summary,
    state_transaction,
)
from tools import (
    add_task,
    agent_overview,
    browser_automation,
    coding_apply_proposal,
    coding_discard_proposal,
    coding_git_diff,
    coding_git_status,
    coding_get_proposal,
    coding_list_files,
    coding_list_proposals,
    coding_propose_text_file,
    coding_read_text_file,
    coding_run_validation,
    coding_set_workspace,
    coding_workspace_overview,
    compose_email,
    create_memory_backup,
    create_calendar_event,
    delete_note,
    fetch_web_page,
    get_note,
    import_memory_backup,
    list_files,
    list_checkpoints,
    list_memory_backups,
    list_notes,
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
    confirm_social_publication,
    inspect_memory_backup,
    list_social_drafts,
    list_social_publications,
    open_assisted_social_post,
    prepare_social_publication,
    save_social_draft,
    social_accounts_overview,
    start_social_oauth,
    update_goal,
    update_internet_settings,
    update_memory_protection_settings,
    update_profile,
    update_task_status,
    verify_memory_backups,
    web_search,
    write_text_file,
)

DEFAULT_MODEL = DEFAULT_OLLAMA_MODEL
DEFAULT_EMPTY_RESPONSE_RETRIES = 1
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


def _format_local_temporal_context(now: datetime | None = None) -> str:
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


def _ollama_client_signature(host: str, timeout_seconds: int, api_key_env_var: str) -> tuple:
    cleaned_host = _normalize_host(host)
    cleaned_env_var = str(api_key_env_var).strip() or DEFAULT_OLLAMA_API_KEY_ENV_VAR
    api_key = os.getenv(cleaned_env_var, "").strip() if _host_uses_ollama_cloud(cleaned_host) else ""
    return cleaned_host, int(timeout_seconds), cleaned_env_var, api_key


def _build_ollama_client(host: str, timeout_seconds: int, api_key_env_var: str):
    cleaned_host, _timeout_seconds, cleaned_env_var, api_key = _ollama_client_signature(
        host,
        timeout_seconds,
        api_key_env_var,
    )
    kwargs = {"timeout": timeout_seconds}
    if cleaned_host:
        kwargs["host"] = cleaned_host
    if api_key:
        kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
    return Client(**kwargs)


def _normalize_openrouter_host(host: str) -> str:
    cleaned = str(host).strip() or DEFAULT_OPENROUTER_HOST
    if cleaned.endswith("/chat/completions"):
        cleaned = cleaned[: -len("/chat/completions")].rstrip("/")
    return cleaned.rstrip("/")


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

        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            error_message = _openrouter_error_message(error_body) or error_body or str(exc)
            raise RuntimeError(f"OpenRouter HTTP {exc.code}: {error_message}") from exc
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


def _build_openrouter_client(host: str, timeout_seconds: int, api_key_env_var: str, api_key: str = ""):
    return OpenRouterClient(host, timeout_seconds, api_key_env_var, api_key=api_key)


client = _build_ollama_client(
    OLLAMA_HOST,
    OLLAMA_TIMEOUT_SECONDS,
    OLLAMA_API_KEY_ENV_VAR,
)
_client_signature = (
    MODEL_PROVIDER_OLLAMA,
    *_ollama_client_signature(
        OLLAMA_HOST,
        OLLAMA_TIMEOUT_SECONDS,
        OLLAMA_API_KEY_ENV_VAR,
    ),
)
_client_timeout_seconds = OLLAMA_TIMEOUT_SECONDS
_client_lock = threading.RLock()
tool_definitions = [
    agent_overview,
    update_profile,
    update_internet_settings,
    request_user_input,
    save_note,
    list_notes,
    get_note,
    delete_note,
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
    coding_set_workspace,
    coding_workspace_overview,
    coding_list_files,
    coding_read_text_file,
    coding_propose_text_file,
    coding_list_proposals,
    coding_get_proposal,
    coding_apply_proposal,
    coding_discard_proposal,
    coding_git_status,
    coding_git_diff,
    coding_run_validation,
    list_files,
    read_text_file,
    write_text_file,
    list_checkpoints,
    restore_checkpoint,
    run_project_tests,
    run_project_check,
    run_system_command,
    web_search,
    fetch_web_page,
    browser_automation,
    create_calendar_event,
    compose_email,
    open_system_target,
    self_overview,
    social_accounts_overview,
    start_social_oauth,
    save_social_draft,
    list_social_drafts,
    list_social_publications,
    prepare_social_publication,
    confirm_social_publication,
    open_assisted_social_post,
]

available_functions = {
    "agent_overview": agent_overview,
    "update_profile": update_profile,
    "update_internet_settings": update_internet_settings,
    "request_user_input": request_user_input,
    "save_note": save_note,
    "list_notes": list_notes,
    "get_note": get_note,
    "delete_note": delete_note,
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
    "coding_set_workspace": coding_set_workspace,
    "coding_workspace_overview": coding_workspace_overview,
    "coding_list_files": coding_list_files,
    "coding_read_text_file": coding_read_text_file,
    "coding_propose_text_file": coding_propose_text_file,
    "coding_list_proposals": coding_list_proposals,
    "coding_get_proposal": coding_get_proposal,
    "coding_apply_proposal": coding_apply_proposal,
    "coding_discard_proposal": coding_discard_proposal,
    "coding_git_status": coding_git_status,
    "coding_git_diff": coding_git_diff,
    "coding_run_validation": coding_run_validation,
    "list_files": list_files,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
    "list_checkpoints": list_checkpoints,
    "restore_checkpoint": restore_checkpoint,
    "run_project_tests": run_project_tests,
    "run_project_check": run_project_check,
    "run_system_command": run_system_command,
    "web_search": web_search,
    "fetch_web_page": fetch_web_page,
    "browser_automation": browser_automation,
    "create_calendar_event": create_calendar_event,
    "compose_email": compose_email,
    "open_system_target": open_system_target,
    "self_overview": self_overview,
    "social_accounts_overview": social_accounts_overview,
    "start_social_oauth": start_social_oauth,
    "save_social_draft": save_social_draft,
    "list_social_drafts": list_social_drafts,
    "list_social_publications": list_social_publications,
    "prepare_social_publication": prepare_social_publication,
    "confirm_social_publication": confirm_social_publication,
    "open_assisted_social_post": open_assisted_social_post,
}

PROACTIVE_SAFE_TOOL_NAMES = {
    "agent_overview",
    "update_profile",
    "request_user_input",
    "save_note",
    "list_notes",
    "get_note",
    "delete_note",
    "add_task",
    "list_tasks",
    "update_task_status",
    "set_plan",
    "create_memory_backup",
    "list_memory_backups",
    "inspect_memory_backup",
    "memory_protection_status",
    "verify_memory_backups",
    "self_overview",
    "coding_workspace_overview",
    "coding_list_files",
    "coding_read_text_file",
    "coding_list_proposals",
    "coding_get_proposal",
    "coding_git_status",
    "coding_git_diff",
    "social_accounts_overview",
    "save_social_draft",
    "list_social_drafts",
    "list_social_publications",
    "prepare_social_publication",
}

ACTION_PROOF_TOOL_NAMES = {
    "update_profile",
    "update_internet_settings",
    "update_memory_protection_settings",
    "request_user_input",
    "save_note",
    "delete_note",
    "add_task",
    "update_task_status",
    "update_goal",
    "set_plan",
    "create_memory_backup",
    "import_memory_backup",
    "coding_set_workspace",
    "coding_propose_text_file",
    "coding_apply_proposal",
    "coding_discard_proposal",
    "coding_run_validation",
    "write_text_file",
    "restore_checkpoint",
    "run_project_tests",
    "run_project_check",
    "run_system_command",
    "browser_automation",
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


def _current_tool_definitions() -> list:
    if not _proactive_safe_mode():
        return tool_definitions
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
    if provider == MODEL_PROVIDER_OPENROUTER:
        return (
            provider,
            *_openrouter_client_signature(
                settings.get("host", DEFAULT_OPENROUTER_HOST),
                settings.get("timeout_seconds", DEFAULT_OPENROUTER_TIMEOUT_SECONDS),
                settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR),
                settings.get("api_key", ""),
            ),
        )
    return (
        MODEL_PROVIDER_OLLAMA,
        *_ollama_client_signature(
            settings.get("host", DEFAULT_OLLAMA_HOST),
            settings.get("timeout_seconds", DEFAULT_OLLAMA_TIMEOUT_SECONDS),
            settings.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR),
        ),
    )


def _build_model_client(settings: dict):
    if settings.get("provider") == MODEL_PROVIDER_OPENROUTER:
        return _build_openrouter_client(
            settings.get("host", DEFAULT_OPENROUTER_HOST),
            settings.get("timeout_seconds", DEFAULT_OPENROUTER_TIMEOUT_SECONDS),
            settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR),
            settings.get("api_key", ""),
        )
    return _build_ollama_client(
        settings.get("host", DEFAULT_OLLAMA_HOST),
        settings.get("timeout_seconds", DEFAULT_OLLAMA_TIMEOUT_SECONDS),
        settings.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR),
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
            "api_key": OPENROUTER_API_KEY,
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
    if provider == MODEL_PROVIDER_OPENROUTER:
        settings = model_provider.get("openrouter", {})
        return settings if isinstance(settings, dict) else {}
    settings = model_provider.get("ollama", state.get("ollama", {}))
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
    provider_model_env = (
        "YARBIS_OPENROUTER_MODEL"
        if provider == MODEL_PROVIDER_OPENROUTER
        else "YARBIS_OLLAMA_MODEL"
    )
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
        "provider_label": "OpenRouter" if provider == MODEL_PROVIDER_OPENROUTER else "Ollama",
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
    global OLLAMA_FALLBACK_MODELS, OLLAMA_HOST, OLLAMA_API_KEY_ENV_VAR, OLLAMA_TIMEOUT_SECONDS
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
- Aprende y adaptate al usuario: guarda contexto personal estable con `update_profile` y hallazgos utiles con `save_note`.
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
- Para creacion de contenido en redes sociales, aterriza nicho, audiencia, objetivo, plataforma, tono, oferta/CTA y restricciones de marca antes de producir piezas definitivas. Puedes crear briefs, calendarios, drafts, captions, guiones, hashtags y publicaciones pendientes con las tools sociales.
- Nunca publiques en redes sociales sin confirmacion exacta del usuario usando `PUBLICAR <id>` y la tool `confirm_social_publication`. Para perfil personal de Facebook usa solo flujo asistido con `open_assisted_social_post`; no intentes publicar automaticamente ni simular el click final.
- Facebook Pages, Instagram profesional y LinkedIn pueden publicarse por API si hay cuentas conectadas. Perfil personal de Facebook no usa Graph API para publicar; prepara el copy, copia al portapapeles y abre Facebook o Share Dialog para que el usuario haga el click final.
- Respeta la politica de internet visible en el estado. Si el usuario pide cambiarla, usa `update_internet_settings`.
- Cuando necesites una respuesta del usuario, usa `request_user_input` con una sola pregunta clara y concreta, explica brevemente por que falta ese dato y detente. No sigas produciendo contenido que dependa de esa respuesta.
- No uses el autoconocimiento como saludo ni como relleno. No te presentes con listas de capacidades salvo que el usuario pregunte que puedes hacer.
- No menciones sistema operativo, CPU, GPU, RAM, arquitectura o hardware salvo que el usuario lo pida o la tarea lo requiera. Si lo mencionas, copia valores verificados literalmente desde herramientas/autoconocimiento; nunca infieras marca o modelo. `AMD64` significa arquitectura x86_64, no procesador AMD.
- Manten las tareas sincronizadas: usa `update_task_status` para moverlas a `in_progress`, `blocked` o `done`.
- Si una tarea queda frenada por falta de informacion del usuario, marcalo con `update_task_status(..., status="blocked", result="...")`.
- Para consultar o eliminar notas persistentes, usa `list_notes`, `get_note` y `delete_note`.
- Tienes autoconocimiento local: identidad, mapa de codigo fuente, sistema operativo y hardware actual. Si necesitas refrescarlo o verlo completo, usa `self_overview`.
- El pulso proactivo puede incluir un snapshot de contexto local de la PC: presencia/idle, proceso en primer plano si esta permitido, salud del sistema y cambios recientes del workspace. Usalo solo como senal auxiliar; no lo trates como certeza absoluta ni reveles detalles sensibles si no aportan.
- El pulso proactivo del servicio corre con acceso completo a las herramientas disponibles del agente cuando el objetivo lo requiera. Si una accion depende de datos que el contexto local no entrega, obtenlos con herramientas disponibles o pide contexto al usuario.
- El servicio administrado por SCM solo inicia, detiene o registra el proceso de fondo. No digas que SCM impide usar herramientas, ver notas, actualizar tareas o ejecutar ciclos; esas acciones dependen del servicio activo, permisos del proceso y herramientas disponibles. Instalar, quitar o reconfigurar el servicio puede requerir administrador.
- Antes de actuar a ciegas, revisa el estado con `agent_overview`, `list_tasks` o `list_notes`.
- Antes de razonar sobre tu propio codigo con detalle, usa `self_overview`, `list_files` o `read_text_file` segun haga falta.
- Antes de editar archivos de codigo, lee primero el archivo actual con `read_text_file`.
- `write_text_file` crea un checkpoint automatico y devuelve un diff. Puede trabajar fuera del workspace; manten los cambios pequenos, enfocados y bien entendidos.
- Para tareas de coding en un repositorio local, usa las tools `coding_*`: configura el workspace con `coding_set_workspace`, inspecciona con `coding_workspace_overview`, `coding_list_files`, `coding_read_text_file`, revisa Git con `coding_git_status`/`coding_git_diff` y genera cambios con `coding_propose_text_file`.
- El modo de coding por defecto es `propose_first`: no modifiques archivos del workspace de codigo activo con `write_text_file`; crea propuestas y espera aprobacion explicita del usuario antes de llamar `coding_apply_proposal`.
- Cuando el usuario apruebe una propuesta concreta o diga que la apliques identificando el cambio, usa `coding_apply_proposal`; despues ejecuta `coding_run_validation` o una validacion apropiada y reporta evidencia.
- Si una propuesta queda obsoleta o el usuario la rechaza, usa `coding_discard_proposal`.
- Usa `run_system_command` para comandos arbitrarios del sistema cuando una tarea lo necesite. Usa `open_system_target`, `compose_email` y `create_calendar_event` para integraciones locales con apps del sistema.
- Despues de modificar codigo o tests, ejecuta `run_project_tests`; antes de cerrar cambios grandes, usa `run_project_check` para tests Python y build .NET.
- Si un cambio rompe algo, revisa `list_checkpoints` y usa `restore_checkpoint` para volver al estado anterior.
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
        note_limit=10,
        include_runtime=False,
        include_last_result=False,
    )
    self_knowledge = state.get("self_knowledge", {})
    if not isinstance(self_knowledge, dict):
        self_knowledge = {}
    self_summary = str(self_knowledge.get("summary", "")).strip()
    if not self_summary:
        self_summary = (
            "Pendiente de autoanalisis. Usa `self` para refrescar identidad, "
            "codigo fuente, sistema operativo y hardware."
        )
    temporal_context = _format_local_temporal_context()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {
            "role": "system",
            "content": (
                f"{memory_contract}\n\n"
                f"{temporal_context}\n\n"
                f"Contexto actual del agente:\n{state_summary}\n\n"
                f"Autoconocimiento de Yarbis:\n{self_summary}"
            ),
        },
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
    elif _host_uses_ollama_cloud(OLLAMA_HOST) and not os.getenv(OLLAMA_API_KEY_ENV_VAR, "").strip():
        cloud_hint = (
            f"\nPara Ollama Cloud directo, define `{OLLAMA_API_KEY_ENV_VAR}` "
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


def _handle_empty_response(state):
    error_text = "El modelo devolvio una respuesta vacia en este ciclo."
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
            errors.append(f"{model_name}: {exc}")

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
        return (
            "No tengo una tarea concreta registrada. Dime que quieres que haga y "
            "avanzare sin inventar datos del equipo."
        )

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

    state_transaction(
        "run_one_cycle_increment",
        lambda current_state: current_state.__setitem__(
            "cycle_count",
            current_state["cycle_count"] + 1,
        ),
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

    for step in range(1, max_steps + 1):
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

                function_to_call = available_functions.get(tool_name)
                if _proactive_safe_mode() and tool_name not in PROACTIVE_SAFE_TOOL_NAMES:
                    tool_output = (
                        "Tool no permitida durante el pulso proactivo seguro: "
                        f"{tool_name}. Registra una tarea o pide confirmacion para ejecutarla fuera del pulso."
                    )
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
                retry_message = {
                    "role": "user",
                    "content": (
                        "Tu respuesta anterior llego vacia. Responde ahora con una salida util, "
                        "concreta y final para este ciclo."
                    ),
                }
                state_transaction(
                    "record_empty_response_retry",
                    lambda current_state: current_state["messages"].append(retry_message),
                )
                state = load_state()
                continue

            final_text = _handle_empty_response(state)
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
            and step < max_steps
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


def run_autonomous_session(cycles=None, model_override: str | None = None):
    state = load_state()
    if cycles is None:
        cycles = state["autonomy"]["auto_cycles_default"]

    completed_cycles = 0
    saw_tasks = bool(state["tasks"])

    for _ in range(cycles):
        current_state = load_state()
        if _stop_requested_for_operation(current_state):
            print("\nYarbis: operacion detenida por solicitud del usuario.")
            break

        if _is_waiting_for_user_input(current_state):
            print("\nYarbis: estoy esperando una respuesta del usuario antes de continuar.")
            print(f"Pregunta pendiente: {current_state['awaiting_user_input']['question']}")
            break

        cycle_result = run_one_cycle(model_override=model_override)
        if not isinstance(cycle_result, dict):
            cycle_result = {
                "status": "error",
                "content": str(cycle_result),
                "used_tools": False,
                "looks_meta": False,
                "needs_user_input": False,
            }
        completed_cycles += 1

        updated_state = load_state()
        saw_tasks = saw_tasks or bool(updated_state["tasks"])
        if cycle_result.get("status") == "cancelled" or _stop_requested_for_operation(updated_state):
            print("\nYarbis: operacion detenida por solicitud del usuario.")
            break

        if cycle_result.get("needs_user_input") or _is_waiting_for_user_input(updated_state):
            print("\nYarbis: falta informacion del usuario. Deteniendo modo autonomo.")
            break

        if saw_tasks and not _has_open_tasks(updated_state):
            print("\nYarbis: no quedan tareas abiertas. Deteniendo modo autonomo.")
            break

        if (
            not _has_open_tasks(updated_state)
            and cycle_result["status"] in {"final", "empty", "error"}
            and not cycle_result["used_tools"]
        ):
            print("\nYarbis: no hay tareas abiertas y este ciclo ya cerro sin seguimiento adicional.")
            break

        if cycle_result["looks_meta"] and not _has_open_tasks(updated_state):
            print("\nYarbis: el modelo se quedo describiendo el proceso sin abrir tareas. Deteniendo modo autonomo.")
            break

    return completed_cycles
