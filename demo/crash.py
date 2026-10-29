"""示例服务：输出几行日志后以退出码 3 结束，用于验证“异常退出”状态。"""

import sys
import time

print("示例·异常退出：开始工作")
time.sleep(0.5)
print("模拟一次失败", file=sys.stderr)
raise SystemExit(3)
