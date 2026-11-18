@echo off
REM Double-click this file to build ChadMathSpire.exe.
REM It must live in the same "Math Meme Spire" folder as polyhedral_spire.py
REM and ChadMathSpire.spec (that's already the case on your Desktop).

cd /d "%~dp0"
echo.
echo === Building ChadMathSpire.exe ===
echo (this can take a minute or two the first time)
echo.

set BUILD_OK=0
set PYINSTALLER_FOUND=0

REM Try a few ways to reach PyInstaller, in order, since "pyinstaller"
REM alone only works if its Scripts folder is on PATH -- often isn't,
REM even when it's installed. Prefer the project venv created with Python 3.12.
REM Global Python installations are used only as a fallback.
if exist "venv\Scripts\pyinstaller.exe" (
    set PYINSTALLER_FOUND=1
    echo Using venv\Scripts\pyinstaller.exe ...
    "venv\Scripts\pyinstaller.exe" ChadMathSpire.spec
    if not errorlevel 1 set BUILD_OK=1
) else if exist ".venv\Scripts\pyinstaller.exe" (
    set PYINSTALLER_FOUND=1
    echo Using .venv\Scripts\pyinstaller.exe ...
    ".venv\Scripts\pyinstaller.exe" ChadMathSpire.spec
    if not errorlevel 1 set BUILD_OK=1
) else (
    where pyinstaller >nul 2>nul
    if not errorlevel 1 (
        set PYINSTALLER_FOUND=1
        echo Using pyinstaller from PATH ...
        pyinstaller ChadMathSpire.spec
        if not errorlevel 1 set BUILD_OK=1
    ) else (
        py -m PyInstaller --version >nul 2>nul
        if not errorlevel 1 (
            set PYINSTALLER_FOUND=1
            echo Using "py -m PyInstaller" ...
            py -m PyInstaller ChadMathSpire.spec
            if not errorlevel 1 set BUILD_OK=1
        ) else (
            python -m PyInstaller --version >nul 2>nul
            if not errorlevel 1 (
                set PYINSTALLER_FOUND=1
                echo Using "python -m PyInstaller" ...
                python -m PyInstaller ChadMathSpire.spec
                if not errorlevel 1 set BUILD_OK=1
            )
        )
    )
)

echo.
if "%BUILD_OK%"=="1" (
    echo === Done! ===
    echo Your new build is at: dist\ChadMathSpire.exe
) else (
    if "%PYINSTALLER_FOUND%"=="1" (
        echo === Build failed -- see the error above. ===
        echo.
        echo If it says "Access is denied" on dist\ChadMathSpire.exe, that
        echo file is locked by something else. Usually that means:
        echo   - the game is still running -- close any open ChadMathSpire.exe
        echo     window (check Task Manager if you're not sure), or
        echo   - a File Explorer window has the dist folder open -- close it, or
        echo   - antivirus is scanning the freshly-built exe -- wait a few
        echo     seconds and try again.
        echo Then double-click this file again to rebuild.
    ) else (
        echo === PyInstaller isn't installed / couldn't be found. ===
        echo.
        echo Open Command Prompt in this folder and run ONE of these once:
        echo     py -m pip install pyinstaller
        echo     python -m pip install pyinstaller
        echo.
        echo Then double-click this file again to build.
    )
)
echo.
pause

