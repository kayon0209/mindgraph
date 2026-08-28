# MindGraph local dev launcher (Windows PowerShell).
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1
#
# Notes (from the 2026-08-27 ops findings):
# 1. Uses the project .venv Python to avoid system-dependency drift.
# 2. PYTHONPATH must include BOTH the repo root (evaluation package) and src/
#    (api/application/... packages).
# 3. AUTH_MODE does NOT need to be exported manually anymore: api.auth resolves
#    it at request time as "process env > .env > default demo". This repo's
#    .env already sets AUTH_MODE=off. To override temporarily:
#      $env:AUTH_MODE = "api_key"
# 4. The first request loads the BGE model (~10s); later requests are fast.
# Keep this file ASCII-only: Windows PowerShell 5.1 parses BOM-less scripts
# as ANSI, and multibyte comments can break string terminators.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path (Join-Path $root ".venv\Scripts\python.exe"))) {
    Write-Error "Project venv not found: .venv\Scripts\python.exe. Create the virtualenv first."
}

$env:PYTHONPATH = "$root;$root\src"
& "$root\.venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8000
