@echo off
chcp 65001 >nul
title OEW Motor - Nucleo Debug Tool

:: Определяем папку со скриптом (запуск из своей папки)
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: Python 3.11 (путь для Saleae + pyserial)
set "PYTHON=C:\Users\190\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PYTHON%" (
    where python >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Python not found.
        pause & exit /b 1
    )
    set "PYTHON=python"
)

:: Устанавливаем PYTHONUTF8 для корректного вывода русских символов
set PYTHONUTF8=1

:: Проверяем зависимости
if not exist "logs" mkdir logs
set "WARNLOG=logs\startup_warnings.log"
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set "DT=%%I"
set "STAMP=%DT:~0,4%-%DT:~4,2%-%DT:~6,2% %DT:~8,2%:%DT:~10,2%:%DT:~12,2%"

%PYTHON% -c "import serial" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Missing pyserial. Run: %PYTHON% -m pip install pyserial
    echo %STAMP% [WARN] Missing pyserial. Run: %PYTHON% -m pip install pyserial >> "%WARNLOG%"
)

%PYTHON% -c "from saleae import automation" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Missing saleae. Run: %PYTHON% -m pip install saleae
    echo %STAMP% [WARN] Missing saleae. Run: %PYTHON% -m pip install saleae >> "%WARNLOG%"
)

echo Starting Nucleo Debug Tool...
%PYTHON% "nucleo_debug_tool.py"
pause
