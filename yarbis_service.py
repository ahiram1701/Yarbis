import ctypes
import json
import os
import subprocess
import sys
import time
import traceback
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
os.chdir(WORKSPACE_ROOT)

# Si OLLAMA_API_KEY no esta en el entorno (ej: servicio SCM sin herencia),
# leerla del registro de Windows donde setx la dejo.
if not os.getenv("OLLAMA_API_KEY"):
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "OLLAMA_API_KEY")
            if value:
                os.environ["OLLAMA_API_KEY"] = value
    except Exception:
        pass

import yarbis_instance
from process_utils import no_window_creationflags

yarbis_instance.configure_from_argv()
yarbis_instance.ensure_instance_registered()

import activity
import yarbis_bus
from service_manager import LOG_FILE, PID_FILE, RUNTIME_DIR, STOP_FILE
from memory import (
    DEFAULT_SERVICE_PROACTIVE_CYCLES,
    DEFAULT_SERVICE_PROACTIVE_ENABLED,
    DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS,
    DEFAULT_SERVICE_PROACTIVE_MODEL,
    DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS,
    MAX_OLLAMA_MODEL_CHARS,
    default_state,
    format_cycle_count,
    load_state,
    normalize_cycle_count,
    state_transaction,
    wait_for_memory_protection_maintenance,
)
from proactive_context import build_proactive_tick_message
from proactive_context import build_self_evolution_tick_message
from proactive_context import PROACTIVE_TICK_BASE_MESSAGE
from proactive_context import SELF_EVOLUTION_TICK_BASE_MESSAGE
from secrets_redaction import redact_secrets
from notifications import (
    get_telegram_settings,
    notify_user_input_required,
    send_telegram_message,
)
from session import (
    SessionOperationBusy,
    clear_abandoned_runtime_operation,
    has_pending_user_question,
    has_unanswered_user_message,
    current_operation_id,
    recover_unanswered_user_message_with_output,
    run_auto_with_output,
    run_startup_self_analysis,
    session_operation_lock,
)
from telegram_inbox import (
    format_telegram_operation_reply,
    process_deferred_telegram_replies,
    start_telegram_polling,
    stop_telegram_polling,
)
from yarbis_mobile import ensure_mobile_ui_servers, start_mobile_ui_from_state, stop_mobile_ui_servers

ENV_SERVICE_PROACTIVE = "YARBIS_SERVICE_PROACTIVE"
ENV_SERVICE_PROACTIVE_INTERVAL_SECONDS = "YARBIS_SERVICE_PROACTIVE_INTERVAL_SECONDS"
ENV_SERVICE_PROACTIVE_CYCLES = "YARBIS_SERVICE_PROACTIVE_CYCLES"
ENV_SERVICE_PROACTIVE_START_DELAY_SECONDS = "YARBIS_SERVICE_PROACTIVE_START_DELAY_SECONDS"
ENV_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS = "YARBIS_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS"
ENV_SERVICE_PROACTIVE_MODEL = "YARBIS_SERVICE_PROACTIVE_MODEL"
ENV_EVOLUTION_ENABLED = "YARBIS_EVOLUTION"
ENV_EVOLUTION_INTERVAL_HOURS = "YARBIS_EVOLUTION_INTERVAL_HOURS"
ENV_EVOLUTION_MAX_RUNTIME_SECONDS = "YARBIS_EVOLUTION_MAX_RUNTIME_SECONDS"

SERVICE_LOOP_SLEEP_SECONDS = 1.0
DEFAULT_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS = 60
MAX_PROACTIVE_OUTPUT_CHARS = 12_000
PROACTIVE_TICK_PREFIX = PROACTIVE_TICK_BASE_MESSAGE.split(":", 1)[0] + ":"

# El pulso proactivo lanza un subproceso que importa toda la app; si varias
# instancias lo hacen a la vez agotan la RAM y sus procesos mueren de golpe
# (exit 1, sin traceback). Serializamos el pulso entre instancias con un mutex
# de sesion (namespace Local\, compartido por los servicios en sesion 0) y, si
# el turno esta ocupado, reintentamos pronto en vez de saltar todo el intervalo.
PROACTIVE_SLOT_BUSY_PREFIX = "Pulso proactivo aplazado"
PROACTIVE_SLOT_RETRY_SECONDS = 45
PROACTIVE_START_JITTER_MAX_SECONDS = 90
_PROACTIVE_SLOT_MUTEX_NAME = "Local\\YarbisProactivePulseSlot"

# Autoevolucion: una sola instancia (mutex propio) revisa a Yarbis en cadencia
# lenta y crea PROPUESTAS que el usuario aprueba; nunca aplica nada sola.
DEFAULT_EVOLUTION_MAX_RUNTIME_SECONDS = 180
DEFAULT_EVOLUTION_CYCLES = 4
_SELF_EVOLUTION_MUTEX_NAME = "Local\\YarbisSelfEvolution"
SELF_EVOLUTION_TICK_PREFIX = SELF_EVOLUTION_TICK_BASE_MESSAGE.split(":", 1)[0] + ":"


class _NoopSlot:
    def release(self) -> None:
        pass


class _WinMutexSlot:
    def __init__(self, kernel32, handle):
        self._kernel32 = kernel32
        self._handle = handle

    def release(self) -> None:
        try:
            self._kernel32.ReleaseMutex(self._handle)
        finally:
            self._kernel32.CloseHandle(self._handle)


def _acquire_named_slot(mutex_name: str):
    """Reserva un turno global entre instancias con un mutex de sesion (0 espera).

    Devuelve un objeto liberable si se obtuvo el turno, o None si otra instancia
    lo tiene. Ante cualquier problema devuelve un slot no-op para no desactivar la
    funcion por accidente. El mutex de Windows se libera solo si el proceso muere,
    asi que un turno abandonado (WAIT_ABANDONED) tambien cuenta como libre.
    """
    if os.name != "nt":
        return _NoopSlot()
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            return _NoopSlot()
        result = kernel32.WaitForSingleObject(handle, 0)
        if result in (0x00000000, 0x00000080):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            return _WinMutexSlot(kernel32, handle)
        kernel32.CloseHandle(handle)
        return None
    except Exception:
        return _NoopSlot()


def _acquire_proactive_slot():
    return _acquire_named_slot(_PROACTIVE_SLOT_MUTEX_NAME)


def _proactive_start_jitter_seconds() -> int:
    instance_id = str(yarbis_instance.current_instance_id())
    if not instance_id:
        return 0
    return sum((index + 1) * ord(char) for index, char in enumerate(instance_id)) % (
        PROACTIVE_START_JITTER_MAX_SECONDS + 1
    )


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(message: object):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    rendered = redact_secrets(message).strip()
    if not rendered:
        return

    timestamp = _timestamp()
    with open(LOG_FILE, "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {rendered}\n")

    try:
        activity.append_activity("Servicio", rendered, timestamp=timestamp)
    except Exception:
        pass


def _write_pid():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _clear_runtime_files():
    try:
        if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    try:
        STOP_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _discard_startup_stop_file() -> float:
    try:
        STOP_FILE.unlink()
        return 0.0
    except FileNotFoundError:
        return 0.0
    except OSError:
        try:
            return STOP_FILE.stat().st_mtime
        except OSError:
            return time.time()


def _stop_file_requests_current_run(ignored_mtime: float) -> bool:
    if not STOP_FILE.exists():
        return False
    if not ignored_mtime:
        return True

    try:
        return STOP_FILE.stat().st_mtime > ignored_mtime
    except OSError:
        return True


def _render_event(message: object) -> str:
    if isinstance(message, dict):
        event_type = str(message.get("type", "")).strip()
        label = str(message.get("label", "")).strip()
        content = str(message.get("content", "")).strip()
        status_text = str(message.get("status_text", "")).strip()
        parts = [part for part in (event_type, label, status_text, content) if part]
        return "Telegram: " + " | ".join(parts)

    return str(message).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    return raw_value.strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except ValueError:
        return default

    return max(minimum, min(maximum, parsed))


def _env_optional_positive_int(name: str, default: int | None) -> int | None:
    raw_value = os.getenv(name)
    default_value = normalize_cycle_count(default)
    if raw_value is None:
        return default_value

    parsed = normalize_cycle_count(raw_value, default=0)
    if parsed == 0:
        return default_value
    return parsed


def _env_text(name: str, default: str, maximum_chars: int) -> str:
    raw_value = os.getenv(name)
    if raw_value is None:
        return str(default or "").strip()
    return str(raw_value).strip()[:maximum_chars]


def get_service_proactive_settings() -> dict:
    try:
        proactive = load_state().get("service", {}).get("proactive", {})
    except Exception:
        proactive = {}
    if not isinstance(proactive, dict):
        proactive = {}

    return {
        "enabled": _env_bool(
            ENV_SERVICE_PROACTIVE,
            bool(proactive.get("enabled", DEFAULT_SERVICE_PROACTIVE_ENABLED)),
        ),
        "interval_seconds": _env_int(
            ENV_SERVICE_PROACTIVE_INTERVAL_SECONDS,
            proactive.get(
                "interval_seconds",
                DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS,
            ),
            minimum=60,
            maximum=24 * 60 * 60,
        ),
        "cycles": _env_optional_positive_int(
            ENV_SERVICE_PROACTIVE_CYCLES,
            proactive.get("cycles", DEFAULT_SERVICE_PROACTIVE_CYCLES),
        ),
        "start_delay_seconds": _env_int(
            ENV_SERVICE_PROACTIVE_START_DELAY_SECONDS,
            proactive.get(
                "start_delay_seconds",
                DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS,
            ),
            minimum=0,
            maximum=24 * 60 * 60,
        ),
        "model": _env_text(
            ENV_SERVICE_PROACTIVE_MODEL,
            proactive.get("model", DEFAULT_SERVICE_PROACTIVE_MODEL),
            MAX_OLLAMA_MODEL_CHARS,
        ),
        "max_runtime_seconds": _env_int(
            ENV_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS,
            proactive.get(
                "max_runtime_seconds",
                DEFAULT_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS,
            ),
            minimum=15,
            maximum=60 * 60,
        ),
    }


def _mark_proactive_pulse() -> str:
    timestamp = _utc_timestamp()

    def mutate(state):
        service_state = state.setdefault("service", {})
        proactive_state = service_state.setdefault("proactive", {})
        proactive_state["last_pulse_at"] = timestamp

    state_transaction("service_proactive_last_pulse", mutate, create_backup=False)
    return timestamp


def _append_proactive_tick_message():
    def mutate(state):
        state["messages"].append({
            "role": "user",
            "content": build_proactive_tick_message(state),
        })

    state_transaction("append_proactive_tick_message", mutate)


def _bounded_proactive_output(output: object) -> str:
    rendered = str(output).strip()
    if len(rendered) <= MAX_PROACTIVE_OUTPUT_CHARS:
        return rendered
    return (
        rendered[:MAX_PROACTIVE_OUTPUT_CHARS].rstrip()
        + "\n...[salida del pulso truncada por longitud]..."
    )


def _drop_trailing_tick_messages(prefix: str, reason: str = "", label: str = "drop_trailing_tick_messages") -> bool:
    def mutate(state):
        messages = state.get("messages", [])
        if not isinstance(messages, list):
            return False

        tick_index = None
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and str(message.get("content", "")).startswith(prefix)
            ):
                tick_index = index
                break

        if tick_index is None:
            return False

        for message in messages[tick_index + 1:]:
            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and not str(message.get("content", "")).startswith(prefix)
            ):
                return False

        del messages[tick_index:]
        state["messages"] = messages
        if reason:
            state["last_result"] = reason
            state["runtime"] = default_state()["runtime"]
        return True

    return bool(state_transaction(label, mutate))


def _drop_trailing_proactive_tick_messages(reason: str = "") -> bool:
    return _drop_trailing_tick_messages(
        PROACTIVE_TICK_PREFIX, reason, "drop_trailing_proactive_tick_messages"
    )


def _drop_trailing_self_evolution_tick_messages(reason: str = "") -> bool:
    return _drop_trailing_tick_messages(
        SELF_EVOLUTION_TICK_PREFIX, reason, "drop_trailing_self_evolution_tick_messages"
    )


def _send_telegram_operation_update(label: str, output: str) -> bool:
    rendered = str(output).strip()
    if not rendered or rendered.startswith("Pulso proactivo omitido"):
        return False

    try:
        notification_settings = load_state().get("notifications", {})
        telegram_settings = get_telegram_settings(notification_settings)
        if (
            not telegram_settings.get("enabled")
            or not telegram_settings.get("bot_token")
            or not telegram_settings.get("chat_id")
        ):
            return False

        return send_telegram_message(
            format_telegram_operation_reply(label, rendered),
            settings=notification_settings,
            chat_id=telegram_settings["chat_id"],
        )
    except Exception as exc:
        _log(f"No pude enviar resumen de {label} a Telegram: {exc}")
        return False


def _send_proactive_telegram_update(output: str) -> bool:
    return _send_telegram_operation_update("Pulso proactivo", output)


def _proactive_waiting_for_user_message(state: dict) -> str:
    if not has_pending_user_question(state):
        return ""

    awaiting_user_input = state.get("awaiting_user_input", {})
    question = str(awaiting_user_input.get("question", "")).strip()
    return (
        "Pulso proactivo omitido: esperando respuesta del usuario."
        + (f"\nPregunta pendiente: {question}" if question else "")
    )


def _recover_unanswered_user_message() -> str:
    state = load_state()
    if has_pending_user_question(state) or not has_unanswered_user_message(state):
        return ""

    try:
        output = recover_unanswered_user_message_with_output(
            emit_notifications=False,
            blocking=False,
        )
    except SessionOperationBusy:
        return (
            "Recuperacion omitida: hay una operacion de Yarbis en curso. "
            "Se intentara de nuevo cuando quede libre."
        )

    if not output:
        return ""

    refreshed_state = load_state()
    if has_pending_user_question(refreshed_state):
        notify_user_input_required(
            refreshed_state["awaiting_user_input"].get("question", ""),
            refreshed_state["awaiting_user_input"].get("reason", ""),
        )
    else:
        _send_telegram_operation_update("Respuesta recuperada", output)

    return "Respuesta de usuario pendiente recuperada.\n" + output


def _run_proactive_pulse_inline(settings: dict, last_pulse_at: str) -> str:
    try:
        with session_operation_lock("Pulso proactivo", blocking=False):
            state = load_state()
            waiting_message = _proactive_waiting_for_user_message(state)
            if waiting_message:
                activity.emit_event(
                    "proactive_pulse_skipped",
                    operation_id=current_operation_id(),
                    label="Pulso proactivo",
                    reason="waiting_for_user",
                    last_pulse_at=last_pulse_at,
                )
                return waiting_message

            if has_unanswered_user_message(state):
                recovered_output = _recover_unanswered_user_message()
                if recovered_output:
                    activity.emit_event(
                        "proactive_pulse_completed",
                        operation_id=current_operation_id(),
                        label="Pulso proactivo",
                        recovered_user_message=True,
                        last_pulse_at=last_pulse_at,
                    )
                    return recovered_output

            _append_proactive_tick_message()
            auto_kwargs = {
                "cycles": settings["cycles"],
                "emit_notifications": False,
            }
            proactive_model = str(settings.get("model", "")).strip()
            if proactive_model:
                auto_kwargs["model_override"] = proactive_model
            output = run_auto_with_output(**auto_kwargs)

            refreshed_state = load_state()
            if has_pending_user_question(refreshed_state):
                notify_user_input_required(
                    refreshed_state["awaiting_user_input"].get("question", ""),
                    refreshed_state["awaiting_user_input"].get("reason", ""),
                )
            else:
                _send_proactive_telegram_update(output)

            activity.emit_event(
                "proactive_pulse_completed",
                operation_id=current_operation_id(),
                label="Pulso proactivo",
                recovered_user_message=False,
                last_pulse_at=last_pulse_at,
            )
            return output
    except SessionOperationBusy:
        activity.emit_event(
            "proactive_pulse_skipped",
            label="Pulso proactivo",
            reason="busy",
            last_pulse_at=last_pulse_at,
        )
        return (
            "Pulso proactivo omitido: hay una operacion de Yarbis en curso. "
            "Se intentara en el siguiente intervalo."
        )


def _creationflags() -> int:
    return no_window_creationflags()


def _kill_process_tree(process: subprocess.Popen):
    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            completed = subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=_creationflags(),
            )
            if completed.returncode == 0:
                try:
                    process.wait(timeout=5)
                except Exception:
                    pass
                if process.poll() is not None:
                    return
        except Exception:
            pass

    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except Exception:
        pass


def _run_proactive_pulse_with_timeout(settings: dict, last_pulse_at: str) -> str:
    slot = _acquire_proactive_slot()
    if slot is None:
        return (
            f"{PROACTIVE_SLOT_BUSY_PREFIX}: otra instancia esta ejecutando su pulso. "
            f"Reintento en {PROACTIVE_SLOT_RETRY_SECONDS}s."
        )
    try:
        return _run_proactive_pulse_child(settings, last_pulse_at)
    finally:
        slot.release()


def _run_proactive_pulse_child(settings: dict, last_pulse_at: str) -> str:
    timeout_seconds = int(settings.get("max_runtime_seconds", DEFAULT_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS))
    child_code = (
        "import json, os, yarbis_service; "
        "settings=json.loads(os.environ['YARBIS_PROACTIVE_SETTINGS_JSON']); "
        "print(yarbis_service._run_proactive_pulse_inline(settings, os.environ['YARBIS_PROACTIVE_LAST_PULSE_AT']))"
    )
    child_env = os.environ.copy()
    child_env["YARBIS_PROACTIVE_SETTINGS_JSON"] = json.dumps(settings)
    child_env["YARBIS_PROACTIVE_LAST_PULSE_AT"] = last_pulse_at
    child_env["YARBIS_OLLAMA_TIMEOUT_SECONDS"] = str(
        max(15, min(60, timeout_seconds - 5))
    )
    # El hijo hace print() de la salida del pulso hacia un pipe; en Windows eso se
    # codifica en cp1252 por defecto y revienta con UnicodeEncodeError al imprimir
    # caracteres como "->". Forzamos UTF-8 para que coincida con encoding="utf-8"
    # del padre y el pulso deje de fallar.
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"

    try:
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", child_code],
            cwd=str(WORKSPACE_ROOT),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creationflags(),
        )
    except OSError as exc:
        raise RuntimeError(f"No pude iniciar el proceso aislado del pulso proactivo: {exc}") from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        clear_abandoned_runtime_operation()
        message = (
            "Pulso proactivo detenido por timeout duro "
            f"({timeout_seconds}s). Se limpio la operacion aislada para mantener vivo el servicio."
        )
        _drop_trailing_proactive_tick_messages(message)
        activity.emit_event(
            "proactive_pulse_timed_out",
            label="Pulso proactivo",
            last_pulse_at=last_pulse_at,
            timeout_seconds=timeout_seconds,
        )
        return message

    combined = "\n".join(part.strip() for part in (stdout, stderr) if str(part).strip())
    if process.returncode != 0:
        clear_abandoned_runtime_operation()
        _drop_trailing_proactive_tick_messages("Pulso proactivo fallo en proceso aislado.")
        raise RuntimeError(
            "Pulso proactivo fallo en proceso aislado"
            + (f" (exit={process.returncode}).\n{combined}" if combined else f" (exit={process.returncode}).")
        )

    return _bounded_proactive_output(combined)


def run_proactive_pulse() -> str:
    settings = get_service_proactive_settings()
    if not settings["enabled"]:
        activity.emit_event(
            "proactive_pulse_skipped",
            label="Pulso proactivo",
            reason="disabled",
        )
        return "Pulso proactivo omitido: proactividad desactivada."

    last_pulse_at = _mark_proactive_pulse()
    waiting_message = _proactive_waiting_for_user_message(load_state())
    if waiting_message:
        activity.emit_event(
            "proactive_pulse_skipped",
            label="Pulso proactivo",
            reason="waiting_for_user",
            last_pulse_at=last_pulse_at,
        )
        return waiting_message

    return _run_proactive_pulse_with_timeout(settings, last_pulse_at)


# ---------------------------------------------------------------------------
# Autoevolucion: una sola instancia revisa a Yarbis en cadencia lenta y crea
# PROPUESTAS que el usuario aprueba. Nunca aplica nada por si misma.
# ---------------------------------------------------------------------------

def get_self_evolution_settings() -> dict:
    try:
        evolution = load_state().get("evolution", {})
    except Exception:
        evolution = {}
    if not isinstance(evolution, dict):
        evolution = {}

    interval_hours = _env_int(
        ENV_EVOLUTION_INTERVAL_HOURS,
        evolution.get("interval_hours", 6),
        minimum=1,
        maximum=168,
    )
    try:
        max_pending = int(evolution.get("max_pending", 2))
    except (TypeError, ValueError):
        max_pending = 2

    return {
        "enabled": _env_bool(ENV_EVOLUTION_ENABLED, bool(evolution.get("enabled", False))),
        "interval_hours": interval_hours,
        "interval_seconds": interval_hours * 3600,
        "max_pending": max(1, min(20, max_pending)),
        "cycles": DEFAULT_EVOLUTION_CYCLES,
        "max_runtime_seconds": _env_int(
            ENV_EVOLUTION_MAX_RUNTIME_SECONDS,
            DEFAULT_EVOLUTION_MAX_RUNTIME_SECONDS,
            minimum=30,
            maximum=30 * 60,
        ),
    }


def _count_pending_coding_proposals(state: dict | None = None) -> int:
    if state is None:
        state = load_state()
    coding = state.get("coding", {}) if isinstance(state, dict) else {}
    pending = coding.get("pending_proposal_ids", []) if isinstance(coding, dict) else []
    return len(pending) if isinstance(pending, list) else 0


def _count_pending_evolution_items(state: dict | None = None) -> int:
    if state is None:
        state = load_state()
    total = _count_pending_coding_proposals(state)
    evolution = state.get("evolution", {}) if isinstance(state, dict) else {}
    if isinstance(evolution, dict):
        for key in ("directives_pending", "suggestions_pending"):
            items = evolution.get(key, [])
            if isinstance(items, list):
                total += len(items)
    return total


def _ensure_coding_workspace() -> None:
    # La autoevolucion propone sobre el propio repo de Yarbis: si el workspace de
    # coding no esta configurado, lo apunta al directorio del proyecto.
    try:
        coding = load_state().get("coding", {})
        if isinstance(coding, dict) and str(coding.get("workspace_path", "")).strip():
            return
    except Exception:
        pass

    def mutate(state):
        coding_state = state.setdefault("coding", {})
        if not str(coding_state.get("workspace_path", "")).strip():
            coding_state["workspace_path"] = str(WORKSPACE_ROOT)

    try:
        state_transaction("evolution_ensure_workspace", mutate, create_backup=False)
    except Exception:
        pass


def _mark_evolution_run() -> str:
    timestamp = _utc_timestamp()

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        evolution_state["last_run_at"] = timestamp

    state_transaction("evolution_last_run", mutate, create_backup=False)
    return timestamp


def _append_self_evolution_tick_message():
    def mutate(state):
        state["messages"].append({
            "role": "user",
            "content": build_self_evolution_tick_message(state),
        })

    state_transaction("append_self_evolution_tick_message", mutate)


def _run_self_evolution_inline(settings: dict) -> str:
    try:
        with session_operation_lock("Autoevolucion", blocking=False):
            state = load_state()
            if has_pending_user_question(state):
                return "Autoevolucion omitida: esperando respuesta del usuario."

            max_pending = int(settings.get("max_pending", 2))
            pending_before = _count_pending_evolution_items(state)
            if pending_before >= max_pending:
                return (
                    f"Autoevolucion omitida: ya hay {pending_before} propuesta(s) pendientes "
                    f"(limite {max_pending}). Aprueba o descarta antes de generar mas."
                )

            _ensure_coding_workspace()
            _append_self_evolution_tick_message()
            output = run_auto_with_output(cycles=settings.get("cycles"), emit_notifications=False)

            refreshed_state = load_state()
            new_count = max(0, _count_pending_evolution_items(refreshed_state) - pending_before)
            if has_pending_user_question(refreshed_state):
                notify_user_input_required(
                    refreshed_state["awaiting_user_input"].get("question", ""),
                    refreshed_state["awaiting_user_input"].get("reason", ""),
                )
            elif new_count > 0:
                _send_telegram_operation_update(
                    "Autoevolucion",
                    f"Genere {new_count} propuesta(s) de mejora para tu aprobacion. "
                    "Revisalas con /coding, evolution_list_pending / evolution_list_suggestions "
                    "o en la UI movil.\n\n"
                    + str(output),
                )

            activity.emit_event(
                "self_evolution_completed",
                operation_id=current_operation_id(),
                label="Autoevolucion",
                new_proposals=new_count,
            )
            return output
    except SessionOperationBusy:
        activity.emit_event("self_evolution_skipped", label="Autoevolucion", reason="busy")
        return "Autoevolucion omitida: hay una operacion de Yarbis en curso."


def _run_self_evolution_child(settings: dict) -> str:
    timeout_seconds = int(settings.get("max_runtime_seconds", DEFAULT_EVOLUTION_MAX_RUNTIME_SECONDS))
    child_code = (
        "import json, os, yarbis_service; "
        "settings=json.loads(os.environ['YARBIS_EVOLUTION_SETTINGS_JSON']); "
        "print(yarbis_service._run_self_evolution_inline(settings))"
    )
    child_env = os.environ.copy()
    child_env["YARBIS_EVOLUTION_SETTINGS_JSON"] = json.dumps(settings)
    child_env["YARBIS_OLLAMA_TIMEOUT_SECONDS"] = str(max(30, min(120, timeout_seconds - 10)))
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"

    try:
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", child_code],
            cwd=str(WORKSPACE_ROOT),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creationflags(),
        )
    except OSError as exc:
        raise RuntimeError(f"No pude iniciar el proceso aislado de autoevolucion: {exc}") from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        clear_abandoned_runtime_operation()
        message = f"Autoevolucion detenida por timeout duro ({timeout_seconds}s)."
        _drop_trailing_self_evolution_tick_messages(message)
        activity.emit_event("self_evolution_timed_out", label="Autoevolucion", timeout_seconds=timeout_seconds)
        return message

    combined = "\n".join(part.strip() for part in (stdout, stderr) if str(part).strip())
    if process.returncode != 0:
        clear_abandoned_runtime_operation()
        _drop_trailing_self_evolution_tick_messages("Autoevolucion fallo en proceso aislado.")
        raise RuntimeError(
            "Autoevolucion fallo en proceso aislado"
            + (f" (exit={process.returncode}).\n{combined}" if combined else f" (exit={process.returncode}).")
        )

    return _bounded_proactive_output(combined)


def run_self_evolution() -> str:
    settings = get_self_evolution_settings()
    if not settings["enabled"]:
        return "Autoevolucion desactivada."

    # Solo una instancia evoluciona a la vez (evita 8 propuestas duplicadas y RAM).
    slot = _acquire_named_slot(_SELF_EVOLUTION_MUTEX_NAME)
    if slot is None:
        return "Autoevolucion aplazada: otra instancia la esta ejecutando."

    _mark_evolution_run()
    try:
        return _run_self_evolution_child(settings)
    finally:
        slot.release()


def run_service_loop(should_stop=None):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    ignored_stop_mtime = _discard_startup_stop_file()

    _write_pid()
    if clear_abandoned_runtime_operation():
        _log("Estado operativo anterior abandonado limpiado.")
    _log("Servicio de Yarbis iniciado.")
    activity.emit_event("service_started", pid=os.getpid())

    try:
        _log(run_startup_self_analysis(force=False, background=True))
        mobile_start = start_mobile_ui_from_state()
        if mobile_start:
            _log(mobile_start)
        start_telegram_polling(event_callback=lambda message: _log(_render_event(message)))
        settings = get_service_proactive_settings()
        # Jitter por instancia para que, al arrancar todas juntas, sus primeros
        # pulsos no caigan en la misma ventana y compitan por el turno global.
        next_proactive_at = (
            time.monotonic() + settings["start_delay_seconds"] + _proactive_start_jitter_seconds()
        )
        if settings["enabled"]:
            cycles_text = format_cycle_count(settings["cycles"])
            model_text = (
                f", modelo {settings['model']}"
                if str(settings.get("model", "")).strip()
                else ", modelo principal"
            )
            _log(
                "Proactividad 24/7 activa: "
                f"{cycles_text} cada {settings['interval_seconds']}s "
                f"tras {settings['start_delay_seconds']}s de espera inicial"
                f"{model_text}."
            )
        else:
            _log("Proactividad 24/7 desactivada por configuracion.")

        evolution_settings = get_self_evolution_settings()
        next_evolution_at = time.monotonic() + evolution_settings["interval_seconds"]
        if evolution_settings["enabled"]:
            _log(
                "Autoevolucion activa: revisa y propone mejoras cada "
                f"{evolution_settings['interval_hours']}h (una sola instancia, "
                "solo propuestas, aprobacion manual)."
            )
        else:
            _log("Autoevolucion desactivada por configuracion.")

        try:
            recovered_output = _recover_unanswered_user_message()
            if recovered_output:
                _log(recovered_output)
        except Exception:
            _log("Error recuperando respuesta pendiente:\n" + traceback.format_exc())

        while True:
            if callable(should_stop):
                if should_stop():
                    break
            elif _stop_file_requests_current_run(ignored_stop_mtime):
                break

            try:
                settings = get_service_proactive_settings()
            except Exception:
                _log("Error leyendo configuracion de proactividad:\n" + traceback.format_exc())
                # Conservamos la ultima configuracion conocida para no detener el servicio.
            try:
                evolution_settings = get_self_evolution_settings()
            except Exception:
                _log("Error leyendo configuracion de autoevolucion:\n" + traceback.format_exc())
            try:
                mobile_status = ensure_mobile_ui_servers()
                if mobile_status:
                    _log(mobile_status)
            except Exception:
                _log("Error asegurando la UI movil:\n" + traceback.format_exc())
            now = time.monotonic()
            try:
                process_deferred_telegram_replies(limit=1)
            except Exception:
                _log("Error procesando respuestas diferidas de Telegram:\n" + traceback.format_exc())

            try:
                processed_messages = yarbis_bus.process_pending_messages(limit=1)
                if processed_messages:
                    _log(f"Mensajes directos procesados: {processed_messages}.")
            except Exception:
                _log("Error procesando mensajes directos entre Yarbis:\n" + traceback.format_exc())

            try:
                import mesh

                synced = mesh.maybe_sync()
                if synced:
                    _log(f"Mensajes de la malla procesados: {synced}.")
            except Exception:
                _log("Error sincronizando la malla de Yarbis:\n" + traceback.format_exc())

            try:
                recovered_output = _recover_unanswered_user_message()
                if recovered_output:
                    _log(recovered_output)
            except Exception:
                _log("Error recuperando respuesta pendiente:\n" + traceback.format_exc())

            if settings["enabled"] and now >= next_proactive_at:
                cycles_text = format_cycle_count(settings["cycles"])
                model_text = (
                    f", modelo {settings['model']}"
                    if str(settings.get("model", "")).strip()
                    else ", modelo principal"
                )
                _log(f"Pulso proactivo iniciado ({cycles_text}{model_text}).")
                pulse_result = ""
                try:
                    pulse_result = run_proactive_pulse()
                    _log(pulse_result)
                except Exception:
                    _log("Error en pulso proactivo:\n" + traceback.format_exc())
                if isinstance(pulse_result, str) and pulse_result.startswith(PROACTIVE_SLOT_BUSY_PREFIX):
                    # Otra instancia tenia el turno: reintentamos pronto, no dentro de un intervalo entero.
                    next_proactive_at = time.monotonic() + PROACTIVE_SLOT_RETRY_SECONDS
                else:
                    next_proactive_at = time.monotonic() + settings["interval_seconds"]
            elif not settings["enabled"]:
                next_proactive_at = now + settings["interval_seconds"]

            if evolution_settings["enabled"] and now >= next_evolution_at:
                _log("Autoevolucion iniciada (revisa y propone mejoras).")
                evo_result = ""
                try:
                    evo_result = run_self_evolution()
                    _log(evo_result)
                except Exception:
                    _log("Error en autoevolucion:\n" + traceback.format_exc())
                if isinstance(evo_result, str) and evo_result.startswith("Autoevolucion aplazada"):
                    next_evolution_at = time.monotonic() + PROACTIVE_SLOT_RETRY_SECONDS
                else:
                    next_evolution_at = time.monotonic() + evolution_settings["interval_seconds"]
            elif not evolution_settings["enabled"]:
                next_evolution_at = now + evolution_settings["interval_seconds"]

            time.sleep(SERVICE_LOOP_SLEEP_SECONDS)
    except Exception:
        activity.emit_event("service_failed", error=traceback.format_exc())
        _log(traceback.format_exc())
        raise
    finally:
        stop_mobile_ui_servers()
        stop_telegram_polling()
        try:
            wait_for_memory_protection_maintenance(timeout_seconds=10)
        except Exception:
            pass
        _clear_runtime_files()
        _log("Servicio de Yarbis detenido.")
        activity.emit_event("service_stopped", pid=os.getpid())


def main():
    run_service_loop()


if __name__ == "__main__":
    main()
