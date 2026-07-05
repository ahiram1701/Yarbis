import contextlib
import ctypes
import io
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows path is covered locally.
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX fallback only.
    fcntl = None

import activity
import yarbis_instance
from intent_text import (
    looks_like_affirmative_action_reply as _looks_like_affirmative_action_reply,
    normalize_intent_text as _normalize_intent_text,
)
from memory import (
    DEFAULT_MODEL_PROVIDER,
    DEFAULT_OLLAMA_API_KEY_ENV_VAR,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_OPENROUTER_API_KEY_ENV_VAR,
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
    MAX_OLLAMA_API_KEY_CHARS,
    MAX_OPENROUTER_API_KEY_CHARS,
    MAX_OLLAMA_MODEL_CHARS,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    VALID_LOCAL_CONTEXT_MODES,
    VALID_MODEL_PROVIDERS,
    MAX_OLLAMA_TIMEOUT_SECONDS,
    MIN_OLLAMA_TIMEOUT_SECONDS,
    default_state,
    format_cycle_count,
    load_state,
    normalize_cycle_count,
    render_state_summary,
    save_state,
    state_transaction,
)
from notifications import (
    get_telegram_settings,
    notify_user_input_required,
    send_telegram_operation_reply,
    send_notification,
    try_link_telegram_chat,
)
from self_knowledge import get_cached_source_signature, render_self_knowledge_summary
from tools import (
    add_task,
    coding_apply_and_validate,
    coding_apply_proposal,
    coding_check_proposal,
    coding_detect_validation_command,
    coding_discard_proposal,
    coding_git_status,
    coding_get_proposal,
    coding_list_proposals,
    coding_propose_edits,
    coding_run_validation,
    coding_read_text_range,
    coding_search_text,
    coding_set_workspace,
    coding_update_validation_command,
    coding_validation_plan,
    coding_workflow_status,
    coding_workspace_overview,
    create_idea_project,
    create_memory_backup,
    create_project_visual_board,
    delete_note,
    export_project_visual_board,
    get_idea_project,
    get_project_visual_board,
    get_note,
    import_memory_backup,
    inspect_memory_backup,
    list_idea_projects,
    list_memory_backups,
    list_notes,
    list_project_visual_boards,
    list_social_publications,
    memory_protection_status,
    open_assisted_social_post,
    save_note,
    social_accounts_overview,
    start_social_oauth,
    promote_idea_project_to_work,
    update_goal as update_goal_tool,
    update_idea_project,
    update_memory_protection_settings,
    update_project_visual_board,
    update_profile,
    verify_memory_backups,
    evolution_status,
    evolution_set_enabled,
    evolution_set_interval,
    evolution_list_pending,
    evolution_list_directives,
    evolution_apply_directive,
    evolution_discard_directive,
    evolution_list_suggestions,
    evolution_apply_suggestion,
    evolution_discard_suggestion,
    analyze_image,
    vision_status,
    vision_set_model,
)
from service_manager import format_readiness_status, readiness_status

SESSION_LOCK = threading.RLock()
OPERATION_LOCK_FILE = yarbis_instance.operation_lock_file()
_OPERATION_LOCK_LOCAL = threading.local()
_OPERATION_LOCK_POLL_SECONDS = 0.25
_WINDOWS_SYNCHRONIZE = 0x00100000
_WINDOWS_STILL_ACTIVE = 259
_STARTUP_SELF_ANALYSIS_MAX_AGE_SECONDS = 300
_SELF_ANALYSIS_BACKGROUND_LOCK = threading.RLock()
_SELF_ANALYSIS_BACKGROUND_RUNNING = False
FACTORY_RESET_RUNTIME_FILES = (
    "service.log",
    "service.stop",
    "telegram_deferred_replies.json",
    "telegram_deferred_replies.json.tmp",
    "pc_context_latest.json",
    "pc_context_latest.json.tmp",
    "pc_context_events.jsonl",
    "pc_context_helper.pid",
    "pc_context_helper.stop",
    "pc_context_helper.status.json",
    "pc_context_helper.status.json.tmp",
)
FACTORY_RESET_RUNTIME_DIRS = (
    "browser",
    "calendar",
    "coding_proposals",
)


class SessionOperationBusy(RuntimeError):
    pass


def _load_agent():
    import agent

    return agent


def run_one_cycle(*args, **kwargs):
    return _load_agent().run_one_cycle(*args, **kwargs)


def run_autonomous_session(*args, **kwargs):
    return _load_agent().run_autonomous_session(*args, **kwargs)


def cancel_active_ollama_request(*args, **kwargs):
    return _load_agent().cancel_active_ollama_request(*args, **kwargs)


def _prepare_operation_lock_file(handle):
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b" ")
        handle.flush()
    handle.seek(0)


def _lock_operation_handle(handle, blocking: bool) -> bool:
    if msvcrt is not None:
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if not blocking:
                    return False
                time.sleep(_OPERATION_LOCK_POLL_SECONDS)

    if fcntl is not None:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
            return True
        except (BlockingIOError, OSError):
            return False

    return True


def _unlock_operation_handle(handle):
    try:
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _write_operation_lock_owner(handle, label: str, operation_id: str):
    try:
        owner = (
            f"pid={os.getpid()}\n"
            f"label={str(label).strip() or 'Operacion'}\n"
            f"operation_id={str(operation_id).strip()}\n"
            f"started_at={datetime.now(timezone.utc).isoformat()}\n"
        )
        handle.seek(0)
        handle.truncate()
        handle.write(owner.encode("utf-8", errors="replace"))
        handle.flush()
    except OSError:
        pass


def _acquire_operation_file_lock(label: str, blocking: bool, operation_id: str):
    OPERATION_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(OPERATION_LOCK_FILE, "a+b")
    try:
        _prepare_operation_lock_file(handle)
        if not _lock_operation_handle(handle, blocking=blocking):
            handle.close()
            return None
        _write_operation_lock_owner(handle, label, operation_id)
        return handle
    except Exception:
        handle.close()
        raise


def _release_operation_file_lock(handle):
    try:
        _unlock_operation_handle(handle)
    finally:
        handle.close()


def _runtime_operation_source_pid(source: object) -> int | None:
    rendered = str(source or "").strip()
    if not rendered.startswith("pid:"):
        return None

    try:
        pid = int(rendered.split(":", 1)[1])
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True

    if os.name == "nt":
        try:
            handle = ctypes.windll.kernel32.OpenProcess(_WINDOWS_SYNCHRONIZE, False, pid)
        except Exception:
            return True

        if not handle:
            return False

        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == _WINDOWS_STILL_ACTIVE
        finally:
            try:
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def clear_abandoned_runtime_operation() -> bool:
    def mutate(state):
        runtime = state.get("runtime", {})
        if not isinstance(runtime, dict):
            return False

        thinking = runtime.get("thinking", {})
        if not isinstance(thinking, dict):
            thinking = {}

        stop_requested = runtime.get("stop_requested", {})
        if not isinstance(stop_requested, dict):
            stop_requested = {}

        source_pid = _runtime_operation_source_pid(thinking.get("source", ""))
        operation_active = bool(thinking.get("active") and str(thinking.get("label", "")).strip())
        should_clear = bool(
            (operation_active and source_pid and not _pid_is_running(source_pid))
            or (stop_requested.get("active") and not operation_active)
        )
        if not should_clear:
            return False

        state["runtime"] = default_state()["runtime"]
        return True

    return bool(state_transaction("runtime_abandoned_operation_clear", mutate, create_backup=False))


def _set_runtime_thinking(label: str, operation_id: str):
    try:
        def mutate(state):
            state["runtime"] = {
                "thinking": {
                    "active": True,
                    "label": str(label).strip() or "Operacion",
                    "source": f"pid:{os.getpid()}",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "operation_id": str(operation_id).strip(),
                },
                "stop_requested": {
                    "active": False,
                    "operation_id": "",
                    "requested_at": "",
                    "source": "",
                    "reason": "",
                },
            }

        state_transaction("runtime_thinking_start", mutate, create_backup=False)
    except Exception:
        pass


def _clear_runtime_thinking():
    try:
        def mutate(state):
            state["runtime"] = {
                "thinking": {
                    "active": False,
                    "label": "",
                    "source": "",
                    "started_at": "",
                    "operation_id": "",
                },
                "stop_requested": {
                    "active": False,
                    "operation_id": "",
                    "requested_at": "",
                    "source": "",
                    "reason": "",
                },
            }

        state_transaction("runtime_thinking_clear", mutate, create_backup=False)
    except Exception:
        pass


def request_stop_current_operation(source: str = "usuario") -> str:
    requested_at = datetime.now(timezone.utc).isoformat()
    cleaned_source = str(source).strip() or "usuario"

    def mutate(state):
        runtime = state.setdefault("runtime", {})
        thinking = runtime.get("thinking", {})
        if not isinstance(thinking, dict):
            thinking = {}

        label = str(thinking.get("label", "")).strip() or "Operacion"
        operation_id = str(thinking.get("operation_id", "")).strip()
        if not (thinking.get("active") and operation_id):
            return "", ""

        runtime["stop_requested"] = {
            "active": True,
            "operation_id": operation_id,
            "requested_at": requested_at,
            "source": cleaned_source,
            "reason": "Solicitud explicita del usuario para detener la operacion en curso.",
        }
        state["runtime"] = runtime
        return label, operation_id

    label, operation_id = state_transaction("runtime_stop_requested", mutate, create_backup=False)
    if not operation_id:
        return "Yarbis no esta pensando ahora."

    try:
        cancel_active_ollama_request()
    except Exception:
        pass

    activity.emit_event(
        "operation_stop_requested",
        operation_id=operation_id,
        label=label,
        source=cleaned_source,
    )
    return f"Solicitud de parada enviada para {label}."


@contextlib.contextmanager
def session_operation_lock(label: str = "Operacion", blocking: bool = True):
    depth = int(getattr(_OPERATION_LOCK_LOCAL, "depth", 0) or 0)
    if depth > 0:
        _OPERATION_LOCK_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _OPERATION_LOCK_LOCAL.depth = depth
        return

    if not SESSION_LOCK.acquire(blocking=blocking):
        raise SessionOperationBusy("Ya hay una operacion de Yarbis en curso.")

    operation_label = str(label).strip() or "Operacion"
    operation_id = activity.new_operation_id(operation_label)
    try:
        handle = _acquire_operation_file_lock(
            label=operation_label,
            blocking=blocking,
            operation_id=operation_id,
        )
        if handle is None:
            activity.emit_event(
                "operation_busy",
                operation_id=operation_id,
                label=operation_label,
                blocking=blocking,
            )
            raise SessionOperationBusy("Ya hay una operacion de Yarbis en curso.")

        _OPERATION_LOCK_LOCAL.depth = 1
        _OPERATION_LOCK_LOCAL.operation_id = operation_id
        activity.emit_event("operation_started", operation_id=operation_id, label=operation_label)
        _set_runtime_thinking(operation_label, operation_id)
        try:
            yield
        except Exception as exc:
            activity.emit_event(
                "operation_failed",
                operation_id=operation_id,
                label=operation_label,
                error=str(exc),
            )
            raise
        else:
            activity.emit_event("operation_finished", operation_id=operation_id, label=operation_label)
        finally:
            _OPERATION_LOCK_LOCAL.depth = 0
            _OPERATION_LOCK_LOCAL.operation_id = ""
            _clear_runtime_thinking()
            _release_operation_file_lock(handle)
    finally:
        SESSION_LOCK.release()


def current_operation_id() -> str:
    return str(getattr(_OPERATION_LOCK_LOCAL, "operation_id", "") or "").strip()


def has_pending_user_question(state) -> bool:
    awaiting_user_input = state.get("awaiting_user_input", {})
    return bool(
        awaiting_user_input.get("pending")
        and str(awaiting_user_input.get("question", "")).strip()
    )


def has_unanswered_user_message(state) -> bool:
    messages = state.get("messages", [])
    if not messages:
        return False

    last_message = messages[-1]
    if not isinstance(last_message, dict):
        return False

    return bool(
        last_message.get("role") == "user"
        and str(last_message.get("content", "")).strip()
    )


def clear_pending_user_question(state):
    state["awaiting_user_input"] = {
        "pending": False,
        "question": "",
        "reason": "",
        "fields": [],
    }


def _message_content_for_user_reply(cleaned_reply: str, state: dict, had_pending_question: bool) -> str:
    if not had_pending_question or not _looks_like_affirmative_action_reply(cleaned_reply):
        return cleaned_reply

    awaiting_user_input = state.get("awaiting_user_input", {})
    question = str(awaiting_user_input.get("question", "")).strip()
    reason = str(awaiting_user_input.get("reason", "")).strip()

    context_lines = [
        cleaned_reply,
        "",
        "Contexto para Yarbis: respuesta afirmativa a la pregunta pendiente.",
    ]
    if question:
        context_lines.append(f"Pregunta pendiente: {question}")
    if reason:
        context_lines.append(f"Motivo original: {reason}")
    context_lines.append(
        "Interpretacion operativa: el usuario autorizo avanzar con la propuesta "
        "anterior. Ejecuta el siguiente paso util con herramientas cuando aplique "
        "y no vuelvas a pedir confirmacion general."
    )
    return "\n".join(context_lines)


def is_self_analysis_request(text: str) -> bool:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return False

    exact_commands = {
        "self",
        "self overview",
        "autoanalisis",
        "auto analisis",
        "hazte un autoanalisis",
        "hazte un auto analisis",
        "refresca tu autoanalisis",
        "refresca tu auto analisis",
    }
    if normalized in exact_commands:
        return True

    direct_phrases = (
        "autoanalisis",
        "auto analisis",
        "self overview",
        "conocete",
        "que sabes de ti",
        "quien eres y donde estas",
    )
    return any(phrase in normalized for phrase in direct_phrases)


def _self_knowledge_age_seconds(self_knowledge: dict) -> float:
    analyzed_at = str(self_knowledge.get("last_analyzed_at", "")).strip()
    if not analyzed_at:
        return float("inf")

    try:
        parsed = datetime.fromisoformat(analyzed_at.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def _self_knowledge_needs_refresh(self_knowledge: dict) -> bool:
    if not str(self_knowledge.get("summary", "")).strip():
        return True
    if not str(self_knowledge.get("source_signature", "")).strip():
        return True
    return _self_knowledge_age_seconds(self_knowledge) > _STARTUP_SELF_ANALYSIS_MAX_AGE_SECONDS


def _persist_self_knowledge_summary(summary: str) -> None:
    source_signature = get_cached_source_signature()
    analyzed_at = datetime.now(timezone.utc).isoformat()

    def mutate(state):
        state["self_knowledge"] = {
            "last_analyzed_at": analyzed_at,
            "summary": summary,
            "source_signature": source_signature,
        }

    state_transaction("startup_self_analysis", mutate)


def _refresh_self_knowledge_state() -> str:
    summary = render_self_knowledge_summary(refresh=True)
    _persist_self_knowledge_summary(summary)
    return (
        "Autoanalisis inicial completado. "
        "Yarbis actualizo identidad, codigo fuente, sistema operativo y hardware."
    )


def _background_self_analysis_worker():
    global _SELF_ANALYSIS_BACKGROUND_RUNNING

    try:
        message = _refresh_self_knowledge_state()
        try:
            activity.append_activity("Autoanalisis inicial", message)
        except Exception:
            pass
    except Exception as exc:
        activity.emit_event(
            "startup_self_analysis_failed",
            label="Autoanalisis inicial",
            error=str(exc),
        )
    finally:
        with _SELF_ANALYSIS_BACKGROUND_LOCK:
            _SELF_ANALYSIS_BACKGROUND_RUNNING = False


def _schedule_startup_self_analysis_refresh() -> bool:
    global _SELF_ANALYSIS_BACKGROUND_RUNNING

    with _SELF_ANALYSIS_BACKGROUND_LOCK:
        if _SELF_ANALYSIS_BACKGROUND_RUNNING:
            return False
        _SELF_ANALYSIS_BACKGROUND_RUNNING = True

    thread = threading.Thread(
        target=_background_self_analysis_worker,
        name="yarbis-startup-self-analysis",
        daemon=True,
    )
    thread.start()
    return True


def run_startup_self_analysis(force: bool = False, background: bool = True) -> str:
    if force or not background:
        with SESSION_LOCK:
            return _refresh_self_knowledge_state()

    state = load_state()
    self_knowledge = state.get("self_knowledge", {})
    if not isinstance(self_knowledge, dict):
        self_knowledge = {}

    summary = str(self_knowledge.get("summary", "")).strip()
    if not summary:
        scheduled = _schedule_startup_self_analysis_refresh()
        if scheduled:
            return (
                "Autoanalisis inicial pendiente. "
                "Yarbis lo refrescara en segundo plano para no bloquear el arranque."
            )
        return "Autoanalisis inicial pendiente; el refresh en segundo plano ya esta activo."

    if _self_knowledge_needs_refresh(self_knowledge):
        scheduled = _schedule_startup_self_analysis_refresh()
        if scheduled:
            return (
                "Autoanalisis inicial reutilizado desde cache. "
                "Yarbis refrescara identidad, codigo fuente, sistema operativo y hardware en segundo plano."
            )
        return "Autoanalisis inicial reutilizado desde cache; el refresh en segundo plano ya esta activo."

    return (
        "Autoanalisis inicial reutilizado desde cache. "
        "Yarbis conserva identidad, codigo fuente, sistema operativo y hardware."
    )


def run_self_analysis_with_output(emit_notifications: bool = True) -> str:
    with SESSION_LOCK:
        message = run_startup_self_analysis(force=True, background=False)
        state = load_state()
        summary = state.get("self_knowledge", {}).get("summary", "").strip()
        if not summary:
            result = message
        else:
            result = f"{message}\n\n{summary}"

    _mirror_telegram_response("Autoanalisis", result, enabled=emit_notifications)
    return result


def _mirror_telegram_response(label: str, content: str, enabled: bool = True) -> bool:
    if not enabled:
        return False

    rendered = str(content).strip()
    if not rendered:
        return False

    try:
        return send_telegram_operation_reply(label, rendered)
    except Exception:
        return False


def _has_open_tasks(state) -> bool:
    return any(
        task["status"] in {"pending", "in_progress", "blocked"}
        for task in state.get("tasks", [])
    )


def update_goal(new_goal: str) -> str:
    with SESSION_LOCK:
        result = update_goal_tool(new_goal)
        if result.startswith("El objetivo no puede quedar vacio"):
            raise ValueError("El objetivo no puede quedar vacio.")
        return result


def get_status_text() -> str:
    with SESSION_LOCK:
        return render_state_summary(load_state())


def get_readiness_text(force: bool = False, compact: bool = False) -> str:
    return format_readiness_status(readiness_status(force=force), compact=compact)


def _clear_factory_runtime_artifacts():
    runtime_dir = activity.RUNTIME_DIR
    for file_name in FACTORY_RESET_RUNTIME_FILES:
        try:
            (runtime_dir / file_name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    for dir_name in FACTORY_RESET_RUNTIME_DIRS:
        try:
            shutil.rmtree(runtime_dir / dir_name)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def should_clear_activity_for_first_run(state: dict | None = None) -> bool:
    state = load_state() if state is None else state
    if str(state.get("goal", "")).strip():
        return False

    try:
        cycle_count = int(state.get("cycle_count", 0) or 0)
    except (TypeError, ValueError):
        cycle_count = 0

    awaiting_user_input = state.get("awaiting_user_input", {})
    if not isinstance(awaiting_user_input, dict):
        awaiting_user_input = {}

    return not any((
        cycle_count,
        str(state.get("last_result", "")).strip(),
        state.get("messages", []),
        state.get("notes", []),
        state.get("tasks", []),
        state.get("current_plan", []),
        awaiting_user_input.get("pending"),
    ))


def clear_activity_for_first_run_if_needed() -> bool:
    with SESSION_LOCK:
        if not should_clear_activity_for_first_run(load_state()):
            return False
        activity.clear_activity_history()
        return True


def factory_reset_yarbis() -> str:
    with SESSION_LOCK:
        save_state(default_state())
        activity.clear_activity_history()
        _clear_factory_runtime_artifacts()
        try:
            OPERATION_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            OPERATION_LOCK_FILE.write_text(" ", encoding="utf-8")
        except OSError:
            pass

    return (
        "Yarbis quedo reiniciado de fabrica. "
        "Estado, memoria, configuracion local, actividad y temporales fueron limpiados."
    )


def get_ui_theme() -> str:
    with SESSION_LOCK:
        state = load_state()
        return state.get("ui", {}).get("theme", "dark")


def update_ui_theme(theme: str) -> str:
    with SESSION_LOCK:
        cleaned_theme = str(theme).strip().lower()
        if cleaned_theme not in {"light", "dark"}:
            raise ValueError("Tema invalido. Usa 'light' o 'dark'.")

        def mutate(state):
            state.setdefault("ui", {})
            state["ui"]["theme"] = cleaned_theme

        state_transaction("update_ui_theme", mutate)
        return f"Tema actualizado a {cleaned_theme}."


def _provider_label(provider: str) -> str:
    return "OpenRouter" if provider == MODEL_PROVIDER_OPENROUTER else "Ollama"


def _parse_fallback_models(fallback_models, current=None) -> list[str]:
    if fallback_models is None:
        return list(current or [])
    if isinstance(fallback_models, str):
        return [
            item.strip()
            for item in re.split(r"[,;\n]+", fallback_models)
            if item.strip()
        ]
    if isinstance(fallback_models, list):
        return [str(item).strip() for item in fallback_models if str(item).strip()]
    return []


def _parse_model_timeout(timeout_seconds: int) -> int:
    try:
        cleaned_timeout = int(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("El timeout debe ser un numero de segundos.") from exc

    if not MIN_OLLAMA_TIMEOUT_SECONDS <= cleaned_timeout <= MAX_OLLAMA_TIMEOUT_SECONDS:
        raise ValueError(
            "El timeout debe estar entre "
            f"{MIN_OLLAMA_TIMEOUT_SECONDS} y {MAX_OLLAMA_TIMEOUT_SECONDS} segundos."
        )
    return cleaned_timeout


def _normalize_provider_host(host: str, *, default: str = "", strip_ollama_api: bool = False) -> str:
    cleaned_host = str(host).strip()
    if not cleaned_host:
        return str(default).strip().rstrip("/")
    if strip_ollama_api and cleaned_host.endswith("/api"):
        cleaned_host = cleaned_host[:-4].rstrip("/")
    if cleaned_host.endswith("/chat/completions"):
        cleaned_host = cleaned_host[: -len("/chat/completions")].rstrip("/")
    return cleaned_host.rstrip("/")


def get_model_provider_settings() -> dict:
    state = load_state()
    settings = state.get("model_provider", {})
    if not isinstance(settings, dict):
        settings = default_state()["model_provider"]
    return settings


def get_default_model_provider() -> str:
    provider = str(get_model_provider_settings().get("default", DEFAULT_MODEL_PROVIDER)).strip().lower()
    return provider if provider in VALID_MODEL_PROVIDERS else DEFAULT_MODEL_PROVIDER


def get_ollama_settings() -> dict:
    state = load_state()
    model_provider = state.get("model_provider", {}) if isinstance(state, dict) else {}
    if isinstance(model_provider, dict) and isinstance(model_provider.get("ollama"), dict):
        return model_provider["ollama"]
    return state.get("ollama", {
        "model": DEFAULT_OLLAMA_MODEL,
        "fallback_models": [],
        "host": DEFAULT_OLLAMA_HOST,
        "api_key": "",
        "api_key_env_var": DEFAULT_OLLAMA_API_KEY_ENV_VAR,
        "timeout_seconds": DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    })


def get_openrouter_settings() -> dict:
    model_provider = get_model_provider_settings()
    settings = model_provider.get("openrouter", {})
    if isinstance(settings, dict):
        return settings
    return {
        "model": DEFAULT_OPENROUTER_MODEL,
        "fallback_models": [],
        "host": DEFAULT_OPENROUTER_HOST,
        "api_key": "",
        "api_key_env_var": DEFAULT_OPENROUTER_API_KEY_ENV_VAR,
        "timeout_seconds": DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
    }


def update_model_provider(default_provider: str) -> str:
    cleaned_provider = str(default_provider).strip().lower()
    if cleaned_provider not in VALID_MODEL_PROVIDERS:
        raise ValueError("Proveedor invalido. Usa 'ollama' u 'openrouter'.")

    def mutate(state):
        model_provider = state.setdefault("model_provider", {})
        model_provider["default"] = cleaned_provider
        model_provider.setdefault("ollama", state.get("ollama", default_state()["ollama"]))
        model_provider.setdefault("openrouter", default_state()["model_provider"]["openrouter"])
        state["ollama"] = model_provider["ollama"]

    state_transaction("update_model_provider", mutate)
    return f"Proveedor por defecto actualizado: {_provider_label(cleaned_provider)}."


def update_ollama_settings(
    model: str,
    timeout_seconds: int,
    host: str | None = None,
    fallback_models=None,
    api_key: str | None = None,
    api_key_env_var: str | None = None,
    set_default: bool = True,
) -> str:
    cleaned_model = str(model).strip()
    if not cleaned_model:
        raise ValueError("El modelo no puede quedar vacio.")

    cleaned_timeout = _parse_model_timeout(timeout_seconds)

    current_settings = get_ollama_settings()
    cleaned_host = (
        _normalize_provider_host(host, strip_ollama_api=True)
        if host is not None
        else _normalize_provider_host(
            current_settings.get("host", DEFAULT_OLLAMA_HOST),
            strip_ollama_api=True,
        )
    )

    cleaned_api_key_env_var = (
        str(api_key_env_var).strip()
        if api_key_env_var is not None
        else str(current_settings.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR)).strip()
    ) or DEFAULT_OLLAMA_API_KEY_ENV_VAR
    cleaned_api_key = (
        str(api_key).strip()
        if api_key is not None
        else str(current_settings.get("api_key", "")).strip()
    )
    if len(cleaned_api_key) > MAX_OLLAMA_API_KEY_CHARS:
        raise ValueError("La API key de Ollama es demasiado larga.")

    cleaned_fallback_models = _parse_fallback_models(
        fallback_models,
        current=current_settings.get("fallback_models", []),
    )
    next_settings = {
        "model": cleaned_model,
        "fallback_models": cleaned_fallback_models,
        "host": cleaned_host,
        "api_key": cleaned_api_key,
        "api_key_env_var": cleaned_api_key_env_var,
        "timeout_seconds": cleaned_timeout,
    }

    def mutate(state):
        model_provider = state.setdefault("model_provider", {})
        model_provider["ollama"] = dict(next_settings)
        if set_default:
            model_provider["default"] = MODEL_PROVIDER_OLLAMA
        model_provider.setdefault("openrouter", default_state()["model_provider"]["openrouter"])
        state["ollama"] = dict(next_settings)

    state_transaction("update_ollama_settings", mutate)
    settings = load_state()["model_provider"]["ollama"]
    host_text = settings["host"] or "local"
    fallback_text = ", ".join(settings["fallback_models"]) or "-"

    return (
        "Configuracion de Ollama actualizada.\n"
        f"Modelo: {settings['model']}\n"
        f"Fallbacks: {fallback_text}\n"
        f"Host: {host_text}\n"
        f"API key: {'guardada' if settings.get('api_key') else 'no configurada'}\n"
        f"API key env: {settings['api_key_env_var']}\n"
        f"Timeout: {settings['timeout_seconds']} segundos"
    )


def update_openrouter_settings(
    model: str,
    timeout_seconds: int,
    host: str | None = None,
    fallback_models=None,
    api_key: str | None = None,
    api_key_env_var: str | None = None,
    set_default: bool = True,
) -> str:
    cleaned_model = str(model).strip()
    if not cleaned_model:
        raise ValueError("El modelo de OpenRouter no puede quedar vacio.")

    cleaned_timeout = _parse_model_timeout(timeout_seconds)
    current_settings = get_openrouter_settings()
    cleaned_host = (
        _normalize_provider_host(host, default=DEFAULT_OPENROUTER_HOST)
        if host is not None
        else _normalize_provider_host(
            current_settings.get("host", DEFAULT_OPENROUTER_HOST),
            default=DEFAULT_OPENROUTER_HOST,
        )
    )
    cleaned_api_key_env_var = (
        str(api_key_env_var).strip()
        if api_key_env_var is not None
        else str(current_settings.get("api_key_env_var", DEFAULT_OPENROUTER_API_KEY_ENV_VAR)).strip()
    ) or DEFAULT_OPENROUTER_API_KEY_ENV_VAR
    cleaned_api_key = (
        str(api_key).strip()
        if api_key is not None
        else str(current_settings.get("api_key", "")).strip()
    )
    if len(cleaned_api_key) > MAX_OPENROUTER_API_KEY_CHARS:
        raise ValueError("La API key de OpenRouter es demasiado larga.")
    cleaned_fallback_models = _parse_fallback_models(
        fallback_models,
        current=current_settings.get("fallback_models", []),
    )
    next_settings = {
        "model": cleaned_model,
        "fallback_models": cleaned_fallback_models,
        "host": cleaned_host,
        "api_key": cleaned_api_key,
        "api_key_env_var": cleaned_api_key_env_var,
        "timeout_seconds": cleaned_timeout,
    }

    def mutate(state):
        model_provider = state.setdefault("model_provider", {})
        model_provider["openrouter"] = dict(next_settings)
        if set_default:
            model_provider["default"] = MODEL_PROVIDER_OPENROUTER
        model_provider.setdefault("ollama", state.get("ollama", default_state()["ollama"]))
        state["ollama"] = model_provider["ollama"]

    state_transaction("update_openrouter_settings", mutate)
    settings = load_state()["model_provider"]["openrouter"]
    fallback_text = ", ".join(settings["fallback_models"]) or "-"

    return (
        "Configuracion de OpenRouter actualizada.\n"
        f"Modelo: {settings['model']}\n"
        f"Fallbacks: {fallback_text}\n"
        f"Host: {settings['host']}\n"
        f"API key: {'guardada' if settings.get('api_key') else 'no configurada'}\n"
        f"API key env: {settings['api_key_env_var']}\n"
        f"Timeout: {settings['timeout_seconds']} segundos"
    )


def get_service_proactive_settings() -> dict:
    with SESSION_LOCK:
        return load_state().get("service", {}).get("proactive", {})


def update_service_proactive_settings(
    enabled: bool,
    interval_seconds: int,
    cycles: int | str | None,
    start_delay_seconds: int,
    model: str | None = None,
) -> str:
    with SESSION_LOCK:
        try:
            cleaned_interval = int(interval_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("El intervalo debe ser un numero de segundos.") from exc

        cleaned_cycles = normalize_cycle_count(cycles, default=0)
        if cleaned_cycles == 0:
            raise ValueError(
                "Los ciclos por pulso deben ser un numero positivo o quedar vacios para no limitar."
            )

        try:
            cleaned_start_delay = int(start_delay_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("La espera inicial debe ser un numero de segundos.") from exc

        if not 60 <= cleaned_interval <= 24 * 60 * 60:
            raise ValueError("El intervalo debe estar entre 60 y 86400 segundos.")
        if not 0 <= cleaned_start_delay <= 24 * 60 * 60:
            raise ValueError("La espera inicial debe estar entre 0 y 86400 segundos.")

        current_settings = get_service_proactive_settings()
        cleaned_model = (
            str(model).strip()
            if model is not None
            else str(current_settings.get("model", "")).strip()
        )
        if len(cleaned_model) > MAX_OLLAMA_MODEL_CHARS:
            raise ValueError(f"El modelo del pulso no puede exceder {MAX_OLLAMA_MODEL_CHARS} caracteres.")

        def mutate(state):
            service_state = state.setdefault("service", {})
            proactive_state = service_state.setdefault("proactive", {})
            proactive_state.update({
                "enabled": bool(enabled),
                "interval_seconds": cleaned_interval,
                "cycles": cleaned_cycles,
                "start_delay_seconds": cleaned_start_delay,
                "model": cleaned_model,
            })

        state_transaction("update_service_proactive_settings", mutate)
        settings = load_state()["service"]["proactive"]

        status = "activo" if settings["enabled"] else "desactivado"
        model_text = settings.get("model") or "Ollama principal"
        return (
            "Pulso proactivo actualizado.\n"
            f"Estado: {status}\n"
            f"Intervalo: {settings['interval_seconds']} segundos\n"
            f"Ciclos por pulso: {format_cycle_count(settings['cycles'])}\n"
            f"Espera inicial: {settings['start_delay_seconds']} segundos\n"
            f"Modelo: {model_text}"
        )


def get_local_context_settings() -> dict:
    with SESSION_LOCK:
        return load_state().get("local_context", {})


def update_local_context_settings(
    enabled: bool,
    mode: str,
    sample_interval_seconds: int,
    max_snapshot_age_seconds: int,
    include_window_title: bool = False,
    include_process_name: bool = True,
    include_workspace_changes: bool = True,
    include_system_health: bool = True,
) -> str:
    with SESSION_LOCK:
        cleaned_mode = str(mode).strip().lower() or "safe"
        if cleaned_mode not in VALID_LOCAL_CONTEXT_MODES:
            raise ValueError("Modo de contexto local invalido. Usa off, safe o detailed.")

        try:
            cleaned_sample_interval = int(sample_interval_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("El intervalo de contexto local debe ser un numero de segundos.") from exc

        try:
            cleaned_max_age = int(max_snapshot_age_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("La vigencia del snapshot debe ser un numero de segundos.") from exc

        if not 5 <= cleaned_sample_interval <= 24 * 60 * 60:
            raise ValueError("El intervalo de contexto local debe estar entre 5 y 86400 segundos.")
        if not 15 <= cleaned_max_age <= 24 * 60 * 60:
            raise ValueError("La vigencia del snapshot debe estar entre 15 y 86400 segundos.")

        cleaned_enabled = bool(enabled) and cleaned_mode != "off"
        cleaned_include_window_title = bool(include_window_title) and cleaned_mode == "detailed"

        def mutate(state):
            state["local_context"] = {
                "enabled": cleaned_enabled,
                "mode": cleaned_mode,
                "sample_interval_seconds": cleaned_sample_interval,
                "max_snapshot_age_seconds": cleaned_max_age,
                "include_window_title": cleaned_include_window_title,
                "include_process_name": bool(include_process_name),
                "include_workspace_changes": bool(include_workspace_changes),
                "include_system_health": bool(include_system_health),
            }

        state_transaction("update_local_context_settings", mutate)
        settings = load_state()["local_context"]
        status = "activo" if settings["enabled"] else "desactivado"
        return (
            "Contexto local actualizado.\n"
            f"Estado: {status}\n"
            f"Modo: {settings['mode']}\n"
            f"Intervalo: {settings['sample_interval_seconds']} segundos\n"
            f"Vigencia: {settings['max_snapshot_age_seconds']} segundos\n"
            f"Proceso en primer plano: {'si' if settings['include_process_name'] else 'no'}\n"
            f"Titulos de ventana: {'si' if settings['include_window_title'] else 'no'}\n"
            f"Workspace: {'si' if settings['include_workspace_changes'] else 'no'}\n"
            f"Salud del sistema: {'si' if settings['include_system_health'] else 'no'}"
        )


def get_notification_settings() -> dict:
    with SESSION_LOCK:
        return load_state().get("notifications", {})


def update_communication_settings_text(
    tone: str = "",
    detail_level: str = "",
    proactivity: str = "",
) -> str:
    with SESSION_LOCK:
        def mutate(state):
            communication = state.setdefault("communication", {})
            if not isinstance(communication, dict):
                communication = {}
                state["communication"] = communication
            if str(tone).strip():
                communication["tone"] = str(tone).strip()
            if str(detail_level).strip():
                communication["detail_level"] = str(detail_level).strip()
            if str(proactivity).strip():
                communication["proactivity"] = str(proactivity).strip()

        state_transaction("update_communication_settings", mutate)
        settings = load_state().get("communication", {})
        return (
            "Comunicacion actualizada.\n"
            f"Tono: {settings.get('tone', 'warm_brief')}\n"
            f"Detalle: {settings.get('detail_level', 'balanced')}\n"
            f"Proactividad: {settings.get('proactivity', 'moderate')}"
        )


def update_notification_settings(
    enabled: bool,
    windows_enabled: bool,
    ntfy_enabled: bool,
    telegram_enabled: bool = False,
    ntfy_server: str = "",
    ntfy_topic: str = "",
    ntfy_token: str = "",
    ntfy_priority: str = "",
    ntfy_tags: str = "",
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
) -> str:
    with SESSION_LOCK:
        current_notifications = load_state().get("notifications", {})
        current_ntfy = (
            current_notifications.get("ntfy", {})
            if isinstance(current_notifications.get("ntfy", {}), dict)
            else {}
        )
        current_telegram = (
            current_notifications.get("telegram", {})
            if isinstance(current_notifications.get("telegram", {}), dict)
            else {}
        )
        existing_ntfy_token = str(current_ntfy.get("token", "")).strip()
        existing_telegram_bot_token = str(current_telegram.get("bot_token", "")).strip()

        channels = []
        if windows_enabled:
            channels.append("windows")
        if ntfy_enabled:
            channels.append("ntfy")
        if telegram_enabled:
            channels.append("telegram")

        if enabled and not channels:
            raise ValueError("Activa al menos un canal de notificacion o desactiva las notificaciones.")

        cleaned_topic = str(ntfy_topic).strip().strip("/")
        if enabled and ntfy_enabled and not cleaned_topic:
            raise ValueError("Para usar ntfy necesitas indicar un topic.")

        cleaned_ntfy_token = str(ntfy_token).strip() or existing_ntfy_token
        cleaned_telegram_bot_token = str(telegram_bot_token).strip() or existing_telegram_bot_token
        cleaned_telegram_chat_id = str(telegram_chat_id).strip()
        if enabled and telegram_enabled and not cleaned_telegram_bot_token:
            raise ValueError("Para usar Telegram necesitas indicar el bot token.")

        def mutate(state):
            defaults = state.get("notifications", {})
            default_ntfy = defaults.get("ntfy", {}) if isinstance(defaults.get("ntfy", {}), dict) else {}
            default_telegram = (
                defaults.get("telegram", {})
                if isinstance(defaults.get("telegram", {}), dict)
                else {}
            )

            previous_telegram_token = str(default_telegram.get("bot_token", "")).strip()
            telegram_last_update_id = default_telegram.get("last_update_id", 0)
            if cleaned_telegram_bot_token and cleaned_telegram_bot_token != previous_telegram_token:
                telegram_last_update_id = 0

            state["notifications"] = {
                "enabled": bool(enabled),
                "channels": channels,
                "ntfy": {
                    "server": str(ntfy_server).strip() or default_ntfy.get("server", "https://ntfy.sh"),
                    "topic": cleaned_topic,
                    "token": cleaned_ntfy_token,
                    "priority": str(ntfy_priority).strip().lower(),
                    "tags": str(ntfy_tags).strip(),
                    "timeout_seconds": default_ntfy.get("timeout_seconds", 10),
                },
                "telegram": {
                    "api_base": default_telegram.get("api_base", "https://api.telegram.org"),
                    "bot_token": cleaned_telegram_bot_token,
                    "chat_id": cleaned_telegram_chat_id,
                    "timeout_seconds": default_telegram.get("timeout_seconds", 10),
                    "poll_timeout_seconds": default_telegram.get("poll_timeout_seconds", 25),
                    "last_update_id": telegram_last_update_id,
                    "pending_power_confirmation": default_telegram.get("pending_power_confirmation", {}),
                },
            }

        state_transaction("update_notification_settings", mutate)

        if not enabled:
            return "Notificaciones desactivadas."

        summary = "Notificaciones actualizadas.\nCanales activos: " + ", ".join(channels) + "."
        if ntfy_enabled:
            summary += f"\nntfy: topic={cleaned_topic}; token={'configurado' if cleaned_ntfy_token else 'sin token'}."
        if telegram_enabled and cleaned_telegram_bot_token and not cleaned_telegram_chat_id:
            summary += (
                "\nTelegram: bot configurado; falta vincular chat. "
                "Abre el bot, envía /start y usa Probar notificación."
            )
        elif telegram_enabled:
            summary += "\nTelegram: configurado y con chat vinculado."

        return summary


def send_test_notification() -> str:
    with SESSION_LOCK:
        settings = load_state().get("notifications", {})
        channels = settings.get("channels", [])
        telegram_settings = get_telegram_settings(settings)
        if (
            settings.get("enabled", True)
            and "telegram" in channels
            and telegram_settings.get("bot_token")
            and not telegram_settings.get("chat_id")
        ):
            if try_link_telegram_chat(settings=settings):
                settings = load_state().get("notifications", {})
            else:
                return (
                    "Telegram tiene bot token, pero todavía no hay chat vinculado.\n"
                    "Siguiente paso: escribe /start al bot desde Telegram y vuelve a probar."
                )

        sent = send_notification(
            "Prueba de Yarbis",
            "Prueba recibida. Las notificaciones de Yarbis están operativas.",
        )
        if sent:
            active_channels = ", ".join(channels) if channels else "sin canales"
            return f"Notificación de prueba enviada.\nCanales activos: {active_channels}."

        return (
            "No pude enviar la notificación de prueba.\n"
            "Revisa que haya al menos un canal activo, que ntfy tenga topic, "
            "que Telegram tenga chat vinculado y que la conexión esté disponible."
        )


def update_profile_text(
    name: str = "",
    role: str = "",
    preferences: str = "",
    constraints: str = "",
) -> str:
    with SESSION_LOCK:
        return update_profile(
            name=name,
            role=role,
            preferences=preferences,
            constraints=constraints,
        )


def save_note_text(title: str, content: str, category: str = "general") -> str:
    with SESSION_LOCK:
        return save_note(title=title, content=content, category=category or "general")


def list_notes_text(category: str = "", limit: int = 10) -> str:
    with SESSION_LOCK:
        return list_notes(category=category, limit=limit)


def get_note_text(identifier: str) -> str:
    with SESSION_LOCK:
        return get_note(identifier=identifier)


def delete_note_text(identifier: str) -> str:
    with SESSION_LOCK:
        return delete_note(identifier=identifier)


def create_memory_backup_text(path: str = "", include_secrets: bool = False) -> str:
    with SESSION_LOCK:
        return create_memory_backup(path=path, include_secrets=include_secrets)


def list_memory_backups_text(limit: int = 10) -> str:
    with SESSION_LOCK:
        return list_memory_backups(limit=limit)


def inspect_memory_backup_text(path_or_id: str) -> str:
    with SESSION_LOCK:
        return inspect_memory_backup(path_or_id=path_or_id)


def import_memory_backup_text(path_or_id: str, mode: str = "replace") -> str:
    with SESSION_LOCK:
        return import_memory_backup(path_or_id=path_or_id, mode=mode)


def memory_protection_status_text() -> str:
    with SESSION_LOCK:
        return memory_protection_status()


def verify_memory_backups_text() -> str:
    with SESSION_LOCK:
        return verify_memory_backups()


def update_memory_protection_settings_text(
    enabled: bool = True,
    backup_on_every_change: bool = True,
    mirror_dir: str = "",
    max_auto_backups: int = 50,
    keep_daily_days: int = 14,
    verify_after_write: bool = False,
    auto_restore: bool = True,
) -> str:
    with SESSION_LOCK:
        return update_memory_protection_settings(
            enabled=enabled,
            backup_on_every_change=backup_on_every_change,
            mirror_dir=mirror_dir,
            max_auto_backups=max_auto_backups,
            keep_daily_days=keep_daily_days,
            verify_after_write=verify_after_write,
            auto_restore=auto_restore,
        )


def add_task_text(title: str, details: str = "", priority: str = "media") -> str:
    with SESSION_LOCK:
        return add_task(title=title, details=details, priority=priority or "media")


def create_idea_project_text(
    title: str,
    kind: str = "mixto",
    summary: str = "",
    audience: str = "",
    desired_outcome: str = "",
    problem: str = "",
    creative_directions: str = "",
    selected_direction: str = "",
    success_criteria: str = "",
    constraints: str = "",
    risks: str = "",
    open_questions: str = "",
    next_steps: str = "",
    status: str = "exploring",
) -> str:
    with SESSION_LOCK:
        return create_idea_project(
            title=title,
            kind=kind,
            summary=summary,
            audience=audience,
            desired_outcome=desired_outcome,
            problem=problem,
            creative_directions=creative_directions,
            selected_direction=selected_direction,
            success_criteria=success_criteria,
            constraints=constraints,
            risks=risks,
            open_questions=open_questions,
            next_steps=next_steps,
            status=status,
        )


def list_idea_projects_text(status: str = "open", limit: int = 20) -> str:
    with SESSION_LOCK:
        return list_idea_projects(status=status, limit=limit)


def get_idea_project_text(project_id: str) -> str:
    with SESSION_LOCK:
        return get_idea_project(project_id=project_id)


def update_idea_project_text(
    project_id: str,
    title: str = "",
    kind: str = "",
    status: str = "",
    summary: str = "",
    audience: str = "",
    desired_outcome: str = "",
    problem: str = "",
    creative_directions: str = "",
    selected_direction: str = "",
    success_criteria: str = "",
    constraints: str = "",
    risks: str = "",
    open_questions: str = "",
    next_steps: str = "",
) -> str:
    with SESSION_LOCK:
        return update_idea_project(
            project_id=project_id,
            title=title,
            kind=kind,
            status=status,
            summary=summary,
            audience=audience,
            desired_outcome=desired_outcome,
            problem=problem,
            creative_directions=creative_directions,
            selected_direction=selected_direction,
            success_criteria=success_criteria,
            constraints=constraints,
            risks=risks,
            open_questions=open_questions,
            next_steps=next_steps,
        )


def promote_idea_project_to_work_text(project_id: str, priority: str = "media") -> str:
    with SESSION_LOCK:
        return promote_idea_project_to_work(project_id=project_id, priority=priority or "media")


def create_project_visual_board_text(project_id: str, board_kind: str, title: str = "") -> str:
    with SESSION_LOCK:
        return create_project_visual_board(project_id=project_id, board_kind=board_kind, title=title)


def list_project_visual_boards_text(project_id: str) -> str:
    with SESSION_LOCK:
        return list_project_visual_boards(project_id=project_id)


def get_project_visual_board_text(project_id: str, board_id: str) -> str:
    with SESSION_LOCK:
        return get_project_visual_board(project_id=project_id, board_id=board_id)


def update_project_visual_board_text(project_id: str, board_id: str, board_json: str) -> str:
    with SESSION_LOCK:
        return update_project_visual_board(project_id=project_id, board_id=board_id, board_json=board_json)


def export_project_visual_board_text(
    project_id: str,
    board_id: str,
    formats: str = "html,svg,json",
    open_file: bool = False,
) -> str:
    with SESSION_LOCK:
        return export_project_visual_board(
            project_id=project_id,
            board_id=board_id,
            formats=formats,
            open_file=open_file,
        )


def coding_set_workspace_text(path: str) -> str:
    with SESSION_LOCK:
        return coding_set_workspace(path=path)


def coding_workspace_overview_text() -> str:
    with SESSION_LOCK:
        return coding_workspace_overview()


def evolution_status_text() -> str:
    with SESSION_LOCK:
        return evolution_status()


def evolution_set_enabled_text(enabled: bool) -> str:
    with SESSION_LOCK:
        return evolution_set_enabled(enabled)


def evolution_set_interval_text(hours: int) -> str:
    with SESSION_LOCK:
        return evolution_set_interval(hours)


def evolution_list_pending_text() -> str:
    with SESSION_LOCK:
        return evolution_list_pending()


def evolution_list_directives_text() -> str:
    with SESSION_LOCK:
        return evolution_list_directives()


def evolution_apply_directive_text(directive_id: str) -> str:
    with SESSION_LOCK:
        return evolution_apply_directive(directive_id)


def evolution_discard_directive_text(directive_id: str) -> str:
    with SESSION_LOCK:
        return evolution_discard_directive(directive_id)


def evolution_list_suggestions_text() -> str:
    with SESSION_LOCK:
        return evolution_list_suggestions()


def evolution_apply_suggestion_text(suggestion_id: str) -> str:
    with SESSION_LOCK:
        return evolution_apply_suggestion(suggestion_id)


def evolution_discard_suggestion_text(suggestion_id: str) -> str:
    with SESSION_LOCK:
        return evolution_discard_suggestion(suggestion_id)


def analyze_image_text(path: str, question: str = "") -> str:
    with SESSION_LOCK:
        return analyze_image(path, question)


def vision_status_text() -> str:
    with SESSION_LOCK:
        return vision_status()


def vision_set_model_text(model: str) -> str:
    with SESSION_LOCK:
        return vision_set_model(model)


def coding_workflow_status_text(include_diff: bool = False) -> str:
    with SESSION_LOCK:
        return coding_workflow_status(include_diff=include_diff)


def coding_search_text_text(
    pattern: str,
    path: str = ".",
    glob: str = "",
    context_lines: int = 2,
    max_results: int = 50,
) -> str:
    with SESSION_LOCK:
        return coding_search_text(
            pattern=pattern,
            path=path,
            glob=glob,
            context_lines=context_lines,
            max_results=max_results,
        )


def coding_read_text_range_text(path: str, start_line: int = 1, line_count: int = 120) -> str:
    with SESSION_LOCK:
        return coding_read_text_range(
            path=path,
            start_line=start_line,
            line_count=line_count,
        )


def coding_list_proposals_text(status: str = "pending", limit: int = 20) -> str:
    with SESSION_LOCK:
        return coding_list_proposals(status=status, limit=limit)


def coding_get_proposal_text(proposal_id: str) -> str:
    with SESSION_LOCK:
        return coding_get_proposal(proposal_id=proposal_id)


def coding_check_proposal_text(proposal_id: str) -> str:
    with SESSION_LOCK:
        return coding_check_proposal(proposal_id=proposal_id)


def coding_propose_edits_text(
    title: str,
    edits_json: str,
    summary: str = "",
    reason: str = "",
    validation_command: str = "",
) -> str:
    with SESSION_LOCK:
        return coding_propose_edits(
            title=title,
            edits_json=edits_json,
            summary=summary,
            reason=reason,
            validation_command=validation_command,
        )


def coding_apply_proposal_text(proposal_id: str) -> str:
    with SESSION_LOCK:
        return coding_apply_proposal(proposal_id=proposal_id)


def coding_apply_and_validate_text(proposal_id: str, command: str = "", timeout_seconds: int = 120) -> str:
    with SESSION_LOCK:
        return coding_apply_and_validate(
            proposal_id=proposal_id,
            command=command,
            timeout_seconds=timeout_seconds,
        )


def coding_discard_proposal_text(proposal_id: str) -> str:
    with SESSION_LOCK:
        return coding_discard_proposal(proposal_id=proposal_id)


def coding_git_status_text() -> str:
    with SESSION_LOCK:
        return coding_git_status()


def coding_detect_validation_command_text() -> str:
    with SESSION_LOCK:
        return coding_detect_validation_command()


def coding_validation_plan_text(proposal_id: str = "") -> str:
    with SESSION_LOCK:
        return coding_validation_plan(proposal_id=proposal_id)


def coding_update_validation_command_text(command: str = "") -> str:
    with SESSION_LOCK:
        return coding_update_validation_command(command=command)


def coding_run_validation_text(command: str = "", proposal_id: str = "", timeout_seconds: int = 120) -> str:
    with SESSION_LOCK:
        return coding_run_validation(
            command=command,
            proposal_id=proposal_id,
            timeout_seconds=timeout_seconds,
        )


def social_accounts_overview_text() -> str:
    with SESSION_LOCK:
        return social_accounts_overview()


def start_social_oauth_text(
    provider: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str = "",
    scopes: str = "",
    authorization_response_url: str = "",
    open_browser: bool = True,
    timeout_seconds: int = 180,
) -> str:
    return start_social_oauth(
        provider=provider,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=scopes,
        authorization_response_url=authorization_response_url,
        open_browser=open_browser,
        timeout_seconds=timeout_seconds,
    )


def list_social_publications_text(status: str = "all", limit: int = 10) -> str:
    with SESSION_LOCK:
        return list_social_publications(status=status, limit=limit)


def open_assisted_social_post_text(publication_id: str = "", draft_id: str = "") -> str:
    return open_assisted_social_post(publication_id=publication_id, draft_id=draft_id)


def _normalize_command_text(text: str) -> str:
    normalized = _normalize_intent_text(text)
    without_punctuation = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(without_punctuation.split())


def _title_from_note_content(content: str) -> str:
    compact = " ".join(str(content).strip().split())
    if len(compact) <= 64:
        return compact or "Nota sin titulo"
    return compact[:64].rstrip() + "..."


def _parse_note_create_payload(payload: str) -> dict:
    cleaned_payload = str(payload).strip()
    parts = [part.strip() for part in cleaned_payload.split("|")]
    parts = [part for part in parts if part]

    if not parts:
        return {"action": "usage"}

    if len(parts) == 1:
        content = parts[0]
        return {
            "action": "create",
            "title": _title_from_note_content(content),
            "content": content,
            "category": "general",
        }

    return {
        "action": "create",
        "title": parts[0],
        "content": parts[1],
        "category": parts[2] if len(parts) > 2 else "general",
    }


def _parse_note_subcommand(argument_text: str) -> dict:
    argument_text = str(argument_text).strip()
    normalized_argument = _normalize_command_text(argument_text)

    if not argument_text:
        return {"action": "usage"}

    if normalized_argument in {"listar", "lista", "list", "ls", "ver todas", "todas"}:
        return {"action": "list", "category": ""}

    for prefix in (
        "crear",
        "crea",
        "agregar",
        "agrega",
        "guardar",
        "guarda",
        "add",
        "save",
    ):
        prefix_with_space = prefix + " "
        if normalized_argument == prefix:
            return {"action": "usage"}
        if normalized_argument.startswith(prefix_with_space):
            return _parse_note_create_payload(argument_text[len(prefix):].strip())

    for prefix in ("borrar", "borra", "eliminar", "elimina", "delete", "rm"):
        prefix_with_space = prefix + " "
        if normalized_argument == prefix:
            return {"action": "usage"}
        if normalized_argument.startswith(prefix_with_space):
            return {
                "action": "delete",
                "identifier": argument_text[len(prefix):].strip(),
            }

    for prefix in ("ver", "mostrar", "muestra", "lee", "show", "open"):
        prefix_with_space = prefix + " "
        if normalized_argument == prefix:
            return {"action": "list", "category": ""}
        if normalized_argument.startswith(prefix_with_space):
            return {
                "action": "show",
                "identifier": argument_text[len(prefix):].strip(),
            }

    if "|" in argument_text:
        return _parse_note_create_payload(argument_text)

    return {"action": "show", "identifier": argument_text}


def parse_note_text_request(text: str) -> dict | None:
    cleaned_text = str(text).strip()
    if not cleaned_text:
        return None

    parts = cleaned_text.split(maxsplit=1)
    command = parts[0].split("@")[0].lower() if parts else ""
    argument_text = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/notas", "notas", "notes"}:
        return {"action": "list", "category": argument_text}

    if command in {"/nota", "nota", "note"}:
        return _parse_note_subcommand(argument_text)

    if command in {"/crear_nota", "/guardar_nota"}:
        return _parse_note_create_payload(argument_text)

    if command in {"/borrar_nota", "/eliminar_nota", "/delete_note"}:
        return {"action": "delete", "identifier": argument_text}

    if command in {"/ver_nota", "/mostrar_nota", "/show_note"}:
        return {"action": "show", "identifier": argument_text}

    normalized_text = _normalize_command_text(cleaned_text)
    if normalized_text in {
        "ver notas",
        "listar notas",
        "lista notas",
        "muestra notas",
        "mostrar notas",
        "muestrame notas",
        "muestrame mis notas",
        "mis notas",
    }:
        return {"action": "list", "category": ""}

    list_match = re.match(
        r"^\s*(?:ver|listar|muestra|mostrar)\s+(?:mis\s+)?notas(?:\s+(?:de|categoria)\s+(.+))?\s*$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if list_match:
        return {
            "action": "list",
            "category": (list_match.group(1) or "").strip(),
        }

    create_match = re.match(
        r"^\s*(?:guarda|guardar|crea|crear|agrega|agregar)\s+(?:una\s+)?nota\b\s*[:\-]?\s*(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if create_match:
        return _parse_note_create_payload(create_match.group(1))

    note_colon_match = re.match(
        r"^\s*nota\s*:\s*(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if note_colon_match:
        return _parse_note_create_payload(note_colon_match.group(1))

    anota_match = re.match(
        r"^\s*anota\s*[:\-]?\s*(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if anota_match:
        return _parse_note_create_payload(anota_match.group(1))

    delete_match = re.match(
        r"^\s*(?:borra|borrar|elimina|eliminar)\s+(?:la\s+)?nota\s+(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if delete_match:
        return {
            "action": "delete",
            "identifier": delete_match.group(1).strip(),
        }

    show_match = re.match(
        r"^\s*(?:ver|muestra|mostrar|lee|abre)\s+(?:la\s+)?nota\s+(.+)$",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if show_match:
        return {
            "action": "show",
            "identifier": show_match.group(1).strip(),
        }

    return None


def note_request_label(text: str) -> str:
    parsed = parse_note_text_request(text)
    if not parsed:
        return ""

    labels = {
        "create": "Guardar nota",
        "list": "Notas",
        "show": "Notas",
        "delete": "Eliminar nota",
        "usage": "Notas",
    }
    return labels.get(parsed.get("action", ""), "Notas")


def _note_usage_text() -> str:
    return (
        "Uso de notas:\n"
        "- notas [categoria]\n"
        "- nota crear Titulo | contenido | categoria\n"
        "- nota ver ID_o_titulo\n"
        "- nota borrar ID_o_titulo\n"
        "Tambien puedes decir: guarda una nota: Titulo | contenido | categoria"
    )


def handle_note_text_request(text: str) -> str | None:
    parsed = parse_note_text_request(text)
    if not parsed:
        return None

    action = parsed.get("action", "")
    if action == "usage":
        return _note_usage_text()
    if action == "list":
        return list_notes_text(category=parsed.get("category", ""), limit=20)
    if action == "show":
        return get_note_text(parsed.get("identifier", ""))
    if action == "delete":
        return delete_note_text(parsed.get("identifier", ""))
    if action == "create":
        return save_note_text(
            title=parsed.get("title", ""),
            content=parsed.get("content", ""),
            category=parsed.get("category", "general"),
        )

    return _note_usage_text()


def _capture_operation_output(func, *args, **kwargs) -> tuple[str, object]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = func(*args, **kwargs)

    output = buffer.getvalue().strip()
    if output:
        return output, result

    if isinstance(result, dict):
        content = str(result.get("content", "")).strip()
        if content:
            return content, result

    if result is None:
        return "", result

    return str(result).strip(), result


def run_cycle_with_output(
    emit_notifications: bool = True,
    mirror_telegram: bool = True,
) -> str:
    with session_operation_lock("Ciclo"):
        output, _ = _capture_operation_output(run_one_cycle)
        state = load_state()
        result = output or "Ciclo ejecutado sin salida visible."

    if emit_notifications and mirror_telegram:
        _mirror_telegram_response("Ciclo", result)

    if emit_notifications and has_pending_user_question(state):
        notify_user_input_required(
            state["awaiting_user_input"].get("question", ""),
            state["awaiting_user_input"].get("reason", ""),
        )

    return result


def recover_unanswered_user_message_with_output(
    emit_notifications: bool = True,
    blocking: bool = True,
    mirror_telegram: bool = True,
) -> str:
    with session_operation_lock("Respuesta recuperada", blocking=blocking):
        state = load_state()
        if has_pending_user_question(state) or not has_unanswered_user_message(state):
            return ""

        output, _ = _capture_operation_output(run_one_cycle)
        state = load_state()
        result = output or "Respuesta pendiente recuperada sin salida visible."

    if emit_notifications and mirror_telegram:
        _mirror_telegram_response("Respuesta recuperada", result)

    if emit_notifications and has_pending_user_question(state):
        notify_user_input_required(
            state["awaiting_user_input"].get("question", ""),
            state["awaiting_user_input"].get("reason", ""),
        )

    return result


def run_auto_with_output(
    cycles=None,
    emit_notifications: bool = True,
    mirror_telegram: bool = True,
    model_override: str | None = None,
) -> str:
    with session_operation_lock("Modo autonomo"):
        capture_kwargs = {"cycles": cycles}
        if str(model_override or "").strip():
            capture_kwargs["model_override"] = str(model_override).strip()
        output, executed_cycles = _capture_operation_output(
            run_autonomous_session,
            **capture_kwargs,
        )
        state = load_state()

        summary = f"Modo autonomo ejecutado por {executed_cycles} ciclo(s)."
        if not output:
            result = summary
        else:
            result = f"{output}\n\n{summary}"

    if emit_notifications and mirror_telegram:
        _mirror_telegram_response("Modo autonomo", result)

    if emit_notifications and has_pending_user_question(state):
        notify_user_input_required(
            state["awaiting_user_input"].get("question", ""),
            state["awaiting_user_input"].get("reason", ""),
        )
    elif emit_notifications and executed_cycles > 0 and not _has_open_tasks(state):
        send_notification(
            "Yarbis termino el trabajo actual",
            state.get("last_result", "").strip() or "No quedan tareas abiertas.",
        )
    elif emit_notifications and executed_cycles > 0:
        send_notification(
            "Yarbis termino el modo autonomo",
            f"Se ejecutaron {executed_cycles} ciclo(s).",
        )

    return result


def submit_user_reply(
    reply_text: str,
    emit_notifications: bool = True,
    blocking: bool = True,
) -> str:
    with session_operation_lock("Respuesta", blocking=blocking):
        cleaned_reply = str(reply_text).strip()
        if not cleaned_reply:
            raise ValueError("La respuesta no puede quedar vacia.")

        if is_self_analysis_request(cleaned_reply):
            result = run_self_analysis_with_output(emit_notifications=False)
            _mirror_telegram_response("Autoanalisis", result, enabled=emit_notifications)
            return result

        note_result = handle_note_text_request(cleaned_reply)
        if note_result is not None:
            _mirror_telegram_response(
                note_request_label(cleaned_reply) or "Notas",
                note_result,
                enabled=emit_notifications,
            )
            return note_result

        def mutate(state):
            had_pending_question = has_pending_user_question(state)
            auto_cycles_default = state["autonomy"]["auto_cycles_default"]
            message_content = _message_content_for_user_reply(
                cleaned_reply,
                state,
                had_pending_question,
            )
            state["messages"].append({
                "role": "user",
                "content": message_content,
            })
            clear_pending_user_question(state)
            return had_pending_question, auto_cycles_default

        had_pending_question, auto_cycles_default = state_transaction("submit_user_reply", mutate)

        if had_pending_question:
            auto_output = run_auto_with_output(
                cycles=auto_cycles_default,
                emit_notifications=emit_notifications,
                mirror_telegram=False,
            )
            result = (
                "Respuesta guardada. Retomando el modo autonomo con esta informacion.\n\n"
                f"{auto_output}"
            )
            _mirror_telegram_response("Respuesta", result, enabled=emit_notifications)
            return result

        cycle_output = run_cycle_with_output(
            emit_notifications=emit_notifications,
            mirror_telegram=False,
        )
        result = f"Respuesta guardada. Ejecutando un ciclo con esta informacion.\n\n{cycle_output}"
        _mirror_telegram_response("Respuesta", result, enabled=emit_notifications)
        return result
