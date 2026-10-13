@echo off
rem NamiBench environment setup for Windows.
rem Works from both cmd.exe and PowerShell (PowerShell: .\setup.bat).
rem
rem Usage:
rem   setup.bat                      use NAMIBENCH_PYTHON, or "py -3.13", or python on PATH
rem   setup.bat C:\path\python.exe   use this Python as the base
rem
rem Creates .\venv (virtualenv if available, otherwise venv) and installs requirements.txt.
rem Run the app with venv\Scripts\python.exe; activation is not required.

setlocal EnableExtensions
cd /d "%~dp0"

set "PY=%~1"
if not "%PY%"=="" goto :check
set "PY=%NAMIBENCH_PYTHON%"
if not "%PY%"=="" goto :check
where py >nul 2>nul
if errorlevel 1 goto :use_path_python
for /f "delims=" %%i in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%i"
if not "%PY%"=="" goto :check
:use_path_python
set "PY=python"

:check
"%PY%" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 13) else 1)" >nul 2>nul
if errorlevel 1 goto :bad_python
echo Base Python: %PY%

if exist "venv\Scripts\python.exe" goto :install
"%PY%" -m virtualenv --version >nul 2>nul
if errorlevel 1 goto :use_venv
echo Creating venv with virtualenv...
"%PY%" -m virtualenv venv
if errorlevel 1 goto :fail
goto :install
:use_venv
echo Creating venv with the venv module...
"%PY%" -m venv venv
if errorlevel 1 goto :no_venv_tool

:install
echo Installing packages...
"venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
echo.
echo Done. Python for this project: %~dp0venv\Scripts\python.exe
endlocal
exit /b 0

:bad_python
echo Python 3.13 or later was not found. Pass the path: setup.bat C:\path\to\python.exe
endlocal
exit /b 1

:no_venv_tool
echo Could not create venv. Install virtualenv into the base Python: "%PY%" -m pip install virtualenv
endlocal
exit /b 1

:fail
echo Setup failed.
endlocal
exit /b 1
