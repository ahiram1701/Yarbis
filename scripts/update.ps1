param(
    [string]$Remote = "origin",
    [string]$Branch = "main",
    [switch]$SkipChecks,
    [switch]$RestartDesktop
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonw = Join-Path $VenvDir "Scripts\pythonw.exe"
$DesktopScript = Join-Path $RepoRoot "yarbis_desktop.py"
$RuntimeDir = Join-Path $RepoRoot ".yarbis_runtime"
$UpdateBackupDir = Join-Path $RuntimeDir "updates"
$StateFile = Join-Path $RepoRoot "state.json"
$ServiceName = "Yarbis"
$DefaultSourceRepo = "C:\DEV\Github\yarbis"

$serviceWasInstalled = $false
$serviceWasRunning = $false
$serviceAutostart = $false
$helperWasRunning = $false
$codeUpdated = $false
$checksSummary = "no ejecutados"
$dependencySummary = "no ejecutadas"
$serviceSummary = "no instalado"
$stateBackup = ""
$stashCreated = $false
$stashRef = ""
$stashMessage = ""
$stashSummary = "sin cambios locales"
$stashConflict = $false
$stashPopAttempted = $false
$resolvedFetchSource = ""
$resolvedFetchLabel = ""

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-NativeCapture([string]$FilePath, [string[]]$Arguments) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $FilePath @Arguments 2>&1
        $nativeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [pscustomobject]@{
        ExitCode = $nativeExitCode
        Output = @($output)
    }
}

function Invoke-CommandChecked([string]$FilePath, [string[]]$Arguments, [string]$Action) {
    Write-Host ("> " + $FilePath + " " + ($Arguments -join " "))
    $result = Invoke-NativeCapture -FilePath $FilePath -Arguments $Arguments
    if ($result.Output) {
        $result.Output | ForEach-Object { Write-Host $_ }
    }
    if ($result.ExitCode -ne 0) {
        throw "$Action fallo con exit=$($result.ExitCode)."
    }
}

function Invoke-GitOutput([string[]]$Arguments, [string]$Action) {
    $gitArguments = @("-C", $RepoRoot) + @($Arguments)
    $result = Invoke-NativeCapture -FilePath "git" -Arguments $gitArguments
    if ($result.ExitCode -ne 0) {
        throw "$Action fallo.`n$($result.Output -join "`n")"
    }
    return ($result.Output -join "`n").Trim()
}

function Get-GitRemoteUrl([string]$RepositoryRoot, [string]$RemoteName) {
    $result = Invoke-NativeCapture -FilePath "git" -Arguments @("-C", $RepositoryRoot, "remote", "get-url", $RemoteName)
    if ($result.ExitCode -ne 0) {
        return ""
    }
    return ($result.Output -join "`n").Trim()
}

function Resolve-UpdateSource {
    $directRemoteUrl = Get-GitRemoteUrl $RepoRoot $Remote
    if ($directRemoteUrl) {
        $script:resolvedFetchSource = $Remote
        $script:resolvedFetchLabel = "$Remote ($directRemoteUrl)"
        return
    }

    $candidates = @()
    if ($env:YARBIS_UPDATE_SOURCE) {
        $candidates += [string]$env:YARBIS_UPDATE_SOURCE
    }
    if ($Remote -and $Remote -ne "origin") {
        $candidates += $Remote
    }
    if ((Test-Path -LiteralPath $DefaultSourceRepo) -and ((Resolve-Path -LiteralPath $DefaultSourceRepo).Path -ne (Resolve-Path -LiteralPath $RepoRoot).Path)) {
        $candidates += $DefaultSourceRepo
    }

    foreach ($candidate in $candidates) {
        $cleaned = [string]$candidate
        if (-not $cleaned.Trim()) {
            continue
        }

        if (Test-Path -LiteralPath $cleaned) {
            $candidateRoot = (Resolve-Path -LiteralPath $cleaned).Path
            $candidateRemoteUrl = Get-GitRemoteUrl $candidateRoot "origin"
            if ($candidateRemoteUrl) {
                $script:resolvedFetchSource = $candidateRemoteUrl
                $script:resolvedFetchLabel = "$candidateRemoteUrl (origin de $candidateRoot)"
                return
            }

            if (Test-Path -LiteralPath (Join-Path $candidateRoot ".git")) {
                $script:resolvedFetchSource = $candidateRoot
                $script:resolvedFetchLabel = $candidateRoot
                return
            }
        }
        else {
            $script:resolvedFetchSource = $cleaned
            $script:resolvedFetchLabel = $cleaned
            return
        }
    }

    throw (
        "No encontre la fuente de actualizacion '$Remote'. " +
        "Configura un remote con `git remote add origin URL`, usa `-Remote URL_O_RUTA`, " +
        "o define YARBIS_UPDATE_SOURCE."
    )
}

function Test-UpdateSource {
    Write-Step "Validando fuente de actualizacion"
    Write-Host "Fuente: $resolvedFetchLabel"
    $result = Invoke-NativeCapture -FilePath "git" -Arguments @("-C", $RepoRoot, "ls-remote", $resolvedFetchSource, $Branch)
    if ($result.ExitCode -ne 0) {
        throw "No pude leer $Branch desde $resolvedFetchLabel.`n$($result.Output -join "`n")"
    }
    if (-not ($result.Output -join "").Trim()) {
        throw "La fuente $resolvedFetchLabel no publico la rama '$Branch'."
    }
}

function Get-GitStatusLines {
    $result = Invoke-NativeCapture -FilePath "git" -Arguments @("-C", $RepoRoot, "status", "--porcelain")
    if ($result.ExitCode -ne 0) {
        throw "No pude revisar cambios locales.`n$($result.Output -join "`n")"
    }
    return @($result.Output | Where-Object { [string]$_ })
}

function Save-LocalChangesForUpdate {
    $statusLines = Get-GitStatusLines
    if (-not $statusLines -or $statusLines.Count -eq 0) {
        $script:stashSummary = "sin cambios locales"
        return
    }

    Write-Step "Guardando cambios locales"
    $script:stashMessage = "yarbis-update-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Host "Cambios detectados; se guardaran temporalmente con git stash."
    $statusLines | ForEach-Object { Write-Host "  $_" }

    Invoke-CommandChecked "git" @(
        "-C",
        $RepoRoot,
        "stash",
        "push",
        "--include-untracked",
        "--message",
        $script:stashMessage
    ) "guardar cambios locales en stash"

    $stashListResult = Invoke-NativeCapture -FilePath "git" -Arguments @("-C", $RepoRoot, "stash", "list", "--format=%gd`t%s")
    if ($stashListResult.ExitCode -ne 0) {
        throw "No pude leer la lista de stash.`n$($stashListResult.Output -join "`n")"
    }
    $stashEntries = @($stashListResult.Output)
    $matchingEntry = $stashEntries | Where-Object { $_ -like "*$($script:stashMessage)*" } | Select-Object -First 1
    if (-not $matchingEntry) {
        throw "Git reporto el stash, pero no pude encontrarlo por mensaje: $($script:stashMessage)"
    }

    $script:stashCreated = $true
    $script:stashRef = ($matchingEntry -split "`t", 2)[0]
    $script:stashSummary = "guardados en $($script:stashRef) ($($script:stashMessage))"
    Write-Host "Cambios locales guardados en $($script:stashRef)."
}

function Restore-LocalChangesFromStash {
    if (-not $script:stashCreated -or $script:stashPopAttempted) {
        return
    }

    Write-Step "Restaurando cambios locales"
    $script:stashPopAttempted = $true
    $result = Invoke-NativeCapture -FilePath "git" -Arguments @("-C", $RepoRoot, "stash", "pop", "--index", $script:stashRef)
    $rendered = ($result.Output -join "`n").Trim()
    if ($result.ExitCode -eq 0) {
        if ($rendered) {
            Write-Host $rendered
        }
        $script:stashSummary = "reaplicados desde $($script:stashRef)"
        return
    }

    $script:stashConflict = $true
    $script:stashSummary = "conflicto al reaplicar $($script:stashRef); el stash se conserva"
    Write-Warning "No pude reaplicar automaticamente los cambios locales."
    if ($rendered) {
        Write-Host $rendered
    }
    Write-Host ""
    Write-Host "Resuelve los conflictos antes de reiniciar Yarbis:" -ForegroundColor Yellow
    Write-Host "  git status"
    Write-Host "  git stash list"
    Write-Host "  git stash show --stat '$($script:stashRef)'"
}

function Invoke-PythonOutput([string]$Code, [string]$Action, [switch]$AllowMissingPython, [switch]$AllowFailure) {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if ($AllowMissingPython) {
            Write-Warning "No encontre $VenvPython. Omito: $Action."
            return ""
        }
        throw "No encontre $VenvPython. Ejecuta .\scripts\setup.ps1 o permite que este actualizador recree .venv."
    }

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $VenvPython -c $Code 2>&1
        $pythonExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($pythonExitCode -ne 0) {
        $rendered = ($output -join "`n").Trim()
        if ($AllowFailure) {
            Write-Warning "$Action fallo: $rendered"
            return $rendered
        }
        throw "$Action fallo.`n$rendered"
    }
    $renderedOutput = ($output -join "`n").Trim()
    if ($renderedOutput) {
        Write-Host $renderedOutput
    }
    return $renderedOutput
}

function Test-IsAdministrator {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    catch {
        return $false
    }
}

function Get-YarbisServiceInfo {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        return [pscustomobject]@{
            Installed = $false
            Running = $false
            Autostart = $false
        }
    }

    $startMode = ""
    try {
        $serviceConfig = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
        $startMode = [string]$serviceConfig.StartMode
    }
    catch {
        $startMode = ""
    }

    return [pscustomobject]@{
        Installed = $true
        Running = $service.Status -eq "Running"
        Autostart = $startMode -eq "Auto"
    }
}

function Stop-YarbisServiceIfNeeded {
    if (-not $script:serviceWasRunning) {
        return
    }

    Write-Step "Deteniendo servicio SCM"
    Stop-Service -Name $ServiceName -ErrorAction Stop
    (Get-Service -Name $ServiceName).WaitForStatus("Stopped", [TimeSpan]::FromSeconds(45))
    Write-Host "Servicio Yarbis detenido."
}

function Ensure-Venv {
    if (Test-Path -LiteralPath $VenvPython) {
        return
    }

    Write-Step "Creando entorno virtual .venv"
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "No encontre python en PATH para crear .venv."
    }
    Invoke-CommandChecked "python" @("-m", "venv", $VenvDir) "crear .venv"
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "Se ejecuto python -m venv, pero no aparecio $VenvPython."
    }
}

function Restart-DesktopIfRequested {
    if (-not $RestartDesktop) {
        return
    }

    Write-Step "Reabriendo Yarbis"
    $launcher = $VenvPythonw
    if (-not (Test-Path -LiteralPath $launcher)) {
        $launcher = $VenvPython
    }
    if (-not (Test-Path -LiteralPath $launcher)) {
        Write-Warning "No pude reabrir la app porque no encontre python/pythonw en .venv."
        return
    }
    Start-Process -FilePath $launcher -ArgumentList "`"$DesktopScript`"" -WorkingDirectory $RepoRoot
    Write-Host "Yarbis se esta abriendo de nuevo."
}

try {
    Push-Location $RepoRoot

    if ($RestartDesktop) {
        Write-Step "Esperando cierre de la app"
        Start-Sleep -Seconds 2
    }

    Write-Step "Validando repositorio"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "No encontre git en PATH."
    }
    Invoke-GitOutput @("rev-parse", "--show-toplevel") "validar repo Git" | Out-Null
    $oldCommit = Invoke-GitOutput @("rev-parse", "--short", "HEAD") "leer commit actual"
    Resolve-UpdateSource
    Test-UpdateSource

    Write-Step "Leyendo estado del servicio y helper"
    $serviceInfo = Get-YarbisServiceInfo
    $serviceWasInstalled = [bool]$serviceInfo.Installed
    $serviceWasRunning = [bool]$serviceInfo.Running
    $serviceAutostart = [bool]$serviceInfo.Autostart

    if ($serviceWasInstalled -and -not (Test-IsAdministrator)) {
        throw "El servicio SCM de Yarbis esta instalado. Ejecuta este actualizador como administrador."
    }

    $helperStatus = Invoke-PythonOutput "from pc_context_runtime import get_context_helper_status; print('running' if get_context_helper_status().get('running') else 'stopped')" "leer helper de contexto local" -AllowMissingPython -AllowFailure
    $helperWasRunning = $helperStatus.Trim().EndsWith("running")

    if ($helperWasRunning) {
        Write-Step "Deteniendo helper de contexto local"
        Invoke-PythonOutput "from pc_context_runtime import stop_context_helper; print(stop_context_helper())" "detener helper de contexto local" -AllowMissingPython -AllowFailure | Out-Null
    }

    Save-LocalChangesForUpdate

    New-Item -ItemType Directory -Force -Path $UpdateBackupDir | Out-Null
    if (Test-Path -LiteralPath $StateFile) {
        $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $stateBackup = Join-Path $UpdateBackupDir "state-$timestamp.json"
        Copy-Item -LiteralPath $StateFile -Destination $stateBackup -Force
        Write-Host "Respaldo de state.json: $stateBackup"
    }
    else {
        Write-Host "No existe state.json; no hay estado que respaldar."
    }

    Stop-YarbisServiceIfNeeded

    Write-Step "Trayendo cambios desde $resolvedFetchLabel/$Branch"
    Invoke-CommandChecked "git" @("-C", $RepoRoot, "fetch", $resolvedFetchSource, $Branch) "git fetch"
    $targetCommit = Invoke-GitOutput @("rev-parse", "--short", "FETCH_HEAD") "leer commit remoto"
    Invoke-CommandChecked "git" @("-C", $RepoRoot, "merge", "--ff-only", "FETCH_HEAD") "fast-forward desde $Remote/$Branch"
    $newCommit = Invoke-GitOutput @("rev-parse", "--short", "HEAD") "leer commit actualizado"
    $codeUpdated = $oldCommit -ne $newCommit

    Write-Step "Actualizando dependencias Python"
    Ensure-Venv
    Invoke-CommandChecked $VenvPython @("-m", "pip", "install", "-r", "requirements.txt") "instalar dependencias Python"
    $dependencySummary = "OK"

    if ($SkipChecks) {
        Write-Step "Checks omitidos"
        $checksSummary = "omitidos por -SkipChecks"
    }
    else {
        Write-Step "Ejecutando checks"
        $checkScript = Join-Path $RepoRoot "scripts\check.ps1"
        Invoke-CommandChecked "powershell" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $checkScript) "ejecutar scripts/check.ps1"
        $checksSummary = "OK"
    }

    Restore-LocalChangesFromStash
    if ($stashConflict) {
        $serviceSummary = "no reiniciado por conflictos locales"
    }

    if ($serviceWasInstalled -and -not $stashConflict) {
        Write-Step "Recompilando/reconfigurando servicio SCM"
        $startAutoLiteral = if ($serviceAutostart) { "True" } else { "False" }
        Invoke-PythonOutput "import service_manager; print(service_manager.install_service(start_auto=$startAutoLiteral))" "reconfigurar servicio SCM" | Out-Null
        $serviceSummary = if ($serviceWasRunning) { "instalado; pendiente de reinicio" } else { "instalado; queda detenido" }
    }

    if ($serviceWasRunning -and -not $stashConflict) {
        Write-Step "Reiniciando servicio SCM"
        Invoke-PythonOutput "import service_manager; print(service_manager.start_service())" "iniciar servicio SCM" | Out-Null
        $serviceSummary = "reiniciado"
    }

    if ($helperWasRunning -and -not $stashConflict) {
        Write-Step "Reiniciando helper de contexto local"
        Invoke-PythonOutput "from pc_context_runtime import start_context_helper; print(start_context_helper())" "iniciar helper de contexto local" -AllowFailure | Out-Null
    }

    Write-Step "Resumen"
    Write-Host "Commit anterior: $oldCommit"
    Write-Host "Commit remoto:   $targetCommit"
    Write-Host "Commit actual:   $newCommit"
    Write-Host "Dependencias:    $dependencySummary"
    Write-Host "Checks:          $checksSummary"
    Write-Host "Cambios locales: $stashSummary"
    Write-Host "Servicio:        $serviceSummary"
    if ($stateBackup) {
        Write-Host "Respaldo estado: $stateBackup"
    }
    Write-Host ""
    if ($stashConflict) {
        Write-Host "Actualizacion aplicada, pero quedan conflictos locales por resolver." -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Actualizacion completa." -ForegroundColor Green

    Restart-DesktopIfRequested
}
catch {
    Write-Host ""
    Write-Host "Actualizacion cancelada o fallida." -ForegroundColor Red
    Write-Host $_.Exception.Message

    if ($stashCreated -and -not $stashPopAttempted) {
        Write-Host ""
        Write-Host "Intentando restaurar cambios locales guardados antes de salir..."
        Restore-LocalChangesFromStash
    }

    if ($serviceWasRunning -and -not $codeUpdated -and -not $stashConflict) {
        Write-Host ""
        Write-Host "Intentando restaurar el servicio porque el codigo no cambio..."
        try {
            if (Test-Path -LiteralPath $VenvPython) {
                Invoke-PythonOutput "import service_manager; print(service_manager.start_service())" "restaurar servicio SCM" -AllowFailure | Out-Null
            }
            else {
                Start-Service -Name $ServiceName -ErrorAction Stop
            }
        }
        catch {
            Write-Warning "No pude restaurar el servicio automaticamente: $($_.Exception.Message)"
        }
    }

    if ($helperWasRunning -and -not $codeUpdated -and -not $stashConflict) {
        Write-Host ""
        Write-Host "Intentando restaurar el helper de contexto local..."
        try {
            Invoke-PythonOutput "from pc_context_runtime import start_context_helper; print(start_context_helper())" "restaurar helper de contexto local" -AllowFailure | Out-Null
        }
        catch {
            Write-Warning "No pude restaurar el helper automaticamente: $($_.Exception.Message)"
        }
    }

    exit 1
}
finally {
    Pop-Location
}
