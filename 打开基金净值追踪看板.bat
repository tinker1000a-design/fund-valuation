@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "PYTHON_HOME=%USERPROFILE%\fund_python\python313"
"%PYTHON_HOME%\python.exe" "%~dp0基金净值追踪.py" --open
if errorlevel 1 echo 更新过程存在失败项，请查看 logs\fund_tracker_*.log
pause
