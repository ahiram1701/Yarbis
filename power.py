import os
import subprocess
import sys


DEFAULT_SHUTDOWN_DELAY_SECONDS = 60
MAX_SHUTDOWN_DELAY_SECONDS = 3600
SHUTDOWN_COMMENT = "Apagado solicitado por Yarbis desde Telegram."
RESTART_COMMENT = "Reinicio solicitado por Yarbis desde Telegram."


def _shutdown_executable() -> str:
    system_root = os.getenv("SystemRoot", r"C:\Windows")
    candidate = os.path.join(system_root, "System32", "shutdown.exe")
    if os.path.exists(candidate):
        return candidate
    return "shutdown.exe"


def _run_shutdown_command(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )


def normalize_shutdown_delay(delay_seconds: int | str | None = None) -> int:
    if delay_seconds is None or str(delay_seconds).strip() == "":
        return DEFAULT_SHUTDOWN_DELAY_SECONDS

    delay = int(delay_seconds)
    return max(0, min(MAX_SHUTDOWN_DELAY_SECONDS, delay))


def request_system_shutdown(delay_seconds: int | str | None = None) -> str:
    if sys.platform != "win32":
        return "El apagado remoto solo esta disponible en Windows."

    try:
        delay = normalize_shutdown_delay(delay_seconds)
    except (TypeError, ValueError):
        return "No pude programar el apagado: el tiempo indicado no es valido."

    command = [
        _shutdown_executable(),
        "/s",
        "/t",
        str(delay),
        "/c",
        SHUTDOWN_COMMENT,
    ]
    try:
        completed = _run_shutdown_command(command)
    except subprocess.TimeoutExpired:
        return "No pude programar el apagado: shutdown.exe no respondio a tiempo."
    except OSError as exc:
        return f"No pude programar el apagado: {exc}"

    if completed.returncode == 0:
        if delay == 0:
            return "Apagado iniciado. Windows comenzara a cerrar la sesion."
        return (
            f"Apagado programado en {delay} segundo(s). "
            "Puedes cancelarlo con /cancelar_apagado."
        )

    details = (completed.stderr or completed.stdout or "").strip()
    if details:
        return f"No pude programar el apagado: {details}"
    return f"No pude programar el apagado (exit={completed.returncode})."


def request_system_restart(delay_seconds: int | str | None = None) -> str:
    if sys.platform != "win32":
        return "El reinicio remoto solo esta disponible en Windows."

    try:
        delay = normalize_shutdown_delay(delay_seconds)
    except (TypeError, ValueError):
        return "No pude programar el reinicio: el tiempo indicado no es valido."

    command = [
        _shutdown_executable(),
        "/r",
        "/t",
        str(delay),
        "/c",
        RESTART_COMMENT,
    ]
    try:
        completed = _run_shutdown_command(command)
    except subprocess.TimeoutExpired:
        return "No pude programar el reinicio: shutdown.exe no respondio a tiempo."
    except OSError as exc:
        return f"No pude programar el reinicio: {exc}"

    if completed.returncode == 0:
        if delay == 0:
            return "Reinicio iniciado. Windows comenzara a cerrar la sesion."
        return (
            f"Reinicio programado en {delay} segundo(s). "
            "Puedes cancelarlo con /cancelar_reinicio."
        )

    details = (completed.stderr or completed.stdout or "").strip()
    if details:
        return f"No pude programar el reinicio: {details}"
    return f"No pude programar el reinicio (exit={completed.returncode})."


def _normalize_power_action_label(action_label: str = "apagado") -> str:
    cleaned = str(action_label).strip().lower()
    if cleaned in {"reinicio", "restart", "reboot"}:
        return "reinicio"
    return "apagado"


def _looks_like_no_pending_shutdown(details: str) -> bool:
    normalized = str(details).strip().lower()
    return "1116" in normalized or "no se puede anular" in normalized


def cancel_system_shutdown(action_label: str = "apagado") -> str:
    label = _normalize_power_action_label(action_label)
    if sys.platform != "win32":
        return f"La cancelacion de {label} solo esta disponible en Windows."

    try:
        completed = _run_shutdown_command([_shutdown_executable(), "/a"])
    except subprocess.TimeoutExpired:
        return f"No pude cancelar el {label}: shutdown.exe no respondio a tiempo."
    except OSError as exc:
        return f"No pude cancelar el {label}: {exc}"

    if completed.returncode == 0:
        return f"{label.capitalize()} cancelado."

    details = (completed.stderr or completed.stdout or "").strip()
    if _looks_like_no_pending_shutdown(details):
        return f"No habia ningun {label} programado."
    if details:
        return f"No pude cancelar el {label}. Puede que no hubiera uno programado: {details}"
    return (
        f"No pude cancelar el {label}. "
        f"Puede que no hubiera uno programado (exit={completed.returncode})."
    )
