@echo off
chcp 65001 >nul 2>&1
setlocal
title ServiceMonitor
cd /d "%~dp0"

rem 环境准备（建 .venv、装依赖）由 run.ps1 与 setup.ps1 负责，bat 只保留双击入口这一件事。
rem 走 run.ps1（控制台版 python.exe）而不是 pythonw：被监控的服务和监控进程共用这个控制台，
rem 停止服务时才收得到 CTRL_BREAK；nvidia-smi、taskkill 这些内部调用一律隐藏控制台。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [错误] ServiceMonitor 以退出码 %RC% 结束，请阅读上面的提示。
  pause
)
endlocal
exit /b %RC%
