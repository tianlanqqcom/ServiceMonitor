#!/usr/bin/env bash
# Linux / macOS 启动：优先使用 .venv
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="$root/.venv/bin/python"
if [ ! -x "$py" ]; then
  echo "未找到 .venv，请先运行 bash setup.sh"
  echo "正在初始化 ..."
  bash "$root/setup.sh"
fi
exec "$py" "$root/monitor.py" "$@"
