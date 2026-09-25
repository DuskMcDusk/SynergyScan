@echo off
REM Double-click this to install SynergyScan.
REM
REM It exists only to get past PowerShell's execution policy, which blocks
REM install.ps1 from running by default on Windows 10 and would otherwise stop
REM the installation on step one.

setlocal
cd /d "%~dp0"

if not exist "%~dp0install.ps1" (
    echo.
    echo install.ps1 is missing. It must sit in the same folder as this file.
    echo Download both from the SynergyScan repository and try again.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*

echo.
pause
