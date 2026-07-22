@echo off
title OEW Motor GUI
cd /d C:\ST\boyler\Motor

REM Используем Python 3.11 из AppData
set PYTHON="C:\Users\190\AppData\Local\Programs\Python\Python311\python.exe"

echo Starting OEW Motor GUI using %PYTHON%...
echo Close this console window to exit the program.
echo.

%PYTHON% measurement_gui.py
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Script exited with code %errorlevel%
    echo Make sure pyserial is installed:
    echo %PYTHON% -m pip install pyserial
)

pause
