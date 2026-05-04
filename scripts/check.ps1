$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "No encontre $Python. Crea el entorno con python -m venv .venv e instala requirements.txt."
}

Push-Location $RepoRoot
try {
    & $Python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & dotnet build service_host\YarbisServiceHost.csproj
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
