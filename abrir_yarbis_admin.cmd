@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo No encontre .venv\Scripts\pythonw.exe
    echo Instala el entorno virtual o recrealo antes de abrir Yarbis.
    pause
    exit /b 1
)

if not exist "yarbis_desktop.py" (
    echo No encontre yarbis_desktop.py en la carpeta del proyecto.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%CD%\.venv\Scripts\pythonw.exe' -ArgumentList '\"%CD%\yarbis_desktop.py\"' -WorkingDirectory '%CD%' -Verb RunAs"

if errorlevel 1 (
    echo No pude solicitar permisos de administrador.
    pause
    exit /b 1
)
