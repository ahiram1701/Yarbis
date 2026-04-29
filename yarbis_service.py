import os
import time
import traceback
from datetime import datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
os.chdir(WORKSPACE_ROOT)

from service_manager import LOG_FILE, PID_FILE, RUNTIME_DIR, STOP_FILE
from session import run_startup_self_analysis
from telegram_inbox import start_telegram_polling, stop_telegram_polling


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


def main():
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
        while not STOP_FILE.exists():
            time.sleep(1)
    except Exception:
        _log(traceback.format_exc())
        raise
    finally:
        stop_telegram_polling()
        _clear_runtime_files()
        _log("Servicio de Yarbis detenido.")


if __name__ == "__main__":
    main()
