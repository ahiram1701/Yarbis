import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
os.chdir(WORKSPACE_ROOT)

import activity
from service_manager import LOG_FILE, PID_FILE, RUNTIME_DIR, STOP_FILE
from memory import (
    DEFAULT_SERVICE_PROACTIVE_CYCLES,
    DEFAULT_SERVICE_PROACTIVE_ENABLED,
    DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS,
    DEFAULT_SERVICE_PROACTIVE_MODEL,
    DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS,
    MAX_OLLAMA_MODEL_CHARS,
    default_state,
    load_state,
    state_transaction,
)
from proactive_context import build_proactive_tick_message
from proactive_context import PROACTIVE_TICK_BASE_MESSAGE
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

ENV_SERVICE_PROACTIVE = "YARBIS_SERVICE_PROACTIVE"
ENV_SERVICE_PROACTIVE_INTERVAL_SECONDS = "YARBIS_SERVICE_PROACTIVE_INTERVAL_SECONDS"
ENV_SERVICE_PROACTIVE_CYCLES = "YARBIS_SERVICE_PROACTIVE_CYCLES"
ENV_SERVICE_PROACTIVE_START_DELAY_SECONDS = "YARBIS_SERVICE_PROACTIVE_START_DELAY_SECONDS"
ENV_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS = "YARBIS_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS"
ENV_SERVICE_PROACTIVE_MODEL = "YARBIS_SERVICE_PROACTIVE_MODEL"

SERVICE_LOOP_SLEEP_SECONDS = 1.0
DEFAULT_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS = 60
MAX_PROACTIVE_OUTPUT_CHARS = 12_000
PROACTIVE_TICK_PREFIX = PROACTIVE_TICK_BASE_MESSAGE.split(":", 1)[0] + ":"


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
        "cycles": _env_int(
            ENV_SERVICE_PROACTIVE_CYCLES,
            proactive.get("cycles", DEFAULT_SERVICE_PROACTIVE_CYCLES),
            minimum=1,
            maximum=5,
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


def _drop_trailing_proactive_tick_messages(reason: str = "") -> bool:
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
                and str(message.get("content", "")).startswith(PROACTIVE_TICK_PREFIX)
            ):
                tick_index = index
                break

        if tick_index is None:
            return False

        for message in messages[tick_index + 1:]:
            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and not str(message.get("content", "")).startswith(PROACTIVE_TICK_PREFIX)
            ):
                return False

        del messages[tick_index:]
        state["messages"] = messages
        if reason:
            state["last_result"] = reason
            state["runtime"] = default_state()["runtime"]
        return True

    return bool(state_transaction("drop_trailing_proactive_tick_messages", mutate))


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
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
        start_telegram_polling(event_callback=lambda message: _log(_render_event(message)))
        settings = get_service_proactive_settings()
        next_proactive_at = time.monotonic() + settings["start_delay_seconds"]
        if settings["enabled"]:
            model_text = (
                f", modelo {settings['model']}"
                if str(settings.get("model", "")).strip()
                else ", modelo principal"
            )
            _log(
                "Proactividad 24/7 activa: "
                f"{settings['cycles']} ciclo(s) cada {settings['interval_seconds']}s "
                f"tras {settings['start_delay_seconds']}s de espera inicial"
                f"{model_text}."
            )
        else:
            _log("Proactividad 24/7 desactivada por configuracion.")

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

            settings = get_service_proactive_settings()
            now = time.monotonic()
            try:
                process_deferred_telegram_replies(limit=1)
            except Exception:
                _log("Error procesando respuestas diferidas de Telegram:\n" + traceback.format_exc())

            try:
                recovered_output = _recover_unanswered_user_message()
                if recovered_output:
                    _log(recovered_output)
            except Exception:
                _log("Error recuperando respuesta pendiente:\n" + traceback.format_exc())

            if settings["enabled"] and now >= next_proactive_at:
                model_text = (
                    f", modelo {settings['model']}"
                    if str(settings.get("model", "")).strip()
                    else ", modelo principal"
                )
                _log(f"Pulso proactivo iniciado ({settings['cycles']} ciclo(s){model_text}).")
                try:
                    _log(run_proactive_pulse())
                except Exception:
                    _log("Error en pulso proactivo:\n" + traceback.format_exc())
                next_proactive_at = time.monotonic() + settings["interval_seconds"]
            elif not settings["enabled"]:
                next_proactive_at = now + settings["interval_seconds"]

            time.sleep(SERVICE_LOOP_SLEEP_SECONDS)
    except Exception:
        activity.emit_event("service_failed", error=traceback.format_exc())
        _log(traceback.format_exc())
        raise
    finally:
        stop_telegram_polling()
        _clear_runtime_files()
        _log("Servicio de Yarbis detenido.")
        activity.emit_event("service_stopped", pid=os.getpid())


def main():
    run_service_loop()


if __name__ == "__main__":
    main()
