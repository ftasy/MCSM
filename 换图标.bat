@echo off
cd /d "%~dp0"

echo.
echo   Pick an icon:
echo     1 = Classic grass block (default)
echo     2 = Blue / gold
echo     3 = Dark night
echo.
set N=icon
set /p C=Enter 1/2/3 then press Enter: 
if "%C%"=="2" set N=icon_blue
if "%C%"=="3" set N=icon_dark

if not exist "icons\%N%.ico" (
  echo   [ERROR] icons\%N%.ico not found
  pause
  exit /b
)

copy /Y "icons\%N%.ico" "icon.ico" >nul
copy /Y "icons\%N%.png" "icon.png" >nul
copy /Y "icons\%N%.ico" "mcserver\icon.ico" >nul
copy /Y "icons\%N%.png" "mcserver\icon.png" >nul

echo.
echo   Switched to %N%
echo   Restart the manager to see it.
echo   Re-run "Create desktop shortcut.bat" to refresh desktop icon.
echo.
pause
