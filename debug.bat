@echo off
chcp 65001 >nul
title OEW Motor - OpenOCD Debug (Nucleo-G474RE)

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: GDB из ARM GNU Toolchain (или из PATH; портативная установка — ../toolchain)
set "GDB=arm-none-eabi-gdb"
where %GDB% >nul 2>&1 || set "GDB=C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\13.2 Rel1\bin\arm-none-eabi-gdb.exe"
set "ELF=build\firmware.elf"

if not exist "%ELF%" (
    echo [ERROR] firmware.elf not found. Run 'make' first.
    pause & exit /b 1
)
where openocd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] openocd not found in PATH ^(см. ..\toolchain\env.bat^).
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

echo  Starting GDB (connect, load, break at main)...
echo  Commands: continue/next/step/print/break/watch; G = run free; q = quit
echo.
"%GDB%" -q -ex "target remote localhost:3333" -x .gdbinit "%ELF%"

:: Cleanup
taskkill /f /im openocd.exe >nul 2>&1
echo.
echo Debug session ended.
pause
