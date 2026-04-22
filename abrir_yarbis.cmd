@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo No encontre .venv\Scripts\pythonw.exe
    echo Instala el entorno virtual o recrealo antes de abrir Yarbis.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "yarbis_desktop.py"
