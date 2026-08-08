@echo off
echo Flashing firmware to STM32G474RE...
"C:\ST\STM32CubeCLT_1.22.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe" -c port=SWD mode=UR -w build/firmware.bin 0x08000000 -v -rst
if %errorlevel% equ 0 (
    echo Flash successful!
) else (
    echo Flash failed!
)
pause
