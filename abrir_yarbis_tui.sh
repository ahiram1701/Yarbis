#!/bin/sh
# Lanzador de la TUI de Yarbis para Linux/macOS (para el futuro multiplataforma).
# Hazlo ejecutable con: chmod +x abrir_yarbis_tui.sh
cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "No encontre Python (.venv/bin/python ni python3)."
    exit 1
fi

exec "$PY" yarbis_tui.py "$@"
