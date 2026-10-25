@echo off
setlocal
cd /d "%~dp0"

echo.
echo   MC Server Manager - Setup
echo   ----------------------------------------
echo.

where powershell >nul 2>nul
if errorlevel 1 (
    echo   [ERROR] Windows PowerShell not found.
    echo   This installer needs Windows PowerShell.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo.
    echo   Installer exited with an error. See the log above.
    pause
)
