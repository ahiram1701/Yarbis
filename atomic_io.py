"""Utilidades de escritura atómica de archivos compartidas por Yarbis.

En Windows, `Path.replace()` (os.replace) falla de forma intermitente con
`PermissionError [WinError 5]` cuando otro proceso (otra instancia de Yarbis, un
lector, o el antivirus) tiene el destino abierto un instante. Con varias
instancias escribiendo el mismo `state.json`/bus/backup a la vez esto se vuelve
frecuente, así que reintentamos con backoff exponencial + jitter.
"""

import random
import time
from pathlib import Path

DEFAULT_MAX_RETRIES = 5
_BASE_DELAY_SECONDS = 0.05


def atomic_replace(tmp_path: Path, target_path: Path, max_retries: int = DEFAULT_MAX_RETRIES) -> None:
    """Reemplaza target_path por tmp_path reintentando ante OSError transitorio.

    Reintenta con backoff exponencial y jitter; si agota los intentos, relanza
    el último OSError. No borra tmp_path: eso lo decide quien llama.
    """
    for attempt in range(max_retries):
        try:
            tmp_path.replace(target_path)
            return
        except OSError:
            if attempt == max_retries - 1:
                raise
            delay = _BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, _BASE_DELAY_SECONDS)
            time.sleep(delay)
