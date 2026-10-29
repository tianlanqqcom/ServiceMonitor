# Windows 启动：优先使用 .venv
# -Hidden 给双击 start-monitor.bat 用：窗口不出现，但进程仍挂在一个控制台里
param(
    [switch]$Hidden
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "未找到 .venv，请先运行 setup.ps1" -ForegroundColor Yellow
    Write-Host "正在初始化 ..."
    & "$root\setup.ps1"
}

if ($Hidden) {
    # 用控制台版 python.exe 加 SW_HIDE，而不是 pythonw：前者仍然拥有控制台，被监控的服务
    # 与它共用，停止时收得到 CTRL_BREAK；pythonw 没有控制台，服务只能等超时后强杀。
    $extra = @($args | ForEach-Object { $_ })
    Start-Process -FilePath $py `
        -ArgumentList (@("`"$root\monitor.py`"") + $extra) `
        -WindowStyle Hidden `
        -WorkingDirectory $root
    exit 0
}

& $py "$root\monitor.py" @args
