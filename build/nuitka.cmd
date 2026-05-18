@echo off
REM Reproducible Nuitka build for LocalEQUS.exe (C6.1).
REM
REM Usage from the repo root:
REM     build\nuitka.cmd            -- build into dist\LocalEQUS\
REM     build\nuitka.cmd --clean    -- delete dist\ first
REM
REM Requires the build deps:
REM     pip install -e ".[build]"

setlocal
cd /d "%~dp0\.."
python build\build_config.py %*
exit /b %ERRORLEVEL%
