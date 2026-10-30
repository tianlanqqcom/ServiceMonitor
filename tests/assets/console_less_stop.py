"""在无控制台（pythonw）的进程里验证优雅停止，结果写回 argv[1]。

由 tests/test_process_control.py 用 pythonw.exe 拉起：开机自启就是这条路径，
这时监控自己收不到也发不出共享控制台的 CTRL_BREAK，必须走附着对方控制台的路子。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from service_monitor.process import control

result_path = Path(sys.argv[1])
python_exe = sys.argv[2]
child = Path(__file__).with_name("break_on_signal.py")

proc = control.spawn('"%s" -u "%s"' % (python_exe, child))
try:
    time.sleep(1.0)
    sent = control.graceful_terminate(proc, proc.pid)
    deadline = time.time() + 15
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.2)
    out = proc.stdout.read().decode("utf-8", "replace")
    result_path.write_text(
        "sent=%s code=%s out=%s" % (sent, proc.returncode, out), encoding="utf-8"
    )
finally:
    if proc.poll() is None:
        control.force_kill(proc.pid, proc)
