@echo off
rem ===== ShiwakeFiler build script (run once on your PC) =====
cd /d %~dp0
python -m pip install --upgrade pyinstaller PySide6 av send2trash
if errorlevel 1 goto err
python -m PyInstaller --noconfirm --clean --windowed --name ShiwakeFiler --collect-all av shiwake_filer.py
if errorlevel 1 goto err
echo.
echo ===== DONE =====
echo Zip the folder  dist\ShiwakeFiler  and send it.
echo Users just run  ShiwakeFiler.exe  inside it (no Python needed).
explorer dist
pause
exit /b 0
:err
echo.
echo ===== BUILD FAILED =====
pause
exit /b 1
