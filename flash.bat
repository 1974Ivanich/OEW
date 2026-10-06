@echo off
rem Прошивка через OpenOCD + ST-Link. Требует openocd в PATH
rem (портативная установка: toolchain\openocd\... из ../toolchain, см. env.sh).
echo Flashing firmware to STM32G474RE via OpenOCD...
where openocd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] openocd not found in PATH.
    echo         Open a shell with: ..\toolchain\env.bat ^&^& openocd --version
    pause & exit /b 1
)
if not exist build\firmware.bin (
    echo [ERROR] build\firmware.bin not found. Run 'make' first.
    pause & exit /b 1
)
openocd -c "program build/firmware.bin verify 0x08000000 exit"
if %errorlevel% equ 0 (
    echo Flash successful!
) else (
    echo Flash failed!
)
pause
