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
%PYTHON% -c "import serial" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Missing pyserial. Run: %PYTHON% -m pip install pyserial
)

%PYTHON% -c "from saleae import automation" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Missing saleae. Run: %PYTHON% -m pip install saleae
)

echo Starting Nucleo Debug Tool...
%PYTHON% "nucleo_debug_tool.py"
pause
