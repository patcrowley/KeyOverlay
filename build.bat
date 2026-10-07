@echo off
cd /d "%~dp0"
echo ============================================================
echo  KeyOverlay - Build EXE
echo ============================================================

:: Refuse to build inside the Claude app's protected folder - Windows blocks
:: PyInstaller from writing the icon/manifest there, producing a broken exe.
echo %~dp0 | find /i "\Packages\Claude_" >nul
if not errorlevel 1 (
    echo.
    echo ERROR: This folder is inside the Claude app's protected storage.
    echo Copy the KeyOverlay folder to C:\KeyOverlay and run build.bat from there.
    echo.
    pause
    exit /b 1
)

:: Reinstall PyInstaller clean (removes any earlier patches)
python -m pip install --force-reinstall --no-deps pyinstaller --quiet
python -m pip install pyinstaller --quiet
if errorlevel 1 (
    echo ERROR: pip/python not found. Make sure Python is in your PATH.
    pause
    exit /b 1
)

if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist
if exist KeyOverlay.spec del /q KeyOverlay.spec

echo Generating icon...
python make_icon.py

echo Building...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name KeyOverlay ^
    --hidden-import=PIL._tkinter_finder ^
    --hidden-import=pystray._win32 ^
    --icon KeyOverlay.ico ^
    overlay.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED. See output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done!  dist\KeyOverlay.exe
echo ============================================================
pause
