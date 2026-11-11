@echo off
rem Updates SynergyScan on this PC: double-click it, nothing else to do.
rem
rem It downloads the current installer from GitHub and runs it. The installer
rem keeps the database and settings, never installs an older release, and on a
rem PC that already runs the newest release only refreshes the launcher and
rem helper scripts - which updates from inside the app never replace.
rem Anything after the file name is passed to the installer (for testing).
setlocal
title Updating SynergyScan
echo.
echo   Updating SynergyScan. This takes a minute or two.
echo   Please leave this window open until it says it is finished.
echo.

set "PS1=%TEMP%\synergyscan-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/DuskMcDusk/SynergyScan/main/bootstrap/install.ps1' -OutFile $env:PS1 -UseBasicParsing"
if errorlevel 1 goto nodownload

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
if errorlevel 1 goto failed

echo.
echo   ============================================================
echo     SynergyScan is up to date.
echo     Restart the computer to finish the update.
echo     Save your work in other programs first.
echo   ============================================================
echo.
choice /c YN /n /m "  Restart the computer now? Press Y for yes, or N for later: "
if errorlevel 2 goto later
shutdown /r /t 10 /c "Restarting to finish the SynergyScan update"
exit /b 0

:later
echo.
echo   OK. The update finishes the next time the computer restarts.
echo   SynergyScan keeps working until then.
echo.
pause
exit /b 0

:nodownload
echo.
echo   The update could not be downloaded.
echo   Check that this computer is connected to the internet, then
echo   double-click this file again. Nothing has been changed.
echo.
pause
exit /b 1

:failed
echo.
echo   ============================================================
echo     The update did not finish. SynergyScan still works as
echo     before and your data is safe.
echo     Please take a photo of this window and send it to support.
echo   ============================================================
echo.
pause
exit /b 1
