@echo off
REM Compile the Inno Setup installer wrapping the Nuitka output (C6.2).
REM
REM Usage from the repo root:
REM     build\installer.cmd
REM
REM Prerequisites:
REM     1. build\nuitka.cmd has produced dist\LocalEQUS\LocalEQUS.exe
REM     2. Inno Setup 6 is installed (set $ISCC for non-default paths)

setlocal
cd /d "%~dp0\.."
python build\build_installer.py %*
exit /b %ERRORLEVEL%
