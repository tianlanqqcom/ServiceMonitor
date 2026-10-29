"""测试脚本：打印一行后以指定退出码结束（默认 3）。"""

import sys

print("before-exit", flush=True)
code = int(sys.argv[1]) if len(sys.argv) > 1 else 3
raise SystemExit(code)
