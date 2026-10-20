@echo off
title Fund Actual NAV Fetcher
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"

echo ========================================================
echo             Fund Actual NAV Fetcher
echo             Fetching latest real daily change...
echo ========================================================
echo.

cd /d "%~dp0"

echo Current directory: %cd%
echo.

set PYTHON_HOME=%USERPROFILE%\fund_python\python313
"%PYTHON_HOME%\python.exe" update_actual_nav.py

echo.
echo ========================================================
echo                 Task finished
echo ========================================================
echo.

pause
