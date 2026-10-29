"""测试脚本：交替输出 stdout / stderr 若干行，然后长时间睡眠。"""

import sys
import time

for index in range(5):
    print(f"out-{index}", flush=True)
    if index == 2:
        print("err-oops", file=sys.stderr, flush=True)
    time.sleep(0.25)

time.sleep(3600)
