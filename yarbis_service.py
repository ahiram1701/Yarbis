import os
import time
import traceback
from datetime import datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
os.chdir(WORKSPACE_ROOT)

from service_manager import LOG_FILE, PID_FILE, RUNTIME_DIR, STOP_FILE
from memory import (
    DEFAULT_SERVICE_PROACTIVE_CYCLES,
    DEFAULT_SERVICE_PROACTIVE_ENABLED,
    DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS,
    DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS,
    load_state,
    save_state,
)
from notifications import (
    get_telegram_settings,
    notify_user_input_required,
    send_telegram_message,
)
from session import (
    SessionOperationBusy,
    has_pending_user_question,
    has_unanswered_user_message,
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

SERVICE_LOOP_SLEEP_SECONDS = 1.0

PROACTIVE_TICK_MESSAGE = (
    "Pulso proactivo 24/7 del servicio: revisa objetivo, perfil, notas, tareas "
    "y autoconocimiento. Avanza un paso util y verificable si existe. Si algo "
    "impide avanzar, pide ayuda con request_user_input. Si no hay nada accionable, "
    "deja una salida breve sin inventar trabajo."
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(message: object):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    rendered = str(message).strip()
    if not rendered:
        return

    with open(LOG_FILE, "a", encoding="utf-8") as log_file:
        log_file.write(f"[{_timestamp()}] {rendered}\n")


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
    }


def _append_proactive_tick_message():
    state = load_state()
    state["messages"].append({
        "role": "user",
        "content": PROACTIVE_TICK_MESSAGE,
    })
    save_state(state)


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
        "Pulso proactivo omitido: Yarbis espera respuesta del usuario."
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


def run_proactive_pulse() -> str:
    settings = get_service_proactive_settings()
    if not settings["enabled"]:
        return "Pulso proactivo omitido: proactividad desactivada."

    waiting_message = _proactive_waiting_for_user_message(load_state())
    if waiting_message:
        return waiting_message

    try:
        with session_operation_lock("Pulso proactivo", blocking=False):
            state = load_state()
            waiting_message = _proactive_waiting_for_user_message(state)
            if waiting_message:
                return waiting_message

            if has_unanswered_user_message(state):
                recovered_output = _recover_unanswered_user_message()
                if recovered_output:
                    return recovered_output

            _append_proactive_tick_message()
            output = run_auto_with_output(
                cycles=settings["cycles"],
                emit_notifications=False,
            )

            refreshed_state = load_state()
            if has_pending_user_question(refreshed_state):
                notify_user_input_required(
                    refreshed_state["awaiting_user_input"].get("question", ""),
                    refreshed_state["awaiting_user_input"].get("reason", ""),
                )
            else:
                _send_proactive_telegram_update(output)

            return output
    except SessionOperationBusy:
        return (
            "Pulso proactivo omitido: hay una operacion de Yarbis en curso. "
            "Se intentara en el siguiente intervalo."
        )


def run_service_loop(should_stop=None):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        STOP_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    _write_pid()
    _log("Servicio de Yarbis iniciado.")

    try:
        _log(run_startup_self_analysis())
        start_telegram_polling(event_callback=lambda message: _log(_render_event(message)))
        settings = get_service_proactive_settings()
        next_proactive_at = time.monotonic() + settings["start_delay_seconds"]
        if settings["enabled"]:
            _log(
                "Proactividad 24/7 activa: "
                f"{settings['cycles']} ciclo(s) cada {settings['interval_seconds']}s "
                f"tras {settings['start_delay_seconds']}s de espera inicial."
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
            elif STOP_FILE.exists():
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
                _log(f"Pulso proactivo iniciado ({settings['cycles']} ciclo(s)).")
                try:
                    _log(run_proactive_pulse())
                except Exception:
                    _log("Error en pulso proactivo:\n" + traceback.format_exc())
                next_proactive_at = time.monotonic() + settings["interval_seconds"]
            elif not settings["enabled"]:
                next_proactive_at = now + settings["interval_seconds"]

            time.sleep(SERVICE_LOOP_SLEEP_SECONDS)
    except Exception:
        _log(traceback.format_exc())
        raise
    finally:
        stop_telegram_polling()
        _clear_runtime_files()
        _log("Servicio de Yarbis detenido.")


def main():
    run_service_loop()


if __name__ == "__main__":
    main()
