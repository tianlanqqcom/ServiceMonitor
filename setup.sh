#!/usr/bin/env bash
# Linux / macOS 一键初始化：创建 .venv 并安装依赖
# 用法： bash setup.sh
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

PYTHON="${PYTHON:-python3}"

if [ ! -d "$root/.venv" ]; then
  echo "[1/3] 创建虚拟环境 .venv ..."
  "$PYTHON" -m venv .venv
else
  echo "[1/3] 已存在 .venv，跳过创建"
fi

echo "[2/3] 升级 pip ..."
"$root/.venv/bin/python" -m pip install --upgrade pip >/dev/null

echo "[3/3] 安装依赖（PySide6 / psutil / pytest）..."
"$root/.venv/bin/python" -m pip install -r "$root/requirements.txt"

echo
echo "完成。启动工具： ./run.sh"
echo "提示：Linux 上 Qt 需要 libEGL/libxcb 等系统库，缺失时请安装发行版的 qt6 运行库。"
