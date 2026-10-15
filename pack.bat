@echo off
rem Makes NamiBench_pack.zip to carry NamiBench to another PC.
rem It holds the files git tracks, as they are in this folder now (no venv, no libiio, no local notes).
rem On the other PC: put NamiBench_pack.zip and setup.bat in one folder and run setup.bat.
rem
rem   pack.bat [--no-pause]

setlocal EnableExtensions
set "PACK=NamiBench_pack.zip"
set "DBLCLICK="
echo.%cmdcmdline% | find /i "%~nx0" >nul 2>nul && set "DBLCLICK=1"
if /i "%~1"=="--no-pause" set "DBLCLICK="
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 goto :no_git
where tar >nul 2>nul
if errorlevel 1 goto :no_tar
set "LIST=%TEMP%\namibench_pack_%RANDOM%%RANDOM%.txt"
git -c core.quotepath=off ls-files > "%LIST%"
if errorlevel 1 goto :fail
git status --porcelain --untracked-files=no | findstr /r "." >nul && echo Note: some tracked files have changes not committed; the pack has them as they are now.
if exist "%PACK%" del "%PACK%"
tar -a -c -f "%PACK%" -T "%LIST%"
if errorlevel 1 goto :fail
del "%LIST%"
for %%f in ("%PACK%") do echo Made %%~ff (%%~zf bytes)
echo Copy it and setup.bat to the other PC, into one folder, and run setup.bat there.
if defined DBLCLICK pause
exit /b 0

:no_git
echo git was not found. pack.bat lists the files with git.
goto :end_fail
:no_tar
echo tar was not found (Windows 10 1803 or later has it).
goto :end_fail
:fail
echo Could not make %PACK%.
:end_fail
if exist "%LIST%" del "%LIST%"
if defined DBLCLICK pause
exit /b 1
