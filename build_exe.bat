@echo off
setlocal EnableExtensions EnableDelayedExpansion
title MCSM - EXE Builder
cd /d "%~dp0"

rem ============================================================
rem  MCSM EXE builder (PyInstaller --onedir, no console window)
rem  Output: dist\MCSM\MCSM.exe
rem
rem  The Python interpreter is no longer hard-coded. Lookup order:
rem    1. first command line argument   build_exe.bat "D:\Py314\python.exe"
rem    2. environment variable          set MCSM_PYTHON=D:\Py314\python.exe
rem    3. py launcher                   py -3.14 / py -3
rem    4. python on PATH
rem    5. common install folders        %LOCALAPPDATA%\Programs\Python,
rem                                     C:\PythonXX, %ProgramFiles%\PythonXX
rem
rem  Usage:
rem    build_exe.bat                       auto-detect Python and build
rem    build_exe.bat "D:\Py314\python.exe" use the given interpreter
rem    build_exe.bat clean                 only remove dist / build
rem    build_exe.bat -h                    show help
rem ============================================================

set "PY="
set "ARG1=%~1"

if /i "%ARG1%"=="-h"      goto :usage
if /i "%ARG1%"=="/?"      goto :usage
if /i "%ARG1%"=="--help"  goto :usage

if /i "%ARG1%"=="clean" (
    call :clean_output
    echo Build output cleaned.
    goto :done
)

rem ---------- 1. environment variable ----------
if defined MCSM_PYTHON call :try_py "%MCSM_PYTHON%"

rem ---------- 2. command line argument ----------
if not defined PY (
    if defined ARG1 call :try_py "%ARG1%"
)

rem ---------- 3. py launcher ----------
if not defined PY call :try_py_launcher "3.14"
if not defined PY call :try_py_launcher "3.13"
if not defined PY call :try_py_launcher "3"

rem ---------- 4. python on PATH ----------
if not defined PY call :try_py "python"
if not defined PY call :try_py "python3"

rem ---------- 5. common install folders ----------
if not defined PY call :scan_common_paths

if not defined PY (
    echo [ERROR] No usable Python 3.9+ interpreter found.
    echo.
    echo   Pick one of these:
    echo     1^) build_exe.bat "C:\path\to\Python314\python.exe"
    echo     2^) set MCSM_PYTHON=C:\path\to\python.exe   then run this script
    echo     3^) add the folder of python.exe to PATH
    echo.
    echo   To find it, run:  where python
    goto :fail
)

echo Using Python: %PY%
"%PY%" -c "import sys;print('  version: %%d.%%d.%%d' %% sys.version_info[:3])"

rem ---------- dependencies ----------
echo [1/3] Checking dependencies ...
"%PY%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo       PySide6 not found, installing ...
    "%PY%" -m pip install PySide6
    if errorlevel 1 (
        echo [ERROR] PySide6 install failed. Check network, or run: pip install PySide6
        goto :fail
    )
)

"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo       PyInstaller not found, installing ...
    "%PY%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller install failed.
        goto :fail
    )
)

rem ---------- clean ----------
echo [2/3] Cleaning old build output ...
call :clean_output

rem ---------- build ----------
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
    goto :fail
)

echo.
echo ============================================================
echo  Build OK
echo  EXE:  %CD%\dist\MCSM\MCSM.exe
echo  Python: %PY%
echo ============================================================
goto :done


rem ============================================================
rem  subroutines
rem ============================================================

:try_py
rem %~1 = full path to python.exe, or a command name on PATH
set "CAND=%~1"
if "%CAND%"=="" exit /b 1
if exist "%CAND%" goto :tp_verify
where "%CAND%" >nul 2>&1
if errorlevel 1 exit /b 1
:tp_verify
"%CAND%" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY=%CAND%"
exit /b 0

:try_py_launcher
rem %~1 = version such as 3.14; ask the py launcher for the real exe path
for /f "delims=" %%P in ('py -%~1 -c "import sys;print(sys.executable)" 2^>nul') do call :try_py "%%P"
exit /b 0

:scan_common_paths
for %%V in (314 313 312 311 310 39) do (
    if not defined PY call :try_py "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    if not defined PY call :try_py "C:\Python%%V\python.exe"
    if not defined PY call :try_py "%ProgramFiles%\Python%%V\python.exe"
)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if not defined PY call :try_py "%%~fD\python.exe"
)
exit /b 0

:clean_output
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist dist_new rmdir /s /q dist_new
if exist build_new rmdir /s /q build_new
exit /b 0

:usage
echo MCSM EXE builder
echo.
echo   build_exe.bat                        auto-detect Python and build
echo   build_exe.bat "D:\Py314\python.exe"  use the given interpreter
echo   build_exe.bat clean                  only remove dist / build
echo.
echo   Or set an environment variable first:
echo     set MCSM_PYTHON=C:\Python314\python.exe
echo     build_exe.bat
goto :done

:fail
pause
endlocal
exit /b 1

:done
endlocal
exit /b 0
