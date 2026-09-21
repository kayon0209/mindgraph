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
# 5. Two env vars are REQUIRED for stability: loading a CrossEncoder reranker AFTER
#    the FAISS index segfaults (exit 139, no Python traceback; symptoms look like
#    "hybrid_rerank silently degraded / run produced no artifact").
#    - OMP_NUM_THREADS=1: faiss-cpu and torch each ship an OpenMP runtime.
#    - HF_DEACTIVATE_ASYNC_LOAD=1: disables transformers' parallel weight loading
#      (spawn_materialize thread pool), which access-violates on large indexes.
#    Measured on the 581-chunk index: OMP=4 alone -> 1/4 ok, OMP=4+ASYNC off -> 3/4,
#    OMP=1+ASYNC off -> 4/4. Cost: BGE encoding is single-threaded (~38ms -> ~100ms
#    per query). src/config.py sets the same defaults; these lines cover entrypoints
#    that bypass config.
# Keep this file ASCII-only: Windows PowerShell 5.1 parses BOM-less scripts
# as ANSI, and multibyte comments can break string terminators.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path (Join-Path $root ".venv\Scripts\python.exe"))) {
    Write-Error "Project venv not found: .venv\Scripts\python.exe. Create the virtualenv first."
}

if (-not $env:OMP_NUM_THREADS) { $env:OMP_NUM_THREADS = "1" }
if (-not $env:HF_DEACTIVATE_ASYNC_LOAD) { $env:HF_DEACTIVATE_ASYNC_LOAD = "1" }
$env:PYTHONPATH = "$root;$root\src"
& "$root\.venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8000
