@echo off
rem Start NamiBench (desktop window). Double-click, or run from cmd / PowerShell (.\run.bat).
rem Arguments are passed through, e.g.  run.bat --web   run.bat serve --lan
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  echo venv was not found. Run setup.bat first.
  pause
  exit /b 1
)
"venv\Scripts\python.exe" -m namibench %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
endlocal & exit /b %RC%
