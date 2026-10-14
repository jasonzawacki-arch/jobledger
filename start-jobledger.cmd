@echo off
rem Starts JobLedger and makes it reachable from your phone through Tailscale.
rem Double-click to run, or put a shortcut to this file in your Startup folder (Win+R, shell:startup).
rem Optional password: remove "rem " from the next line and change the value.
rem set JL_TOKEN=change-me

title JobLedger
cd /d "%~dp0"

if not exist "dist\index.html" (
  echo Building the app for the first time...
  call npm install || goto :fail
  call npm run build || goto :fail
)

py -3 -m jobledger --tailscale
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo JobLedger stopped with an error. Read the message above.
pause
exit /b 1
