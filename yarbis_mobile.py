import base64
import hashlib
import hmac
import json
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import activity
import conversation_ux
import yarbis_instance
import voice as yarbis_voice
import voice_conversation
from secrets_redaction import build_secret_redactor
from memory import (
    DEFAULT_MOBILE_UI_PORT,
    DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_OPENROUTER_HOST,
    DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
    MAX_MOBILE_UI_JOB_TIMEOUT_SECONDS,
    MIN_MOBILE_UI_JOB_TIMEOUT_SECONDS,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    default_state,
    load_state,
    render_state_summary,
    state_transaction,
)
from notifications import send_notification
from service_manager import (
    format_health_status,
    get_service_status,
    install_service,
    remove_service,
    set_autostart_enabled,
    start_service,
    stop_service,
)
from session import (
    add_task_text,
    coding_apply_and_validate_text,
    coding_apply_proposal_text,
    coding_check_proposal_text,
    coding_detect_validation_command_text,
    coding_discard_proposal_text,
    coding_get_proposal_text,
    coding_list_proposals_text,
    coding_propose_edits_text,
    coding_read_text_range_text,
    coding_run_validation_text,
    coding_search_text_text,
    coding_set_workspace_text,
    coding_update_validation_command_text,
    coding_validation_plan_text,
    coding_workflow_status_text,
    create_idea_project_text,
    create_memory_backup_text,
    create_project_visual_board_text,
    export_project_visual_board_text,
    factory_reset_yarbis,
    get_project_visual_board_text,
    get_notification_settings,
    import_memory_backup_text,
    inspect_memory_backup_text,
    list_project_visual_boards_text,
    promote_idea_project_to_work_text,
    request_stop_current_operation,
    save_note_text,
    send_test_notification,
    start_social_oauth_text,
    update_goal,
    update_idea_project_text,
    update_local_context_settings,
    update_memory_protection_settings_text,
    update_model_provider,
    update_notification_settings,
    update_ollama_settings,
    update_openrouter_settings,
    update_profile_text,
    update_service_proactive_settings,
    update_project_visual_board_text,
    verify_memory_backups_text,
)
from tools import (
    delete_note,
    get_note,
    open_assisted_social_post,
    set_plan,
    update_internet_settings,
    update_task_status,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = yarbis_instance.runtime_dir()
MOBILE_SESSION_SECONDS = 7 * 24 * 60 * 60
MOBILE_COOKIE_NAME = "yarbis_mobile"
PIN_HASH_ITERATIONS = 200_000
MAX_REQUEST_BYTES = 512 * 1024
MAX_VOICE_REQUEST_BYTES = int(yarbis_voice.MAX_VOICE_AUDIO_BYTES * 1.4) + 4096
MAX_JOBS = 50
MOBILE_CACHE_TTL_SECONDS = 10.0
MOBILE_ACTIVITY_EVENT_LIMIT = 40
MOBILE_ACTIVITY_MAX_BYTES = 32 * 1024
MOBILE_LAST_RESULT_CHARS = 6_000
_MOBILE_LOCK = threading.RLock()
_MOBILE_SERVERS: list[dict] = []
_MOBILE_SERVER_KEY: tuple | None = None
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.RLock()
_MOBILE_CACHE_LOCK = threading.RLock()
_MOBILE_VALUE_CACHE: dict[str, dict] = {}

_MOBILE_SESSION_OPERATION_SCRIPT = r"""
import json
import sys

from session import run_auto_with_output, run_cycle_with_output, submit_user_reply

request_path = sys.argv[1]
response_path = sys.argv[2]

with open(request_path, "r", encoding="utf-8") as file:
    request_payload = json.load(file)

operation = str(request_payload.get("operation", "")).strip()
payload = request_payload.get("payload", {})
if not isinstance(payload, dict):
    payload = {}

try:
    if operation == "run_cycle":
        result = run_cycle_with_output()
    elif operation == "run_auto":
        result = run_auto_with_output(cycles=payload.get("cycles"))
    elif operation == "submit_user_reply":
        result = submit_user_reply(str(payload.get("reply_text", "")))
    else:
        raise ValueError(f"Operacion movil desconocida: {operation}")
except Exception as exc:
    response_payload = {
        "ok": False,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
    exit_code = 1
else:
    response_payload = {"ok": True, "result": str(result)}
    exit_code = 0

with open(response_path, "w", encoding="utf-8") as file:
    json.dump(response_payload, file, ensure_ascii=False)

sys.exit(exit_code)
"""


class MobileUiError(RuntimeError):
    pass


class MobileJobTimeoutError(RuntimeError):
    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds
        super().__init__(
            "La operacion movil excedio el tiempo limite "
            f"({timeout_seconds} segundos) y fue detenida."
        )


class _MobileHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool_text(value: bool) -> str:
    return "activo" if value else "desactivado"


def _truncate_text(value: object, max_chars: int = MOBILE_LAST_RESULT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars].rstrip()}\n\n[recortado en movil: {omitted} caracteres mas]"


def _cached_value(key: str, signature, ttl_seconds: float, builder):
    now = time.monotonic()
    with _MOBILE_CACHE_LOCK:
        cached = _MOBILE_VALUE_CACHE.get(key)
        if (
            cached
            and cached.get("signature") == signature
            and now - float(cached.get("created_at", 0.0)) < ttl_seconds
        ):
            return cached.get("value")

    value = builder()
    with _MOBILE_CACHE_LOCK:
        _MOBILE_VALUE_CACHE[key] = {
            "created_at": now,
            "signature": signature,
            "value": value,
        }
    return value


def _cached_tailscale_ipv4() -> str:
    return str(_cached_value(
        "tailscale_ipv4",
        "tailscale-ipv4",
        MOBILE_CACHE_TTL_SECONDS,
        detect_tailscale_ipv4,
    ) or "")


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def hash_mobile_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    cleaned_pin = str(pin)
    if not 4 <= len(cleaned_pin) <= 64:
        raise ValueError("El PIN de la UI movil debe tener entre 4 y 64 caracteres.")
    cleaned_salt = str(salt or secrets.token_hex(16)).strip()
    if not cleaned_salt:
        cleaned_salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        cleaned_pin.encode("utf-8"),
        cleaned_salt.encode("utf-8"),
        PIN_HASH_ITERATIONS,
    )
    return _b64_encode(digest), cleaned_salt


def verify_mobile_pin(pin: str, pin_hash: str, pin_salt: str) -> bool:
    if not str(pin_hash).strip() or not str(pin_salt).strip():
        return False
    try:
        candidate, _salt = hash_mobile_pin(str(pin), salt=str(pin_salt).strip())
    except ValueError:
        return False
    return hmac.compare_digest(candidate, str(pin_hash).strip())


def _sign_value(value: str, secret: str) -> str:
    digest = hmac.new(str(secret).encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    return _b64_encode(digest)


def create_session_cookie(session_secret: str) -> tuple[str, str]:
    if not str(session_secret).strip():
        raise ValueError("La UI movil no tiene secreto de sesion configurado.")
    csrf = secrets.token_urlsafe(24)
    payload = {
        "exp": int(time.time() + MOBILE_SESSION_SECONDS),
        "csrf": csrf,
    }
    body = _b64_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign_value(body, session_secret)
    return f"v1.{body}.{signature}", csrf


def verify_session_cookie(cookie_value: str, session_secret: str) -> dict | None:
    if not str(cookie_value).strip() or not str(session_secret).strip():
        return None
    parts = str(cookie_value).split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    expected = _sign_value(parts[1], session_secret)
    if not hmac.compare_digest(expected, parts[2]):
        return None
    try:
        payload = json.loads(_b64_decode(parts[1]).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    try:
        expires_at = int(payload.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if expires_at < int(time.time()):
        return None
    csrf = str(payload.get("csrf", "")).strip()
    if not csrf:
        return None
    return {"expires_at": expires_at, "csrf": csrf}


def _mobile_defaults() -> dict:
    return default_state()["service"]["mobile_ui"]


def _normalize_mobile_job_timeout(value) -> int:
    try:
        timeout_seconds = int(value)
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS
    if not MIN_MOBILE_UI_JOB_TIMEOUT_SECONDS <= timeout_seconds <= MAX_MOBILE_UI_JOB_TIMEOUT_SECONDS:
        raise ValueError(
            "El timeout de operaciones moviles debe estar entre "
            f"{MIN_MOBILE_UI_JOB_TIMEOUT_SECONDS} y {MAX_MOBILE_UI_JOB_TIMEOUT_SECONDS} segundos."
        )
    return timeout_seconds


def get_mobile_ui_settings() -> dict:
    state = load_state()
    service = state.get("service", {}) if isinstance(state, dict) else {}
    settings = service.get("mobile_ui", {}) if isinstance(service, dict) else {}
    if isinstance(settings, dict):
        return settings
    return _mobile_defaults()


def _set_mobile_bind_error(error_text: str) -> None:
    cleaned = str(error_text or "").strip()

    def mutate(state):
        mobile = state.setdefault("service", {}).setdefault("mobile_ui", {})
        mobile["last_bind_error"] = cleaned

    try:
        state_transaction("mobile_ui_bind_error", mutate, create_backup=False)
    except Exception:
        pass


def update_mobile_ui_settings(
    enabled: bool,
    port: int | str = DEFAULT_MOBILE_UI_PORT,
    pin: str = "",
    job_timeout_seconds: int | str | None = None,
) -> str:
    try:
        cleaned_port = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("El puerto de la UI movil debe ser un numero.") from exc
    if not 1 <= cleaned_port <= 65535:
        raise ValueError("El puerto de la UI movil debe estar entre 1 y 65535.")

    cleaned_pin = str(pin or "")
    current = get_mobile_ui_settings()
    next_hash = str(current.get("pin_hash", "")).strip()
    next_salt = str(current.get("pin_salt", "")).strip()
    if cleaned_pin:
        next_hash, next_salt = hash_mobile_pin(cleaned_pin)
    elif enabled and not next_hash:
        raise ValueError("Configura un PIN antes de activar la UI movil.")

    timeout_source = (
        job_timeout_seconds
        if job_timeout_seconds is not None
        else current.get("job_timeout_seconds", DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS)
    )
    cleaned_job_timeout_seconds = _normalize_mobile_job_timeout(timeout_source)
    session_secret = str(current.get("session_secret", "")).strip() or secrets.token_urlsafe(32)

    def mutate(state):
        mobile = state.setdefault("service", {}).setdefault("mobile_ui", {})
        mobile.update({
            "enabled": bool(enabled),
            "port": cleaned_port,
            "job_timeout_seconds": cleaned_job_timeout_seconds,
            "pin_hash": next_hash,
            "pin_salt": next_salt,
            "session_secret": session_secret,
            "last_bind_error": "",
        })

    state_transaction("update_mobile_ui_settings", mutate)
    status = public_mobile_ui_status()
    url_text = status.get("tailscale_url") or status.get("local_url")
    pin_text = "PIN actualizado" if cleaned_pin else "PIN conservado"
    return (
        "UI movil actualizada.\n"
        f"Estado: {_bool_text(bool(enabled))}\n"
        f"Puerto: {cleaned_port}\n"
        f"Timeout operaciones: {cleaned_job_timeout_seconds} segundos\n"
        f"{pin_text}.\n"
        f"URL: {url_text or 'pendiente de Tailscale'}"
    )


def _ensure_mobile_session_secret(settings: dict) -> dict:
    if str(settings.get("session_secret", "")).strip():
        return settings
    secret = secrets.token_urlsafe(32)

    def mutate(state):
        mobile = state.setdefault("service", {}).setdefault("mobile_ui", {})
        mobile["session_secret"] = secret

    state_transaction("mobile_ui_session_secret", mutate, create_backup=False)
    refreshed = dict(settings)
    refreshed["session_secret"] = secret
    return refreshed


def detect_tailscale_ipv4(timeout_seconds: float = 2.0) -> str:
    tailscale = shutil.which("tailscale")
    if not tailscale:
        candidate = Path(r"C:\Program Files\Tailscale\tailscale.exe")
        if candidate.exists():
            tailscale = str(candidate)
    if not tailscale:
        return ""
    try:
        completed = subprocess.run(
            [tailscale, "ip", "-4"],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    for line in completed.stdout.splitlines():
        candidate = line.strip()
        if candidate.startswith("100.") and candidate.count(".") == 3:
            return candidate
    return ""


def _bind_hosts() -> tuple[str, ...]:
    hosts = ["127.0.0.1"]
    tailscale_ip = detect_tailscale_ipv4()
    if tailscale_ip and tailscale_ip not in hosts:
        hosts.append(tailscale_ip)
    return tuple(hosts)


def _active_mobile_urls() -> list[str]:
    with _MOBILE_LOCK:
        return [server["url"] for server in _MOBILE_SERVERS]


def public_mobile_ui_status(settings: dict | None = None) -> dict:
    settings = settings or get_mobile_ui_settings()
    port = int(settings.get("port", DEFAULT_MOBILE_UI_PORT) or DEFAULT_MOBILE_UI_PORT)
    job_timeout_seconds = int(
        settings.get("job_timeout_seconds", DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS)
        or DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS
    )
    tailscale_ip = _cached_tailscale_ipv4()
    return {
        "enabled": bool(settings.get("enabled")),
        "configured": bool(str(settings.get("pin_hash", "")).strip()),
        "port": port,
        "job_timeout_seconds": job_timeout_seconds,
        "local_url": f"http://127.0.0.1:{port}",
        "tailscale_ip": tailscale_ip,
        "tailscale_url": f"http://{tailscale_ip}:{port}" if tailscale_ip else "",
        "active_urls": _active_mobile_urls(),
        "last_bind_error": str(settings.get("last_bind_error", "")).strip(),
    }


def _python_console_path() -> Path:
    candidate = WORKSPACE_ROOT / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return candidate
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        python = executable.with_name("python.exe")
        if python.exists():
            return python
    return executable


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                cwd=str(WORKSPACE_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _mobile_job_timeout_seconds() -> int:
    settings = get_mobile_ui_settings()
    return _normalize_mobile_job_timeout(
        settings.get("job_timeout_seconds", DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS)
    )


def _session_operation_subprocess(operation: str, **payload) -> str:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    timeout_seconds = _mobile_job_timeout_seconds()
    token = f"{time.time_ns()}-{threading.get_ident()}"
    request_path = RUNTIME_DIR / f"mobile-operation-{token}.request.json"
    response_path = RUNTIME_DIR / f"mobile-operation-{token}.response.json"
    request_payload = {
        "operation": str(operation).strip(),
        "payload": payload,
    }
    try:
        request_path.write_text(json.dumps(request_payload, ensure_ascii=False), encoding="utf-8")
        process = subprocess.Popen(
            [
                str(_python_console_path()),
                "-c",
                _MOBILE_SESSION_OPERATION_SCRIPT,
                str(request_path),
                str(response_path),
            ],
            cwd=str(WORKSPACE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                request_stop_current_operation(source="mobile-timeout")
            except Exception:
                pass
            _terminate_process_tree(process)
            process.communicate()
            raise MobileJobTimeoutError(timeout_seconds) from exc

        response_payload = {}
        if response_path.exists():
            response_payload = json.loads(response_path.read_text(encoding="utf-8"))
        if process.returncode == 0 and response_payload.get("ok"):
            return str(response_payload.get("result", ""))
        error_text = str(response_payload.get("error", "")).strip()
        if not error_text:
            error_text = "\n".join(part.strip() for part in (stdout, stderr) if str(part).strip())
        raise RuntimeError(error_text or f"Operacion {operation} termino con exit={process.returncode}.")
    finally:
        for path in (request_path, response_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _prune_jobs_unlocked() -> None:
    if len(_JOBS) <= MAX_JOBS:
        return
    ordered = sorted(_JOBS.values(), key=lambda item: item.get("started_at", ""))
    for job in ordered[: max(0, len(_JOBS) - MAX_JOBS)]:
        _JOBS.pop(job["id"], None)


def _start_job(label: str, operation: str, payload: dict | None = None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    job_id = activity.new_operation_id(f"mobile-{operation}")
    job = {
        "id": job_id,
        "label": str(label).strip() or "Operacion movil",
        "operation": operation,
        "status": "running",
        "started_at": _utc_now(),
        "finished_at": "",
        "result": "",
        "error": "",
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        _prune_jobs_unlocked()

    activity.emit_event(
        "remote_job_started",
        operation_id=job_id,
        label=job["label"],
        source="mobile_ui",
        status_text=f"Estoy pensando: {job['label']}...",
    )

    def worker():
        try:
            result = _session_operation_subprocess(operation, **payload)
        except MobileJobTimeoutError as exc:
            with _JOBS_LOCK:
                job["status"] = "failed"
                job["finished_at"] = _utc_now()
                job["error"] = str(exc)
            activity.emit_event(
                "remote_job_failed",
                operation_id=job_id,
                label=job["label"],
                source="mobile_ui",
                content=str(exc),
            )
            activity.append_activity(f"{job['label']} movil (timeout)", str(exc))
            _notify_mobile_job_timeout(job, exc)
        except Exception as exc:
            with _JOBS_LOCK:
                job["status"] = "failed"
                job["finished_at"] = _utc_now()
                job["error"] = str(exc)
            activity.emit_event(
                "remote_job_failed",
                operation_id=job_id,
                label=job["label"],
                source="mobile_ui",
                content=str(exc),
            )
            activity.append_activity(f"{job['label']} movil (error)", str(exc))
        else:
            with _JOBS_LOCK:
                job["status"] = "finished"
                job["finished_at"] = _utc_now()
                job["result"] = str(result)
            activity.emit_event(
                "remote_job_finished",
                operation_id=job_id,
                label=job["label"],
                source="mobile_ui",
                content=str(result),
            )
            activity.append_activity(f"{job['label']} movil", str(result))

    threading.Thread(target=worker, daemon=True).start()
    return dict(job)


def _recent_jobs() -> list[dict]:
    with _JOBS_LOCK:
        return sorted(_JOBS.values(), key=lambda item: item.get("started_at", ""), reverse=True)[:10]


def _get_job(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(str(job_id).strip())
        return dict(job) if job else None


def _notify_mobile_job_timeout(job: dict, exc: MobileJobTimeoutError) -> None:
    label = str(job.get("label", "Operacion movil")).strip() or "Operacion movil"
    body = (
        f"{label} excedio el timeout movil de {exc.timeout_seconds} segundos y fue detenida.\n"
        "Puedes aumentar este limite desde Configuracion > UI movil."
    )
    try:
        send_notification("Yarbis detuvo una operacion movil", body)
    except Exception:
        pass


def _public_model_settings(settings: dict, provider: str) -> dict:
    default_model = "" if provider == MODEL_PROVIDER_OPENROUTER else DEFAULT_OLLAMA_MODEL
    default_host = DEFAULT_OPENROUTER_HOST if provider == MODEL_PROVIDER_OPENROUTER else ""
    default_timeout = (
        DEFAULT_OPENROUTER_TIMEOUT_SECONDS
        if provider == MODEL_PROVIDER_OPENROUTER
        else DEFAULT_OLLAMA_TIMEOUT_SECONDS
    )
    return {
        "model": str(settings.get("model", default_model)).strip() or default_model,
        "fallback_models": settings.get("fallback_models", []) if isinstance(settings.get("fallback_models"), list) else [],
        "host": str(settings.get("host", default_host)).strip(),
        "api_key": "",
        "api_key_configured": bool(str(settings.get("api_key", "")).strip()),
        "api_key_env_var": str(settings.get("api_key_env_var", "")).strip(),
        "timeout_seconds": settings.get("timeout_seconds", default_timeout),
    }


def _public_notifications(settings: dict) -> dict:
    ntfy = settings.get("ntfy", {}) if isinstance(settings.get("ntfy", {}), dict) else {}
    telegram = settings.get("telegram", {}) if isinstance(settings.get("telegram", {}), dict) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "channels": settings.get("channels", []) if isinstance(settings.get("channels"), list) else [],
        "ntfy": {
            "server": str(ntfy.get("server", "https://ntfy.sh")).strip(),
            "topic": str(ntfy.get("topic", "")).strip(),
            "token": "",
            "token_configured": bool(str(ntfy.get("token", "")).strip()),
            "priority": str(ntfy.get("priority", "")).strip(),
            "tags": str(ntfy.get("tags", "")).strip(),
        },
        "telegram": {
            "bot_token": "",
            "bot_token_configured": bool(str(telegram.get("bot_token", "")).strip()),
            "chat_id": str(telegram.get("chat_id", "")).strip(),
        },
    }


def _public_social(state: dict) -> dict:
    social = state.get("social", {}) if isinstance(state.get("social", {}), dict) else {}
    return {
        "settings": social.get("settings", {}) if isinstance(social.get("settings", {}), dict) else {},
        "accounts_count": len(social.get("accounts", [])) if isinstance(social.get("accounts", []), list) else 0,
        "drafts_count": len(social.get("drafts", [])) if isinstance(social.get("drafts", []), list) else 0,
        "pending_publications": (
            social.get("pending_publications", [])
            if isinstance(social.get("pending_publications", []), list)
            else []
        ),
    }


def _model_provider_from_state(state: dict) -> dict:
    model_provider = state.get("model_provider", {}) if isinstance(state, dict) else {}
    if not isinstance(model_provider, dict):
        model_provider = {}
    default_provider = str(model_provider.get("default", MODEL_PROVIDER_OLLAMA)).strip().lower()
    if default_provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
        default_provider = MODEL_PROVIDER_OLLAMA
    ollama = model_provider.get(MODEL_PROVIDER_OLLAMA, state.get("ollama", {}))
    if not isinstance(ollama, dict):
        ollama = {}
    openrouter = model_provider.get(MODEL_PROVIDER_OPENROUTER, {})
    if not isinstance(openrouter, dict):
        openrouter = {}
    return {
        "default": default_provider,
        MODEL_PROVIDER_OLLAMA: _public_model_settings(ollama, MODEL_PROVIDER_OLLAMA),
        MODEL_PROVIDER_OPENROUTER: _public_model_settings(openrouter, MODEL_PROVIDER_OPENROUTER),
    }


def _mobile_health_status_from_state(state: dict, service_status: dict) -> dict:
    notifications = state.get("notifications", {}) if isinstance(state, dict) else {}
    if not isinstance(notifications, dict):
        notifications = {}
    channels = notifications.get("channels", [])
    if not isinstance(channels, list):
        channels = []
    telegram = notifications.get("telegram", {})
    if not isinstance(telegram, dict):
        telegram = {}

    service = state.get("service", {})
    if not isinstance(service, dict):
        service = {}
    proactive = service.get("proactive", {})
    if not isinstance(proactive, dict):
        proactive = {}
    mobile_settings = service.get("mobile_ui", {})
    if not isinstance(mobile_settings, dict):
        mobile_settings = {}
    runtime = state.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
    thinking = runtime.get("thinking", {})
    if not isinstance(thinking, dict):
        thinking = {}

    model_provider = _model_provider_from_state(state)
    provider = model_provider.get("default", MODEL_PROVIDER_OLLAMA)
    active_model = dict(model_provider.get(provider, {}))
    active_model["label"] = "OpenRouter" if provider == MODEL_PROVIDER_OPENROUTER else "Ollama"
    operation_active = bool(thinking.get("active") and str(thinking.get("label", "")).strip())

    return {
        "service": service_status,
        "telegram": {
            "enabled": bool(notifications.get("enabled", True) and "telegram" in channels),
            "configured": bool(str(telegram.get("bot_token", "")).strip()),
            "linked": bool(str(telegram.get("chat_id", "")).strip()),
        },
        "proactive": {
            "enabled": bool(proactive.get("enabled")),
            "interval_seconds": proactive.get("interval_seconds"),
            "cycles": proactive.get("cycles"),
            "model": str(proactive.get("model", "")).strip(),
            "last_pulse_at": str(proactive.get("last_pulse_at", "")).strip(),
        },
        "mobile_ui": {
            "enabled": bool(mobile_settings.get("enabled")),
            "configured": bool(str(mobile_settings.get("pin_hash", "")).strip()),
            "port": mobile_settings.get("port"),
            "job_timeout_seconds": mobile_settings.get("job_timeout_seconds", DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS),
            "last_bind_error": str(mobile_settings.get("last_bind_error", "")).strip(),
        },
        "operation": {
            "active": operation_active,
            "label": str(thinking.get("label", "")).strip() if operation_active else "",
            "operation_id": str(thinking.get("operation_id", "")).strip() if operation_active else "",
            "started_at": str(thinking.get("started_at", "")).strip() if operation_active else "",
        },
        "ollama": model_provider.get(MODEL_PROVIDER_OLLAMA, {}),
        "openrouter": model_provider.get(MODEL_PROVIDER_OPENROUTER, {}),
        "model_provider": active_model,
        "events_file": str(activity.EVENTS_FILE),
        "state_error": "",
    }


def _mobile_readiness_text_from_state(state: dict, service_status: dict) -> str:
    if service_status.get("running"):
        return "Servicio activo. UI movil lista."
    goal = str(state.get("goal", "")).strip() if isinstance(state, dict) else ""
    if not service_status.get("installed"):
        return "Servicio no instalado. La UI puede usarse mientras Yarbis este abierto."
    if not goal:
        return "Servicio detenido y falta objetivo principal."
    return f"Servicio detenido ({service_status.get('state', 'unknown')})."


def _mobile_memory_protection_status(state: dict) -> str:
    settings = state.get("memory_protection", {}) if isinstance(state, dict) else {}
    if not isinstance(settings, dict):
        settings = {}
    retention = settings.get("retention", {})
    if not isinstance(retention, dict):
        retention = {}
    lines = [
        f"Proteccion: {'activa' if settings.get('enabled') else 'desactivada'}",
        f"Backup por cambio: {'si' if settings.get('backup_on_every_change') else 'no'}",
        f"Verificar al escribir: {'si' if settings.get('verify_after_write') else 'no'}",
        f"Auto-restaurar: {'si' if settings.get('auto_restore') else 'no'}",
        f"Max backups: {retention.get('max_auto_backups', '-')}",
        f"Conservar diarios: {retention.get('keep_daily_days', '-')} dias",
    ]
    mirror_dir = str(settings.get("mirror_dir", "")).strip()
    if mirror_dir:
        lines.append(f"Espejo: {mirror_dir}")
    if settings.get("last_backup_at"):
        lines.append(f"Ultimo backup: {settings['last_backup_at']}")
    if settings.get("last_error"):
        lines.append(f"Ultimo aviso: {settings['last_error']}")
    return "\n".join(lines)


def _mobile_social_accounts_text(state: dict) -> str:
    social = state.get("social", {}) if isinstance(state, dict) else {}
    if not isinstance(social, dict):
        social = {}
    accounts = social.get("accounts", []) if isinstance(social.get("accounts", []), list) else []
    drafts = social.get("drafts", []) if isinstance(social.get("drafts", []), list) else []
    pending = (
        social.get("pending_publications", [])
        if isinstance(social.get("pending_publications", []), list)
        else []
    )
    return (
        f"Cuentas: {len(accounts)}\n"
        f"Drafts: {len(drafts)}\n"
        f"Pendientes de confirmacion: {len(pending)}"
    )


def _mobile_coding_proposals_text(state: dict) -> str:
    try:
        return coding_list_proposals_text(status="pending", limit=20)
    except Exception:
        coding = state.get("coding", {}) if isinstance(state, dict) else {}
        if not isinstance(coding, dict):
            coding = {}
        pending_ids = coding.get("pending_proposal_ids", [])
        if not isinstance(pending_ids, list):
            pending_ids = []
        cleaned_ids = [str(item).strip() for item in pending_ids if str(item).strip()]
        if not cleaned_ids:
            return "No hay propuestas de coding pendientes."
        return "Propuestas pendientes:\n" + "\n".join(f"- {proposal_id}" for proposal_id in cleaned_ids[:20])


def _mobile_activity_history(state: dict) -> str:
    signature = activity.activity_history_signature()

    def build() -> str:
        redactor = build_secret_redactor(state)
        human_log = activity.read_activity_log(max_bytes=MOBILE_ACTIVITY_MAX_BYTES)
        rendered_events = []
        for event in activity.read_recent_events(limit=MOBILE_ACTIVITY_EVENT_LIMIT):
            rendered_events.append(activity.format_activity_entry(
                activity._event_title(event),
                activity._event_content(event),
                timestamp=activity._parse_event_timestamp(event.get("timestamp", "")),
                redactor=redactor,
            ))
        event_log = "".join(rendered_events)
        if human_log and event_log:
            return human_log + "Eventos recientes\n\n" + event_log
        return human_log or event_log

    return str(_cached_value(
        "activity_history",
        signature,
        MOBILE_CACHE_TTL_SECONDS,
        build,
    ) or "")


def _public_base_state(state: dict) -> dict:
    service = state.get("service", {}) if isinstance(state, dict) else {}
    if not isinstance(service, dict):
        service = {}
    mobile_settings = service.get("mobile_ui", {})
    if not isinstance(mobile_settings, dict):
        mobile_settings = {}
    service_status = get_service_status()
    health = _mobile_health_status_from_state(state, service_status)
    jobs = _recent_jobs()
    conversation = conversation_ux.build_conversation_view(
        state,
        service_status=service_status,
        jobs=jobs,
        voice_status=voice_conversation.desktop_status(),
        channel="mobile",
        limit=6,
    )
    return {
        "now": _utc_now(),
        "goal": state.get("goal", ""),
        "cycle_count": state.get("cycle_count", 0),
        "last_result": _truncate_text(state.get("last_result", "")),
        "awaiting_user_input": state.get("awaiting_user_input", {}),
        "conversation": conversation,
        "service": {
            "status": service_status,
            "mobile_ui": public_mobile_ui_status(mobile_settings),
        },
        "health_text": format_health_status(health),
        "readiness_text": _mobile_readiness_text_from_state(state, service_status),
        "jobs": jobs,
    }


def _public_context_state(state: dict) -> dict:
    coding = state.get("coding", {}) if isinstance(state.get("coding", {}), dict) else {}
    return {
        "profile": state.get("profile", {}),
        "tasks": state.get("tasks", []),
        "notes": state.get("notes", []),
        "current_plan": state.get("current_plan", []),
        "idea_projects": state.get("idea_projects", []),
        "autonomy": state.get("autonomy", {}),
        "coding": {
            **coding,
            "proposals_text": _mobile_coding_proposals_text(state),
        },
    }


def _public_visual_state(state: dict) -> dict:
    return {
        "idea_projects": state.get("idea_projects", []),
    }


def _public_settings_state(state: dict) -> dict:
    service = state.get("service", {}) if isinstance(state.get("service", {}), dict) else {}
    mobile_settings = service.get("mobile_ui", {}) if isinstance(service.get("mobile_ui", {}), dict) else {}
    social = _public_social(state)
    return {
        "internet": state.get("internet", {}),
        "memory_protection": state.get("memory_protection", {}),
        "memory_protection_status": _mobile_memory_protection_status(state),
        "model_provider": _model_provider_from_state(state),
        "service": {
            "proactive": service.get("proactive", {}),
            "mobile_ui": public_mobile_ui_status(mobile_settings),
        },
        "local_context": state.get("local_context", {}),
        "notifications": _public_notifications(state.get("notifications", {})),
        "voice": state.get("voice", {}),
        "social": social,
        "social_accounts_text": _mobile_social_accounts_text(state),
        "social_drafts_text": f"Drafts: {social.get('drafts_count', 0)}",
        "social_publications": social["pending_publications"],
    }


def _public_activity_state(state: dict) -> dict:
    return {
        "summary_text": render_state_summary(state),
        "activity_text": _mobile_activity_history(state),
    }


def _public_state(view: str = "") -> dict:
    normalized_view = str(view or "").strip().lower()
    if normalized_view not in {"", "home", "run", "context", "visual", "settings", "activity"}:
        raise ValueError("Vista movil invalida.")

    state = load_state()
    public = _public_base_state(state)
    view_state = {}
    if normalized_view == "context":
        view_state = _public_context_state(state)
    elif normalized_view == "visual":
        view_state = _public_visual_state(state)
    elif normalized_view == "settings":
        view_state = _public_settings_state(state)
    elif normalized_view == "activity":
        view_state = _public_activity_state(state)
    view_service = view_state.pop("service", None)
    if isinstance(view_service, dict):
        public.setdefault("service", {}).update(view_service)
    public.update(view_state)
    return public


def _payload_text(payload: dict, key: str, default: str = "") -> str:
    return str(payload.get(key, default) if isinstance(payload, dict) else default).strip()


def _copy_social_confirmation(publication_id: str) -> str:
    cleaned_id = str(publication_id).strip().lower()
    if not cleaned_id:
        raise ValueError("Indica un id de publicacion pendiente.")
    state = load_state()
    publications = state.get("social", {}).get("pending_publications", [])
    matches = [
        publication
        for publication in publications
        if str(publication.get("id", "")).lower() == cleaned_id
        or str(publication.get("id", "")).lower().startswith(cleaned_id)
    ]
    if len(matches) != 1:
        raise ValueError("No encontre una publicacion pendiente unica con ese id.")
    return str(matches[0].get("confirmation_phrase", f"PUBLICAR {matches[0]['id']}"))


def _transcribe_mobile_voice(payload: dict) -> str:
    raw_audio, suffix = yarbis_voice.decode_audio_b64(
        _payload_text(payload, "audio_b64"),
        mime_type=_payload_text(payload, "mime_type"),
    )
    return yarbis_voice.transcribe_audio_bytes(
        raw_audio,
        mime_type=_payload_text(payload, "mime_type"),
        suffix=suffix,
        settings=load_state(),
    )


def _decode_mobile_audio_payload(payload: dict) -> tuple[bytes, str]:
    return yarbis_voice.decode_audio_b64(
        _payload_text(payload, "audio_b64"),
        mime_type=_payload_text(payload, "mime_type"),
    )


def _start_mobile_live_voice() -> dict:
    session = voice_conversation.start_mobile_session(load_state())
    return {"voice_session": session}


def _append_mobile_live_voice(payload: dict) -> dict:
    raw_audio, _suffix = _decode_mobile_audio_payload(payload)
    session = voice_conversation.append_mobile_audio_chunk(
        _payload_text(payload, "session_id"),
        raw_audio,
        mime_type=_payload_text(payload, "mime_type"),
        settings=load_state(),
    )
    return {"voice_session": session}


def _stop_mobile_live_voice(payload: dict) -> dict:
    session = voice_conversation.stop_mobile_session(_payload_text(payload, "session_id"))
    return {"voice_session": session}


def _public_voice_payload(*, include_downloadable: bool = False, refresh_catalog: bool = False) -> dict:
    try:
        voices = yarbis_voice.list_tts_voices(
            load_state(),
            include_downloadable=include_downloadable,
            language="all" if include_downloadable else None,
            refresh_catalog=refresh_catalog,
        )
    except Exception:
        voices = []
    return {
        "voices": voices,
        "settings": load_state().get("voice", {}),
    }


def _synthesize_mobile_speech(payload: dict) -> tuple[str, str]:
    text = _payload_text(payload, "text")
    if not text:
        raise ValueError("No hay texto para escuchar.")
    requested_format = _payload_text(payload, "format", "wav").lower()
    if requested_format in {"ogg", "opus", "audio/ogg"}:
        synthesizer = yarbis_voice.synthesize_speech_file
        mime_type = "audio/ogg"
    else:
        synthesizer = yarbis_voice.synthesize_speech_wav_file
        mime_type = "audio/wav"
    audio_path = None
    try:
        audio_path = synthesizer(text, settings=load_state())
        raw_audio = audio_path.read_bytes()
        return base64.b64encode(raw_audio).decode("ascii"), mime_type
    finally:
        yarbis_voice.cleanup_voice_file(audio_path)


def _execute_action(action: str, payload: dict | None = None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    action = str(action).strip()
    if action == "run_cycle":
        return {"job": _start_job("Ciclo", "run_cycle", {})}
    if action == "run_auto":
        return {"job": _start_job("Modo autonomo", "run_auto", {"cycles": payload.get("cycles")})}
    if action == "submit_reply":
        reply_text = _payload_text(payload, "reply_text")
        if not reply_text:
            raise ValueError("Escribe una respuesta o contexto antes de enviarlo.")
        return {"job": _start_job("Respuesta", "submit_user_reply", {"reply_text": reply_text})}
    if action == "stop_operation":
        return {"result": request_stop_current_operation(source="mobile")}
    if action == "update_goal":
        return {"result": update_goal(_payload_text(payload, "goal"))}
    if action == "update_profile":
        return {"result": update_profile_text(
            name=_payload_text(payload, "name"),
            role=_payload_text(payload, "role"),
            preferences=_payload_text(payload, "preferences"),
            constraints=_payload_text(payload, "constraints"),
        )}
    if action == "save_note":
        return {"result": save_note_text(
            title=_payload_text(payload, "title"),
            content=_payload_text(payload, "content"),
            category=_payload_text(payload, "category", "general") or "general",
        )}
    if action == "get_note":
        return {"result": get_note(_payload_text(payload, "identifier"))}
    if action == "delete_note":
        return {"result": delete_note(_payload_text(payload, "identifier"))}
    if action == "add_task":
        return {"result": add_task_text(
            title=_payload_text(payload, "title"),
            details=_payload_text(payload, "details"),
            priority=_payload_text(payload, "priority", "media") or "media",
        )}
    if action == "create_idea_project":
        return {"result": create_idea_project_text(
            title=_payload_text(payload, "title"),
            kind=_payload_text(payload, "kind", "mixto") or "mixto",
            summary=_payload_text(payload, "summary"),
            audience=_payload_text(payload, "audience"),
            desired_outcome=_payload_text(payload, "desired_outcome"),
            problem=_payload_text(payload, "problem"),
            creative_directions=_payload_text(payload, "creative_directions"),
            selected_direction=_payload_text(payload, "selected_direction"),
            success_criteria=_payload_text(payload, "success_criteria"),
            constraints=_payload_text(payload, "constraints"),
            risks=_payload_text(payload, "risks"),
            open_questions=_payload_text(payload, "open_questions"),
            next_steps=_payload_text(payload, "next_steps"),
            status=_payload_text(payload, "status", "exploring") or "exploring",
        )}
    if action == "update_idea_project":
        return {"result": update_idea_project_text(
            project_id=_payload_text(payload, "project_id"),
            title=_payload_text(payload, "title"),
            kind=_payload_text(payload, "kind"),
            status=_payload_text(payload, "status"),
            summary=_payload_text(payload, "summary"),
            audience=_payload_text(payload, "audience"),
            desired_outcome=_payload_text(payload, "desired_outcome"),
            problem=_payload_text(payload, "problem"),
            creative_directions=_payload_text(payload, "creative_directions"),
            selected_direction=_payload_text(payload, "selected_direction"),
            success_criteria=_payload_text(payload, "success_criteria"),
            constraints=_payload_text(payload, "constraints"),
            risks=_payload_text(payload, "risks"),
            open_questions=_payload_text(payload, "open_questions"),
            next_steps=_payload_text(payload, "next_steps"),
        )}
    if action == "promote_idea_project":
        return {"result": promote_idea_project_to_work_text(
            project_id=_payload_text(payload, "project_id"),
            priority=_payload_text(payload, "priority", "media") or "media",
        )}
    if action == "create_visual_board":
        return {"result": create_project_visual_board_text(
            project_id=_payload_text(payload, "project_id"),
            board_kind=_payload_text(payload, "board_kind", "idea_canvas") or "idea_canvas",
            title=_payload_text(payload, "title"),
        )}
    if action == "list_visual_boards":
        return {"result": list_project_visual_boards_text(
            project_id=_payload_text(payload, "project_id"),
        )}
    if action == "get_visual_board":
        return {"result": get_project_visual_board_text(
            project_id=_payload_text(payload, "project_id"),
            board_id=_payload_text(payload, "board_id"),
        )}
    if action == "update_visual_board":
        return {"result": update_project_visual_board_text(
            project_id=_payload_text(payload, "project_id"),
            board_id=_payload_text(payload, "board_id"),
            board_json=(
                payload.get("board_json")
                if isinstance(payload.get("board_json"), str)
                else json.dumps(payload.get("board", {}), ensure_ascii=False)
            ),
        )}
    if action == "export_visual_board":
        return {"result": export_project_visual_board_text(
            project_id=_payload_text(payload, "project_id"),
            board_id=_payload_text(payload, "board_id"),
            formats=_payload_text(payload, "formats", "html,svg,json") or "html,svg,json",
            open_file=bool(payload.get("open_file")),
        )}
    if action == "update_task_status":
        return {"result": update_task_status(
            task_id=_payload_text(payload, "task_id"),
            status=_payload_text(payload, "status"),
            result=_payload_text(payload, "result"),
        )}
    if action == "set_plan":
        return {"result": set_plan(_payload_text(payload, "plan_text"))}
    if action == "coding_workspace":
        return {"result": coding_set_workspace_text(_payload_text(payload, "path"))}
    if action == "coding_status":
        return {"result": coding_workflow_status_text(include_diff=bool(payload.get("include_diff")))}
    if action == "coding_search":
        return {"result": coding_search_text_text(
            pattern=_payload_text(payload, "pattern"),
            path=_payload_text(payload, "path", ".") or ".",
            glob=_payload_text(payload, "glob"),
        )}
    if action == "coding_read_range":
        return {"result": coding_read_text_range_text(
            path=_payload_text(payload, "path"),
            start_line=payload.get("start_line", 1),
            line_count=payload.get("line_count", 120),
        )}
    if action == "coding_propose_edits":
        return {"result": coding_propose_edits_text(
            title=_payload_text(payload, "title", "Edicion localizada") or "Edicion localizada",
            edits_json=_payload_text(payload, "edits_json"),
            summary=_payload_text(payload, "summary"),
            reason=_payload_text(payload, "reason"),
            validation_command=_payload_text(payload, "validation_command"),
        )}
    if action == "coding_get":
        return {"result": coding_get_proposal_text(_payload_text(payload, "proposal_id"))}
    if action == "coding_check":
        return {"result": coding_check_proposal_text(_payload_text(payload, "proposal_id"))}
    if action == "coding_apply":
        return {"result": coding_apply_proposal_text(_payload_text(payload, "proposal_id"))}
    if action == "coding_apply_validate":
        return {"result": coding_apply_and_validate_text(
            proposal_id=_payload_text(payload, "proposal_id"),
            command=_payload_text(payload, "command"),
        )}
    if action == "coding_discard":
        return {"result": coding_discard_proposal_text(_payload_text(payload, "proposal_id"))}
    if action == "coding_validate":
        return {"result": coding_run_validation_text(
            proposal_id=_payload_text(payload, "proposal_id"),
            command=_payload_text(payload, "command"),
        )}
    if action == "coding_validation":
        return {"result": coding_update_validation_command_text(_payload_text(payload, "command"))}
    if action == "coding_detect_validation":
        return {"result": coding_detect_validation_command_text()}
    if action == "coding_validation_plan":
        return {"result": coding_validation_plan_text(_payload_text(payload, "proposal_id"))}
    if action == "update_model":
        provider = _payload_text(payload, "provider", MODEL_PROVIDER_OLLAMA).lower()
        if provider == MODEL_PROVIDER_OPENROUTER:
            result = update_openrouter_settings(
                model=_payload_text(payload, "model"),
                timeout_seconds=payload.get("timeout_seconds"),
                host=_payload_text(payload, "host"),
                fallback_models=payload.get("fallback_models", ""),
                api_key=_payload_text(payload, "api_key") or None,
                api_key_env_var=_payload_text(payload, "api_key_env_var") or None,
            )
        else:
            result = update_ollama_settings(
                model=_payload_text(payload, "model"),
                timeout_seconds=payload.get("timeout_seconds"),
                host=_payload_text(payload, "host"),
                fallback_models=payload.get("fallback_models", ""),
                api_key=_payload_text(payload, "api_key") or None,
                api_key_env_var=_payload_text(payload, "api_key_env_var") or None,
            )
        provider_result = update_model_provider(provider)
        return {"result": f"{provider_result}\n{result}"}
    if action == "service_proactive":
        return {"result": update_service_proactive_settings(
            enabled=bool(payload.get("enabled")),
            interval_seconds=payload.get("interval_seconds"),
            cycles=payload.get("cycles"),
            start_delay_seconds=payload.get("start_delay_seconds"),
            model=payload.get("model", ""),
        )}
    if action == "local_context":
        return {"result": update_local_context_settings(
            enabled=bool(payload.get("enabled")),
            mode=_payload_text(payload, "mode", "safe"),
            sample_interval_seconds=payload.get("sample_interval_seconds"),
            max_snapshot_age_seconds=payload.get("max_snapshot_age_seconds"),
            include_window_title=bool(payload.get("include_window_title")),
            include_process_name=bool(payload.get("include_process_name")),
            include_workspace_changes=bool(payload.get("include_workspace_changes")),
            include_system_health=bool(payload.get("include_system_health")),
        )}
    if action == "notifications":
        current_notifications = get_notification_settings()
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
        return {"result": update_notification_settings(
            enabled=bool(payload.get("enabled")),
            windows_enabled=bool(payload.get("windows_enabled")),
            ntfy_enabled=bool(payload.get("ntfy_enabled")),
            telegram_enabled=bool(payload.get("telegram_enabled")),
            ntfy_server=_payload_text(payload, "ntfy_server"),
            ntfy_topic=_payload_text(payload, "ntfy_topic"),
            ntfy_token=_payload_text(payload, "ntfy_token") or str(current_ntfy.get("token", "")).strip(),
            ntfy_priority=_payload_text(payload, "ntfy_priority"),
            ntfy_tags=_payload_text(payload, "ntfy_tags"),
            telegram_bot_token=(
                _payload_text(payload, "telegram_bot_token")
                or str(current_telegram.get("bot_token", "")).strip()
            ),
            telegram_chat_id=_payload_text(payload, "telegram_chat_id"),
        )}
    if action == "send_test_notification":
        return {"result": send_test_notification()}
    if action == "mobile_ui":
        return {"result": update_mobile_ui_settings(
            enabled=bool(payload.get("enabled")),
            port=payload.get("port", DEFAULT_MOBILE_UI_PORT),
            job_timeout_seconds=payload.get("job_timeout_seconds"),
            pin=str(payload.get("pin", "")),
        )}
    if action == "voice_settings":
        tts_provider = _payload_text(payload, "tts_provider", "system") or "system"
        return {"result": yarbis_voice.update_voice_settings_text(
            enabled=bool(payload.get("enabled", True)),
            tts_provider=tts_provider,
            tts_voice_id=_payload_text(payload, "tts_voice_id"),
            tts_rate=payload.get("tts_rate"),
            kokoro_voice_id=_payload_text(payload, "kokoro_voice_id"),
            browser_voice_name=_payload_text(payload, "browser_voice_name"),
            browser_tts_rate=payload.get("browser_tts_rate"),
            browser_tts_pitch=payload.get("browser_tts_pitch"),
            telegram_reply_mode=_payload_text(payload, "telegram_reply_mode", "auto") or "auto",
            live_enabled=bool(payload.get("live_enabled", True)),
            live_wake_phrase=_payload_text(payload, "live_wake_phrase"),
            live_silence_ms=payload.get("live_silence_ms"),
            live_max_turn_seconds=payload.get("live_max_turn_seconds"),
            live_auto_speak=bool(payload.get("live_auto_speak", True)),
            live_barge_in=bool(payload.get("live_barge_in", True)),
        )}
    if action == "memory_protection":
        return {"result": update_memory_protection_settings_text(**payload)}
    if action == "memory_backup":
        return {"result": create_memory_backup_text(
            path=_payload_text(payload, "path"),
            include_secrets=bool(payload.get("include_secrets")),
        )}
    if action == "memory_inspect":
        return {"result": inspect_memory_backup_text(_payload_text(payload, "path_or_id"))}
    if action == "memory_import":
        return {"result": import_memory_backup_text(
            path_or_id=_payload_text(payload, "path_or_id"),
            mode=_payload_text(payload, "mode", "replace") or "replace",
        )}
    if action == "memory_verify":
        return {"result": verify_memory_backups_text()}
    if action == "service_start":
        return {"result": start_service()}
    if action == "service_stop":
        return {"result": stop_service()}
    if action == "service_install":
        return {"result": install_service(
            start_auto=bool(payload.get("start_auto", True)),
            account_name=_payload_text(payload, "account_name"),
            password=_payload_text(payload, "password"),
        )}
    if action == "service_remove":
        return {"result": remove_service()}
    if action == "service_autostart":
        return {"result": set_autostart_enabled(bool(payload.get("enabled")))}
    if action == "social_oauth":
        return {"result": start_social_oauth_text(**payload)}
    if action == "social_confirmation":
        return {"result": _copy_social_confirmation(_payload_text(payload, "publication_id"))}
    if action == "social_assisted":
        return {"result": open_assisted_social_post(
            publication_id=_payload_text(payload, "publication_id"),
            draft_id=_payload_text(payload, "draft_id"),
        )}
    if action == "internet":
        return {"result": update_internet_settings(
            mode=_payload_text(payload, "mode"),
            provider=_payload_text(payload, "provider"),
            max_search_results=payload.get("max_search_results", 0),
            max_page_chars=payload.get("max_page_chars", 0),
            request_timeout_seconds=payload.get("request_timeout_seconds", 0),
            allowed_domains=_payload_text(payload, "allowed_domains"),
            blocked_domains=_payload_text(payload, "blocked_domains"),
        )}
    if action == "clear_activity":
        activity.clear_activity_history()
        return {"result": "Actividad limpiada."}
    if action == "factory_reset":
        confirmation = _payload_text(payload, "confirmation")
        if confirmation != "REINICIAR":
            raise ValueError("Escribe REINICIAR para confirmar el reinicio de fabrica.")
        return {"result": factory_reset_yarbis()}
    raise ValueError(f"Accion movil desconocida: {action}")


def _html_page() -> str:
    return r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Yarbis movil</title>
<style>
:root {
  color-scheme: dark;
  --bg: #0f1115;
  --surface: #141820;
  --panel: #191e27;
  --panel-2: #242b36;
  --text: #f5f7fa;
  --muted: #a6afbd;
  --line: #303846;
  --accent: #5b8cff;
  --accent-2: #32c776;
  --warning: #d9a441;
  --danger: #e35d6a;
  --field: #0b0e13;
  --shadow: 0 14px 36px rgba(0,0,0,.28);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  letter-spacing: 0;
  line-height: 1.45;
}
button, input, textarea, select {
  font: inherit;
}
.shell {
  max-width: 980px;
  margin: 0 auto;
  min-height: 100vh;
  padding: 16px 16px 92px;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 2;
  margin: -16px -16px 14px;
  padding: 13px 16px;
  background: rgba(15, 17, 21, .96);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}
.title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
h1, h2, h3 {
  margin: 0;
  font-weight: 700;
}
h1 { font-size: 1.35rem; }
h2 { font-size: 1.05rem; margin-bottom: 10px; }
h3 { font-size: .98rem; margin-bottom: 8px; }
.muted { color: var(--muted); }
.hero {
  display: grid;
  gap: 10px;
  padding: 14px 0 4px;
}
.hero h2 {
  font-size: 1.24rem;
  margin-bottom: 0;
}
.pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 4px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  font-size: .82rem;
  white-space: nowrap;
}
.pill.good {
  border-color: rgba(50, 199, 118, .55);
  color: #8ee8b5;
}
.pill.warn {
  border-color: rgba(217, 164, 65, .6);
  color: #f0cf86;
}
.pill.bad {
  border-color: rgba(227, 93, 106, .65);
  color: #ffb5bd;
}
.grid {
  display: grid;
  gap: 10px;
}
.two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.section {
  border-top: 1px solid var(--line);
  padding: 16px 0;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
  box-shadow: var(--shadow);
}
.visual-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 12px;
  align-items: start;
}
.visual-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}
.visual-toolbar select,
.visual-toolbar input {
  width: auto;
  min-width: 150px;
}
.visual-board-shell {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #0b0e13;
  min-height: 620px;
  overflow: hidden;
  position: relative;
}
.visual-canvas {
  width: 100%;
  height: min(68vh, 680px);
  min-height: 520px;
  display: block;
  touch-action: none;
  cursor: grab;
}
.visual-canvas.dragging {
  cursor: grabbing;
}
.visual-node rect {
  fill: #202838;
  stroke: #526074;
  stroke-width: 1.4;
}
.visual-node.selected rect {
  stroke: #8eb6ff;
  stroke-width: 2.4;
}
.visual-node .title {
  fill: #f7f9fc;
  font: 700 15px system-ui;
  pointer-events: none;
}
.visual-node .body {
  fill: #c0c9d6;
  font: 12px system-ui;
  pointer-events: none;
}
.visual-lane rect {
  fill: #151b24;
  stroke: #2f3948;
  stroke-width: 1.2;
}
.visual-lane text {
  fill: #d9e2ef;
  font: 700 14px system-ui;
  pointer-events: none;
}
.visual-edge {
  stroke: #6d7a8d;
  stroke-width: 2;
  fill: none;
  pointer-events: none;
}
.visual-inspector {
  display: grid;
  gap: 10px;
}
.visual-inspector textarea {
  min-height: 132px;
}
.metric {
  min-height: 74px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
}
.metric strong {
  font-size: 1.15rem;
}
.metric .muted {
  font-size: .82rem;
  text-transform: uppercase;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.actions.tight {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.toolbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
button {
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-2);
  color: var(--text);
  padding: 9px 11px;
  cursor: pointer;
}
button.primary {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
  font-weight: 700;
}
button.secondary {
  background: var(--accent-2);
  border-color: var(--accent-2);
  color: #07140d;
  font-weight: 700;
}
button.danger {
  border-color: var(--danger);
  color: #ffd6d4;
}
button:disabled {
  opacity: .55;
}
label {
  display: block;
  color: var(--muted);
  font-size: .86rem;
  margin-bottom: 4px;
}
input, textarea, select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--field);
  color: var(--text);
  padding: 10px;
  min-height: 42px;
}
textarea {
  min-height: 94px;
  resize: vertical;
}
.form-grid {
  display: grid;
  gap: 10px;
}
.compact {
  min-height: 42px;
  padding: 8px 10px;
}
.tabs {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 3;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 1px;
  background: var(--line);
  border-top: 1px solid var(--line);
}
.tab {
  border: 0;
  border-radius: 0;
  min-height: 58px;
  background: #15191d;
  color: var(--muted);
  font-size: .78rem;
}
.tab.active {
  color: var(--text);
  background: var(--panel-2);
}
.view { display: none; }
.view.active { display: block; }
pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: .86rem;
}
.list {
  display: grid;
  gap: 8px;
}
.item {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  background: var(--panel);
}
.setting-group {
  display: grid;
  gap: 10px;
}
.setting-group + .setting-group {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}
.empty {
  color: var(--muted);
  border: 1px dashed var(--line);
  border-radius: 8px;
  padding: 12px;
}
.toast {
  position: fixed;
  left: 12px;
  right: 12px;
  bottom: 72px;
  z-index: 5;
  background: #f4f1ec;
  color: #141414;
  border-radius: 8px;
  padding: 10px 12px;
  box-shadow: 0 10px 30px rgba(0,0,0,.35);
  display: none;
}
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 18px;
  background: rgba(3, 6, 10, .66);
}
.modal-backdrop.open {
  display: flex;
}
.modal {
  width: min(460px, 100%);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  box-shadow: var(--shadow);
}
.modal-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 12px;
}
.login {
  max-width: 420px;
  margin: 14vh auto 0;
}
.hidden { display: none !important; }
@media (min-width: 760px) {
  .tabs {
    left: 50%;
    transform: translateX(-50%);
    max-width: 980px;
  }
  .form-grid.wide {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 900px) {
  .visual-layout {
    grid-template-columns: 1fr;
  }
  .visual-toolbar select,
  .visual-toolbar input {
    width: 100%;
  }
}
@media (max-width: 520px) {
  .two, .actions, .actions.tight {
    grid-template-columns: 1fr;
  }
  .shell {
    padding-left: 12px;
    padding-right: 12px;
  }
  .topbar {
    margin-left: -12px;
    margin-right: -12px;
  }
}
</style>
</head>
<body>
<div id="toast" class="toast"></div>
<div id="modalBackdrop" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
  <div class="modal">
    <h2 id="modalTitle"></h2>
    <p id="modalMessage" class="muted"></p>
    <div id="modalInputWrap" class="hidden">
      <label id="modalInputLabel" for="modalInput">Valor</label>
      <input id="modalInput">
    </div>
    <div class="modal-actions">
      <button id="modalCancel" type="button">Cancelar</button>
      <button id="modalOk" class="primary" type="button">Continuar</button>
    </div>
  </div>
</div>
<main class="shell">
  <section id="loginView" class="login panel hidden">
    <h1>Yarbis movil</h1>
    <p class="muted">Acceso privado por Tailscale.</p>
    <form id="loginForm" class="form-grid">
      <div>
        <label for="pinInput">PIN</label>
        <input id="pinInput" type="password" inputmode="numeric" autocomplete="current-password">
      </div>
      <button class="primary" type="submit">Entrar</button>
      <p id="loginMessage" class="muted"></p>
    </form>
  </section>

  <section id="appView" class="hidden">
    <header class="topbar">
      <div class="title-row">
        <div>
          <h1>Yarbis</h1>
          <div id="urlLine" class="muted"></div>
        </div>
        <button class="compact" data-action="logout">Salir</button>
      </div>
    </header>

    <div id="home" class="view active"></div>
    <div id="run" class="view"></div>
    <div id="context" class="view"></div>
    <div id="visual" class="view"></div>
    <div id="settings" class="view"></div>
    <div id="activity" class="view"></div>
  </section>
</main>
<nav id="tabs" class="tabs hidden">
  <button class="tab active" data-tab="home">Inicio</button>
  <button class="tab" data-tab="run">Ejecutar</button>
  <button class="tab" data-tab="context">Contexto</button>
  <button class="tab" data-tab="visual">Visual</button>
  <button class="tab" data-tab="settings">Config</button>
  <button class="tab" data-tab="activity">Actividad</button>
</nav>
<script>
let csrfToken = "";
let appState = null;
const validTabs = new Set(["home", "run", "context", "visual", "settings", "activity"]);
let currentTab = validTabs.has(window.location.hash.replace("#", "")) ? window.location.hash.replace("#", "") : "home";
let refreshInFlight = false;
const loadedViews = { home: true, run: true };
let voiceRecorder = null;
let voiceStream = null;
let voiceChunks = [];
let voiceStopTimer = null;
let liveVoiceRecorder = null;
let liveVoiceStream = null;
let liveVoiceSessionId = "";
let liveVoiceBusy = false;
let localTtsVoices = [];
let browserVoices = [];
let voiceOptionsLoaded = false;
let localSpeechAudio = null;
let kokoroVoiceFilter = "";
let visualSelection = { projectId: "", boardId: "", nodeId: "" };
let codingSelection = { proposalId: "" };
let visualDrag = null;
let visualPan = null;
const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}

function toneClass(text) {
  const value = String(text || "").toLowerCase();
  if (value.includes("error") || value.includes("fall") || value.includes("detenido") || value.includes("falta")) return "bad";
  if (value.includes("pendiente") || value.includes("esperando") || value.includes("revis")) return "warn";
  if (value.includes("activo") || value.includes("listo") || value.includes("usable")) return "good";
  return "";
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.style.display = "block";
  window.clearTimeout(window._toastTimer);
  window._toastTimer = window.setTimeout(() => node.style.display = "none", 4200);
}

function askModal({ title, message = "", input = false, label = "Valor", defaultValue = "", okText = "Continuar" }) {
  return new Promise((resolve) => {
    const backdrop = $("modalBackdrop");
    const inputWrap = $("modalInputWrap");
    const inputNode = $("modalInput");
    $("modalTitle").textContent = title || "Confirmar";
    $("modalMessage").textContent = message || "";
    $("modalInputLabel").textContent = label;
    $("modalOk").textContent = okText;
    inputWrap.classList.toggle("hidden", !input);
    inputNode.value = defaultValue || "";
    backdrop.classList.add("open");
    const cleanup = (value) => {
      backdrop.classList.remove("open");
      $("modalOk").onclick = null;
      $("modalCancel").onclick = null;
      inputNode.onkeydown = null;
      resolve(value);
    };
    $("modalCancel").onclick = () => cleanup(null);
    $("modalOk").onclick = () => cleanup(input ? inputNode.value : true);
    inputNode.onkeydown = (event) => {
      if (event.key === "Enter") cleanup(inputNode.value);
      if (event.key === "Escape") cleanup(null);
    };
    if (input) window.setTimeout(() => inputNode.focus(), 0);
  });
}

function askConfirm(title, message, okText = "Continuar") {
  return askModal({ title, message, okText });
}

function askText(title, message, defaultValue = "", label = "Valor") {
  return askModal({ title, message, input: true, defaultValue, label, okText: "Aceptar" });
}

function base64ToBlob(base64Text, mimeType) {
  const binary = atob(base64Text || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return new Blob([bytes], { type: mimeType || "audio/wav" });
}

function preferredLocalSpeechFormat() {
  const audio = document.createElement("audio");
  if (audio.canPlayType && audio.canPlayType("audio/ogg; codecs=opus")) return "ogg";
  return "wav";
}

async function speakText(text) {
  const clean = String(text || "").trim();
  if (!clean) {
    toast("No hay texto para escuchar");
    return;
  }
  const voiceSettings = (appState && appState.voice) || {};
  if (voiceSettings.tts_provider === "kokoro") {
    try {
      stopSpeech(false);
      toast("Generando voz local...");
      const data = await api("/api/voice/speak", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: { csrf: csrfToken, text: clean, format: preferredLocalSpeechFormat() }
      });
      csrfToken = data.csrf || csrfToken;
      const blob = base64ToBlob(data.audio_b64, data.mime_type);
      localSpeechAudio = new Audio(URL.createObjectURL(blob));
      localSpeechAudio.preload = "auto";
      localSpeechAudio.playsInline = true;
      localSpeechAudio.onended = () => {
        if (localSpeechAudio && localSpeechAudio.src) URL.revokeObjectURL(localSpeechAudio.src);
        localSpeechAudio = null;
      };
      await localSpeechAudio.play();
      toast("Voz local reproduciendo");
      return;
    } catch (error) {
      toast(`${error.message}; usando voz del navegador`);
    }
  }
  if (!("speechSynthesis" in window)) {
    toast("Este navegador no tiene lectura de voz");
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(clean);
  const browserVoiceName = voiceSettings.browser_voice_name || window.localStorage.getItem("yarbis_browser_voice_name") || "";
  const selectedVoice = browserVoices.find(voice => voice.name === browserVoiceName);
  if (selectedVoice) utterance.voice = selectedVoice;
  utterance.lang = (selectedVoice && selectedVoice.lang) || "es-MX";
  utterance.rate = Number(voiceSettings.browser_tts_rate || window.localStorage.getItem("yarbis_browser_tts_rate") || 1);
  utterance.pitch = Number(voiceSettings.browser_tts_pitch || window.localStorage.getItem("yarbis_browser_tts_pitch") || 1);
  window.speechSynthesis.speak(utterance);
}

function stopSpeech(showToast = true) {
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  if (localSpeechAudio) {
    localSpeechAudio.pause();
    localSpeechAudio.currentTime = 0;
    if (localSpeechAudio.src) URL.revokeObjectURL(localSpeechAudio.src);
    localSpeechAudio = null;
  }
  if (showToast) toast("Voz detenida");
}

function refreshBrowserVoices() {
  if (!("speechSynthesis" in window)) {
    browserVoices = [];
    return;
  }
  browserVoices = window.speechSynthesis.getVoices() || [];
}

function liveRecordingBlockReason() {
  if (!window.isSecureContext) return "iPhone exige HTTPS para abrir el microfono aqui. Usa Grabar archivo.";
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return "Este navegador no expone microfono directo. Usa Grabar archivo.";
  if (!window.MediaRecorder) return "Este navegador no soporta grabacion directa. Usa Grabar archivo.";
  return "";
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize);
    binary += String.fromCharCode.apply(null, chunk);
  }
  return btoa(binary);
}

async function transcribeBlob(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const data = await api("/api/voice/transcribe", {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
    body: {
      csrf: csrfToken,
      audio_b64: bytesToBase64(bytes),
      mime_type: blob.type || "audio/webm"
    }
  });
  return data.text || "";
}

function insertTranscript(text) {
  const clean = String(text || "").trim();
  if (!clean) return;
  $("replyText").value = ($("replyText").value ? `${$("replyText").value}\n${clean}` : clean);
}

async function transcribeVoiceBlob(blob) {
  toast("Transcribiendo voz...");
  const text = await transcribeBlob(blob);
  insertTranscript(text);
  toast(text ? "Voz transcrita" : "No detecte texto");
}

function stopVoiceTracks() {
  if (voiceStream) {
    voiceStream.getTracks().forEach(track => track.stop());
    voiceStream = null;
  }
}

async function toggleReplyRecording() {
  if (voiceRecorder && voiceRecorder.state === "recording") {
    voiceRecorder.stop();
    toast("Transcribiendo voz...");
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
    toast(liveRecordingBlockReason());
    const fileInput = $("voiceFileInput");
    if (fileInput) fileInput.click();
    return;
  }
  const blockedReason = liveRecordingBlockReason();
  if (blockedReason) {
    toast(blockedReason);
    const fileInput = $("voiceFileInput");
    if (fileInput) fileInput.click();
    return;
  }
  try {
    voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    voiceChunks = [];
    voiceRecorder = new MediaRecorder(voiceStream);
    voiceRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) voiceChunks.push(event.data);
    };
    voiceRecorder.onstop = async () => {
      window.clearTimeout(voiceStopTimer);
      stopVoiceTracks();
      try {
        const blob = new Blob(voiceChunks, { type: voiceRecorder.mimeType || "audio/webm" });
        await transcribeVoiceBlob(blob);
      } catch (error) {
        toast(error.message);
      } finally {
        voiceRecorder = null;
        voiceChunks = [];
        renderCurrent();
      }
    };
    voiceRecorder.start();
    voiceStopTimer = window.setTimeout(() => {
      if (voiceRecorder && voiceRecorder.state === "recording") voiceRecorder.stop();
    }, 120000);
    toast("Grabando voz...");
    renderCurrent();
  } catch (error) {
    stopVoiceTracks();
    toast(error.message);
  }
}

function liveVoiceSessionText() {
  const sessionState = window._lastLiveVoiceSession || {};
  if (liveVoiceSessionId) {
    return sessionState.detail || "Conversación en vivo activa.";
  }
  const voice = ((appState && appState.conversation) || {}).voice || {};
  return voice.headline || "Di Yarbis cuando la conversación en vivo esté activa.";
}

async function startLiveVoice() {
  const blockedReason = liveRecordingBlockReason();
  if (blockedReason) {
    toast(blockedReason);
    return;
  }
  if (liveVoiceSessionId) return;
  try {
    liveVoiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const data = await api("/api/voice/live/start", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: { csrf: csrfToken }
    });
    csrfToken = data.csrf || csrfToken;
    window._lastLiveVoiceSession = data.voice_session || {};
    liveVoiceSessionId = (data.voice_session || {}).id || "";
    liveVoiceRecorder = new MediaRecorder(liveVoiceStream);
    liveVoiceRecorder.ondataavailable = async (event) => {
      if (!event.data || event.data.size <= 0 || liveVoiceBusy || !liveVoiceSessionId) return;
      liveVoiceBusy = true;
      try {
        const bytes = new Uint8Array(await event.data.arrayBuffer());
        const chunk = await api("/api/voice/live/chunk", {
          method: "POST",
          headers: { "X-CSRF-Token": csrfToken },
          body: {
            csrf: csrfToken,
            session_id: liveVoiceSessionId,
            audio_b64: bytesToBase64(bytes),
            mime_type: event.data.type || liveVoiceRecorder.mimeType || "audio/webm"
          }
        });
        csrfToken = chunk.csrf || csrfToken;
        window._lastLiveVoiceSession = chunk.voice_session || {};
        const spoken = (chunk.voice_session || {}).spoken_text || "";
        if (spoken) await speakText(spoken);
        renderCurrent();
      } catch (error) {
        toast(error.message);
      } finally {
        liveVoiceBusy = false;
      }
    };
    liveVoiceRecorder.start(3000);
    toast("Conversación en vivo activa");
    renderCurrent();
  } catch (error) {
    stopLiveVoiceTracks();
    liveVoiceSessionId = "";
    toast(error.message);
  }
}

function stopLiveVoiceTracks() {
  if (liveVoiceRecorder && liveVoiceRecorder.state === "recording") {
    try { liveVoiceRecorder.stop(); } catch (_error) {}
  }
  liveVoiceRecorder = null;
  if (liveVoiceStream) {
    liveVoiceStream.getTracks().forEach(track => track.stop());
    liveVoiceStream = null;
  }
}

async function stopLiveVoice() {
  const sessionId = liveVoiceSessionId;
  stopLiveVoiceTracks();
  liveVoiceSessionId = "";
  if (sessionId) {
    try {
      const data = await api("/api/voice/live/stop", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: { csrf: csrfToken, session_id: sessionId }
      });
      csrfToken = data.csrf || csrfToken;
      window._lastLiveVoiceSession = data.voice_session || {};
    } catch (error) {
      toast(error.message);
    }
  }
  toast("Conversación en vivo detenida");
  renderCurrent();
}

async function toggleLiveVoice() {
  if (liveVoiceSessionId) await stopLiveVoice();
  else await startLiveVoice();
}

async function loadVoiceOptions(force = false, includeCatalog = false, refreshCatalog = false) {
  if (voiceOptionsLoaded && !force) return;
  try {
    const query = includeCatalog ? `?catalog=1${refreshCatalog ? "&refresh=1" : ""}` : "";
    const data = await api(`/api/voice/voices${query}`);
    csrfToken = data.csrf || csrfToken;
    localTtsVoices = data.voices || [];
    if (!appState) appState = {};
    appState.voice = { ...(appState.voice || {}), ...(data.settings || {}) };
    voiceOptionsLoaded = true;
    if (currentTab === "settings") renderCurrent();
  } catch (error) {
    toast(error.message);
  }
}

function selectedKokoroVoiceId() {
  const selected = $("kokoroVoiceId") ? $("kokoroVoiceId").value : "";
  if (selected) return selected;
  const candidates = localTtsVoices.filter(item => item.provider === "kokoro");
  const filtered = candidates.filter(item => {
    const needle = kokoroVoiceFilter.trim().toLowerCase();
    if (!needle) return true;
    return `${item.id || ""} ${item.name || ""} ${(item.languages || []).join(" ")}`.toLowerCase().includes(needle);
  });
  const first = (filtered[0] || candidates[0] || {});
  return first.id || "";
}

async function saveVoiceSettings(providerOverride = null, kokoroVoiceOverride = null) {
  const provider = providerOverride || $("voiceProvider").value;
  const kokoroVoiceId = kokoroVoiceOverride || $("kokoroVoiceId").value;
  window.localStorage.setItem("yarbis_browser_voice_name", $("browserVoiceName").value);
  window.localStorage.setItem("yarbis_browser_tts_rate", $("browserTtsRate").value);
  window.localStorage.setItem("yarbis_browser_tts_pitch", $("browserTtsPitch").value);
  const data = await action("voice_settings", {
    enabled: $("voiceEnabled").checked,
    tts_provider: provider,
    tts_voice_id: $("ttsVoiceId").value,
    tts_rate: $("ttsRate").value,
    kokoro_voice_id: kokoroVoiceId,
    browser_voice_name: $("browserVoiceName").value,
    browser_tts_rate: $("browserTtsRate").value,
    browser_tts_pitch: $("browserTtsPitch").value,
    telegram_reply_mode: $("telegramVoiceMode").value,
    live_enabled: $("liveVoiceEnabled") ? $("liveVoiceEnabled").checked : true,
    live_wake_phrase: $("liveWakePhrase") ? $("liveWakePhrase").value : "Yarbis",
    live_silence_ms: $("liveSilenceMs") ? $("liveSilenceMs").value : 900,
    live_max_turn_seconds: $("liveMaxTurnSeconds") ? $("liveMaxTurnSeconds").value : 45,
    live_auto_speak: $("liveAutoSpeak") ? $("liveAutoSpeak").checked : true,
    live_barge_in: $("liveBargeIn") ? $("liveBargeIn").checked : true
  });
  voiceOptionsLoaded = false;
  return data;
}

async function api(path, options = {}) {
  const init = { credentials: "same-origin", ...options };
  if (init.body && typeof init.body !== "string") {
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok || data.ok === false) {
    const error = data.error || `${response.status} ${response.statusText}`;
    throw new Error(error);
  }
  return data;
}

async function action(name, payload = {}) {
  const data = await api("/api/action", {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
    body: { csrf: csrfToken, action: name, payload }
  });
  if (data.job) {
    toast(`${data.job.label} iniciado`);
    if (!appState) appState = {};
    appState.jobs = [data.job, ...((appState.jobs || []).filter(job => job.id !== data.job.id))].slice(0, 10);
    renderCurrent();
    pollJob(data.job.id);
  } else {
    toast(data.result || "Listo");
    await loadView(currentTab, true);
  }
  return data;
}

async function pollJob(id) {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const data = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (!data.job || data.job.status === "running") continue;
    if (!appState) appState = {};
    appState.jobs = [data.job, ...((appState.jobs || []).filter(job => job.id !== data.job.id))].slice(0, 10);
    toast(data.job.result || data.job.error || "Operacion terminada");
    renderCurrent();
    await refresh({ silent: true });
    break;
  }
}

function mergeState(nextState) {
  const previous = appState || {};
  appState = {
    ...previous,
    ...nextState,
    service: {
      ...(previous.service || {}),
      ...(nextState.service || {}),
      status: {
        ...((previous.service || {}).status || {}),
        ...((nextState.service || {}).status || {})
      },
      mobile_ui: {
        ...((previous.service || {}).mobile_ui || {}),
        ...((nextState.service || {}).mobile_ui || {})
      },
      proactive: {
        ...((previous.service || {}).proactive || {}),
        ...((nextState.service || {}).proactive || {})
      }
    }
  };
}

function statePath(view = "") {
  return view ? `/api/state?view=${encodeURIComponent(view)}` : "/api/state";
}

async function refresh(options = {}) {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const data = await api(statePath(""));
    csrfToken = data.csrf || csrfToken;
    mergeState(data.state || {});
    $("loginView").classList.add("hidden");
    $("appView").classList.remove("hidden");
    $("tabs").classList.remove("hidden");
    document.querySelectorAll(".view").forEach(node => node.classList.toggle("active", node.id === currentTab));
    document.querySelectorAll(".tab").forEach(node => node.classList.toggle("active", node.dataset.tab === currentTab));
    renderCurrent();
  } catch (error) {
    if (!options.silent) {
      $("appView").classList.add("hidden");
      $("tabs").classList.add("hidden");
      $("loginView").classList.remove("hidden");
      $("loginMessage").textContent = error.message.includes("503") ? error.message : "";
    }
  } finally {
    refreshInFlight = false;
  }
}

async function loadView(name, force = false) {
  if (name === "home" || name === "run") {
    if (force || !appState) await refresh();
    renderCurrent();
    return;
  }
  if (!force && loadedViews[name]) {
    renderCurrent();
    return;
  }
  try {
    const data = await api(statePath(name));
    csrfToken = data.csrf || csrfToken;
    mergeState(data.state || {});
    loadedViews[name] = true;
    renderCurrent();
  } catch (error) {
    toast(error.message);
  }
}

function renderCurrent() {
  if (!appState) return;
  const mobile = (appState.service || {}).mobile_ui || {};
  $("urlLine").textContent = mobile.tailscale_url || mobile.local_url || "";
  if (currentTab === "home") renderHome();
  else if (currentTab === "run") renderRun();
  else if (currentTab === "context") renderContext();
  else if (currentTab === "visual") renderVisual();
  else if (currentTab === "settings") renderSettings();
  else if (currentTab === "activity") renderActivity();
}

function renderHome() {
  const pending = appState.awaiting_user_input || {};
  const service = appState.service || {};
  const status = service.status || {};
  const runningText = status.running ? "Servicio activo" : "Servicio detenido";
  const thinking = status.running ? appState.health_text : appState.readiness_text;
  const nextStep = pending.pending
    ? "Responde la pregunta pendiente para retomar los ciclos."
    : (status.running ? "Puedes ejecutar un ciclo o dejar que el pulso continúe." : "Inicia el servicio o ejecuta desde esta sesión.");
  $("home").innerHTML = `
    <section class="hero">
      <div class="toolbar">
        <span class="pill ${toneClass(runningText)}">${escapeHtml(runningText)}</span>
        <span class="pill">${escapeHtml(appState.cycle_count)} ciclo(s)</span>
      </div>
      <h2>${escapeHtml(nextStep)}</h2>
    </section>
    <section class="section">
      <div class="grid two">
        <div class="panel metric"><span class="muted">Ciclos</span><strong>${escapeHtml(appState.cycle_count)}</strong></div>
        <div class="panel metric"><span class="muted">Servicio</span><strong>${status.running ? "Activo" : "Detenido"}</strong></div>
      </div>
    </section>
    <section class="section">
      <h2>Objetivo</h2>
      <div class="panel">${escapeHtml(appState.goal || "Sin objetivo definido.")}</div>
    </section>
    ${pending.pending ? `<section class="section"><h2>Pendiente</h2><div class="panel">${escapeHtml(pending.question)}</div></section>` : ""}
    <section class="section">
      <h2>Estado operativo</h2>
      <div class="panel"><pre>${escapeHtml(thinking)}</pre></div>
    </section>
    <section class="section">
      <h2>Último resultado</h2>
      <div class="actions tight">
        <button data-speak="${escapeHtml(appState.last_result || "")}">Escuchar</button>
        <button data-action="stop-speaking">Detener habla</button>
        <button data-action="refresh">Refrescar</button>
      </div>
      <div class="panel"><pre>${escapeHtml(appState.last_result || "Sin resultado reciente.")}</pre></div>
    </section>`;
}

function renderRun() {
  const conversation = appState.conversation || {};
  const voice = conversation.voice || {};
  const pending = conversation.pending || {};
  const liveActive = Boolean(liveVoiceSessionId);
  const liveLabel = liveActive ? "Detener conversacion" : "Conversacion en vivo";
  const liveDetail = liveVoiceSessionText();
  const timeline = conversation.timeline || [];
  $("run").innerHTML = `
    <section class="hero">
      <h2>Ejecuta, responde o dicta sin salir del teléfono.</h2>
      <div class="muted">Las operaciones largas quedan como trabajos y se actualizan automáticamente.</div>
    </section>
    <section class="section">
      <h2>Conversacion</h2>
      <div class="panel">
        <div class="muted">${escapeHtml(pending.active ? "Pregunta pendiente" : "Hilo reciente")}</div>
        <div class="list">${timeline.map(item => `
          <div class="item">
            <strong>${item.role === "user" ? "Tu" : "Yarbis"}</strong>
            <pre>${escapeHtml(item.text || "")}</pre>
          </div>`).join("") || `<div class="muted">Sin conversacion reciente.</div>`}
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Voz en vivo</h2>
      <div class="panel">
        <div>${escapeHtml(liveDetail)}</div>
        <div class="muted">Activacion: ${escapeHtml(voice.wake_phrase || "Yarbis")}</div>
        <div class="actions tight">
          <button class="${liveActive ? "danger" : "primary"}" data-action="toggle-live-voice">${liveLabel}</button>
          <button data-action="stop-speaking">Detener habla</button>
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Ejecutar</h2>
      <div class="actions tight">
        <button class="primary" data-action="run-cycle">Ejecutar ciclo</button>
        <button class="secondary" data-action="run-auto">Modo autónomo</button>
        <button class="danger" data-action="stop-operation">Detener pensando</button>
        <button data-action="stop-speaking">Detener habla</button>
        <button data-action="refresh">Refrescar</button>
      </div>
    </section>
    <section class="section">
      <h2>Respuesta o contexto</h2>
      <div class="form-grid">
        <textarea id="replyText" placeholder="Escribe respuesta, instrucción o contexto libre"></textarea>
        <input id="voiceFileInput" class="hidden" type="file" accept="audio/*" capture>
        <div class="actions tight">
          <button data-action="record-reply">${voiceRecorder && voiceRecorder.state === "recording" ? "Detener voz" : "Grabar voz"}</button>
          <button data-action="voice-file">Grabar archivo</button>
          <button class="primary" data-action="send-reply">Enviar y ejecutar</button>
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Trabajos</h2>
      <div class="list">${appState.jobs.map(job => `
        <div class="item">
          <strong>${escapeHtml(job.label)}</strong>
          <div class="muted">${escapeHtml(job.status)} ${escapeHtml(job.started_at || "")}</div>
          <button data-speak="${escapeHtml(job.result || job.error || "")}">Escuchar</button>
          <pre>${escapeHtml(job.result || job.error || "")}</pre>
        </div>`).join("") || `<div class="muted">Sin trabajos recientes.</div>`}
      </div>
    </section>`;
}

function renderContext() {
  const profile = appState.profile || {};
  const ideaProjects = appState.idea_projects || [];
  const coding = appState.coding || {};
  const codingProposalLines = String(coding.proposals_text || "")
    .split(/\n+/)
    .map(line => line.trim())
    .filter(line => line.startsWith("[") && line.includes("]"));
  const codingProposals = codingProposalLines.map(line => ({
    id: line.split("]", 1)[0].replace("[", "").trim(),
    label: line
  })).filter(item => item.id);
  if (codingSelection.proposalId && !codingProposals.some(item => item.id === codingSelection.proposalId)) {
    codingSelection.proposalId = "";
  }
  if (!codingSelection.proposalId && codingProposals.length) {
    codingSelection.proposalId = codingProposals[0].id;
  }
  const selectedCodingProposalId = codingSelection.proposalId || (codingProposals[0] || {}).id || "";
  $("context").innerHTML = `
    <section class="hero">
      <h2>Contexto que Yarbis usa para trabajar mejor.</h2>
      <div class="muted">Objetivo, perfil, notas, tareas y propuestas de código.</div>
    </section>
    <section class="section">
      <h2>Objetivo</h2>
      <div class="form-grid">
        <textarea id="goalInput">${escapeHtml(appState.goal || "")}</textarea>
        <button data-action="save-goal">Guardar objetivo</button>
      </div>
    </section>
    <section class="section">
      <h2>Perfil</h2>
      <div class="form-grid wide">
        <div><label>Nombre</label><input id="profileName" value="${escapeHtml(profile.name || "")}"></div>
        <div><label>Rol</label><input id="profileRole" value="${escapeHtml(profile.role || "")}"></div>
        <div><label>Preferencias</label><textarea id="profilePrefs">${escapeHtml((profile.preferences || []).join("\n"))}</textarea></div>
        <div><label>Restricciones</label><textarea id="profileConstraints">${escapeHtml((profile.constraints || []).join("\n"))}</textarea></div>
        <button data-action="save-profile">Guardar perfil</button>
      </div>
    </section>
    <section class="section">
      <h2>Ideas/proyectos</h2>
      <div class="form-grid wide">
        <input id="ideaTitle" placeholder="Titulo de la idea">
        <select id="ideaKind"><option value="mixto">mixto</option><option value="producto_negocio">producto_negocio</option><option value="vida_proyecto">vida_proyecto</option><option value="otro">otro</option></select>
        <textarea id="ideaSummary" placeholder="Resumen o brief"></textarea>
        <textarea id="ideaDirections" placeholder="Direcciones creativas, una por linea"></textarea>
        <textarea id="ideaQuestions" placeholder="Preguntas abiertas"></textarea>
        <textarea id="ideaSteps" placeholder="Proximos pasos"></textarea>
        <button data-action="create-idea">Crear proyecto</button>
      </div>
      <div class="form-grid">
        <input id="ideaProjectId" placeholder="Id para editar o activar">
        <select id="ideaStatus"><option value="">estado sin cambio</option><option value="exploring">exploring</option><option value="planned">planned</option><option value="active">active</option><option value="paused">paused</option><option value="done">done</option><option value="archived">archived</option></select>
        <textarea id="ideaSelected" placeholder="Direccion elegida"></textarea>
        <textarea id="ideaNextStepsUpdate" placeholder="Reemplazar proximos pasos"></textarea>
        <button data-action="update-idea">Actualizar</button>
        <button data-action="promote-idea">Activar</button>
      </div>
      <div class="list">${ideaProjects.map(project => `
        <div class="item">
          <strong>${escapeHtml(project.title)}</strong>
          <div class="muted">${escapeHtml(project.status)} - ${escapeHtml(project.kind)} - ${escapeHtml(project.id)}</div>
          <pre>${escapeHtml(project.summary || project.selected_direction || "")}</pre>
          ${(project.creative_directions || []).length ? `<pre>${escapeHtml((project.creative_directions || []).slice(0, 3).join("\n"))}</pre>` : ""}
          <div class="actions tight">
            <button data-action="select-idea" data-project-id="${escapeHtml(project.id)}">Editar</button>
            <button data-action="promote-idea-card" data-project-id="${escapeHtml(project.id)}">Activar</button>
          </div>
        </div>`).join("") || `<div class="muted">Sin proyectos de ideas.</div>`}
      </div>
    </section>
    <section class="section">
      <h2>Notas</h2>
      <div class="form-grid">
        <input id="noteTitle" placeholder="Título">
        <input id="noteCategory" placeholder="Categoría" value="general">
        <textarea id="noteContent" placeholder="Contenido"></textarea>
        <button data-action="save-note">Guardar nota</button>
      </div>
      <div class="list">${(appState.notes || []).map(note => `
        <div class="item">
          <strong>${escapeHtml(note.title)}</strong>
          <div class="muted">${escapeHtml(note.category)} · ${escapeHtml(note.id)}</div>
          <pre>${escapeHtml(note.content || "")}</pre>
        </div>`).join("") || `<div class="muted">Sin notas.</div>`}
      </div>
    </section>
    <section class="section">
      <h2>Tareas</h2>
      <div class="form-grid">
        <input id="taskTitle" placeholder="Título">
        <textarea id="taskDetails" placeholder="Detalles"></textarea>
        <select id="taskPriority"><option>media</option><option>alta</option><option>baja</option></select>
        <button data-action="add-task">Crear tarea</button>
      </div>
      <div class="list">${(appState.tasks || []).map(task => `
        <div class="item">
          <strong>${escapeHtml(task.title)}</strong>
          <div class="muted">${escapeHtml(task.status)} · ${escapeHtml(task.priority)} · ${escapeHtml(task.id)}</div>
          <pre>${escapeHtml(task.details || task.result || "")}</pre>
        </div>`).join("") || `<div class="muted">Sin tareas.</div>`}
      </div>
    </section>
    <section class="section">
      <h2>Coding</h2>
      <div class="form-grid">
        <input id="codingPath" value="${escapeHtml(coding.workspace_path || "")}" placeholder="Ruta del repositorio">
        <button data-action="save-coding-workspace">Guardar workspace</button>
      </div>
      <div class="form-grid">
        <input id="codingValidation" value="${escapeHtml(coding.validation_command || "")}" placeholder="Comando de validación">
        <button data-action="save-coding-validation">Guardar validación</button>
        <button data-action="coding-detect-validation">Detectar validación</button>
      </div>
      <div class="actions tight">
        <button data-action="coding-status">Estado</button>
        <button data-action="coding-validation-plan">Plan validaciÃ³n</button>
        <button data-action="refresh">Refrescar</button>
      </div>
      <div class="form-grid">
        <input id="codingSearchPattern" placeholder="Buscar texto o simbolo">
        <input id="codingSearchPath" value="." placeholder="Ruta">
        <input id="codingSearchGlob" placeholder="Glob opcional, ej. *.py">
        <button data-action="coding-search">Buscar</button>
      </div>
      <div class="form-grid">
        <input id="codingRangePath" placeholder="Archivo para leer rango">
        <input id="codingRangeStart" type="number" value="1" placeholder="Linea">
        <input id="codingRangeCount" type="number" value="120" placeholder="Cantidad">
        <button data-action="coding-read-range">Leer rango</button>
      </div>
      <div class="form-grid">
        <textarea id="codingEditsJson" placeholder='[{"type":"exact_replace","path":"app.py","old_text":"old","new_text":"new"}]'></textarea>
        <input id="codingEditsTitle" value="Edicion localizada" placeholder="Titulo">
        <button data-action="coding-propose-edits">Proponer ediciones</button>
      </div>
      <div class="list">${codingProposals.map(item => `
        <div class="item">
          <strong>${escapeHtml(item.id)}</strong>
          <div class="muted">${escapeHtml(item.label)}</div>
          <div class="actions tight">
            <button data-action="select-coding-proposal" data-proposal-id="${escapeHtml(item.id)}">Seleccionar</button>
            <button data-action="coding-card-check" data-proposal-id="${escapeHtml(item.id)}">Revisar</button>
            <button data-action="coding-card-get" data-proposal-id="${escapeHtml(item.id)}">Ver</button>
          </div>
        </div>`).join("") || `<div class="muted">Sin propuestas pendientes.</div>`}
      </div>
      <div class="form-grid">
        <input id="codingProposalId" value="${escapeHtml(selectedCodingProposalId)}" placeholder="Id de propuesta seleccionada" readonly>
        <button data-action="coding-check">Revisar</button>
        <button data-action="coding-get">Ver</button>
        <button data-action="coding-validate">Validar</button>
        <button data-action="coding-apply">Aplicar</button>
        <button data-action="coding-apply-validate">Aplicar+validar</button>
        <button data-action="coding-discard">Descartar</button>
      </div>
      <div class="panel"><pre>${escapeHtml(coding.proposals_text || "")}</pre></div>
    </section>`;
}

function fillIdeaProjectForm(projectId) {
  const project = (appState.idea_projects || []).find(item => item.id === projectId);
  if (!project) return;
  if ($("ideaProjectId")) $("ideaProjectId").value = project.id || "";
  if ($("ideaStatus")) $("ideaStatus").value = project.status || "";
  if ($("ideaSelected")) $("ideaSelected").value = project.selected_direction || "";
  if ($("ideaNextStepsUpdate")) $("ideaNextStepsUpdate").value = (project.next_steps || []).join("\n");
  if ($("ideaTitle")) $("ideaTitle").value = project.title || "";
  if ($("ideaKind")) $("ideaKind").value = project.kind || "mixto";
  if ($("ideaSummary")) $("ideaSummary").value = project.summary || "";
  if ($("ideaDirections")) $("ideaDirections").value = (project.creative_directions || []).join("\n");
  if ($("ideaQuestions")) $("ideaQuestions").value = (project.open_questions || []).join("\n");
  if ($("ideaSteps")) $("ideaSteps").value = (project.next_steps || []).join("\n");
}

function selectedCodingProposalId() {
  const node = $("codingProposalId");
  return (node ? node.value : codingSelection.proposalId || "").trim();
}

function visualProjects() {
  return appState ? (appState.idea_projects || []) : [];
}

function selectedVisualProject() {
  const projects = visualProjects();
  if (!projects.length) return null;
  if (!visualSelection.projectId || !projects.some(project => project.id === visualSelection.projectId)) {
    const withBoards = projects.find(project => (project.visual_boards || []).length);
    visualSelection.projectId = (withBoards || projects[0]).id || "";
    visualSelection.boardId = "";
    visualSelection.nodeId = "";
  }
  return projects.find(project => project.id === visualSelection.projectId) || projects[0] || null;
}

function selectedVisualBoard(project = selectedVisualProject()) {
  if (!project) return null;
  const boards = project.visual_boards || [];
  if (!boards.length) {
    visualSelection.boardId = "";
    visualSelection.nodeId = "";
    return null;
  }
  if (!visualSelection.boardId || !boards.some(board => board.id === visualSelection.boardId)) {
    visualSelection.boardId = boards[0].id || "";
    visualSelection.nodeId = "";
  }
  return boards.find(board => board.id === visualSelection.boardId) || boards[0] || null;
}

function selectedVisualNode(board = selectedVisualBoard()) {
  if (!board || !visualSelection.nodeId) return null;
  return (board.nodes || []).find(node => node.id === visualSelection.nodeId) || null;
}

function visualProjectOptions(projects, selectedId) {
  return projects.map(project => (
    `<option value="${escapeHtml(project.id || "")}" ${project.id === selectedId ? "selected" : ""}>${escapeHtml(project.title || project.id || "Proyecto")}</option>`
  )).join("");
}

function visualBoardOptions(boards, selectedId) {
  return boards.map(board => (
    `<option value="${escapeHtml(board.id || "")}" ${board.id === selectedId ? "selected" : ""}>${escapeHtml(board.title || board.id || "Board")}</option>`
  )).join("");
}

function visualTextLines(value, maxChars = 30, maxLines = 6) {
  const words = String(value || "").replace(/\r/g, "").split(/\s+/).filter(Boolean);
  const lines = [];
  let current = "";
  for (const word of words) {
    const candidate = `${current} ${word}`.trim();
    if (current && candidate.length > maxChars) {
      lines.push(current);
      current = word;
    } else {
      current = candidate;
    }
    if (lines.length >= maxLines) break;
  }
  if (current && lines.length < maxLines) lines.push(current);
  return lines.length ? lines.slice(0, maxLines) : [""];
}

function visualBounds(board) {
  const items = [...(board.lanes || []), ...(board.nodes || [])];
  if (!items.length) return { minX: 0, minY: 0, maxX: 1000, maxY: 700 };
  const minX = Math.min(...items.map(item => Number(item.x || 0)));
  const minY = Math.min(...items.map(item => Number(item.y || 0)));
  const maxX = Math.max(...items.map(item => Number(item.x || 0) + Number(item.width || 220)));
  const maxY = Math.max(...items.map(item => Number(item.y || 0) + Number(item.height || 120)));
  return { minX, minY, maxX, maxY };
}

function visualFitBoard(board) {
  if (!board) return;
  const bounds = visualBounds(board);
  const width = Math.max(120, bounds.maxX - bounds.minX);
  const height = Math.max(120, bounds.maxY - bounds.minY);
  const zoom = Math.max(0.2, Math.min(1.8, Math.min(1220 / (width + 160), 760 / (height + 160))));
  board.viewport = {
    x: (1400 - (bounds.minX + bounds.maxX) * zoom) / 2,
    y: (900 - (bounds.minY + bounds.maxY) * zoom) / 2,
    zoom
  };
}

function visualAutoLayout(board) {
  if (!board) return;
  if ((board.lanes || []).length) {
    const laneCounts = {};
    for (const node of board.nodes || []) {
      const lane = (board.lanes || []).find(item => item.id === node.lane) || (board.lanes || [])[0];
      if (!lane) continue;
      laneCounts[lane.id] = laneCounts[lane.id] || 0;
      node.x = Number(lane.x || 0) + 16;
      node.y = Number(lane.y || 0) + 54 + laneCounts[lane.id] * 122;
      node.width = Math.max(120, Number(lane.width || 220) - 32);
      node.height = Number(node.height || 104);
      laneCounts[lane.id] += 1;
    }
  } else if ((board.kind || "") === "mind_map" && (board.nodes || []).length > 1) {
    const center = (board.nodes || []).find(node => node.id === "center") || board.nodes[0];
    center.x = 540;
    center.y = 360;
    const others = (board.nodes || []).filter(node => node !== center);
    const radiusX = 430;
    const radiusY = 260;
    others.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(1, others.length);
      node.x = Math.round(620 + Math.cos(angle) * radiusX - Number(node.width || 220) / 2);
      node.y = Math.round(430 + Math.sin(angle) * radiusY - Number(node.height || 120) / 2);
    });
  } else {
    (board.nodes || []).forEach((node, index) => {
      node.x = 50 + (index % 3) * 280;
      node.y = 60 + Math.floor(index / 3) * 180;
    });
  }
  visualFitBoard(board);
}

function visualSvgText(lines, x, y, cssClass, lineHeight = 17) {
  return lines.map((line, index) => (
    `<text class="${cssClass}" x="${x}" y="${y + index * lineHeight}">${escapeHtml(line)}</text>`
  )).join("");
}

function visualRenderCanvas(board) {
  const svg = $("visualCanvas");
  if (!svg || !board) return;
  const viewport = board.viewport || { x: 0, y: 0, zoom: 1 };
  const zoom = Number(viewport.zoom || 1);
  const tx = Number(viewport.x || 0);
  const ty = Number(viewport.y || 0);
  const nodesById = {};
  (board.nodes || []).forEach(node => nodesById[node.id] = node);
  const lanes = (board.lanes || []).map(lane => `
    <g class="visual-lane">
      <rect x="${Number(lane.x || 0)}" y="${Number(lane.y || 0)}" width="${Number(lane.width || 260)}" height="${Number(lane.height || 420)}" rx="8"></rect>
      <text x="${Number(lane.x || 0) + 14}" y="${Number(lane.y || 0) + 28}">${escapeHtml(lane.title || "Lane")}</text>
    </g>`).join("");
  const edges = (board.edges || []).map(edge => {
    const source = nodesById[edge.source];
    const target = nodesById[edge.target];
    if (!source || !target) return "";
    const x1 = Number(source.x || 0) + Number(source.width || 220) / 2;
    const y1 = Number(source.y || 0) + Number(source.height || 120) / 2;
    const x2 = Number(target.x || 0) + Number(target.width || 220) / 2;
    const y2 = Number(target.y || 0) + Number(target.height || 120) / 2;
    return `<line class="visual-edge" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" marker-end="url(#visualArrow)"></line>`;
  }).join("");
  const nodes = (board.nodes || []).map(node => {
    const width = Number(node.width || 220);
    const titleLines = visualTextLines(node.title || "Nodo", Math.max(12, Math.floor(width / 9)), 1);
    const bodyLines = visualTextLines(node.text || "", Math.max(16, Math.floor(width / 8)), 6);
    return `
      <g class="visual-node ${visualSelection.nodeId === node.id ? "selected" : ""}" data-visual-node-id="${escapeHtml(node.id || "")}">
        <rect x="${Number(node.x || 0)}" y="${Number(node.y || 0)}" width="${width}" height="${Number(node.height || 120)}" rx="8"></rect>
        ${visualSvgText(titleLines, Number(node.x || 0) + 14, Number(node.y || 0) + 27, "title")}
        ${visualSvgText(bodyLines, Number(node.x || 0) + 14, Number(node.y || 0) + 54, "body")}
      </g>`;
  }).join("");
  svg.innerHTML = `
    <defs>
      <marker id="visualArrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#6d7a8d"></path>
      </marker>
    </defs>
    <rect x="0" y="0" width="1400" height="900" fill="#0b0e13"></rect>
    <g transform="translate(${tx} ${ty}) scale(${zoom})">${lanes}${edges}${nodes}</g>`;
}

function renderVisualInspector(board) {
  const node = selectedVisualNode(board);
  if (!board) return `<div class="panel muted">Crea o selecciona un board visual.</div>`;
  return `
    <div class="panel visual-inspector">
      <h2>Inspector</h2>
      <div><label>Titulo del board</label><input id="visualBoardTitle" value="${escapeHtml(board.title || "")}"></div>
      <div><label>Nodo</label><input id="visualNodeTitle" value="${escapeHtml(node ? node.title || "" : "")}" ${node ? "" : "disabled"}></div>
      <div><label>Texto</label><textarea id="visualNodeText" ${node ? "" : "disabled"}>${escapeHtml(node ? node.text || "" : "")}</textarea></div>
      <div class="actions tight">
        <button data-action="visual-update-node" ${node ? "" : "disabled"}>Aplicar nodo</button>
        <button data-action="visual-delete-node" class="danger" ${node ? "" : "disabled"}>Eliminar nodo</button>
        <button data-action="visual-add-node">Agregar nodo</button>
      </div>
      <pre>${escapeHtml(node ? `x:${Math.round(Number(node.x || 0))} y:${Math.round(Number(node.y || 0))}` : "Sin nodo seleccionado")}</pre>
    </div>`;
}

function renderVisual() {
  const projects = visualProjects();
  const project = selectedVisualProject();
  const board = selectedVisualBoard(project);
  if (!projects.length) {
    $("visual").innerHTML = `
      <section class="hero"><h2>Mesa visual</h2><div class="muted">Crea un proyecto de idea en Contexto para empezar.</div></section>`;
    return;
  }
  const boards = project ? (project.visual_boards || []) : [];
  $("visual").innerHTML = `
    <section class="hero">
      <h2>Mesa visual de ideas y proyectos.</h2>
      <div class="muted">Canvas, matrices, roadmap y mapas mentales ligados a proyectos guardados.</div>
    </section>
    <section class="section">
      <div class="visual-toolbar">
        <select id="visualProjectSelect">${visualProjectOptions(projects, project ? project.id : "")}</select>
        <select id="visualBoardSelect">${boards.length ? visualBoardOptions(boards, board ? board.id : "") : `<option value="">sin boards</option>`}</select>
        <select id="visualKind">
          <option value="idea_canvas">idea_canvas</option>
          <option value="decision_matrix">decision_matrix</option>
          <option value="roadmap_kanban">roadmap_kanban</option>
          <option value="mind_map">mind_map</option>
        </select>
        <input id="visualNewTitle" placeholder="Titulo opcional">
        <button data-action="visual-create-board">Crear</button>
        <button data-action="visual-regenerate" ${project ? "" : "disabled"}>Regenerar</button>
        <button data-action="visual-save-board" ${board ? "" : "disabled"}>Guardar</button>
        <button data-action="visual-export-board" ${board ? "" : "disabled"}>Exportar</button>
        <button data-action="visual-fit" ${board ? "" : "disabled"}>Ajustar</button>
        <button data-action="visual-autolayout" ${board ? "" : "disabled"}>Auto-layout</button>
      </div>
      <div class="visual-layout">
        <div class="visual-board-shell">
          <svg id="visualCanvas" class="visual-canvas" viewBox="0 0 1400 900" aria-label="Canvas visual"></svg>
        </div>
        ${renderVisualInspector(board)}
      </div>
    </section>`;
  const kind = $("visualKind");
  if (kind && board) kind.value = board.kind || "idea_canvas";
  visualRenderCanvas(board);
}

function visualCanvasPoint(event, board) {
  const svg = $("visualCanvas");
  const rect = svg.getBoundingClientRect();
  const svgX = ((event.clientX - rect.left) / Math.max(1, rect.width)) * 1400;
  const svgY = ((event.clientY - rect.top) / Math.max(1, rect.height)) * 900;
  const viewport = board.viewport || { x: 0, y: 0, zoom: 1 };
  const zoom = Number(viewport.zoom || 1);
  return {
    x: (svgX - Number(viewport.x || 0)) / zoom,
    y: (svgY - Number(viewport.y || 0)) / zoom,
    svgX,
    svgY
  };
}

function applyVisualInspectorEdits() {
  const board = selectedVisualBoard();
  if (!board) return;
  if ($("visualBoardTitle")) board.title = $("visualBoardTitle").value;
  const node = selectedVisualNode(board);
  if (node) {
    if ($("visualNodeTitle")) node.title = $("visualNodeTitle").value;
    if ($("visualNodeText")) node.text = $("visualNodeText").value;
  }
}

function renderSettings() {
  if (!appState.model_provider) {
    $("settings").innerHTML = `<section class="section"><div class="panel">Cargando configuración...</div></section>`;
    return;
  }
  const mp = appState.model_provider;
  const provider = mp.default || "ollama";
  const active = mp[provider] || mp.ollama;
  const proactive = appState.service.proactive || {};
  const mobile = appState.service.mobile_ui || {};
  const local = appState.local_context || {};
  const notifications = appState.notifications || {};
  const ntfy = notifications.ntfy || {};
  const telegram = notifications.telegram || {};
  const notificationChannels = notifications.channels || [];
  const notificationSummary = [
    notifications.enabled === false ? "desactivadas" : "activas",
    `canales: ${notificationChannels.length ? notificationChannels.join(", ") : "sin canales"}`,
    `ntfy: ${notificationChannels.includes("ntfy") ? (ntfy.topic ? "listo" : "falta topic") : "off"}`,
    `Telegram: ${notificationChannels.includes("telegram") ? (telegram.chat_id ? "listo" : (telegram.bot_token_configured ? "falta chat" : "falta token")) : "off"}`
  ].join(" | ");
  const internet = appState.internet || {};
  const voice = appState.voice || {};
  const live = voice.live_conversation || {};
  refreshBrowserVoices();
  if (!voiceOptionsLoaded) window.setTimeout(() => loadVoiceOptions(false, true), 0);
  const systemVoiceOptions = localTtsVoices.filter(item => (item.provider || "system") === "system").map(item => {
    const label = `${item.index}. ${item.name}${item.languages && item.languages.length ? " - " + item.languages.join(", ") : ""}`;
    return `<option value="${escapeHtml(item.id || "")}">${escapeHtml(label)}</option>`;
  }).join("");
  const kokoroNeedle = kokoroVoiceFilter.trim().toLowerCase();
  const kokoroVoiceOptions = localTtsVoices.filter(item => item.provider === "kokoro").filter(item => {
    if (!kokoroNeedle) return true;
    return `${item.id || ""} ${item.name || ""} ${(item.languages || []).join(" ")}`.toLowerCase().includes(kokoroNeedle);
  }).map(item => {
    const label = `${item.index}. ${item.name}`;
    return `<option value="${escapeHtml(item.id || "")}">${escapeHtml(label)}</option>`;
  }).join("");
  const browserVoiceOptions = browserVoices.map(item => (
    `<option value="${escapeHtml(item.name || "")}">${escapeHtml((item.name || "Voz") + (item.lang ? " - " + item.lang : ""))}</option>`
  )).join("");
  $("settings").innerHTML = `
    <section class="hero">
      <h2>Configuración operativa</h2>
      <div class="muted">Ajustes por área, guardados sin cambiar la forma de trabajar de Yarbis.</div>
    </section>
    <section class="section">
      <h2>Modelo</h2>
      <div class="setting-group form-grid wide">
        <div><label>Proveedor</label><select id="modelProvider"><option value="ollama">ollama</option><option value="openrouter">openrouter</option></select></div>
        <div><label>Modelo</label><input id="modelName" value="${escapeHtml(active.model || "")}"></div>
        <div><label>Host</label><input id="modelHost" value="${escapeHtml(active.host || "")}"></div>
        <div><label>Timeout segundos</label><input id="modelTimeout" type="number" value="${escapeHtml(active.timeout_seconds || 900)}"></div>
        <div><label>Fallbacks</label><textarea id="modelFallbacks">${escapeHtml((active.fallback_models || []).join("\n"))}</textarea></div>
        <div><label>Nueva API key</label><input id="modelApiKey" type="password" placeholder="${active.api_key_configured ? "guardada" : "sin configurar"}"></div>
        <button data-action="save-model">Guardar modelo</button>
      </div>
    </section>
    <section class="section">
      <h2>UI móvil</h2>
      <div class="setting-group form-grid wide">
        <label><input id="mobileEnabled" type="checkbox" ${mobile.enabled ? "checked" : ""}> Activa</label>
        <div><label>Puerto</label><input id="mobilePort" type="number" value="${escapeHtml(mobile.port || 8787)}"></div>
        <div><label>Timeout operaciones</label><input id="mobileJobTimeout" type="number" min="60" max="86400" value="${escapeHtml(mobile.job_timeout_seconds || 1800)}"></div>
        <div><label>Nuevo PIN</label><input id="mobilePin" type="password" placeholder="${mobile.configured ? "conservar PIN" : "PIN requerido"}"></div>
        <div class="panel"><pre>${escapeHtml((mobile.tailscale_url || mobile.local_url || "") + (mobile.last_bind_error ? "\n" + mobile.last_bind_error : ""))}</pre></div>
        <button data-action="save-mobile">Guardar UI movil</button>
      </div>
    </section>
    <section class="section">
      <h2>Voz</h2>
      <div class="setting-group form-grid wide">
        <label><input id="voiceEnabled" type="checkbox" ${voice.enabled === false ? "" : "checked"}> Activa</label>
        <div><label>Motor TTS</label><select id="voiceProvider"><option value="system">Sistema</option><option value="kokoro">Kokoro local</option></select></div>
        <div><label>Voz sistema/Telegram</label><select id="ttsVoiceId"><option value="">predeterminada</option>${systemVoiceOptions}</select></div>
        <div><label>Buscar Kokoro</label><input id="kokoroVoiceFilter" value="${escapeHtml(kokoroVoiceFilter)}" placeholder="es, dora, alex, english"></div>
        <div><label>Voz Kokoro</label><select id="kokoroVoiceId"><option value="">elige voz Kokoro</option>${kokoroVoiceOptions}</select></div>
        <div><label>Velocidad sistema</label><input id="ttsRate" type="number" min="80" max="320" value="${escapeHtml(voice.tts_rate || 175)}"></div>
        <div><label>Voz navegador</label><select id="browserVoiceName"><option value="">predeterminada</option>${browserVoiceOptions}</select></div>
        <div><label>Velocidad navegador</label><input id="browserTtsRate" type="number" min="0.5" max="2" step="0.1" value="${escapeHtml(voice.browser_tts_rate || 1)}"></div>
        <div><label>Tono navegador</label><input id="browserTtsPitch" type="number" min="0" max="2" step="0.1" value="${escapeHtml(voice.browser_tts_pitch || 1)}"></div>
        <div><label>Telegram voz</label><select id="telegramVoiceMode"><option value="off">off</option><option value="auto">auto</option><option value="always">always</option></select></div>
        <label><input id="liveVoiceEnabled" type="checkbox" ${live.enabled === false ? "" : "checked"}> Voz en vivo disponible</label>
        <div><label>Frase de activacion</label><input id="liveWakePhrase" value="${escapeHtml(live.wake_phrase || "Yarbis")}"></div>
        <div><label>Silencio ms</label><input id="liveSilenceMs" type="number" min="250" max="5000" value="${escapeHtml(live.silence_ms || 900)}"></div>
        <div><label>Turno max segundos</label><input id="liveMaxTurnSeconds" type="number" min="3" max="300" value="${escapeHtml(live.max_turn_seconds || 45)}"></div>
        <label><input id="liveAutoSpeak" type="checkbox" ${live.auto_speak === false ? "" : "checked"}> Responder con voz automaticamente</label>
        <label><input id="liveBargeIn" type="checkbox" ${live.barge_in === false ? "" : "checked"}> Permitir interrupcion</label>
        <button data-action="refresh-voice-catalog">Catálogo Kokoro</button>
        <button data-action="use-free-voice">Usar seleccionada</button>
        <button data-action="test-voice">Probar voz</button>
        <button data-action="save-voice">Guardar voz</button>
        <button data-action="stop-speaking">Detener habla</button>
      </div>
    </section>
    <section class="section">
      <h2>Pulso proactivo</h2>
      <div class="setting-group form-grid wide">
        <label><input id="pulseEnabled" type="checkbox" ${proactive.enabled ? "checked" : ""}> Activo</label>
        <div><label>Intervalo segundos</label><input id="pulseInterval" type="number" value="${escapeHtml(proactive.interval_seconds || 1800)}"></div>
        <div><label>Ciclos</label><input id="pulseCycles" value="${escapeHtml(proactive.cycles ?? "")}" placeholder="vacio = hasta terminar"></div>
        <div><label>Espera inicial</label><input id="pulseDelay" type="number" value="${escapeHtml(proactive.start_delay_seconds || 60)}"></div>
        <div><label>Modelo</label><input id="pulseModel" value="${escapeHtml(proactive.model || "")}"></div>
        <button data-action="save-pulse">Guardar pulso</button>
      </div>
    </section>
    <section class="section">
      <h2>Contexto local</h2>
      <div class="setting-group form-grid wide">
        <label><input id="localEnabled" type="checkbox" ${local.enabled ? "checked" : ""}> Activo</label>
        <div><label>Modo</label><select id="localMode"><option value="safe">safe</option><option value="detailed">detailed</option><option value="off">off</option></select></div>
        <div><label>Muestra cada</label><input id="localSample" type="number" value="${escapeHtml(local.sample_interval_seconds || 30)}"></div>
        <div><label>Vigencia</label><input id="localAge" type="number" value="${escapeHtml(local.max_snapshot_age_seconds || 180)}"></div>
        <label><input id="localTitle" type="checkbox" ${local.include_window_title ? "checked" : ""}> Titulos de ventana</label>
        <label><input id="localProcess" type="checkbox" ${local.include_process_name ? "checked" : ""}> Proceso</label>
        <label><input id="localWorkspace" type="checkbox" ${local.include_workspace_changes ? "checked" : ""}> Workspace</label>
        <label><input id="localSystem" type="checkbox" ${local.include_system_health ? "checked" : ""}> Sistema</label>
        <button data-action="save-local">Guardar contexto local</button>
      </div>
    </section>
    <section class="section">
      <h2>Notificaciones</h2>
      <div class="panel"><pre>${escapeHtml(notificationSummary)}</pre></div>
      <div class="setting-group form-grid wide">
        <label><input id="notifyEnabled" type="checkbox" ${notifications.enabled === false ? "" : "checked"}> Activas</label>
        <label><input id="notifyWindows" type="checkbox" ${notificationChannels.includes("windows") ? "checked" : ""}> Windows</label>
        <label><input id="notifyNtfy" type="checkbox" ${notificationChannels.includes("ntfy") ? "checked" : ""}> ntfy</label>
        <label><input id="notifyTelegram" type="checkbox" ${notificationChannels.includes("telegram") ? "checked" : ""}> Telegram</label>
        <div><label>ntfy servidor</label><input id="ntfyServer" value="${escapeHtml(ntfy.server || "https://ntfy.sh")}"></div>
        <div><label>ntfy topic</label><input id="ntfyTopic" value="${escapeHtml(ntfy.topic || "")}"></div>
        <div><label>ntfy prioridad</label><select id="ntfyPriority"><option value=""></option><option value="min">min</option><option value="low">low</option><option value="default">default</option><option value="high">high</option><option value="urgent">urgent</option></select></div>
        <div><label>ntfy tags</label><input id="ntfyTags" value="${escapeHtml(ntfy.tags || "")}"></div>
        <div><label>ntfy token nuevo</label><input id="ntfyToken" type="password" placeholder="${ntfy.token_configured ? "guardado; vacío conserva" : "opcional"}"></div>
        <div><label>Telegram token nuevo</label><input id="telegramToken" type="password" placeholder="${telegram.bot_token_configured ? "guardado; vacío conserva" : "requerido si activas Telegram"}"></div>
        <div><label>Telegram chat</label><input id="telegramChat" value="${escapeHtml(telegram.chat_id || "")}" placeholder="se vincula con /start + probar"></div>
        <button data-action="save-notifications">Guardar notificaciones</button>
        <button data-action="test-notification">Probar notificación</button>
      </div>
    </section>
    <section class="section">
      <h2>Memoria</h2>
      <div class="panel"><pre>${escapeHtml(appState.memory_protection_status || "")}</pre></div>
      <div class="form-grid">
        <input id="backupPath" placeholder="Ruta Windows para respaldo/importacion">
        <div class="actions">
          <button data-action="memory-backup">Respaldar</button>
          <button data-action="memory-inspect">Inspeccionar</button>
          <button data-action="memory-import">Trasplantar</button>
          <button data-action="memory-verify">Verificar</button>
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Internet y servicio</h2>
      <div class="form-grid">
        <select id="internetMode"><option value="auto">auto</option><option value="off">off</option></select>
        <button data-action="save-internet">Guardar internet</button>
        <div class="actions">
          <button data-action="service-start">Iniciar servicio</button>
          <button data-action="service-stop" class="danger">Detener servicio</button>
          <button data-action="service-autostart">Alternar arranque</button>
          <button data-action="service-remove" class="danger">Quitar SCM</button>
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Redes sociales</h2>
      <div class="panel"><pre>${escapeHtml(appState.social_accounts_text || "")}</pre></div>
      <input id="socialId" placeholder="Id de publicacion o draft">
      <div class="actions">
        <button data-action="social-confirmation">Ver confirmación</button>
        <button data-action="social-assisted">Abrir asistido</button>
      </div>
    </section>`;
  const providerNode = $("modelProvider");
  if (providerNode) providerNode.value = provider;
  const localMode = $("localMode");
  if (localMode) localMode.value = local.mode || "safe";
  const internetMode = $("internetMode");
  if (internetMode) internetMode.value = internet.mode || "auto";
  const ttsVoiceId = $("ttsVoiceId");
  if (ttsVoiceId) ttsVoiceId.value = voice.tts_voice_id || "";
  const voiceProvider = $("voiceProvider");
  if (voiceProvider) voiceProvider.value = voice.tts_provider || "system";
  const kokoroVoiceId = $("kokoroVoiceId");
  if (kokoroVoiceId) kokoroVoiceId.value = voice.kokoro_voice_id || "";
  const browserVoiceName = $("browserVoiceName");
  if (browserVoiceName) browserVoiceName.value = voice.browser_voice_name || window.localStorage.getItem("yarbis_browser_voice_name") || "";
  const telegramVoiceMode = $("telegramVoiceMode");
  if (telegramVoiceMode) telegramVoiceMode.value = voice.telegram_reply_mode || "auto";
  const ntfyPriority = $("ntfyPriority");
  if (ntfyPriority) ntfyPriority.value = ntfy.priority || "";
}

function renderActivity() {
  if (!("activity_text" in appState) && !("summary_text" in appState)) {
    $("activity").innerHTML = `
      <section class="section">
        <div class="actions"><button data-action="refresh">Cargar actividad</button></div>
      </section>`;
    return;
  }
  $("activity").innerHTML = `
    <section class="section">
      <div class="actions">
        <button data-action="refresh">Refrescar</button>
        <button class="danger" data-action="clear-activity">Limpiar actividad</button>
      </div>
    </section>
    <section class="section">
      <h2>Resumen</h2>
      <div class="panel"><pre>${escapeHtml(appState.summary_text || "")}</pre></div>
    </section>
    <section class="section">
      <h2>Actividad</h2>
      <div class="panel"><pre>${escapeHtml(appState.activity_text || "")}</pre></div>
    </section>`;
}

function showTab(name) {
  currentTab = name;
  if (validTabs.has(name)) window.history.replaceState(null, "", `#${name}`);
  document.querySelectorAll(".view").forEach(node => node.classList.toggle("active", node.id === name));
  document.querySelectorAll(".tab").forEach(node => node.classList.toggle("active", node.dataset.tab === name));
  renderCurrent();
  loadView(name);
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.speak !== undefined) {
    try {
      await speakText(button.dataset.speak);
    } catch (error) {
      toast(error.message);
    }
    return;
  }
  if (button.dataset.tab) {
    showTab(button.dataset.tab);
    return;
  }
  const name = button.dataset.action;
  if (!name) return;
  try {
    if (name === "logout") {
      await api("/api/logout", { method: "POST", headers: { "X-CSRF-Token": csrfToken }, body: { csrf: csrfToken } });
      csrfToken = "";
      await refresh();
    } else if (name === "refresh") {
      await loadView(currentTab, true);
    } else if (name === "run-cycle") {
      await action("run_cycle");
    } else if (name === "run-auto") {
      const cycles = await askText("Modo autónomo", "Deja vacío para continuar hasta terminar.", "", "Ciclos");
      if (cycles !== null) await action("run_auto", { cycles });
    } else if (name === "stop-operation") {
      await action("stop_operation");
    } else if (name === "stop-speaking") {
      stopSpeech();
    } else if (name === "record-reply") {
      await toggleReplyRecording();
    } else if (name === "toggle-live-voice") {
      await toggleLiveVoice();
    } else if (name === "voice-file") {
      const fileInput = $("voiceFileInput");
      if (fileInput) fileInput.click();
    } else if (name === "send-reply") {
      await action("submit_reply", { reply_text: $("replyText").value });
      $("replyText").value = "";
    } else if (name === "save-goal") {
      await action("update_goal", { goal: $("goalInput").value });
    } else if (name === "save-profile") {
      await action("update_profile", {
        name: $("profileName").value,
        role: $("profileRole").value,
        preferences: $("profilePrefs").value,
        constraints: $("profileConstraints").value
      });
    } else if (name === "save-note") {
      await action("save_note", { title: $("noteTitle").value, category: $("noteCategory").value, content: $("noteContent").value });
    } else if (name === "add-task") {
      await action("add_task", { title: $("taskTitle").value, details: $("taskDetails").value, priority: $("taskPriority").value });
    } else if (name === "create-idea") {
      await action("create_idea_project", {
        title: $("ideaTitle").value,
        kind: $("ideaKind").value,
        summary: $("ideaSummary").value,
        creative_directions: $("ideaDirections").value,
        open_questions: $("ideaQuestions").value,
        next_steps: $("ideaSteps").value
      });
    } else if (name === "select-idea") {
      fillIdeaProjectForm(button.dataset.projectId || "");
    } else if (name === "update-idea") {
      await action("update_idea_project", {
        project_id: $("ideaProjectId").value,
        title: $("ideaTitle").value,
        kind: $("ideaKind").value,
        status: $("ideaStatus").value,
        summary: $("ideaSummary").value,
        creative_directions: $("ideaDirections").value,
        selected_direction: $("ideaSelected").value,
        open_questions: $("ideaQuestions").value,
        next_steps: $("ideaNextStepsUpdate").value || $("ideaSteps").value
      });
    } else if (name === "promote-idea") {
      await action("promote_idea_project", { project_id: $("ideaProjectId").value, priority: "media" });
    } else if (name === "promote-idea-card") {
      await action("promote_idea_project", { project_id: button.dataset.projectId || "", priority: "media" });
    } else if (name === "visual-create-board") {
      const project = selectedVisualProject();
      if (!project) throw new Error("Selecciona un proyecto.");
      await action("create_visual_board", {
        project_id: project.id,
        board_kind: $("visualKind").value,
        title: $("visualNewTitle").value
      });
      loadedViews.visual = false;
    } else if (name === "visual-regenerate") {
      const project = selectedVisualProject();
      if (!project) throw new Error("Selecciona un proyecto.");
      const ok = await askConfirm(
        "Regenerar board",
        "Se creara un board nuevo desde los datos actuales del proyecto. El board existente no se borra.",
        "Crear nuevo"
      );
      if (ok) {
        await action("create_visual_board", {
          project_id: project.id,
          board_kind: $("visualKind").value,
          title: $("visualNewTitle").value || "Regenerado"
        });
        loadedViews.visual = false;
      }
    } else if (name === "visual-save-board") {
      const project = selectedVisualProject();
      const board = selectedVisualBoard(project);
      if (!project || !board) throw new Error("Selecciona un board visual.");
      applyVisualInspectorEdits();
      await action("update_visual_board", {
        project_id: project.id,
        board_id: board.id,
        board
      });
      loadedViews.visual = false;
    } else if (name === "visual-export-board") {
      const project = selectedVisualProject();
      const board = selectedVisualBoard(project);
      if (!project || !board) throw new Error("Selecciona un board visual.");
      applyVisualInspectorEdits();
      await action("update_visual_board", { project_id: project.id, board_id: board.id, board });
      await action("export_visual_board", {
        project_id: project.id,
        board_id: board.id,
        formats: "html,svg,json",
        open_file: false
      });
      loadedViews.visual = false;
    } else if (name === "visual-fit") {
      const board = selectedVisualBoard();
      visualFitBoard(board);
      renderVisual();
    } else if (name === "visual-autolayout") {
      const board = selectedVisualBoard();
      visualAutoLayout(board);
      renderVisual();
    } else if (name === "visual-update-node") {
      applyVisualInspectorEdits();
      renderVisual();
    } else if (name === "visual-delete-node") {
      const board = selectedVisualBoard();
      const node = selectedVisualNode(board);
      if (board && node) {
        board.nodes = (board.nodes || []).filter(item => item.id !== node.id);
        board.edges = (board.edges || []).filter(edge => edge.source !== node.id && edge.target !== node.id);
        visualSelection.nodeId = "";
        renderVisual();
      }
    } else if (name === "visual-add-node") {
      const board = selectedVisualBoard();
      if (!board) throw new Error("Selecciona un board visual.");
      const id = `node-${Date.now().toString(36)}`;
      board.nodes = board.nodes || [];
      board.nodes.push({
        id,
        type: "note",
        title: "Nuevo nodo",
        text: "",
        x: 520,
        y: 340,
        width: 230,
        height: 120,
        lane: "",
        color: "",
        meta: {}
      });
      visualSelection.nodeId = id;
      renderVisual();
    } else if (name === "save-coding-workspace") {
      await action("coding_workspace", { path: $("codingPath").value });
    } else if (name === "save-coding-validation") {
      await action("coding_validation", { command: $("codingValidation").value });
    } else if (name === "coding-detect-validation") {
      await action("coding_detect_validation", {});
    } else if (name === "coding-status") {
      await action("coding_status", {});
    } else if (name === "coding-validation-plan") {
      await action("coding_validation_plan", { proposal_id: selectedCodingProposalId() });
    } else if (name === "coding-search") {
      await action("coding_search", {
        pattern: $("codingSearchPattern").value,
        path: $("codingSearchPath").value || ".",
        glob: $("codingSearchGlob").value
      });
    } else if (name === "coding-read-range") {
      await action("coding_read_range", {
        path: $("codingRangePath").value,
        start_line: $("codingRangeStart").value,
        line_count: $("codingRangeCount").value
      });
    } else if (name === "coding-propose-edits") {
      await action("coding_propose_edits", {
        title: $("codingEditsTitle").value,
        edits_json: $("codingEditsJson").value,
        validation_command: $("codingValidation").value
      });
    } else if (name === "select-coding-proposal") {
      codingSelection.proposalId = button.dataset.proposalId || "";
      if ($("codingProposalId")) $("codingProposalId").value = codingSelection.proposalId;
      await action("coding_check", { proposal_id: codingSelection.proposalId });
    } else if (name === "coding-card-check") {
      codingSelection.proposalId = button.dataset.proposalId || "";
      await action("coding_check", { proposal_id: codingSelection.proposalId });
    } else if (name === "coding-card-get") {
      codingSelection.proposalId = button.dataset.proposalId || "";
      await action("coding_get", { proposal_id: codingSelection.proposalId });
    } else if (name === "coding-check") {
      await action("coding_check", { proposal_id: selectedCodingProposalId() });
    } else if (name === "coding-get") {
      await action("coding_get", { proposal_id: selectedCodingProposalId() });
    } else if (name === "coding-validate") {
      await action("coding_validate", { proposal_id: selectedCodingProposalId(), command: $("codingValidation").value });
    } else if (name === "coding-apply") {
      await action("coding_apply", { proposal_id: selectedCodingProposalId() });
    } else if (name === "coding-apply-validate") {
      await action("coding_apply_validate", { proposal_id: selectedCodingProposalId(), command: $("codingValidation").value });
    } else if (name === "coding-discard") {
      await action("coding_discard", { proposal_id: selectedCodingProposalId() });
    } else if (name === "save-model") {
      await action("update_model", {
        provider: $("modelProvider").value,
        model: $("modelName").value,
        host: $("modelHost").value,
        timeout_seconds: $("modelTimeout").value,
        fallback_models: $("modelFallbacks").value,
        api_key: $("modelApiKey").value
      });
    } else if (name === "save-mobile") {
      await action("mobile_ui", { enabled: $("mobileEnabled").checked, port: $("mobilePort").value, job_timeout_seconds: $("mobileJobTimeout").value, pin: $("mobilePin").value });
    } else if (name === "refresh-voice-catalog") {
      toast("Cargando voces Kokoro...");
      await loadVoiceOptions(true, true, true);
      toast("Voces Kokoro listas");
    } else if (name === "use-free-voice") {
      if (!localTtsVoices.some(item => item.provider === "kokoro")) {
        toast("Cargando voces Kokoro...");
        await loadVoiceOptions(true, true, true);
      }
      const kokoroVoiceId = selectedKokoroVoiceId();
      if (!kokoroVoiceId) throw new Error("No encontre voces Kokoro.");
      if ($("voiceProvider")) $("voiceProvider").value = "kokoro";
      if ($("kokoroVoiceId")) $("kokoroVoiceId").value = kokoroVoiceId;
      await saveVoiceSettings("kokoro", kokoroVoiceId);
      await speakText("Hola, soy Yarbis con esta voz.");
    } else if (name === "test-voice") {
      await speakText("Hola, soy Yarbis probando esta voz local.");
    } else if (name === "save-voice") {
      await saveVoiceSettings();
    } else if (name === "save-pulse") {
      await action("service_proactive", {
        enabled: $("pulseEnabled").checked,
        interval_seconds: $("pulseInterval").value,
        cycles: $("pulseCycles").value,
        start_delay_seconds: $("pulseDelay").value,
        model: $("pulseModel").value
      });
    } else if (name === "save-local") {
      await action("local_context", {
        enabled: $("localEnabled").checked,
        mode: $("localMode").value,
        sample_interval_seconds: $("localSample").value,
        max_snapshot_age_seconds: $("localAge").value,
        include_window_title: $("localTitle").checked,
        include_process_name: $("localProcess").checked,
        include_workspace_changes: $("localWorkspace").checked,
        include_system_health: $("localSystem").checked
      });
    } else if (name === "save-notifications") {
      await action("notifications", {
        enabled: $("notifyEnabled").checked,
        windows_enabled: $("notifyWindows").checked,
        ntfy_enabled: $("notifyNtfy").checked,
        telegram_enabled: $("notifyTelegram").checked,
        ntfy_server: $("ntfyServer").value,
        ntfy_topic: $("ntfyTopic").value,
        ntfy_token: $("ntfyToken").value,
        ntfy_priority: $("ntfyPriority").value,
        ntfy_tags: $("ntfyTags").value,
        telegram_bot_token: $("telegramToken").value,
        telegram_chat_id: $("telegramChat").value
      });
    } else if (name === "test-notification") {
      await action("send_test_notification");
    } else if (name === "memory-backup") {
      await action("memory_backup", { path: $("backupPath").value, include_secrets: false });
    } else if (name === "memory-inspect") {
      await action("memory_inspect", { path_or_id: $("backupPath").value });
    } else if (name === "memory-import") {
      if (await askConfirm("Trasplantar memoria", "Esto puede reemplazar o fusionar memoria. ¿Continuar?")) {
        await action("memory_import", { path_or_id: $("backupPath").value, mode: "replace" });
      }
    } else if (name === "memory-verify") {
      await action("memory_verify");
    } else if (name === "save-internet") {
      await action("internet", { mode: $("internetMode").value, provider: "duckduckgo_html" });
    } else if (name === "service-start") {
      await action("service_start");
    } else if (name === "service-stop") {
      if (await askConfirm("Detener servicio", "Detener el servicio cerrará esta UI hasta que vuelva a iniciar. ¿Continuar?")) await action("service_stop");
    } else if (name === "service-autostart") {
      await action("service_autostart", { enabled: !(appState.service.status.autostart_enabled) });
    } else if (name === "service-remove") {
      if (await askConfirm("Quitar servicio", "¿Quitar el servicio de SCM?")) await action("service_remove");
    } else if (name === "social-confirmation") {
      const data = await action("social_confirmation", { publication_id: $("socialId").value });
      if (data.result) await askText("Confirmación", "Copia esta frase para publicar.", data.result, "Frase");
    } else if (name === "social-assisted") {
      await action("social_assisted", { publication_id: $("socialId").value, draft_id: $("socialId").value });
    } else if (name === "clear-activity") {
      if (await askConfirm("Limpiar actividad", "¿Limpiar la actividad local?")) await action("clear_activity");
    }
  } catch (error) {
    toast(error.message);
  }
});

document.addEventListener("change", async (event) => {
  const input = event.target;
  if (input && input.id === "kokoroVoiceFilter") {
    kokoroVoiceFilter = input.value || "";
    renderCurrent();
    return;
  }
  if (input && input.id === "visualProjectSelect") {
    visualSelection.projectId = input.value || "";
    visualSelection.boardId = "";
    visualSelection.nodeId = "";
    renderVisual();
    return;
  }
  if (input && input.id === "visualBoardSelect") {
    visualSelection.boardId = input.value || "";
    visualSelection.nodeId = "";
    renderVisual();
    return;
  }
  if (input && input.id === "visualKind") {
    return;
  }
  if (!input || input.id !== "voiceFileInput") return;
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  try {
    await transcribeVoiceBlob(file);
  } catch (error) {
    toast(error.message);
  }
});

document.addEventListener("pointerdown", (event) => {
  if (currentTab !== "visual") return;
  const svg = event.target.closest && event.target.closest("#visualCanvas");
  if (!svg) return;
  const board = selectedVisualBoard();
  if (!board) return;
  const nodeGroup = event.target.closest("[data-visual-node-id]");
  const point = visualCanvasPoint(event, board);
  if (nodeGroup) {
    const nodeId = nodeGroup.dataset.visualNodeId || "";
    const node = (board.nodes || []).find(item => item.id === nodeId);
    if (!node) return;
    visualSelection.nodeId = nodeId;
    visualDrag = {
      nodeId,
      offsetX: point.x - Number(node.x || 0),
      offsetY: point.y - Number(node.y || 0)
    };
    svg.classList.add("dragging");
    svg.setPointerCapture(event.pointerId);
    visualRenderCanvas(board);
    return;
  }
  visualPan = {
    startX: point.svgX,
    startY: point.svgY,
    originX: Number((board.viewport || {}).x || 0),
    originY: Number((board.viewport || {}).y || 0)
  };
  svg.classList.add("dragging");
  svg.setPointerCapture(event.pointerId);
});

document.addEventListener("pointermove", (event) => {
  if (currentTab !== "visual") return;
  const board = selectedVisualBoard();
  const svg = $("visualCanvas");
  if (!board || !svg) return;
  if (visualDrag) {
    const node = (board.nodes || []).find(item => item.id === visualDrag.nodeId);
    if (!node) return;
    const point = visualCanvasPoint(event, board);
    node.x = Math.round(point.x - visualDrag.offsetX);
    node.y = Math.round(point.y - visualDrag.offsetY);
    visualRenderCanvas(board);
    return;
  }
  if (visualPan) {
    const rect = svg.getBoundingClientRect();
    const svgX = ((event.clientX - rect.left) / Math.max(1, rect.width)) * 1400;
    const svgY = ((event.clientY - rect.top) / Math.max(1, rect.height)) * 900;
    board.viewport = board.viewport || { x: 0, y: 0, zoom: 1 };
    board.viewport.x = visualPan.originX + (svgX - visualPan.startX);
    board.viewport.y = visualPan.originY + (svgY - visualPan.startY);
    visualRenderCanvas(board);
  }
});

document.addEventListener("pointerup", (event) => {
  const svg = $("visualCanvas");
  if (svg && event.pointerId !== undefined) {
    try { svg.releasePointerCapture(event.pointerId); } catch (_) {}
    svg.classList.remove("dragging");
  }
  if (currentTab === "visual" && (visualDrag || visualPan)) {
    visualDrag = null;
    visualPan = null;
    renderVisual();
  }
});

document.addEventListener("wheel", (event) => {
  if (currentTab !== "visual") return;
  const svg = event.target.closest && event.target.closest("#visualCanvas");
  if (!svg) return;
  const board = selectedVisualBoard();
  if (!board) return;
  event.preventDefault();
  const before = visualCanvasPoint(event, board);
  board.viewport = board.viewport || { x: 0, y: 0, zoom: 1 };
  const oldZoom = Number(board.viewport.zoom || 1);
  const nextZoom = Math.max(0.2, Math.min(3, oldZoom * (event.deltaY > 0 ? 0.9 : 1.1)));
  board.viewport.zoom = nextZoom;
  board.viewport.x = before.svgX - before.x * nextZoom;
  board.viewport.y = before.svgY - before.y * nextZoom;
  visualRenderCanvas(board);
}, { passive: false });

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/login", { method: "POST", body: { pin: $("pinInput").value } });
    csrfToken = data.csrf || "";
    $("pinInput").value = "";
    await refresh();
  } catch (error) {
    $("loginMessage").textContent = error.message;
  }
});

if ("speechSynthesis" in window) {
  refreshBrowserVoices();
  window.speechSynthesis.onvoiceschanged = () => {
    refreshBrowserVoices();
    if (currentTab === "settings") renderCurrent();
  };
}

function isEditingField() {
  const tag = (document.activeElement && document.activeElement.tagName || "").toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

refresh().then(() => {
  if (currentTab !== "home" && currentTab !== "run") loadView(currentTab, true);
});
window.setInterval(() => {
  if ($("appView").classList.contains("hidden")) return;
  if (currentTab === "activity") return;
  if (isEditingField()) return;
  refresh({ silent: true });
}, 15000);
</script>
</body>
</html>"""


class MobileRequestHandler(BaseHTTPRequestHandler):
    server_version = "YarbisMobile/0.1"

    def log_message(self, _format, *_args):  # pragma: no cover - evita ruido en servicio.
        return

    def _send_bytes(self, status: int, content: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _send_html(self, status: int, html: str) -> None:
        self._send_bytes(status, html.encode("utf-8"), "text/html; charset=utf-8")

    def _send_json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", headers=headers)

    def _read_json(self, max_bytes: int = MAX_REQUEST_BYTES) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length > max_bytes:
            raise MobileUiError("La peticion es demasiado grande.")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MobileUiError("JSON invalido.") from exc
        if not isinstance(payload, dict):
            raise MobileUiError("La peticion debe ser un objeto JSON.")
        return payload

    def _settings(self) -> dict:
        return get_mobile_ui_settings()

    def _cookie_value(self) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        if not raw_cookie:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return ""
        morsel = cookie.get(MOBILE_COOKIE_NAME)
        return morsel.value if morsel else ""

    def _session(self, settings: dict | None = None) -> dict | None:
        settings = settings or self._settings()
        return verify_session_cookie(self._cookie_value(), str(settings.get("session_secret", "")))

    def _require_session(self, payload: dict | None = None) -> tuple[dict, dict]:
        settings = self._settings()
        session = self._session(settings)
        if session is None:
            raise PermissionError("Sesion movil requerida.")
        payload = payload if isinstance(payload, dict) else {}
        csrf = str(payload.get("csrf", "") or self.headers.get("X-CSRF-Token", "")).strip()
        if self.command == "POST" and not hmac.compare_digest(csrf, session["csrf"]):
            raise PermissionError("Token CSRF invalido.")
        return settings, session

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTTPStatus.OK, _html_page())
            return
        if parsed.path == "/api/state":
            try:
                _settings, session = self._require_session({})
                view = parse_qs(parsed.query).get("view", [""])[0]
                self._send_json(HTTPStatus.OK, {
                    "ok": True,
                    "authenticated": True,
                    "csrf": session["csrf"],
                    "state": _public_state(view),
                })
            except PermissionError as exc:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": str(exc)})
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        if parsed.path.startswith("/api/jobs/"):
            try:
                _settings, session = self._require_session({})
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = _get_job(job_id)
                if not job:
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Trabajo no encontrado."})
                    return
                self._send_json(HTTPStatus.OK, {"ok": True, "csrf": session["csrf"], "job": job})
            except PermissionError as exc:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/voice/live/status":
            try:
                _settings, session = self._require_session({})
                query = parse_qs(parsed.query)
                session_id = query.get("session_id", [""])[0]
                voice_session = voice_conversation.mobile_session_status(session_id)
                if not voice_session:
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Sesión de voz no encontrada."})
                    return
                self._send_json(HTTPStatus.OK, {"ok": True, "csrf": session["csrf"], "voice_session": voice_session})
            except PermissionError as exc:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/voice/voices":
            try:
                _settings, session = self._require_session({})
                query = parse_qs(parsed.query)
                self._send_json(HTTPStatus.OK, {
                    "ok": True,
                    "csrf": session["csrf"],
                    **_public_voice_payload(
                        include_downloadable=query.get("catalog", ["0"])[0] == "1",
                        refresh_catalog=query.get("refresh", ["0"])[0] == "1",
                    ),
                })
            except PermissionError as exc:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Ruta no encontrada."})

    def do_POST(self):
        try:
            max_bytes = (
                MAX_VOICE_REQUEST_BYTES
                if self.path in {"/api/voice/transcribe", "/api/voice/live/chunk"}
                else MAX_REQUEST_BYTES
            )
            payload = self._read_json(max_bytes=max_bytes)
        except MobileUiError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        if self.path == "/api/login":
            self._handle_login(payload)
            return

        try:
            _settings, session = self._require_session(payload)
            if self.path == "/api/logout":
                headers = {
                    "Set-Cookie": (
                        f"{MOBILE_COOKIE_NAME}=; Max-Age=0; Path=/; "
                        "HttpOnly; SameSite=Lax"
                    )
                }
                self._send_json(HTTPStatus.OK, {"ok": True}, headers=headers)
                return
            if self.path == "/api/action":
                action = str(payload.get("action", "")).strip()
                action_payload = payload.get("payload", {})
                result = _execute_action(action, action_payload if isinstance(action_payload, dict) else {})
                rendered = str(result.get("result", "")).strip()
                if rendered:
                    activity.append_activity("UI movil", rendered)
                self._send_json(HTTPStatus.OK, {"ok": True, "csrf": session["csrf"], **result})
                return
            if self.path == "/api/voice/transcribe":
                text = _transcribe_mobile_voice(payload)
                activity.append_activity("UI movil voz", text)
                self._send_json(HTTPStatus.OK, {"ok": True, "csrf": session["csrf"], "text": text})
                return
            if self.path == "/api/voice/live/start":
                result = _start_mobile_live_voice()
                self._send_json(HTTPStatus.OK, {"ok": True, "csrf": session["csrf"], **result})
                return
            if self.path == "/api/voice/live/chunk":
                result = _append_mobile_live_voice(payload)
                self._send_json(HTTPStatus.OK, {"ok": True, "csrf": session["csrf"], **result})
                return
            if self.path == "/api/voice/live/stop":
                result = _stop_mobile_live_voice(payload)
                self._send_json(HTTPStatus.OK, {"ok": True, "csrf": session["csrf"], **result})
                return
            if self.path == "/api/voice/speak":
                audio_b64, mime_type = _synthesize_mobile_speech(payload)
                self._send_json(HTTPStatus.OK, {
                    "ok": True,
                    "csrf": session["csrf"],
                    "audio_b64": audio_b64,
                    "mime_type": mime_type,
                })
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Ruta no encontrada."})
        except PermissionError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    def _handle_login(self, payload: dict) -> None:
        settings = self._settings()
        if not settings.get("enabled"):
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "La UI movil esta desactivada. Activalo desde Yarbis de escritorio.",
            })
            return
        if not str(settings.get("pin_hash", "")).strip():
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Falta configurar PIN desde Yarbis de escritorio.",
            })
            return
        settings = _ensure_mobile_session_secret(settings)
        pin = str(payload.get("pin", ""))
        if not verify_mobile_pin(pin, settings.get("pin_hash", ""), settings.get("pin_salt", "")):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "PIN incorrecto."})
            return
        cookie_value, csrf = create_session_cookie(settings["session_secret"])
        headers = {
            "Set-Cookie": (
                f"{MOBILE_COOKIE_NAME}={cookie_value}; Max-Age={MOBILE_SESSION_SECONDS}; "
                "Path=/; HttpOnly; SameSite=Lax"
            )
        }
        self._send_json(HTTPStatus.OK, {"ok": True, "csrf": csrf}, headers=headers)


def stop_mobile_ui_servers() -> str:
    global _MOBILE_SERVER_KEY
    with _MOBILE_LOCK:
        servers = list(_MOBILE_SERVERS)
        _MOBILE_SERVERS.clear()
        _MOBILE_SERVER_KEY = None
    for entry in servers:
        server = entry.get("server")
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    if servers:
        return "UI movil detenida."
    return ""


def ensure_mobile_ui_servers() -> str:
    global _MOBILE_SERVER_KEY
    settings = get_mobile_ui_settings()
    enabled = bool(settings.get("enabled"))
    configured = bool(str(settings.get("pin_hash", "")).strip())
    port = int(settings.get("port", DEFAULT_MOBILE_UI_PORT) or DEFAULT_MOBILE_UI_PORT)

    if not enabled:
        stopped = stop_mobile_ui_servers()
        return stopped or ""
    if not configured:
        stopped = stop_mobile_ui_servers()
        message = "UI movil activada, pero falta configurar PIN."
        _set_mobile_bind_error(message)
        return "\n".join(part for part in (stopped, message) if part)

    settings = _ensure_mobile_session_secret(settings)
    hosts = _bind_hosts()
    key = (port, hosts, settings.get("pin_hash"), settings.get("session_secret"))
    with _MOBILE_LOCK:
        if _MOBILE_SERVER_KEY == key and _MOBILE_SERVERS:
            return ""

    stop_mobile_ui_servers()
    started = []
    errors = []
    for host in hosts:
        try:
            server = _MobileHTTPServer((host, port), MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://{host}:{port}"
            started.append({"server": server, "thread": thread, "host": host, "port": port, "url": url})
        except OSError as exc:
            errors.append(f"{host}:{port} - {exc}")
    with _MOBILE_LOCK:
        _MOBILE_SERVERS.extend(started)
        _MOBILE_SERVER_KEY = key if started else None

    if not started:
        message = "No pude iniciar la UI movil: " + "; ".join(errors)
        _set_mobile_bind_error(message)
        return message

    warning = ""
    if len(hosts) == 1:
        warning = "Tailscale no detectado; UI movil solo disponible en localhost."
    elif errors:
        warning = "Algunos enlaces fallaron: " + "; ".join(errors)
    _set_mobile_bind_error(warning)
    urls = ", ".join(entry["url"] for entry in started)
    if warning:
        return f"UI movil activa en {urls}. {warning}"
    return f"UI movil activa en {urls}."


def start_mobile_ui_from_state() -> str:
    try:
        return ensure_mobile_ui_servers()
    except Exception:
        error = "Error iniciando UI movil:\n" + traceback.format_exc()
        _set_mobile_bind_error(error)
        return error


def main() -> None:
    message = start_mobile_ui_from_state()
    if message:
        print(message)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_mobile_ui_servers()


if __name__ == "__main__":
    main()
