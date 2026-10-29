# Windows 一键初始化：创建 .venv 并安装依赖
# 用法： powershell -ExecutionPolicy Bypass -File setup.ps1
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path "$root\.venv")) {
    Write-Host "[1/3] 创建虚拟环境 .venv ..."
    & $Python -m venv .venv
} else {
    Write-Host "[1/3] 已存在 .venv，跳过创建"
}

Write-Host "[2/3] 升级 pip ..."
& "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip

Write-Host "[3/3] 安装依赖（PySide6 / psutil / pytest）..."
& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements.txt"

Write-Host ""
Write-Host "完成。启动工具： .\run.ps1"
