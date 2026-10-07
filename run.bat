@echo off
echo Installing dependencies (first run only)...
pip install Pillow pystray keyboard --quiet
echo.
echo Starting Key Press Overlay...
echo (This window shows errors. Minimize it.)
echo.
python overlay.py
echo.
echo --- App exited ---
pause
