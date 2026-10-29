@echo off
setlocal
title ServiceMonitor
cd /d "%~dp0"

rem Keep this file ASCII-only on purpose. cmd.exe re-reads a batch file by byte offset,
rem so a UTF-8 Chinese comment line (especially after "chcp 65001") gets torn apart and
rem the fragment is executed as a command.
rem -Hidden starts the GUI attached to a hidden console: no window is left on screen and
rem monitored services still share that console, so CTRL_BREAK keeps working.
rem First run prepares .venv through run.ps1 / setup.ps1 and takes a few minutes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Hidden
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo [ServiceMonitor] Launcher failed with exit code %RC%.
  echo Run "powershell -ExecutionPolicy Bypass -File run.ps1" to read the error output.
  pause
)
endlocal
exit /b %RC%
