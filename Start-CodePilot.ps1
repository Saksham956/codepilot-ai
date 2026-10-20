$ErrorActionPreference = 'Stop'
$backendRoot = Join-Path $PSScriptRoot 'backend'
$pythonPath = Join-Path $backendRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create backend\venv and install backend\requirements-mvp.txt first. See README-MVP.md.'
}
Set-Location -LiteralPath $backendRoot
Write-Host 'CodePilot AI: open http://127.0.0.1:8010 in your browser. Press Ctrl+C to stop.'
& $pythonPath -m uvicorn mvp_app:app --host 127.0.0.1 --port 8010
