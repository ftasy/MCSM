@echo off
setlocal
title MCSM - EXE Builder
cd /d "%~dp0"

rem ============================================================
rem  MCSM EXE builder (PyInstaller --onedir, no console)
rem  Output: dist\MCSM\MCSM.exe
rem ============================================================

set "PY=C:\Users\YiTian\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY%" (
    echo [ERROR] Python 3.14 not found: %PY%
    echo Please edit PY path in this bat, run: where python
    pause
    exit /b 1
)

echo [1/3] Checking PyInstaller ...
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found, installing ...
    "%PY%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller install failed.
        pause
        exit /b 1
    )
)

echo [2/3] Cleaning old build output ...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo [3/3] Building EXE (--onedir, 2-5 min) ...
"%PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --noconsole ^
  --name MCSM ^
  --icon=icon.ico ^
  --add-data "icon.ico;." ^
  --add-data "icon.png;." ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQml ^
  --exclude-module tkinter ^
  --exclude-module numpy ^
  mcserver\__main__.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed, see messages above.
    pause
    exit /b 1
)

echo.
echo Build OK. Run: dist\MCSM\MCSM.exe
pause