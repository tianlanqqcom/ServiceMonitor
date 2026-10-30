"""真实进程的启动、树查找与停止（跨平台）。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ASSETS
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

    assert os.path.samefile(out, str(tmp_path))


def test_popen_kwargs_include_session_flags():
    kwargs = control.popen_kwargs(None)
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.PIPE
    if sys.platform == "win32":
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert callable(kwargs["preexec_fn"])


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK 是 Windows 的优雅停止方式")
def test_service_spawn_stays_on_the_shared_console():
    """被监控的服务不能加 CREATE_NO_WINDOW：那会让它另开一个控制台，CTRL_BREAK 再也送不进去，
    优雅停止退化成每次都强杀。不弹黑框靠的是三个标准句柄都不指向控制台。"""
    flags = control.popen_kwargs(None)["creationflags"]
    assert not flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP


def test_gpu_probe_hides_the_console_window(monkeypatch):
    """nvidia-smi 是控制台程序，每 5 秒两次调用，不隐藏就是一闪一闪的黑框。"""
    from service_monitor.metrics import gpu

    recorded: list[dict] = []

    def fake_run(cmd, **kwargs):
        recorded.append(kwargs)
        raise OSError("只关心传进去的参数")

    monkeypatch.setattr(gpu, "nvidia_smi_path", lambda: "nvidia-smi")
    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    assert gpu._run("query-gpu", "index,utilization.gpu", timeout=0.5) is None
    assert recorded
    flags = recorded[0].get("creationflags", 0)
    if sys.platform == "win32":
        assert flags & subprocess.CREATE_NO_WINDOW
    else:
        assert flags == 0, "非 Windows 不该传 Windows 专有标志"


def test_child_env_forces_unbuffered_python_output(monkeypatch):
    """stdout 是管道时 Python 会切到 8KB 满缓冲，启动 banner 卡在缓冲区里看不见。"""
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    assert control.child_env()["PYTHONUNBUFFERED"] == "1"
    monkeypatch.setenv("PYTHONUNBUFFERED", "0")
    assert control.child_env()["PYTHONUNBUFFERED"] == "0", "用户显式设过的值不该被覆盖"


def test_startup_banner_arrives_before_the_service_exits(python_exe):
    """不加 -u、只靠环境变量：banner 应当在进程还活着的时候就到，而不是等它退出。"""
    proc = control.spawn(
        f'"{python_exe}" -c "import time;print(\'BANNER\');time.sleep(20)"'
    )
    started = time.monotonic()
    try:
        line = proc.stdout.readline()
    finally:
        elapsed = time.monotonic() - started
        control.force_kill(proc.pid, proc)
        proc.wait(timeout=10)
    assert line.strip() == b"BANNER"
    assert elapsed < 5.0, f"等了 {elapsed:.1f}s，说明 Python 仍在满缓冲，输出憋到退出才 flush"


def test_child_env_never_touches_the_process_environment(monkeypatch):
    monkeypatch.setenv("SERVICE_MONITOR_PROBE", "1")
    env = control.child_env()
    env["SERVICE_MONITOR_PROBE"] = "0"
    assert os.environ["SERVICE_MONITOR_PROBE"] == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="应用包目录是 Windows 的事")
def test_child_env_drops_the_unexecutable_store_package_dir(monkeypatch):
    """Store 版 Python 会把自己的包目录塞到 PATH 第 0 位，包外的进程执行不了它。

    监控透传这个 PATH，服务里的 `python` 就只会打印"拒绝访问。"，
    而用户双击同一个 bat 是好的，所以交给服务的 PATH 必须没有这一条。
    """
    roots = control._locked_package_roots()
    assert roots, "Windows 上应该能算出应用包根目录"
    poisoned = roots[0] + "\\PythonSoftwareFoundation.PythonManager_25.2.240.0_x64__3847v3x7pw1km"
    alias = os.environ["USERPROFILE"] + "\\AppData\\Local\\Microsoft\\WindowsApps"
    monkeypatch.setenv("PATH", os.pathsep.join([poisoned, alias, r"C:\Windows\system32"]))

    kept = control.child_env()["PATH"].split(os.pathsep)
    assert poisoned not in kept
    assert alias in kept, "用户级 alias 目录里的 python.exe 是真正能跑的那个，不能一起删"
    assert r"C:\Windows\system32" in kept
    assert not any(control._inside_locked_package(entry) for entry in kept)


@pytest.mark.skipif(sys.platform != "win32", reason="应用包目录是 Windows 的事")
def test_service_receives_a_path_it_can_actually_use(monkeypatch):
    roots = control._locked_package_roots()
    monkeypatch.setenv("PATH", os.pathsep.join([roots[0] + "\\FakePackage", r"C:\Windows\system32"]))
    env = control.popen_kwargs(None)["env"]
    assert "FakePackage" not in env["PATH"]
    assert env["PATH"].split(os.pathsep) == [r"C:\Windows\system32"]


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill 只在 Windows 上调用")
def test_force_kill_hides_the_taskkill_console(monkeypatch):
    recorded: dict = {}

    def fake_run(cmd, **kwargs):
        recorded["cmd"] = cmd
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(control.subprocess, "run", fake_run)
    monkeypatch.setattr(control, "tree_pids", lambda pid: [pid])
    control.force_kill(424242, None)
    assert recorded["cmd"][0] == "taskkill"
    assert recorded["kwargs"]["creationflags"] & subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK 与控制台语义是 Windows 的事")
def test_console_less_stop_does_not_hard_kill(monkeypatch):
    """监控没有控制台时 send_signal 会报"句柄无效"，这时绝不能退回 proc.terminate()：
    那等于跳过整个宽限期直接强杀，服务连收尾的机会都没有。"""
    calls = []

    class Proc:
        def poll(self):
            return None

        def send_signal(self, sig):
            raise OSError(9, "句柄无效。")

        def terminate(self):
            calls.append("terminate")

    monkeypatch.setattr(
        control, "_send_break_via_child_console", lambda pid: calls.append("break") or True
    )
    assert control.graceful_terminate(Proc(), 4242) is True
    assert calls == ["break"]


@pytest.mark.skipif(sys.platform != "win32", reason="pythonw 没有控制台，只在 Windows 上成立")
def test_graceful_stop_reaches_the_service_without_a_monitor_console(tmp_path, python_exe):
    """开机自启跑在 pythonw 上（这是"绝不出现控制台"的代价），停止时 CTRL_BREAK
    仍然要送得进去：服务自己打印 GRACEFUL-BREAK 并以 0 退出，而不是被强杀。"""
    pythonw = Path(python_exe).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("该虚拟环境里没有 pythonw.exe")
    result = tmp_path / "result.txt"
    subprocess.run(
        [str(pythonw), str(ASSETS / "console_less_stop.py"), str(result), str(python_exe)],
        timeout=120,
    )
    text = result.read_text(encoding="utf-8")
    assert "sent=True" in text, text
    assert "GRACEFUL-BREAK" in text, text
    assert "code=0" in text, text

