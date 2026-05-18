@echo off
REM Sign LocalEQUS.exe and the installer with the OV certificate (C6.3).
REM
REM Usage from the repo root:
REM     build\sign.cmd                       REM signs both default targets
REM     build\sign.cmd path\to\file.exe      REM signs just that file
REM
REM Environment variables (set ONE of the two cert groups):
REM
REM   .pfx mode:
REM     SIGNING_CERT=C:\secure\codesign.pfx
REM     SIGNING_PASSWORD=...
REM
REM   Cert store mode (EV certs on hardware tokens):
REM     SIGNING_THUMBPRINT=<hex SHA-1, no separators>
REM
REM   Optional:
REM     SIGNING_TIMESTAMP_URL=http://timestamp.digicert.com  (default)
REM     SIGNTOOL=C:\path\to\signtool.exe  (auto-discovered otherwise)
REM
REM Prerequisites:
REM   - Windows SDK installed (for signtool.exe)
REM   - build\nuitka.cmd and/or build\installer.cmd have produced
REM     the file(s) to sign

setlocal
cd /d "%~dp0\.."
python build\sign.py %*
exit /b %ERRORLEVEL%
