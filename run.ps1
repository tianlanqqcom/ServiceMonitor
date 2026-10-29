# Windows 启动：优先使用 .venv
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "未找到 .venv，请先运行 setup.ps1" -ForegroundColor Yellow
    Write-Host "正在初始化 ..."
    & "$root\setup.ps1"
}
& $py "$root\monitor.py" @args
