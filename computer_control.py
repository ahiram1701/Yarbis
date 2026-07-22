"""Control del sistema operativo (mouse, teclado, captura de pantalla).

Modulo hoja usado por los tools desktop_* de tools.py. Requiere pyautogui.
El control es potente: la habilitacion (computer_control.enabled) y las
confirmaciones de acciones sensibles se aplican en la capa de tools/estado.
"""

import re
import time
from pathlib import Path

import yarbis_instance

DESKTOP_RUNTIME_DIR = yarbis_instance.runtime_dir() / "desktop"
_SENSITIVE_LABEL = re.compile(
    r"\b(publicar|publish|compartir|share|enviar|send|pagar|pay|comprar|buy|order|"
    r"eliminar|delete|borrar|desactivar|confirmar\s+pago)\b",
    re.IGNORECASE,
)


class ComputerControlError(RuntimeError):
    pass


def _pyautogui():
    # Punto unico de todos los tools desktop_*: si el dispositivo no tiene
    # escritorio (servidor headless, contenedor, Android/Termux, servicio en la
    # sesion 0), fallar aqui con el motivo real en vez de un error opaco de X11.
    try:
        import device_profile

        allowed, reason = device_profile.capability_allows(device_profile.CAP_DESKTOP_CONTROL)
    except Exception:
        allowed, reason = True, ""
    if not allowed:
        raise ComputerControlError(f"No puedo controlar el escritorio: {reason}.")

    try:
        import pyautogui
    except Exception as exc:  # pragma: no cover - depende del entorno
        raise ComputerControlError(
            "Falta pyautogui. Instala dependencias con python -m pip install -r requirements.txt."
        ) from exc
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


def screen_size() -> tuple[int, int]:
    pg = _pyautogui()
    size = pg.size()
    return int(size[0]), int(size[1])


def take_screenshot(path: str = "") -> Path:
    pg = _pyautogui()
    DESKTOP_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    target = Path(path).expanduser() if str(path).strip() else DESKTOP_RUNTIME_DIR / f"screen-{int(time.time())}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image = pg.screenshot()
    image.save(str(target))
    return target


def look(prompt: str = "", settings: dict | None = None) -> str:
    """Captura la pantalla y la describe con el modelo de vision."""
    shot = take_screenshot()
    try:
        import vision

        question = str(prompt or "").strip() or "Describe la pantalla y donde estan los elementos principales."
        return vision.analyze_image(str(shot), question, settings)
    except Exception as exc:
        raise ComputerControlError(f"No pude analizar la pantalla con vision: {exc}") from exc


def move_mouse(x: int, y: int, duration: float = 0.3) -> str:
    pg = _pyautogui()
    width, height = screen_size()
    x = max(0, min(width - 1, int(x)))
    y = max(0, min(height - 1, int(y)))
    pg.moveTo(x, y, duration=max(0.0, min(3.0, float(duration))))
    return f"Mouse en ({x}, {y})."


def click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    pg = _pyautogui()
    width, height = screen_size()
    x = max(0, min(width - 1, int(x)))
    y = max(0, min(height - 1, int(y)))
    btn = str(button or "left").strip().lower()
    if btn not in ("left", "right", "middle"):
        btn = "left"
    pg.click(x=x, y=y, clicks=max(1, min(3, int(clicks))), button=btn)
    return f"Click {btn} en ({x}, {y})."


def type_text(text: str, interval: float = 0.02) -> str:
    pg = _pyautogui()
    pg.typewrite(str(text), interval=max(0.0, min(0.5, float(interval))))
    return f"Escribi {len(str(text))} caracteres."


def press_keys(keys: str) -> str:
    pg = _pyautogui()
    parts = [k.strip() for k in re.split(r"[+\s,]+", str(keys)) if k.strip()]
    if not parts:
        raise ComputerControlError("Indica una o mas teclas (ej. 'enter' o 'ctrl+v').")
    if len(parts) == 1:
        pg.press(parts[0])
    else:
        pg.hotkey(*parts)
    return f"Presione: {' + '.join(parts)}."


def label_is_sensitive(label: str) -> bool:
    return bool(_SENSITIVE_LABEL.search(str(label or "")))
