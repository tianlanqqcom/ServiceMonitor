"""测试脚本：输出中文，用于验证管道解码回退（Windows 重定向时子进程按 GBK 写）。"""

import sys
import time

print("警告：心跳变慢", flush=True)
print("中文标准错误", file=sys.stderr, flush=True)
time.sleep(3600)
