@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

set PY=
where py >nul 2>nul
if not errorlevel 1 set PY=py -3
if "!PY!"=="" (
  where python >nul 2>nul
  if not errorlevel 1 set PY=python
)
if "!PY!"=="" goto NOPY

!PY! -c "import PySide6" >nul 2>nul
if errorlevel 1 goto NOQT

echo Starting MC Server Manager ...
set "PYW="
for /f "tokens=*" %%i in ('!PY! -c "import sys;print(sys.executable)"') do set "PYW=%%i"
set "PYW=!PYW:\python.exe=\pythonw.exe!"
if not exist "!PYW!" set "PYW=!PYW:\pythonw.exe=\python.exe!"
start "" "!PYW!" -m mcserver
exit /b 0

:NOPY
echo.
echo  [ERROR] Python not found.
echo  Install: https://www.python.org/downloads/
echo  Remember: check "Add Python.exe to PATH"
echo.
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('没有找到 Python。请先运行 安装.bat，或自己安装 Python 3.9+ 并添加到 PATH。', '无法启动')"
goto END

:NOQT
echo.
echo  [ERROR] PySide6 is not installed.
echo  Run:
echo      pip install PySide6
echo.
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('缺少界面库 PySide6。请双击 安装.bat 来修复，或手动执行 pip install PySide6。', '无法启动')"
goto END
:END
echo.
pause
