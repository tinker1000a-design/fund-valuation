@echo off
title 往 fund_system 环境安装 Python 包
chcp 65001 >nul

echo ========================================================
echo       向 fund_system 专用环境(venv)安装第三方包
echo ========================================================
echo.
echo 环境位置: %USERPROFILE%\fund_python\python313
echo.
echo 用法: 把要装的包名写在下方的 set PACKAGES 里,
echo       例如 set PACKAGES=requests numpy pandas
echo.
set PACKAGES=%1
if "%PACKAGES%"=="" set PACKAGES=requests

echo 即将安装: %PACKAGES%
echo 注意: 请勿直接 pip install 到 versions 目录,那里会被 WorkBuddy
echo       更新时覆盖重置,装进 venv 才安全!
echo.
pause

"%USERPROFILE%\fund_python\python313\python.exe" -m pip install %PACKAGES%

echo.
echo ========================================================
echo 安装完成! 若想验证: 运行 环境自检.bat
echo ========================================================
echo.
pause
