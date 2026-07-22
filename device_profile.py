"""Perfil del dispositivo donde corre Yarbis y sus capacidades efectivas.

Yarbis corre en Windows, Linux, macOS y Android (Termux). Este modulo detecta
QUE clase de dispositivo es y QUE puede hacer realmente ahi, para que el agente
no intente cosas imposibles (control de escritorio sin pantalla, voz sin audio,
navegador visible en un servidor, Ollama local en 2 GB de RAM).

Principio de diseno: **nunca escribe estado**. El `state.json` guarda la
intencion del usuario; este modulo dice que es posible AHORA en este equipo. Asi
la memoria se puede trasplantar entre maquinas y la adaptacion se recalcula sola
en vez de viajar apagada.

Es un modulo hoja: reutiliza `self_knowledge`/`pc_context` con imports perezosos
para no crear ciclos con `computer_control`/`voice`/`tools`/`agent`.
"""

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

CACHE_SECONDS = 120

_CACHE: dict = {"created_at": 0.0, "profile": None}

CLASS_WORKSTATION = "workstation"
CLASS_LAPTOP = "laptop"
CLASS_SERVER_HEADLESS = "server-headless"
CLASS_SBC = "sbc"
CLASS_CONTAINER = "container"
CLASS_VM = "vm"
CLASS_ANDROID_TERMUX = "android-termux"
# Para un SO que no reconocemos (incluso uno que aun no exista): en vez de asumir
# Windows, Yarbis prueba COMPORTAMIENTO (ver probe_capabilities) y sigue.
CLASS_UNKNOWN = "unknown"

_KNOWN_SYSTEMS = {"windows", "linux", "darwin"}

# Umbrales. Un equipo con menos de esto no deberia cargar un modelo local.
# 6 GB deja pasar equipos de 8 GB nominales (que reportan ~7.8 GB utiles).
LOW_MEMORY_BYTES = 2 * 1024**3
LOCAL_LLM_MIN_BYTES = 6 * 1024**3

# Capacidades que el resto del codigo consulta por nombre.
CAP_GUI = "gui"
CAP_DESKTOP_CONTROL = "desktop_control"
CAP_VISIBLE_BROWSER = "visible_browser"
CAP_BROWSER = "browser"
CAP_AUDIO_IN = "audio_in"
CAP_AUDIO_OUT = "audio_out"
CAP_LOCAL_LLM = "local_llm"


def _run(args: list[str], timeout: float = 3.0) -> str:
    """Ejecuta un comando y devuelve stdout; nunca lanza."""
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# Deteccion de entorno
# --------------------------------------------------------------------------- #

def is_termux() -> bool:
    """Android corriendo bajo Termux (userland Linux real con Python)."""
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return True
    if os.environ.get("TERMUX_VERSION"):
        return True
    if Path("/data/data/com.termux").exists():
        return True
    # ANDROID_ROOT solo aparece en Android; en un Linux normal no existe.
    return bool(os.environ.get("ANDROID_ROOT")) and platform.system().lower() == "linux"


def is_wsl() -> bool:
    if platform.system().lower() != "linux":
        return False
    return "microsoft" in _read_text("/proc/version").lower()


def is_container() -> bool:
    if platform.system().lower() != "linux":
        return False
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    cgroup = _read_text("/proc/1/cgroup").lower()
    return any(marker in cgroup for marker in ("docker", "lxc", "kubepods", "containerd"))


def detect_virtualization() -> str:
    """Devuelve el tipo de virtualizacion o '' si es fisico/desconocido."""
    if platform.system().lower() != "linux":
        return ""
    if not shutil.which("systemd-detect-virt"):
        return ""
    virt = _run(["systemd-detect-virt"]).strip().lower()
    if virt in ("", "none"):
        return ""
    return virt


def sbc_model() -> str:
    """Modelo de placa (Raspberry Pi y similares) o '' si no aplica."""
    if platform.system().lower() != "linux":
        return ""
    model = _read_text("/proc/device-tree/model").replace("\x00", "").strip()
    return model


def has_display() -> bool:
    """Si el equipo tiene un escritorio grafico usable AHORA."""
    system = platform.system().lower()
    if is_termux():
        return False
    if system == "windows":
        # Un servicio en la sesion 0 no puede pintar en el escritorio del usuario.
        try:
            from browser_automation import _running_in_session0

            if _running_in_session0():
                return False
        except Exception:
            pass
        return True
    if system == "darwin":
        # Sin consola (SSH puro) no hay Aqua disponible.
        return bool(os.environ.get("TERM_PROGRAM") or os.environ.get("Apple_PubSub_Socket_Render")) or not os.environ.get("SSH_CONNECTION")
    if system == "linux":
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return False


# --------------------------------------------------------------------------- #
# Hardware (reutiliza los modulos existentes con import perezoso)
# --------------------------------------------------------------------------- #

def _total_memory_bytes() -> int:
    try:
        from self_knowledge import _get_total_memory_bytes

        return int(_get_total_memory_bytes() or 0)
    except Exception:
        return 0


def _memory_status() -> dict:
    try:
        from pc_context import _get_memory_status

        status = _get_memory_status()
        return status if isinstance(status, dict) else {}
    except Exception:
        return {}


def _power_status() -> dict:
    try:
        from pc_context import _get_power_status

        status = _get_power_status()
        return status if isinstance(status, dict) else {}
    except Exception:
        return {}


def _has_gpu() -> bool:
    system = platform.system().lower()
    if system == "linux":
        if shutil.which("nvidia-smi") and _run(["nvidia-smi", "-L"]):
            return True
        return Path("/dev/dri").exists()
    if system == "darwin":
        return True  # todo Mac tiene GPU integrada utilizable
    if system == "windows":
        return True  # cualquier Windows con escritorio tiene GPU
    return False


# --------------------------------------------------------------------------- #
# Perfil + capacidades
# --------------------------------------------------------------------------- #

def _derive_device_class(signals: dict) -> str:
    if signals["termux"]:
        return CLASS_ANDROID_TERMUX
    if signals["container"]:
        return CLASS_CONTAINER
    if signals["sbc_model"]:
        return CLASS_SBC
    if not signals["display"]:
        return CLASS_SERVER_HEADLESS
    if signals["virtualization"]:
        return CLASS_VM
    if signals["battery_present"]:
        return CLASS_LAPTOP
    # SO no reconocido y con pantalla: no asumas Windows, marca desconocido.
    if not signals.get("known_system", True):
        return CLASS_UNKNOWN
    return CLASS_WORKSTATION


def _build_profile() -> dict:
    system = platform.system()
    termux = is_termux()
    container = is_container()
    display = has_display()
    power = _power_status()
    memory = _memory_status()
    total_memory = _total_memory_bytes()

    battery_present = bool(power.get("available")) and power.get("battery_percent") is not None
    on_battery = str(power.get("ac_line_status", "")).lower() == "battery"

    signals = {
        "termux": termux,
        "container": container,
        "wsl": is_wsl(),
        "virtualization": detect_virtualization(),
        "sbc_model": sbc_model(),
        "display": display,
        "battery_present": battery_present,
        "known_system": system.lower() in _KNOWN_SYSTEMS,
    }

    device_class = _derive_device_class(signals)
    headless = not display
    low_memory = bool(total_memory) and total_memory < LOW_MEMORY_BYTES
    # En un servidor Linux sin pantalla normalmente no hay audio utilizable.
    audio = not termux and not container and not (system.lower() == "linux" and headless)

    capabilities = {
        CAP_GUI: display,
        CAP_DESKTOP_CONTROL: display,
        CAP_VISIBLE_BROWSER: display,
        # Playwright no publica binarios de navegador para Android/Termux.
        CAP_BROWSER: not termux,
        CAP_AUDIO_IN: audio,
        CAP_AUDIO_OUT: audio,
        CAP_LOCAL_LLM: (
            not termux
            and not signals["sbc_model"]
            and bool(total_memory)
            and total_memory >= LOCAL_LLM_MIN_BYTES
        ),
    }

    return {
        "device_class": device_class,
        "system": system,
        "known_system": signals["known_system"],
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count() or 0,
        "total_memory_bytes": total_memory,
        "memory_load_percent": memory.get("load_percent"),
        "available_memory": memory.get("available_memory", ""),
        "has_gpu": _has_gpu(),
        "headless": headless,
        "termux": termux,
        "container": container,
        "wsl": signals["wsl"],
        "virtualization": signals["virtualization"],
        "sbc_model": signals["sbc_model"],
        "battery_present": battery_present,
        "on_battery": on_battery,
        "power_constrained": battery_present and on_battery,
        "low_memory": low_memory,
        "capabilities": capabilities,
    }


def get_profile(refresh: bool = False) -> dict:
    now = time.monotonic()
    cached = _CACHE.get("profile")
    if not refresh and cached and (now - float(_CACHE.get("created_at", 0.0))) < CACHE_SECONDS:
        return cached
    profile = _build_profile()
    _CACHE["profile"] = profile
    _CACHE["created_at"] = now
    return profile


_CAPABILITY_REASONS = {
    CAP_GUI: "este dispositivo no tiene un escritorio grafico disponible",
    CAP_DESKTOP_CONTROL: "no hay escritorio grafico que controlar en este dispositivo",
    CAP_VISIBLE_BROWSER: "no hay pantalla para mostrar un navegador visible",
    CAP_BROWSER: "no hay binarios de navegador para este dispositivo (Android/Termux)",
    CAP_AUDIO_IN: "este dispositivo no tiene entrada de audio utilizable",
    CAP_AUDIO_OUT: "este dispositivo no tiene salida de audio utilizable",
    CAP_LOCAL_LLM: "este dispositivo no tiene RAM suficiente para un modelo local",
}


def capability_allows(name: str) -> tuple[bool, str]:
    """(permitido, motivo). El motivo solo se llena cuando NO esta permitido."""
    profile = get_profile()
    allowed = bool(profile["capabilities"].get(name, False))
    if allowed:
        return True, ""
    reason = _CAPABILITY_REASONS.get(name, "no disponible en este dispositivo")
    return False, f"{reason} (perfil: {profile['device_class']})"


# --------------------------------------------------------------------------- #
# Sonda de comportamiento: prueba lo que el dispositivo PUEDE hacer, no como se
# llama su SO. Sirve para entornos desconocidos (incluso los que aun no existen):
# si Python corre ahi, estas pruebas responden. Es explicita (no se corre en cada
# get_profile) porque lanza un subproceso y, opcionalmente, toca la red.
# --------------------------------------------------------------------------- #

def _probe_write_files() -> bool:
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(prefix="yarbis-probe-", delete=True) as handle:
            handle.write(b"probe")
            handle.flush()
        return True
    except (OSError, ValueError):
        return False


def _probe_spawn_process() -> bool:
    import sys

    executable = sys.executable
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, "-c", "pass"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _probe_network(timeout: float = 2.5) -> bool:
    """Egress best-effort: intenta abrir un socket a endpoints publicos comunes.

    No envia datos ni identifica al usuario; solo comprueba si hay salida. Ante la
    minima duda (bloqueo, error) devuelve False sin ruido.
    """
    import socket

    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _service_manager_label() -> str:
    if os.name == "nt":
        return "scm"
    try:
        from native_service import service_manager_kind

        return service_manager_kind()
    except Exception:
        return "none"


def probe_capabilities(check_network: bool = True) -> dict:
    """Prueba de comportamiento del entorno actual, agnostica al nombre del SO."""
    probes = {
        "can_write_files": _probe_write_files(),
        "can_spawn_process": _probe_spawn_process(),
        "service_manager": _service_manager_label(),
    }
    if check_network:
        probes["can_network"] = _probe_network()
    return probes


def render_probe_summary(probes: dict | None = None) -> str:
    probes = probes if probes is not None else probe_capabilities()
    partes = []
    if "can_write_files" in probes:
        partes.append("archivos:" + ("si" if probes["can_write_files"] else "no"))
    if "can_spawn_process" in probes:
        partes.append("procesos:" + ("si" if probes["can_spawn_process"] else "no"))
    if "can_network" in probes:
        partes.append("red:" + ("si" if probes["can_network"] else "no"))
    if probes.get("service_manager"):
        partes.append(f"servicio:{probes['service_manager']}")
    return "Sonda del entorno -> " + ", ".join(partes) if partes else "Sonda del entorno sin datos."


def _format_bytes(value: int) -> str:
    if not value:
        return "desconocida"
    gigabytes = value / (1024**3)
    if gigabytes >= 1:
        return f"{gigabytes:.1f} GB"
    return f"{value / (1024**2):.0f} MB"


def render_profile_summary(profile: dict | None = None) -> str:
    """Linea compacta de dispositivo + capacidades, para el prompt y las tools."""
    profile = profile or get_profile()
    caps = profile["capabilities"]
    # local_llm es consultivo (alimenta sugerencias), no una limitacion dura:
    # no debe leerse como "no puedo hacerlo" en el prompt del agente.
    unavailable = [
        name for name, ok in caps.items() if not ok and name != CAP_LOCAL_LLM
    ]

    etiquetas = {
        CAP_GUI: "escritorio",
        CAP_DESKTOP_CONTROL: "control de escritorio",
        CAP_VISIBLE_BROWSER: "navegador visible",
        CAP_BROWSER: "navegador",
        CAP_AUDIO_IN: "microfono",
        CAP_AUDIO_OUT: "voz hablada",
    }

    partes = [
        f"{profile['device_class']}",
        f"{profile['system']} {profile['machine']}".strip(),
        f"RAM {_format_bytes(profile['total_memory_bytes'])}",
    ]
    if profile["headless"]:
        partes.append("sin pantalla")
    if profile["power_constrained"]:
        partes.append("en bateria")
    if profile["container"]:
        partes.append("contenedor")
    if profile["wsl"]:
        partes.append("WSL")
    if profile["termux"]:
        partes.append("Android/Termux")

    linea = f"Dispositivo: {', '.join(partes)}."
    if unavailable:
        faltan = ", ".join(etiquetas.get(name, name) for name in unavailable)
        linea += f" No disponible aqui: {faltan}."
    return linea
