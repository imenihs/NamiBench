@echo off
rem NamiBench setup for Windows. Double-click it, or run it from cmd / PowerShell (.\setup.bat).
rem
rem   setup.bat [C:\path\to\python.exe] [--no-pause]
rem
rem Two ways to use it:
rem   - Next to NamiBench_pack.zip (made by pack.bat): unpacks it into .\NamiBench and sets that folder up.
rem   - Inside a NamiBench folder: sets this folder up.
rem Steps: Python 3.13+ (64-bit) -> venv (made again if the one here does not run, e.g. copied from another PC)
rem -> packages from requirements.txt -> libiio for PlutoSDR (official v0.26, checked by SHA-256) -> self-check.
rem Needs internet for the packages and libiio. Python itself is not installed by this file.

setlocal EnableExtensions
set "NAME=NamiBench"
set "PACK=NamiBench_pack.zip"
set "PKG=namibench"
set "HERE=%~dp0"
set "PY="
set "NOPAUSE="

:args
if "%~1"=="" goto :args_done
if /i "%~1"=="--no-pause" goto :arg_nopause
set "PY=%~1"
shift
goto :args
:arg_nopause
set "NOPAUSE=1"
shift
goto :args
:args_done

rem Pause at the end only when started by double-click (its own cmd window would close at once)
set "DBLCLICK="
echo.%cmdcmdline% | find /i "%~nx0" >nul 2>nul && set "DBLCLICK=1"
if defined NOPAUSE set "DBLCLICK="

cd /d "%HERE%"
if exist "%PKG%\__init__.py" goto :in_project
if not exist "%PACK%" goto :no_project
echo Unpacking %PACK% into %HERE%%NAME% ...
if not exist "%NAME%" mkdir "%NAME%"
where tar >nul 2>nul
if errorlevel 1 goto :unpack_ps
tar -xf "%PACK%" -C "%NAME%"
if errorlevel 1 goto :fail_unpack
goto :unpacked
:unpack_ps
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%HERE%%PACK%' -DestinationPath '%HERE%%NAME%' -Force"
if errorlevel 1 goto :fail_unpack
:unpacked
cd /d "%HERE%%NAME%"
if not exist "%PKG%\__init__.py" goto :fail_unpack
:in_project
echo Project folder: %CD%

rem ---- Python 3.13 or later, 64-bit (the first usable one of: the argument, NAMIBENCH_PYTHON, py -3.13,
rem the usual install folders, python on PATH)
set "GIVEN=%PY%"
set "PY="
if not "%GIVEN%"=="" goto :check_given
if not "%NAMIBENCH_PYTHON%"=="" set "GIVEN=%NAMIBENCH_PYTHON%"
if not "%GIVEN%"=="" goto :check_given
where py >nul 2>nul
if errorlevel 1 goto :search_known
for /f "delims=" %%i in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do call :try "%%i"
:search_known
call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try "%ProgramFiles%\Python313\python.exe"
rem python on PATH (the Microsoft Store shortcut in WindowsApps only opens the Store, so it is skipped)
for /f "delims=" %%i in ('where python 2^>nul ^| findstr /v /i "\\WindowsApps\\"') do call :try "%%i"
if "%PY%"=="" goto :no_python
goto :have_python
:check_given
call :try "%GIVEN%"
if "%PY%"=="" goto :no_python
:have_python
echo Base Python: %PY%

rem ---- venv: keep it only if its base Python (home in pyvenv.cfg) is on this PC and it runs here
if not exist "venv\Scripts\python.exe" goto :make_venv
set "VHOME="
if exist "venv\pyvenv.cfg" for /f "usebackq tokens=1,* delims==" %%a in ("venv\pyvenv.cfg") do if /i "%%a"=="home " set "VHOME=%%b"
if defined VHOME for /f "tokens=*" %%c in ("%VHOME%") do set "VHOME=%%c"
if not defined VHOME goto :rebuild_venv
if not exist "%VHOME%\python.exe" goto :rebuild_venv
"venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 13) else 1)" >nul 2>nul
if not errorlevel 1 goto :install
:rebuild_venv
echo The venv here was made for another PC or does not run here. Making it again...
rmdir /s /q venv
if exist venv goto :venv_busy
:make_venv
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
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :fail
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

rem ---- libiio for PlutoSDR (not fatal: GigaSt and RTL-SDR work without it)
"venv\Scripts\python.exe" tools\get_libiio.py
if errorlevel 1 echo Note: libiio could not be placed. PlutoSDR cannot be used until setup.bat runs again with internet.

rem ---- self-check
echo Checking...
"venv\Scripts\python.exe" tools\check_setup.py
if errorlevel 1 goto :fail
echo.
echo Done. Start NamiBench with run.bat in %CD%
echo PlutoSDR also needs its USB driver on this PC (Analog Devices "PlutoSDR-M2k-USB-Drivers"), once per PC.
call :end 0
exit /b 0

rem ---- helpers
:try
rem Keeps the first usable Python: sets PY to %1 if PY is still empty and %1 is Python 3.13+ 64-bit
if not "%PY%"=="" goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import sys, struct; sys.exit(0 if sys.version_info[:2] >= (3, 13) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 goto :eof
set "PY=%~1"
goto :eof

:end
if defined DBLCLICK pause
goto :eof

rem ---- failures
:no_project
echo This folder has neither the %NAME% files nor %PACK%.
echo Put setup.bat next to %PACK% (made by pack.bat), or run it inside the %NAME% folder.
call :end 1
exit /b 1

:fail_unpack
echo Could not unpack %PACK%.
call :end 1
exit /b 1

:no_python
echo Python 3.13 (64-bit) was not found.
echo Install it from https://www.python.org/downloads/ (Windows installer, 64-bit), then run setup.bat again,
echo or pass its path: setup.bat C:\path\to\python.exe
call :end 1
exit /b 1

:venv_busy
echo Could not remove the old venv. Close NamiBench and anything using this folder, then run setup.bat again.
call :end 1
exit /b 1

:no_venv_tool
echo Could not create venv. Install virtualenv into the base Python: "%PY%" -m pip install virtualenv
call :end 1
exit /b 1

:fail
echo Setup failed (see the messages above).
call :end 1
exit /b 1
