$ErrorActionPreference = 'Stop'
$backendRoot = Join-Path $PSScriptRoot 'backend'
$pythonPath = Join-Path $backendRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Backend virtual environment is missing.' }
Set-Location -LiteralPath $backendRoot
& $pythonPath -B (Join-Path $backendRoot 'verify_local_mvp.py')
if ($LASTEXITCODE -ne 0) { throw 'Acceptance checks failed. Read backend\local_mvp_acceptance_results.json.' }
