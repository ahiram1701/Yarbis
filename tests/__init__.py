"""Aislamiento GLOBAL de la suite de tests: nunca tocar una instancia real.

Correr los tests (por `run_project_tests`, por `run_system_command` con
`python -m unittest ...`, o manualmente) importa este paquete ANTES de importar
`memory`/`yarbis_instance`. Forzamos aqui `YARBIS_INSTANCE` a un sandbox
descartable para que ningun test que escriba estado toque el `state.json` de una
instancia productiva (asistente, etc.).

Causa raiz de reseteos repetidos: un test sin aislar hacia `memory.save_state`
sobre el estado real de la instancia que lanzaba la suite (asistente), dejandola
"como nueva". `run_project_tests` ya inyecta un sandbox por env, pero
`run_system_command`/ejecuciones manuales no; este modulo cubre TODOS los casos.
"""

import os as _os

_ENV = "YARBIS_INSTANCE"
# Si ya venimos con un sandbox (p.ej. el que crea run_project_tests), respetarlo;
# en cualquier otro caso (instancia real o sin definir), forzar el sandbox fijo.
if not _os.environ.get(_ENV, "").startswith("test-sandbox"):
    _os.environ[_ENV] = "test-sandbox"
