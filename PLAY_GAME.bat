@echo off
REM Player launcher: this does not install Python or rebuild anything.
cd /d "%~dp0"
if not exist "ChadMathSpire.exe" (
    echo ChadMathSpire.exe was not found.
    echo Please extract the complete download before running this file.
    pause
    exit /b 1
)
start "" "%~dp0ChadMathSpire.exe"
