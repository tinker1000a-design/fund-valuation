@echo off
title 基金估值引擎 - 正在运行...

:: 解决中文乱码（关键一行）
chcp 65001 >nul

echo ========================================================
echo           基金盘中估值引擎 v3.5（本地版）
echo ========================================================
echo.

cd /d "%~dp0"

echo 当前目录: %cd%
echo 开始执行估值脚本...
echo.

set "PYTHON_EXE=%FUND_SYSTEM_PYTHON%"
if not defined PYTHON_EXE if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\fund_python\python313\python.exe" set "PYTHON_EXE=%USERPROFILE%\fund_python\python313\python.exe"
if defined PYTHON_EXE (
  "%PYTHON_EXE%" "%~dp0daily_valuation.py"
) else (
  py -3 "%~dp0daily_valuation.py"
)

echo.
echo ========================================================
echo 脚本执行完毕！
echo.

timeout /t 30