@echo off
setlocal
cd /d "%~dp0"
echo ==========================================
echo   Preparando ambiente MISS UESP
echo ==========================================
py -3.15 --version >nul 2>&1
if errorlevel 1 (
  echo ERRO: Python 3.15 nao foi encontrado.
  echo Instale Python 3.15.2 de 64 bits e tente novamente.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.15 -m venv .venv
  if errorlevel 1 (
    echo ERRO ao criar o ambiente virtual.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo ERRO ao atualizar o pip.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERRO ao instalar as dependencias.
  pause
  exit /b 1
)
echo.
echo Ambiente pronto. Execute iniciar_servidor.bat para iniciar o sistema.
pause
