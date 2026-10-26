@echo off
setlocal
cd /d "%~dp0"

echo.
echo   MC Server Manager - Uninstall
echo   ----------------------------------------
echo.

where powershell >nul 2>nul
if errorlevel 1 (
    echo   [ERROR] Windows PowerShell not found.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
if errorlevel 1 pause
