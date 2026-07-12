"""Utilidades de escritura atómica de archivos compartidas por Yarbis.

En Windows, `Path.replace()` (os.replace) falla de forma intermitente con
`PermissionError [WinError 5]` cuando otro proceso (otra instancia de Yarbis, un
lector, o el antivirus) tiene el destino abierto un instante. Con varias
instancias escribiendo el mismo `state.json`/bus/backup a la vez esto se vuelve
frecuente, así que reintentamos con backoff exponencial + jitter.
"""

import json
import random
import time
from pathlib import Path

DEFAULT_MAX_RETRIES = 5
_BASE_DELAY_SECONDS = 0.05

_UTF8_BOM = "﻿"


def file_has_bom(path: Path) -> bool:
    """True si el archivo empieza con un BOM UTF-8 (\\xef\\xbb\\xbf)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(3) == b"\xef\xbb\xbf"
    except OSError:
        return False


def read_text_bom_safe(path: Path) -> str:
    """Lee texto UTF-8 tolerando un BOM opcional.

    Windows PowerShell (`Out-File`/`Set-Content -Encoding utf8`) escribe UTF-8 CON
    BOM; leer con `utf-8` estricto rompe (`Unexpected UTF-8 BOM`). `utf-8-sig`
    descarta el BOM si existe y es no-op si no. Evita falsos "archivo corrupto".
    """
    return Path(path).read_text(encoding="utf-8-sig")


def read_json_bom_safe(path: Path):
    """Carga JSON tolerando un BOM UTF-8 opcional al inicio del archivo."""
    return json.loads(read_text_bom_safe(path))


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
