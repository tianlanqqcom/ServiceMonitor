"""收到 SIGBREAK 就打印一行再正常退出（Windows 上的 CTRL_BREAK 会走到这里）。"""

import signal
import time


def _on_break(signum, frame):
    print("GRACEFUL-BREAK", flush=True)
    raise SystemExit(0)


signal.signal(signal.SIGBREAK, _on_break)
print("service-up", flush=True)
while True:
    time.sleep(0.1)
