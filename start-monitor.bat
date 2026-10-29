@echo off
chcp 65001 >nul 2>&1
setlocal
title ServiceMonitor
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "HOME=%APPDATA%\ServiceMonitor"
set "LOG=%HOME%\launch-error.log"

rem 首次运行准备环境：虚拟环境 + 依赖（控制行保持 ASCII，中文只出现在 echo 里）
if not exist "%PY%" call :create_venv || goto :fail
"%PY%" -c "import PySide6, psutil" >nul 2>&1 || call :install_deps || goto :fail

if not exist "%PYW%" set "PYW=%PY%"
if not exist "%HOME%" mkdir "%HOME%" 2>nul

rem 没有控制台窗口（pythonw），把启动异常留在日志里，否则失败无从查起
if exist "%HOME%" (
  start "ServiceMonitor" "%PYW%" monitor.py 2>"%LOG%"
) else (
  start "ServiceMonitor" "%PYW%" monitor.py
)

ping -n 4 127.0.0.1 >nul 2>&1
echo ServiceMonitor 已启动。窗口没出现时，请查看运行日志：
echo   "%LOG%"
echo 提示：关闭窗口默认只缩到系统托盘，右键托盘图标才能退出监控。
ping -n 4 127.0.0.1 >nul 2>&1
endlocal
exit /b 0

:create_venv
echo [ServiceMonitor] 首次运行，正在创建虚拟环境 .venv ...
py -3 -m venv .venv >nul 2>&1
if not exist "%PY%" python -m venv .venv >nul 2>&1
if not exist "%PY%" exit /b 1
exit /b 0

:install_deps
echo [ServiceMonitor] 正在安装依赖 PySide6 与 psutil，首次运行需要几分钟...
"%PY%" -m pip install --upgrade pip >nul 2>&1
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
exit /b 0

:fail
echo.
echo [错误] 启动环境准备失败，请依次确认：
echo   1. 已安装 Python 3.9+，命令行运行 python --version 能正常返回
echo   2. 本脚本所在目录可写（虚拟环境建在 .venv）
echo   3. 网络可以访问 PyPI（首次运行需要下载 PySide6）
echo 也可以手动执行： setup.ps1（PowerShell）或 python -m pip install -r requirements.txt
pause
endlocal
exit /b 1
