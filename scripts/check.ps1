$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$CheckBackupRoot = Join-Path ([IO.Path]::GetTempPath()) ("yarbis-check-" + [guid]::NewGuid().ToString("N"))
$ProtectedLocalPaths = @(
    "state.json",
    ".yarbis_runtime\activity.log",
    ".yarbis_runtime\events.jsonl",
    ".yarbis_runtime\memory_protection.json",
    ".yarbis_runtime\credentials",
    ".yarbis_runtime\coding_proposals",
    ".yarbis_runtime\calendar",
    ".yarbis_runtime\browser",
    ".yarbis_runtime\voice",
    ".yarbis_instances",
    ".yarbis_checkpoints"
)
$ProtectedPathExists = @{}
$ExitCode = 0

function Copy-ProtectedPath([string]$SourceRoot, [string]$DestinationRoot, [string]$RelativePath) {
    $source = Join-Path $SourceRoot $RelativePath
    $destination = Join-Path $DestinationRoot $RelativePath
    if (-not (Test-Path -LiteralPath $source)) {
        return
    }

    $destinationParent = Split-Path -Parent $destination
    if ($destinationParent) {
        New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    }

    $item = Get-Item -LiteralPath $source -Force
    if ($item.PSIsContainer) {
        Copy-Item -LiteralPath $source -Destination $destinationParent -Recurse -Force
    }
    else {
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function Backup-LocalStateForChecks {
    New-Item -ItemType Directory -Force -Path $CheckBackupRoot | Out-Null
    foreach ($relativePath in $ProtectedLocalPaths) {
        $source = Join-Path $RepoRoot $relativePath
        $exists = Test-Path -LiteralPath $source
        $script:ProtectedPathExists[$relativePath] = $exists
        if ($exists) {
            Copy-ProtectedPath $RepoRoot $CheckBackupRoot $relativePath
        }
    }
}

function Remove-ProtectedPathIfCreated([string]$RelativePath) {
    $target = Join-Path $RepoRoot $RelativePath
    if (-not (Test-Path -LiteralPath $target)) {
        return
    }

    $resolvedTarget = (Resolve-Path -LiteralPath $target).Path
    $resolvedRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
    if (-not $resolvedTarget.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Ruta protegida fuera del repo: $resolvedTarget"
    }

    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
}

function Restore-LocalStateAfterChecks {
    foreach ($relativePath in $ProtectedLocalPaths) {
        if ($ProtectedPathExists.ContainsKey($relativePath) -and -not $ProtectedPathExists[$relativePath]) {
            Remove-ProtectedPathIfCreated $relativePath
            continue
        }

        Copy-ProtectedPath $CheckBackupRoot $RepoRoot $relativePath
    }

    if (Test-Path -LiteralPath $CheckBackupRoot) {
        Remove-Item -LiteralPath $CheckBackupRoot -Recurse -Force
    }
}

function Invoke-ValidationChecks {
    & $Python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) {
        return $LASTEXITCODE
    }

    & dotnet build service_host\YarbisServiceHost.csproj
    if ($LASTEXITCODE -ne 0) {
        return $LASTEXITCODE
    }

    return 0
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "No encontre $Python. Crea el entorno con python -m venv .venv e instala requirements.txt."
}

Push-Location $RepoRoot
try {
    Backup-LocalStateForChecks
    $ExitCode = Invoke-ValidationChecks
}
finally {
    Restore-LocalStateAfterChecks
    Pop-Location
}

if ($ExitCode -ne 0) {
    exit $ExitCode
}
