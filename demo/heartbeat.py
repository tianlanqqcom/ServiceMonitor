"""示例服务：持续输出心跳日志，并制造可观测的 CPU / 内存波动。"""

import math
import random
import sys
import time

iteration = 0
buffers: list[bytearray] = []
while True:
    iteration += 1
    load = sum(math.sin(x) for x in range(40000))
    buffers.append(bytearray(random.getrandbits(8) for _ in range(200_000)))
    if len(buffers) > 40:
        buffers.pop(0)
    print(
        f"beat #{iteration} load={load:.2f} cached={len(buffers) * 200}KB",
        flush=True,
    )
    if iteration % 12 == 0:
        print("警告：心跳变慢", file=sys.stderr, flush=True)
    time.sleep(1.0)
