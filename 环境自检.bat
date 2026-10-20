@echo off
title fund_system 环境自检
chcp 65001 >nul
echo ========================================================
echo           fund_system 环境健康自检
echo ========================================================
echo.

echo [1/3] 检查 venv Python 是否存在...
if exist "%USERPROFILE%\fund_python\python313\python.exe" (
    echo   OK: venv Python 存在
) else (
    echo   FAIL: venv 不存在! 环境可能被破坏, 请重新创建
    pause & exit /b 1
)

echo.
echo [2/3] 检查核心依赖 (akshare / pandas / requests)...
"%USERPROFILE%\fund_python\python313\python.exe" -c "import akshare, pandas, requests; print('   OK: akshare', akshare.__version__, '| pandas', pandas.__version__, '| requests', requests.__version__)"
if errorlevel 1 (
    echo   FAIL: 依赖缺失! 请运行 一键安装Python包.bat
) else (
    echo   PASS: 核心依赖全部就绪
)

echo.
echo [3/3] 测试行情接口连通性...
"%USERPROFILE%\fund_python\python313\python.exe" -c "import urllib.request; r=urllib.request.urlopen('http://qt.gtimg.cn/q=sh600519', timeout=8); print('   OK: 腾讯行情接口可达, 返回', len(r.read()), '字节')"
if errorlevel 1 (
    echo   FAIL: 网络或接口异常
) else (
    echo   PASS: 行情接口正常
)

echo.
echo ========================================================
echo 自检完成!
echo ========================================================
echo.
pause
