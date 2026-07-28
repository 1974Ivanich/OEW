@echo off
chcp 65001 >nul
title OEW Motor - Nucleo Debug Tool

:: Ищем python в PATH
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH. Install Python 3.11+ and add to PATH.
    pause
    exit /b 1
)

:: Определяем папку со скриптом (запуск из своей папки)
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: Устанавливаем PYTHONUTF8 для корректного вывода русских символов
set PYTHONUTF8=1

:: Проверяем зависимости
python -c "import serial" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Missing pyserial. Run: pip install pyserial
)

python -c "from saleae import automation" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Missing saleae. Run: pip install saleae
)

echo Starting Nucleo Debug Tool...
python "nucleo_debug_tool.py"
pause
