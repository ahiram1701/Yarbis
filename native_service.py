"""Servicios nativos en Linux (systemd, ambito --user) y macOS (launchd,
LaunchAgent), equivalentes al gestor SCM de Windows (service_manager.py).

Windows NO usa este modulo: alli sigue mandando service_manager (SCM), byte
identico. En Linux/macOS instala, arranca, detiene, habilita y consulta un
servicio por instancia que corre `<python> yarbis_service.py` con la variable
YARBIS_INSTANCE fijada, replicando lo que hace el host .NET/SCM en Windows.

Reutiliza el mismo shape de dict que service_manager.get_service_status para que
la UI (TUI) y las tools puedan tratar ambos backends igual. La frontera con el
sistema es la funcion `_run`, que los tests mockean para no tocar systemd/launchd
reales.
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import yarbis_instance

WORKSPACE_ROOT = Path(__file__).resolve().parent
SERVICE_SCRIPT = WORKSPACE_ROOT / "yarbis_service.py"

MANAGER_SYSTEMD = "systemd"
MANAGER_LAUNCHD = "launchd"
MANAGER_TERMUX = "termux"
MANAGER_NONE = "none"

_RUN_TIMEOUT_SECONDS = 30


class NativeServiceError(RuntimeError):
    """Error al operar el servicio nativo (systemd/launchd)."""


def _run(args: list[str], timeout_seconds: int = _RUN_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Ejecuta un comando y captura salida. Frontera unica con el SO (mockeable)."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


def _is_termux() -> bool:
    """Android/Termux: es Linux, pero sin systemd."""
    try:
        import device_profile

        return device_profile.is_termux()
    except Exception:
        import os

        return "com.termux" in os.environ.get("PREFIX", "")


def service_manager_kind() -> str:
    """Gestor de servicios del dispositivo: systemd, launchd, termux o none."""
    system = platform.system().lower()
    if system == "linux":
        # Android/Termux no tiene systemd: usa runit (termux-services).
        return MANAGER_TERMUX if _is_termux() else MANAGER_SYSTEMD
    if system == "darwin":
        return MANAGER_LAUNCHD
    return MANAGER_NONE


def service_available() -> bool:
    """True si el gestor nativo esta disponible en el PATH."""
    kind = service_manager_kind()
    if kind == MANAGER_SYSTEMD:
        return shutil.which("systemctl") is not None
    if kind == MANAGER_LAUNCHD:
        return shutil.which("launchctl") is not None
    if kind == MANAGER_TERMUX:
        # Con termux-services (runit) hay gestion real; sin el, proceso plano.
        return True
    return False


def _python_executable() -> str:
    """Interprete a usar en el servicio: prioriza el venv del proyecto."""
    for candidate in (
        WORKSPACE_ROOT / ".venv" / "bin" / "python",
        WORKSPACE_ROOT / ".venv" / "bin" / "python3",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python3"


def _normalized_id(instance_id: object | None = None) -> str:
    raw = yarbis_instance.current_instance_id() if instance_id is None else instance_id
    return yarbis_instance.normalize_instance_id(raw)


def _display_name(instance_id: object | None = None) -> str:
    return yarbis_instance.service_display_name(instance_id)


def _description(instance_id: object | None = None) -> str:
    return yarbis_instance.service_description(instance_id)


def _log_file(instance_id: object | None = None) -> Path:
    return yarbis_instance.runtime_dir(instance_id) / "service.log"


def _service_binary(instance_id: object | None = None) -> str:
    return f"{_python_executable()} {SERVICE_SCRIPT}"


def _base_status(instance_id: object | None = None) -> dict:
    kind = service_manager_kind()
    return {
        "service_name": _unit_identifier(instance_id),
        "display_name": _display_name(instance_id),
        "installed": False,
        "running": False,
        "state": "not_installed",
        "pid": None,
        "autostart_enabled": False,
        "start_type": "not_installed",
        "account_name": "",
        "log_file": str(_log_file(instance_id)),
        "service_binary": _service_binary(instance_id),
        "configured_binary": "",
        "workspace_mismatch": False,
        "manager": kind,
        "unit_file": str(_unit_path(instance_id)) if kind != MANAGER_NONE else "",
    }


def _unit_identifier(instance_id: object | None = None) -> str:
    kind = service_manager_kind()
    if kind == MANAGER_SYSTEMD:
        return f"{_systemd_unit_stem(instance_id)}.service"
    if kind == MANAGER_LAUNCHD:
        return _launchd_label(instance_id)
    if kind == MANAGER_TERMUX:
        return _termux_service_name(instance_id)
    return yarbis_instance.service_name(instance_id)


def _unit_path(instance_id: object | None = None) -> Path:
    kind = service_manager_kind()
    if kind == MANAGER_SYSTEMD:
        return _systemd_unit_path(instance_id)
    if kind == MANAGER_LAUNCHD:
        return _launchd_plist_path(instance_id)
    if kind == MANAGER_TERMUX:
        return _termux_run_script_path(instance_id)
    return Path()


# --------------------------------------------------------------------------- #
# systemd (Linux, ambito --user)
# --------------------------------------------------------------------------- #

def _systemd_unit_stem(instance_id: object | None = None) -> str:
    normalized = _normalized_id(instance_id)
    if normalized == yarbis_instance.DEFAULT_INSTANCE_ID:
        return "yarbis"
    return f"yarbis-{normalized}"


def _systemd_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _systemd_unit_path(instance_id: object | None = None) -> Path:
    return _systemd_unit_dir() / f"{_systemd_unit_stem(instance_id)}.service"


def _systemd_unit_text(instance_id: object | None = None) -> str:
    normalized = _normalized_id(instance_id)
    return (
        "[Unit]\n"
        f"Description={_description(instance_id)}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={WORKSPACE_ROOT}\n"
        f"Environment=YARBIS_INSTANCE={normalized}\n"
        f"ExecStart={_python_executable()} {SERVICE_SCRIPT}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemctl(args: list[str], timeout_seconds: int = _RUN_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return _run(["systemctl", "--user", *args], timeout_seconds)


def _systemd_status(instance_id: object | None = None) -> dict:
    status = _base_status(instance_id)
    unit = f"{_systemd_unit_stem(instance_id)}.service"
    unit_exists = _systemd_unit_path(instance_id).exists()

    completed = _systemctl(["show", unit, "-p", "ActiveState,MainPID,UnitFileState,LoadState"])
    props: dict[str, str] = {}
    for line in (completed.stdout or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()

    load_state = props.get("LoadState", "")
    active_state = props.get("ActiveState", "")
    unit_file_state = props.get("UnitFileState", "")
    main_pid = props.get("MainPID", "0")

    installed = unit_exists or load_state == "loaded"
    running = active_state == "active"
    try:
        pid = int(main_pid)
    except (TypeError, ValueError):
        pid = 0

    status.update({
        "installed": installed,
        "running": running,
        "state": active_state or ("stopped" if installed else "not_installed"),
        "pid": pid if (running and pid > 0) else None,
        "autostart_enabled": unit_file_state == "enabled",
        "start_type": unit_file_state or "not_installed",
        "configured_binary": _service_binary(instance_id) if installed else "",
    })
    return status


def _systemd_install(instance_id: object | None, start_auto: bool) -> str:
    unit_path = _systemd_unit_path(instance_id)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(_systemd_unit_text(instance_id), encoding="utf-8")

    reload_result = _systemctl(["daemon-reload"])
    if reload_result.returncode != 0:
        raise NativeServiceError(_fail_text("recargar systemd", reload_result))

    unit = f"{_systemd_unit_stem(instance_id)}.service"
    if start_auto:
        enabled = _systemctl(["enable", unit])
        if enabled.returncode != 0:
            raise NativeServiceError(_fail_text("habilitar el servicio de Yarbis", enabled))

    linger_note = ""
    if start_auto:
        linger_note = (
            " Para que arranque sin tu sesion iniciada, habilita el lingering: "
            "`sudo loginctl enable-linger $USER`."
        )
    modo = "arranque automatico" if start_auto else "arranque manual"
    return f"Servicio de Yarbis instalado en systemd (--user) con {modo}.{linger_note}"


def _systemd_start(instance_id: object | None) -> str:
    unit = f"{_systemd_unit_stem(instance_id)}.service"
    started = _systemctl(["start", unit], timeout_seconds=45)
    if started.returncode != 0:
        raise NativeServiceError(_fail_text("iniciar el servicio de Yarbis", started))
    refreshed = _systemd_status(instance_id)
    if refreshed["running"]:
        return f"Servicio de Yarbis iniciado por systemd (PID {refreshed['pid']})."
    return "systemd recibio la orden de inicio, pero no pude confirmar que Yarbis quedara activo."


def _systemd_stop(instance_id: object | None) -> str:
    unit = f"{_systemd_unit_stem(instance_id)}.service"
    stopped = _systemctl(["stop", unit], timeout_seconds=45)
    if stopped.returncode != 0:
        raise NativeServiceError(_fail_text("detener el servicio de Yarbis", stopped))
    return "Servicio de Yarbis detenido por systemd."


def _systemd_remove(instance_id: object | None) -> str:
    unit = f"{_systemd_unit_stem(instance_id)}.service"
    _systemctl(["disable", unit])
    _systemctl(["stop", unit])
    unit_path = _systemd_unit_path(instance_id)
    try:
        unit_path.unlink()
    except FileNotFoundError:
        pass
    _systemctl(["daemon-reload"])
    return "Servicio de Yarbis quitado de systemd."


def _systemd_set_autostart(instance_id: object | None, enabled: bool) -> str:
    unit = f"{_systemd_unit_stem(instance_id)}.service"
    action = "enable" if enabled else "disable"
    result = _systemctl([action, unit])
    if result.returncode != 0:
        raise NativeServiceError(_fail_text("cambiar el arranque del servicio de Yarbis", result))
    if enabled:
        return (
            "Yarbis quedo configurado para iniciar automaticamente con tu sesion (systemd --user). "
            "Para que arranque sin sesion iniciada: `sudo loginctl enable-linger $USER`."
        )
    return "Yarbis ya no arrancara automaticamente (systemd --user)."


# --------------------------------------------------------------------------- #
# launchd (macOS, LaunchAgent del usuario)
# --------------------------------------------------------------------------- #

def _launchd_label(instance_id: object | None = None) -> str:
    return f"org.yarbis.{_normalized_id(instance_id)}"


def _launchd_plist_path(instance_id: object | None = None) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_launchd_label(instance_id)}.plist"


def _launchd_plist_text(instance_id: object | None, start_auto: bool) -> str:
    normalized = _normalized_id(instance_id)
    runtime = yarbis_instance.runtime_dir(instance_id)
    run_at_load = "<true/>" if start_auto else "<false/>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{_launchd_label(instance_id)}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{_python_executable()}</string>\n"
        f"        <string>{SERVICE_SCRIPT}</string>\n"
        "    </array>\n"
        "    <key>EnvironmentVariables</key>\n"
        "    <dict>\n"
        "        <key>YARBIS_INSTANCE</key>\n"
        f"        <string>{normalized}</string>\n"
        "    </dict>\n"
        "    <key>WorkingDirectory</key>\n"
        f"    <string>{WORKSPACE_ROOT}</string>\n"
        "    <key>RunAtLoad</key>\n"
        f"    {run_at_load}\n"
        "    <key>KeepAlive</key>\n"
        "    <dict>\n"
        "        <key>Crashed</key>\n"
        "        <true/>\n"
        "    </dict>\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{runtime / 'launchd.out.log'}</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{runtime / 'launchd.err.log'}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _launchctl(args: list[str], timeout_seconds: int = _RUN_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return _run(["launchctl", *args], timeout_seconds)


def _launchd_plist_run_at_load(instance_id: object | None) -> bool:
    """Lee RunAtLoad del plist en disco (autostart) sin depender de launchctl."""
    path = _launchd_plist_path(instance_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    marker = "<key>RunAtLoad</key>"
    idx = text.find(marker)
    if idx == -1:
        return False
    return "<true/>" in text[idx: idx + len(marker) + 40]


def _launchd_status(instance_id: object | None = None) -> dict:
    status = _base_status(instance_id)
    plist_exists = _launchd_plist_path(instance_id).exists()
    label = _launchd_label(instance_id)

    listing = _launchctl(["list", label])
    loaded = listing.returncode == 0
    pid = None
    if loaded:
        for line in (listing.stdout or "").splitlines():
            stripped = line.strip()
            if stripped.startswith('"PID"'):
                _, _, value = stripped.partition("=")
                digits = value.strip().strip(";").strip()
                if digits.isdigit():
                    pid = int(digits)
                break

    installed = plist_exists or loaded
    running = pid is not None and pid > 0
    autostart = _launchd_plist_run_at_load(instance_id)
    status.update({
        "installed": installed,
        "running": running,
        "state": "running" if running else ("stopped" if installed else "not_installed"),
        "pid": pid if running else None,
        "autostart_enabled": autostart,
        "start_type": "auto_start" if autostart else ("demand" if installed else "not_installed"),
        "configured_binary": _service_binary(instance_id) if installed else "",
    })
    return status


def _launchd_install(instance_id: object | None, start_auto: bool) -> str:
    plist_path = _launchd_plist_path(instance_id)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(_launchd_plist_text(instance_id, start_auto), encoding="utf-8")

    # -w marca el agente como habilitado (arranca al iniciar sesion si RunAtLoad).
    loaded = _launchctl(["load", "-w", str(plist_path)])
    if loaded.returncode != 0:
        raise NativeServiceError(_fail_text("cargar el servicio de Yarbis en launchd", loaded))
    modo = "arranque automatico" if start_auto else "arranque manual"
    return f"Servicio de Yarbis instalado en launchd (LaunchAgent) con {modo}."


def _launchd_start(instance_id: object | None) -> str:
    label = _launchd_label(instance_id)
    started = _launchctl(["start", label])
    if started.returncode != 0:
        raise NativeServiceError(_fail_text("iniciar el servicio de Yarbis", started))
    refreshed = _launchd_status(instance_id)
    if refreshed["running"]:
        return f"Servicio de Yarbis iniciado por launchd (PID {refreshed['pid']})."
    return "launchd recibio la orden de inicio; puede tardar un momento en confirmar el proceso."


def _launchd_stop(instance_id: object | None) -> str:
    label = _launchd_label(instance_id)
    stopped = _launchctl(["stop", label])
    if stopped.returncode != 0:
        raise NativeServiceError(_fail_text("detener el servicio de Yarbis", stopped))
    return "Servicio de Yarbis detenido por launchd."


def _launchd_remove(instance_id: object | None) -> str:
    plist_path = _launchd_plist_path(instance_id)
    _launchctl(["unload", "-w", str(plist_path)])
    try:
        plist_path.unlink()
    except FileNotFoundError:
        pass
    return "Servicio de Yarbis quitado de launchd."


def _launchd_set_autostart(instance_id: object | None, enabled: bool) -> str:
    plist_path = _launchd_plist_path(instance_id)
    if not plist_path.exists():
        return _launchd_install(instance_id, enabled)
    # Reescribe el plist con el nuevo RunAtLoad y recarga.
    _launchctl(["unload", str(plist_path)])
    plist_path.write_text(_launchd_plist_text(instance_id, enabled), encoding="utf-8")
    reloaded = _launchctl(["load", "-w", str(plist_path)])
    if reloaded.returncode != 0:
        raise NativeServiceError(_fail_text("recargar el servicio de Yarbis", reloaded))
    if enabled:
        return "Yarbis quedo configurado para iniciar automaticamente al iniciar sesion (launchd)."
    return "Yarbis ya no arrancara automaticamente (launchd)."


# --------------------------------------------------------------------------- #
# Termux (Android): runit via termux-services, o proceso plano con wake lock
# --------------------------------------------------------------------------- #

def _termux_prefix() -> Path:
    import os

    prefix = os.environ.get("PREFIX", "")
    if prefix:
        return Path(prefix)
    return Path("/data/data/com.termux/files/usr")


def _termux_service_name(instance_id: object | None = None) -> str:
    return _systemd_unit_stem(instance_id)  # yarbis / yarbis-<id>


def _termux_service_dir(instance_id: object | None = None) -> Path:
    return _termux_prefix() / "var" / "service" / _termux_service_name(instance_id)


def _termux_run_script_path(instance_id: object | None = None) -> Path:
    return _termux_service_dir(instance_id) / "run"


def _termux_has_runit() -> bool:
    return shutil.which("sv") is not None


def _termux_run_script_text(instance_id: object | None = None) -> str:
    """Script `run` de runit. termux-wake-lock evita que Android mate el proceso."""
    normalized = _normalized_id(instance_id)
    wake = "termux-wake-lock 2>/dev/null || true"
    return (
        "#!/data/data/com.termux/files/usr/bin/sh\n"
        f"cd {WORKSPACE_ROOT}\n"
        f"export YARBIS_INSTANCE={normalized}\n"
        f"{wake}\n"
        f"exec {_python_executable()} {SERVICE_SCRIPT} 2>&1\n"
    )


def _termux_pid_file(instance_id: object | None = None) -> Path:
    return yarbis_instance.runtime_dir(instance_id) / "service.pid"


def _pid_alive(pid: int) -> bool:
    import os

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError):
        return False
    return True


def _termux_running_pid(instance_id: object | None = None) -> int | None:
    """PID vivo del servicio, leyendo el service.pid que escribe yarbis_service."""
    try:
        raw = _termux_pid_file(instance_id).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    pid = int(raw)
    return pid if _pid_alive(pid) else None


def _termux_status(instance_id: object | None = None) -> dict:
    status = _base_status(instance_id)
    name = _termux_service_name(instance_id)
    script_exists = _termux_run_script_path(instance_id).exists()

    pid = _termux_running_pid(instance_id)
    autostart = False
    if _termux_has_runit() and script_exists:
        # runit: el enlace en var/service ya implica arranque al iniciar el daemon.
        autostart = True
        listing = _run(["sv", "status", name])
        out = (listing.stdout or "").strip()
        if out.startswith("run:") and pid is None:
            import re as _re

            match = _re.search(r"\(pid (\d+)\)", out)
            if match:
                candidate = int(match.group(1))
                pid = candidate if _pid_alive(candidate) else None

    running = pid is not None
    status.update({
        "installed": script_exists,
        "running": running,
        "state": "running" if running else ("stopped" if script_exists else "not_installed"),
        "pid": pid,
        "autostart_enabled": autostart,
        "start_type": "auto_start" if autostart else ("demand" if script_exists else "not_installed"),
        "configured_binary": _service_binary(instance_id) if script_exists else "",
    })
    return status


def _termux_install(instance_id: object | None, start_auto: bool) -> str:
    script = _termux_run_script_path(instance_id)
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_termux_run_script_text(instance_id), encoding="utf-8")
    try:
        script.chmod(0o755)
    except OSError:
        pass

    if not _termux_has_runit():
        return (
            "Servicio de Yarbis preparado en Termux (script runit escrito), pero no "
            "encontre `sv`. Instala termux-services para gestion real "
            "(`pkg install termux-services`) o usa start/stop, que corren el proceso "
            "directo con wake lock."
        )

    if start_auto and shutil.which("sv-enable"):
        enabled = _run(["sv-enable", _termux_service_name(instance_id)])
        if enabled.returncode != 0:
            raise NativeServiceError(_fail_text("habilitar el servicio de Yarbis", enabled))
    modo = "arranque automatico" if start_auto else "arranque manual"
    return f"Servicio de Yarbis instalado en Termux (runit) con {modo}."


def _termux_start(instance_id: object | None) -> str:
    name = _termux_service_name(instance_id)
    if _termux_has_runit():
        started = _run(["sv", "up", name], timeout_seconds=45)
        if started.returncode != 0:
            raise NativeServiceError(_fail_text("iniciar el servicio de Yarbis", started))
        pid = _termux_running_pid(instance_id)
        if pid:
            return f"Servicio de Yarbis iniciado en Termux (runit, PID {pid})."
        return "runit recibio la orden de inicio; puede tardar un momento en confirmar el proceso."

    # Sin runit: proceso plano con wake lock, en segundo plano.
    import os
    import subprocess as _sp

    if shutil.which("termux-wake-lock"):
        _run(["termux-wake-lock"])
    env = dict(os.environ)
    env["YARBIS_INSTANCE"] = _normalized_id(instance_id)
    try:
        _sp.Popen(
            [_python_executable(), str(SERVICE_SCRIPT)],
            cwd=str(WORKSPACE_ROOT),
            env=env,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise NativeServiceError(f"No pude iniciar el servicio de Yarbis: {exc}") from exc
    return "Servicio de Yarbis iniciado en Termux como proceso con wake lock."


def _termux_stop(instance_id: object | None) -> str:
    name = _termux_service_name(instance_id)
    if _termux_has_runit():
        stopped = _run(["sv", "down", name], timeout_seconds=45)
        if stopped.returncode != 0:
            raise NativeServiceError(_fail_text("detener el servicio de Yarbis", stopped))
        return "Servicio de Yarbis detenido en Termux (runit)."

    import os
    import signal

    pid = _termux_running_pid(instance_id)
    if pid is None:
        return "El servicio de Yarbis ya estaba detenido."
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise NativeServiceError(f"No pude detener el servicio de Yarbis: {exc}") from exc
    if shutil.which("termux-wake-unlock"):
        _run(["termux-wake-unlock"])
    return "Servicio de Yarbis detenido en Termux."


def _termux_remove(instance_id: object | None) -> str:
    name = _termux_service_name(instance_id)
    if _termux_has_runit() and shutil.which("sv-disable"):
        _run(["sv-disable", name])
    try:
        _termux_run_script_path(instance_id).unlink()
    except FileNotFoundError:
        pass
    try:
        _termux_service_dir(instance_id).rmdir()
    except OSError:
        pass
    return "Servicio de Yarbis quitado de Termux."


def _termux_set_autostart(instance_id: object | None, enabled: bool) -> str:
    name = _termux_service_name(instance_id)
    if not _termux_has_runit():
        return (
            "En Termux sin termux-services no hay arranque automatico. "
            "Instala `termux-services` (`pkg install termux-services`) para habilitarlo."
        )
    tool = "sv-enable" if enabled else "sv-disable"
    if not shutil.which(tool):
        raise NativeServiceError(f"No encontre `{tool}` en Termux.")
    result = _run([tool, name])
    if result.returncode != 0:
        raise NativeServiceError(_fail_text("cambiar el arranque del servicio de Yarbis", result))
    if enabled:
        return "Yarbis quedo configurado para arrancar con el daemon de runit en Termux."
    return "Yarbis ya no arrancara automaticamente en Termux."


# --------------------------------------------------------------------------- #
# Fachada publica (dispatch por gestor)
# --------------------------------------------------------------------------- #

def _fail_text(action: str, completed: subprocess.CompletedProcess) -> str:
    details = (completed.stderr or completed.stdout or "").strip()
    if details:
        return f"No pude {action}: {details}"
    return f"No pude {action} (exit={completed.returncode})."


def _require_available() -> str:
    kind = service_manager_kind()
    if kind == MANAGER_NONE:
        raise NativeServiceError(
            "Este SO no usa systemd, launchd ni Termux. En Windows usa el gestor de servicios (SCM)."
        )
    if not service_available():
        tool = "systemctl" if kind == MANAGER_SYSTEMD else "launchctl"
        raise NativeServiceError(f"No encontre `{tool}` en el sistema; no puedo gestionar el servicio nativo.")
    return kind


def get_service_status(instance_id: object | None = None) -> dict:
    kind = service_manager_kind()
    if kind == MANAGER_SYSTEMD and service_available():
        return _systemd_status(instance_id)
    if kind == MANAGER_LAUNCHD and service_available():
        return _launchd_status(instance_id)
    if kind == MANAGER_TERMUX:
        return _termux_status(instance_id)
    return _base_status(instance_id)


def is_service_running(instance_id: object | None = None) -> bool:
    return bool(get_service_status(instance_id)["running"])


def install_service(start_auto: bool = True, instance_id: object | None = None) -> str:
    kind = _require_available()
    if not SERVICE_SCRIPT.exists():
        raise NativeServiceError("No encontre yarbis_service.py para instalar el servicio.")
    if kind == MANAGER_SYSTEMD:
        return _systemd_install(instance_id, start_auto)
    if kind == MANAGER_TERMUX:
        return _termux_install(instance_id, start_auto)
    return _launchd_install(instance_id, start_auto)


def start_service(instance_id: object | None = None) -> str:
    kind = _require_available()
    status = get_service_status(instance_id)
    if not status["installed"]:
        install_service(start_auto=False, instance_id=instance_id)
    if get_service_status(instance_id)["running"]:
        return "El servicio de Yarbis ya estaba activo."
    if kind == MANAGER_SYSTEMD:
        return _systemd_start(instance_id)
    if kind == MANAGER_TERMUX:
        return _termux_start(instance_id)
    return _launchd_start(instance_id)


def stop_service(instance_id: object | None = None) -> str:
    kind = _require_available()
    status = get_service_status(instance_id)
    if not status["installed"]:
        return "El servicio de Yarbis no esta instalado."
    if not status["running"]:
        return "El servicio de Yarbis ya estaba detenido."
    if kind == MANAGER_SYSTEMD:
        return _systemd_stop(instance_id)
    if kind == MANAGER_TERMUX:
        return _termux_stop(instance_id)
    return _launchd_stop(instance_id)


def remove_service(instance_id: object | None = None) -> str:
    kind = _require_available()
    if not get_service_status(instance_id)["installed"]:
        return "El servicio de Yarbis no esta instalado."
    if kind == MANAGER_SYSTEMD:
        return _systemd_remove(instance_id)
    if kind == MANAGER_TERMUX:
        return _termux_remove(instance_id)
    return _launchd_remove(instance_id)


def set_autostart_enabled(enabled: bool, instance_id: object | None = None) -> str:
    kind = _require_available()
    status = get_service_status(instance_id)
    if not status["installed"]:
        return install_service(start_auto=bool(enabled), instance_id=instance_id)
    if kind == MANAGER_SYSTEMD:
        return _systemd_set_autostart(instance_id, bool(enabled))
    if kind == MANAGER_TERMUX:
        return _termux_set_autostart(instance_id, bool(enabled))
    return _launchd_set_autostart(instance_id, bool(enabled))
