"""Utilidades de subprocesos compartidas por Yarbis."""

import os
import subprocess


def no_window_creationflags() -> int:
    """Flags para lanzar subprocesos sin abrir ventana de consola.

    Devuelve CREATE_NO_WINDOW en Windows y 0 en el resto de sistemas.
    """
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
