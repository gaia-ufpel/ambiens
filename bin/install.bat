@echo off
REM Instalacao no Windows: cria o ambiente virtual .venv-win e instala as dependencias.
REM O nome nao e .venv porque a pasta pode ser a mesma usada pelo Linux (dual boot),
REM e o .venv de la nao serve no Windows (e vice-versa).
REM Usa o uv quando ele esta no PATH (baixa o Python 3.10 sozinho); senao, o py launcher.
setlocal
cd /d "%~dp0.."

set "VENV=.venv-win"
set "PY=%VENV%\Scripts\python.exe"

where uv >nul 2>nul
if not errorlevel 1 goto :uv

where py >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Nem uv nem Python encontrados. Instale o uv:
    echo        winget install astral-sh.uv
    echo        ou o Python 3.10/3.11 de https://www.python.org/downloads/windows/
    echo        marcando "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist "%PY%" (
    echo [INFO] Criando ambiente virtual %VENV% com o py launcher ...
    py -3.10 -m venv %VENV% 2>nul || py -3.11 -m venv %VENV%
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual. O projeto precisa do Python 3.10 ou 3.11.
        pause
        exit /b 1
    )
)
echo [INFO] Instalando dependencias ...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt -c constraints.txt
if errorlevel 1 goto :falha
goto :ok

:uv
if not exist "%PY%" (
    echo [INFO] Criando ambiente virtual %VENV% com o uv ^(Python 3.10^) ...
    uv venv %VENV% --python 3.10
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        pause
        exit /b 1
    )
)
echo [INFO] Instalando dependencias ...
uv pip install --python "%PY%" -r requirements.txt -c constraints.txt
if errorlevel 1 goto :falha
goto :ok

:falha
echo [ERRO] Falha ao instalar as dependencias.
pause
exit /b 1

:ok
echo.
echo [OK] Instalacao concluida. Execute "executar.bat" para abrir o programa.
echo      O EnergyPlus 9.4 tambem precisa estar instalado (padrao: C:\EnergyPlusV9-4-0).
pause
