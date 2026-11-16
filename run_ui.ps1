# Bletiner AI launcher (PowerShell)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[Bletiner AI] First run: creating the local virtual environment..." -ForegroundColor Cyan
    py -3.13 -m venv .venv
    & $py -m pip install --upgrade pip
    Write-Host "[Bletiner AI] Installing dependencies (this can take a few minutes)..." -ForegroundColor Cyan
    & $py -m pip install -e ".[ui,secrets]"
}
& $py -m bletiner --ui @args
