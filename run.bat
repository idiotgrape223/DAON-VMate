@echo off
setlocal EnableExtensions
rem Trailing backslash before closing quote breaks cmd parsing; use . to anchor path
cd /d "%~dp0."

if exist ".venv\Scripts\python.exe" goto run_app

where uv >nul 2>&1
if errorlevel 1 (
  echo [DAON-VMate] .venv is missing and uv is not on PATH.
  echo Install uv: winget install astral-sh.uv
  exit /b 1
)

if exist ".venv" (
  uv venv .venv --clear
) else (
  uv venv .venv
)
if errorlevel 1 exit /b 1
uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 exit /b 1

:run_app
".venv\Scripts\python.exe" main.py %*
exit /b %ERRORLEVEL%
