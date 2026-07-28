[CmdletBinding()]
param(
    [int]$BackendPort = 8010,
    [int]$FrontendPort = 5190,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

foreach ($port in @($BackendPort, $FrontendPort)) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        throw "Port $port is already in use by PID $($listener[0].OwningProcess)."
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating Python virtual environment..."
    & python -m venv (Join-Path $projectRoot ".venv")
}

if (-not $SkipInstall) {
    Write-Host "Installing backend dependencies..."
    & $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $backendDir "requirements.txt")
    Write-Host "Installing frontend dependencies..."
    & npm.cmd --prefix $frontendDir install --silent
}

$env:TOKEN_AUDIT_CONFIG = Join-Path $backendDir "config.toml"
$env:TOKEN_AUDIT_DB_PATH = Join-Path $backendDir "audit.local.db"
$env:TOKEN_AUDIT_PROJECTS_DIR = Join-Path $env:USERPROFILE ".claude\projects"
$env:TOKEN_AUDIT_WATCH = "1"
$env:TOKEN_AUDIT_POLL_INTERVAL = "0"
$env:TOKEN_AUDIT_REINGEST_INTERVAL = "120"
$env:FLIGHTDECK_WORKSPACE = [System.IO.Path]::GetFullPath((Join-Path $projectRoot ".."))
$env:PYTHONUTF8 = "1"

$backend = $null
$frontend = $null
try {
    $backend = Start-Process `
        -FilePath $venvPython `
        -ArgumentList @(
            "-m", "uvicorn", "flightdeck.server:app",
            "--host", "127.0.0.1",
            "--port", "$BackendPort",
            "--timeout-graceful-shutdown", "2"
        ) `
        -WorkingDirectory $backendDir `
        -NoNewWindow `
        -PassThru

    $frontend = Start-Process `
        -FilePath "npm.cmd" `
        -ArgumentList @(
            "run", "dev", "--",
            "--host", "127.0.0.1",
            "--port", "$FrontendPort"
        ) `
        -WorkingDirectory $frontendDir `
        -NoNewWindow `
        -PassThru

    Write-Host ""
    Write-Host "FlightDeck backend:  http://127.0.0.1:$BackendPort"
    Write-Host "FlightDeck frontend: http://127.0.0.1:$FrontendPort"
    Write-Host "Press Ctrl+C to stop both services."

    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Seconds 1
        $backend.Refresh()
        $frontend.Refresh()
    }

    if ($backend.HasExited) {
        throw "Backend exited with code $($backend.ExitCode)."
    }
    throw "Frontend exited with code $($frontend.ExitCode)."
}
finally {
    foreach ($process in @($backend, $frontend)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

