"""真实进程的启动、树查找与停止（跨平台）。"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from service_monitor.process import control

SLEEP_SNIPPET = "import time; time.sleep(120)"
PRINT_SNIPPET = "import sys; print('hello'); sys.stderr.write('bad\\n')"


def command_of(exe: str, snippet: str) -> str:
    return f'"{exe}" -u -c "{snippet}"'


@pytest.mark.skipif(sys.platform not in ("win32", "linux", "darwin"), reason="覆盖 Windows 与 Linux")
def test_spawn_reads_output_and_kills_whole_tree(python_exe):
    proc = control.spawn(command_of(python_exe, PRINT_SNIPPET))
    try:
        stdout = proc.stdout.readline()
        stderr = proc.stderr.readline()
    finally:
        control.force_kill(proc.pid, proc)
        proc.wait(timeout=10)
    # 管道必须是二进制的：文本模式会用本地编码提前解码，非 UTF-8 输出会变成 U+FFFD
    assert isinstance(stdout, bytes)
    assert b"hello" in stdout
    assert b"bad" in stderr
    assert proc.returncode is not None


def test_tree_pids_and_resolve_main_pid(python_exe):
    proc = control.spawn(command_of(python_exe, SLEEP_SNIPPET))
    try:
        time.sleep(1.0)
        tree = control.tree_pids(proc.pid)
        main = control.resolve_main_pid(proc.pid)
        assert proc.pid in tree
        assert main in tree or main == proc.pid
        assert control._alive(main)
    finally:
        control.force_kill(proc.pid, proc)
        proc.wait(timeout=10)


def test_graceful_terminate_stops_process(python_exe):
    proc = control.spawn(command_of(python_exe, SLEEP_SNIPPET))
    control.graceful_terminate(proc, proc.pid)
    deadline = time.time() + 12
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.2)
    control.force_kill(proc.pid, proc)
    assert proc.poll() is not None
    assert control.wait_gone([proc.pid], 10)


def test_spawn_rejects_empty_command():
    with pytest.raises(ValueError):
        control.spawn("   ")


def test_spawn_uses_working_directory(python_exe, tmp_path):
    proc = control.spawn(command_of(python_exe, "import os;print(os.getcwd())"), cwd=str(tmp_path))
    out = proc.stdout.readline().strip()
    proc.wait(timeout=15)
    import os

    assert os.path.samefile(out, str(tmp_path))


def test_popen_kwargs_include_session_flags():
    kwargs = control.popen_kwargs(None)
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.PIPE
    if sys.platform == "win32":
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert callable(kwargs["preexec_fn"])

