import os
import time
import traceback
from pathlib import Path

import activity
from memory import load_state
from pc_context import (
    clear_snapshot,
    collect_and_write_snapshot,
    local_context_enabled,
    normalize_local_context_settings,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent
WORKER_SLEEP_FALLBACK_SECONDS = 30


def _log(message: object) -> None:
    try:
        activity.append_activity("Contexto local", str(message).strip())
    except Exception:
        pass


def run_worker(should_stop=None) -> None:
    os.chdir(WORKSPACE_ROOT)
    last_disabled = False
    while True:
        if callable(should_stop) and should_stop():
            return

        try:
            state = load_state()
            settings = normalize_local_context_settings(state.get("local_context", {}))
            sleep_seconds = settings["sample_interval_seconds"]
            if not local_context_enabled(settings):
                if not last_disabled:
                    clear_snapshot()
                    last_disabled = True
                time.sleep(sleep_seconds)
                continue

            last_disabled = False
            collect_and_write_snapshot(settings=settings, source="pc_context_worker")
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            return
        except Exception:
            _log("Error en observador de contexto local:\n" + traceback.format_exc())
            time.sleep(WORKER_SLEEP_FALLBACK_SECONDS)


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()
