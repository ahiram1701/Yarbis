import os
import threading
import time
import traceback
from pathlib import Path

import yarbis_instance

yarbis_instance.configure_from_argv()
yarbis_instance.ensure_instance_registered()

import activity
from memory import load_state
from pc_context import (
    clear_snapshot,
    collect_and_write_snapshot,
    local_context_enabled,
    normalize_local_context_settings,
)
import pc_context_runtime
import service_manager

WORKSPACE_ROOT = Path(__file__).resolve().parent
HELPER_SOURCE = "pc_context_tray"
HELPER_FALLBACK_SLEEP_SECONDS = 30
TRAY_STATE_COLORS = {
    "capturando": (46, 160, 67),
    "pausado": (210, 153, 34),
    "desactivado": (110, 118, 129),
    "error": (207, 34, 46),
    "sin helper": (110, 118, 129),
}


def _log(message: object) -> None:
    try:
        activity.append_activity("Contexto local", str(message).strip())
    except Exception:
        pass


def _status_payload(state: str, detail: str = "", **extra) -> dict:
    payload = {
        "state": state,
        "detail": str(detail).strip(),
        "service_running": bool(extra.pop("service_running", False)),
        "context_enabled": bool(extra.pop("context_enabled", False)),
    }
    payload.update(extra)
    return payload


def capture_once(
    load_state_func=load_state,
    get_service_status_func=service_manager.get_service_status,
    collect_func=collect_and_write_snapshot,
    clear_func=clear_snapshot,
    write_status_func=pc_context_runtime.write_helper_status,
) -> dict:
    try:
        state = load_state_func()
        settings = normalize_local_context_settings(state.get("local_context", {}))
        context_enabled = local_context_enabled(settings)
    except Exception as exc:
        payload = _status_payload("error", f"No pude leer configuracion local: {exc}")
        write_status_func(payload)
        return payload

    try:
        service_status = get_service_status_func()
        service_running = bool(service_status.get("running"))
    except Exception as exc:
        service_running = False
        service_status = {"error": str(exc)}

    if not context_enabled:
        clear_func()
        payload = _status_payload(
            "desactivado",
            "Contexto local desactivado por configuracion.",
            service_running=service_running,
            context_enabled=False,
            sample_interval_seconds=settings["sample_interval_seconds"],
        )
        write_status_func(payload)
        return payload

    if not service_running:
        clear_func()
        detail = "Servicio de Yarbis detenido."
        if service_status.get("error"):
            detail = f"No pude confirmar servicio activo: {service_status['error']}"
        payload = _status_payload(
            "pausado",
            detail,
            service_running=False,
            context_enabled=True,
            sample_interval_seconds=settings["sample_interval_seconds"],
        )
        write_status_func(payload)
        return payload

    try:
        snapshot = collect_func(settings=settings, source=HELPER_SOURCE)
    except Exception as exc:
        payload = _status_payload(
            "error",
            f"No pude capturar contexto local: {exc}",
            service_running=True,
            context_enabled=True,
            sample_interval_seconds=settings["sample_interval_seconds"],
        )
        write_status_func(payload)
        return payload

    payload = _status_payload(
        "capturando",
        "Snapshot local fresco.",
        service_running=True,
        context_enabled=True,
        sample_interval_seconds=settings["sample_interval_seconds"],
        captured_at=snapshot.get("captured_at", ""),
    )
    write_status_func(payload)
    return payload


class ContextTrayController:
    def __init__(self):
        self.stop_event = threading.Event()
        self.capture_now_event = threading.Event()
        self.status_lock = threading.Lock()
        self.status = _status_payload("pausado", "Inicializando helper.")
        self.worker_thread = None

    def status_text(self) -> str:
        with self.status_lock:
            state = str(self.status.get("state", "pausado")).strip() or "pausado"
            detail = str(self.status.get("detail", "")).strip()
        return f"Estado: {state}" + (f" - {detail}" if detail else "")

    def request_capture_now(self) -> None:
        self.capture_now_event.set()

    def request_stop(self, icon=None) -> None:
        self.stop_event.set()
        pc_context_runtime.request_helper_stop()
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def start_worker(self, icon=None) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.worker_thread = threading.Thread(target=self._worker_loop, args=(icon,), daemon=True)
        self.worker_thread.start()

    def _update_status(self, status: dict, icon=None) -> None:
        with self.status_lock:
            self.status = status
        if icon is not None:
            _update_icon(icon, status)

    def _wait_for_next_tick(self, seconds: int) -> None:
        deadline = time.monotonic() + max(1, int(seconds))
        while not self.stop_event.is_set() and not pc_context_runtime.helper_stop_requested():
            if self.capture_now_event.is_set():
                self.capture_now_event.clear()
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.stop_event.wait(min(1.0, remaining))

    def _worker_loop(self, icon=None) -> None:
        os.chdir(WORKSPACE_ROOT)
        pc_context_runtime.write_helper_pid()
        pc_context_runtime.clear_helper_stop_request()
        _log("Helper de contexto local iniciado.")
        try:
            while not self.stop_event.is_set() and not pc_context_runtime.helper_stop_requested():
                try:
                    status = capture_once()
                    self._update_status(status, icon=icon)
                    sleep_seconds = int(status.get("sample_interval_seconds") or HELPER_FALLBACK_SLEEP_SECONDS)
                    self._wait_for_next_tick(sleep_seconds)
                except Exception:
                    _log("Error en helper de contexto local:\n" + traceback.format_exc())
                    status = _status_payload("error", "Error inesperado en helper de contexto local.")
                    pc_context_runtime.write_helper_status(status)
                    self._update_status(status, icon=icon)
                    self._wait_for_next_tick(HELPER_FALLBACK_SLEEP_SECONDS)
        finally:
            pc_context_runtime.clear_helper_pid(os.getpid())
            pc_context_runtime.clear_helper_stop_request()
            pc_context_runtime.write_helper_status(
                _status_payload("sin helper", "Helper de contexto local detenido.")
            )
            _log("Helper de contexto local detenido.")


def _load_tray_modules():
    import pystray
    from PIL import Image, ImageDraw

    return pystray, Image, ImageDraw


def _make_icon_image(state: str):
    _pystray, Image, ImageDraw = _load_tray_modules()
    color = TRAY_STATE_COLORS.get(state, TRAY_STATE_COLORS["pausado"])
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=color + (255,), outline=(255, 255, 255, 255), width=4)
    draw.rectangle((28, 16, 36, 48), fill=(255, 255, 255, 255))
    draw.rectangle((16, 28, 48, 36), fill=(255, 255, 255, 255))
    return image


def _update_icon(icon, status: dict) -> None:
    state = str(status.get("state", "pausado")).strip() or "pausado"
    try:
        icon.icon = _make_icon_image(state)
        icon.title = f"Yarbis contexto local: {state}"
        icon.update_menu()
    except Exception:
        pass


def _run_with_tray() -> None:
    pystray, _Image, _ImageDraw = _load_tray_modules()
    controller = ContextTrayController()

    def open_yarbis(*_args):
        pc_context_runtime.open_desktop_app()

    def capture_now(*_args):
        controller.request_capture_now()

    def quit_helper(icon, *_args):
        controller.request_stop(icon)

    menu = pystray.Menu(
        pystray.MenuItem(lambda _item: controller.status_text(), lambda *_args: None, enabled=False),
        pystray.MenuItem("Abrir Yarbis", open_yarbis),
        pystray.MenuItem("Actualizar contexto ahora", capture_now),
        pystray.MenuItem("Salir", quit_helper),
    )
    icon = pystray.Icon(
        pc_context_runtime.TASK_NAME,
        _make_icon_image("pausado"),
        "Yarbis contexto local: pausado",
        menu,
    )
    icon.run(setup=lambda tray_icon: controller.start_worker(tray_icon))


def _run_headless() -> None:
    controller = ContextTrayController()
    controller.start_worker()
    while controller.worker_thread and controller.worker_thread.is_alive():
        controller.worker_thread.join(timeout=1.0)


def main() -> None:
    if not pc_context_runtime.acquire_helper_instance_lock():
        return
    try:
        try:
            _run_with_tray()
        except ImportError as exc:
            pc_context_runtime.write_helper_status(
                _status_payload("error", f"Faltan dependencias de bandeja: {exc}.")
            )
            _run_headless()
    finally:
        pc_context_runtime.release_helper_instance_lock()


if __name__ == "__main__":
    main()
