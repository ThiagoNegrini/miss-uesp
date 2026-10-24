@echo off
setlocal
cd /d "%~dp0"
echo ===============================================
echo   UESP 2027 - CENTRAL DE CONTROLE (NOTEBOOK)
echo ===============================================
where python >nul 2>&1
if errorlevel 1 (
  echo Python nao foi encontrado neste notebook.
  echo Instale Python 3 para Windows e marque "Add Python to PATH".
  pause
  exit /b 1
)
python -c "import openpyxl" >nul 2>&1
if errorlevel 1 (
  echo Instalando dependencia para importar planilhas Excel...
  python -m pip install openpyxl
  if errorlevel 1 (echo Nao foi possivel instalar openpyxl. Verifique a internet e tente novamente.& pause & exit /b 1)
)
start "" /min cmd /c "timeout /t 2 ^>nul ^& start "" http://127.0.0.1:8765/central.html"
echo.
echo Central local: http://127.0.0.1:8765/central.html
echo Para tablets na mesma rede Wi-Fi, use o endereco IP mostrado abaixo pelo servidor.
echo Mantenha esta janela aberta durante o uso.
echo.
python servidor.py
pause
