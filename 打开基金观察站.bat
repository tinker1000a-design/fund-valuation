@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0open_fund_watch.ps1"
if errorlevel 1 (
  echo Fund watch failed to start. Check the logs folder.
  pause
)
