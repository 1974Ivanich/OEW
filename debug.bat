@echo off
chcp 65001 >nul
title OEW Motor - OpenOCD Debug

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: Путь к arm-none-eabi-gdb
set "GDB=C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\13.2 Rel1\bin\arm-none-eabi-gdb.exe"
set "ELF=build\firmware.elf"

if not exist "%ELF%" (
    echo [ERROR] firmware.elf not found. Run 'make' first.
    pause & exit /b 1
)

echo.
echo ======================================================
echo  OpenOCD + GDB Debug — Nucleo-G474RE
echo ======================================================
echo.
echo  Starting OpenOCD in background...
start "OpenOCD" /MIN cmd /c "openocd -f openocd.cfg > openocd_log.txt 2>&1"
timeout /t 3 /nobreak >nul

echo  Starting GDB (break at main)...
echo  Commands: continue/next/step/print/break/watch
echo  Type 'quit' to exit
echo.
"%GDB%" -x .gdbinit "%ELF%"

:: Cleanup
taskkill /f /im openocd.exe >nul 2>&1
echo.
echo Debug session ended.
pause
