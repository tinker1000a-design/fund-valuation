@echo off
title 基金持仓透视镜 - 暴君索要模式 v4.0
color 0A
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo ========================================================
echo           基金持仓透视镜 (暴君索要版) v4.0
echo           正在强制更新所有基金最新持仓...
echo ========================================================
echo.

cd /d "%~dp0"

echo 当前工作目录: %cd%
echo.

set PYTHON_HOME=%USERPROFILE%\fund_python\python313
"%PYTHON_HOME%\python.exe" update_fund_holdings.py

echo.
echo ========================================================
echo                   全部任务执行完毕！
echo ========================================================
echo.

pause
