#!/usr/bin/env bash
# Corre el core de Yarbis como PROCESO PLANO en Linux/macOS (sin el host .NET/SCM
# de Windows). Cada instancia se distingue por la variable YARBIS_INSTANCE; el
# mutex de instancia unica de Windows se vuelve no-op fuera de Windows.
#
# Uso:
#   ./run_yarbis_service.sh                 # instancia "default", en primer plano
#   ./run_yarbis_service.sh trader          # instancia "trader", en primer plano
#   ./run_yarbis_service.sh trader --bg     # en segundo plano con nohup + log
#
# Para un arranque gestionado (reinicio automatico al bootear) usa systemd
# (Linux) o launchd (macOS); ver la seccion "Correr en Linux/macOS" del README.
set -euo pipefail

cd "$(dirname "$0")"

INSTANCE="${1:-default}"
MODE="${2:-}"
export YARBIS_INSTANCE="$INSTANCE"

# Interprete: prioriza el venv del proyecto, luego python3/python del sistema.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

if [ "$MODE" = "--bg" ]; then
  LOG_DIR=".yarbis_runtime"
  mkdir -p "$LOG_DIR"
  LOG_FILE="$LOG_DIR/service_${INSTANCE}.out"
  echo "Arrancando Yarbis (instancia '$INSTANCE') en segundo plano -> $LOG_FILE"
  nohup "$PY" yarbis_service.py >>"$LOG_FILE" 2>&1 &
  echo "PID $!  (detener con: kill $!)"
else
  echo "Arrancando Yarbis (instancia '$INSTANCE') en primer plano. Ctrl+C para detener."
  exec "$PY" yarbis_service.py
fi
