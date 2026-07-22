@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No encontre .venv\Scripts\python.exe
    echo Instala el entorno virtual o recrealo antes de abrir la TUI de Yarbis.
    pause
    exit /b 1
)

REM La TUI necesita una consola (no pythonw). Doble-clic abre esta ventana.
".venv\Scripts\python.exe" "yarbis_tui.py" %*
