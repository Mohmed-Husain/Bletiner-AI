@echo off
setlocal
cd /d "%~dp0"
title Bletiner AI

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [Bletiner AI] First run: creating the local virtual environment...
  py -3.13 -m venv .venv
  if errorlevel 1 (
    echo [Bletiner AI] Python 3.13 was not found. Install Python 3.11+ and retry.
    pause
    exit /b 1
  )
  "%PY%" -m pip install --upgrade pip
  echo [Bletiner AI] Installing dependencies (this can take a few minutes)...
  "%PY%" -m pip install -e ".[ui,secrets]"
  if errorlevel 1 (
    echo [Bletiner AI] Dependency install failed. See messages above.
    pause
    exit /b 1
  )
)

"%PY%" -m bletiner --ui %*
if errorlevel 1 pause
endlocal
