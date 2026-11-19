@echo off
REM Abre a interface grafica do Ambiens.
setlocal
cd /d "%~dp0.."

set "PY=.venv-win\Scripts\python.exe"
if not exist "%PY%" set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERRO] Ambiente virtual nao encontrado. Execute "install.bat" primeiro.
    pause
    exit /b 1
)

"%PY%" main.py
if errorlevel 1 pause
