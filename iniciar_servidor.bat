@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente nao encontrado. Execute instalar_ambiente.bat primeiro.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" servidor.py
pause
