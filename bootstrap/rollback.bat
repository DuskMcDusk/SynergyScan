@echo off
REM Goes back to the previous version of SynergyScan.
REM Safe to double-click: it asks before changing anything.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rollback.ps1" -InstallRoot "%~dp0." %*
