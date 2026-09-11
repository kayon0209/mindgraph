@echo off
REM ============================================
REM  MindGraph local startup (NO Docker needed)
REM  SQLite WAL + local BGE, zero external deps
REM  Requires: .venv ready + web/dist built
REM  Rebuild frontend after code changes:
REM    cd web && npx vite build
REM ============================================
title MindGraph Launcher
cd /d "%~dp0"

echo [1/2] Starting API on http://127.0.0.1:8000 ...
set PYTHONPATH=src
start "MindGraph API" cmd /k ".venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000"

echo [2/2] Starting Web on http://127.0.0.1:5174 ...
cd web
start "MindGraph Web" cmd /k "node --max-old-space-size=384 _serve.mjs"

echo.
echo ==========================================
echo  Web : http://127.0.0.1:5174
echo  API : http://127.0.0.1:8000
echo  Docs: http://127.0.0.1:8000/api/docs
echo ==========================================
echo Close both opened windows to stop.
timeout /t 5 >nul
