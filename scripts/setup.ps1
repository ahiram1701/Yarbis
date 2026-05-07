$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Push-Location $RepoRoot
try {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host "Creando entorno virtual .venv..."
        python -m venv .venv
    }

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "No pude encontrar $VenvPython despues de crear el entorno."
    }

    Write-Host "Actualizando pip..."
    & $VenvPython -m pip install --upgrade pip

    Write-Host "Instalando dependencias..."
    & $VenvPython -m pip install -r requirements.txt

    Write-Host "Validando imports basicos..."
    @'
import importlib
for name in ("ollama", "win11toast", "tkinter", "pystray", "PIL"):
    importlib.import_module(name)
print("Dependencias Python OK.")
'@ | & $VenvPython -

    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Host "Modelos Ollama disponibles:"
        ollama list
    }
    else {
        Write-Warning "No encontre ollama en PATH. Instala/inicia Ollama antes de ejecutar ciclos."
    }

    if (Get-Command dotnet -ErrorAction SilentlyContinue) {
        Write-Host "Validando .NET SDK..."
        dotnet --version
    }
    else {
        Write-Warning "No encontre dotnet. Instala .NET SDK 8 si quieres usar el servicio SCM."
    }

    Write-Host ""
    Write-Host "Setup completo. Abre Yarbis con:"
    Write-Host ".\abrir_yarbis.cmd"
}
finally {
    Pop-Location
}
