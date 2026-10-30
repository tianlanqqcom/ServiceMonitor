"""跨平台的进程启动 / 停止。

Windows 用 cmd.exe（shell=True），Linux/macOS 用 /bin/sh（shell=True）；
命令产生的整棵进程树都被纳入统计，停止时连同子进程一起结束。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    # 被监控的服务不加 CREATE_NO_WINDOW：那个标志会让子进程另开一个控制台，
    # 于是 CTRL_BREAK 再也送不进去（GenerateConsoleCtrlEvent 只能发给调用方
    # 自己控制台里的进程组），优雅停止就退化成每次强杀。
    # 实测也不会闪窗：stdout/stderr 是管道、stdin 是 DEVNULL，三个句柄都不指向
    # 控制台时 conhost 根本不去创建窗口。
    _DETACH_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(
        subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0
    )
else:
    _DETACH_FLAGS = 0

# nvidia-smi、taskkill 这类内部工具就不同了：stdin 继承自本进程，从 pythonw 启动时
# Windows 会给每一次调用单独开一个黑窗口，界面上就是一闪一闪（GPU 每 5 秒调两次，
# 看起来就是"不定时闪一个 cmd 窗口"）。
HIDDEN_CONSOLE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def _locked_package_roots() -> tuple[str, ...]:
    """Windows 应用包的安装根；包外的进程连目录都进不去（ERROR_ACCESS_DENIED）。"""
    if not IS_WINDOWS:
        return ()
    roots: list[str] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if not base:
            continue
        root = os.path.normcase(os.path.normpath(os.path.join(base, "WindowsApps")))
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _inside_locked_package(entry: str) -> bool:
    if not entry:
        return False
    path = os.path.normcase(os.path.normpath(entry))
    return any(path == root or path.startswith(root + os.sep) for root in _locked_package_roots())


def child_env() -> dict[str, str]:
    """交给被监控服务的环境变量。

    监控自己跑在 Store 版 Python 上时，解释器会把它的安装包目录塞到 PATH 第 0 位，
    而那个目录包外的进程根本没法执行：服务里一句 `python` 命中它就只打印
    "拒绝访问。"，用户双击同一个 bat 却是好的。留着它对子进程只有害处。
    """
    env = os.environ.copy()
    if IS_WINDOWS and env.get("PATH"):
        env["PATH"] = os.pathsep.join(
            entry for entry in env["PATH"].split(os.pathsep) if not _inside_locked_package(entry)
        )
    # 服务的 stdout 是管道，Python 于是改成 8KB 满缓冲：启动时打印的版本号、banner
    # 一直躺在缓冲区里，界面上就是"少了开头一段"，进程被强杀后彻底丢失（stderr
    # 是行缓冲，所以请求日志照常实时出现，反差更明显）。
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def popen_kwargs(cwd: str | None) -> dict:
    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "cwd": cwd or None,
        "env": child_env(),
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = _DETACH_FLAGS
    else:
        kwargs["preexec_fn"] = _new_session
    return kwargs


def _new_session() -> None:  # pragma: no cover - 仅在子进程中执行
    os.setsid()


def spawn(command: str, cwd: str | None = None) -> subprocess.Popen:
    if not command.strip():
        raise ValueError("启动命令不能为空")
    # 管道保持二进制：子进程输出可能是 GBK/UTF-8，交给 supervisor._decode 逐级回退，
    # 用 subprocess 的 text 模式解码会把非 UTF-8 字符提前变成 U+FFFD。
    return subprocess.Popen(command, shell=True, **popen_kwargs(cwd))


def tree_pids(root_pid: int) -> list[int]:
    """root 及其所有后代；psutil 不可用时退回 root 自己。"""
    try:
        import psutil

        root = psutil.Process(root_pid)
    except Exception:
        return [root_pid]
    pids = [root_pid]
    try:
        for child in root.children(recursive=True):
            pids.append(int(child.pid))
    except Exception:
        pass
    return pids


def resolve_main_pid(root_pid: int) -> int:
    """沿“唯一子进程”链下钻，找到真正的服务进程（cmd.exe -> python.exe -> …）。"""
    try:
        import psutil

        node = psutil.Process(root_pid)
    except Exception:
        return root_pid
    current = root_pid
    for _ in range(16):
        try:
            children = node.children()
        except Exception:
            break
        if len(children) != 1:
            break
        current = int(children[0].pid)
        node = children[0]
    return current


def wait_gone(pids: list[int], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return not any(_alive(p) for p in pids)


def _alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


# 一个进程同时只能挂在一个控制台上，attach → send → detach 必须整段做完
_console_lock = threading.Lock()


def _send_break_via_child_console(root_pid: int) -> bool:
    """把 CTRL_BREAK 送进服务自己的控制台。

    监控从 pythonw（开机自启就是这条路径）启动时自己没有控制台，`send_signal` 只会
    报"句柄无效"。这里临时附着到服务那棵进程树的控制台，只发给它自己那个进程组，
    随后立刻 detach。实测附着期间 conhost 的窗口仍然是隐藏的（服务的三个标准句柄
    都不指向控制台），所以既不会闪黑框，事件也不会打断监控自己。
    """
    import ctypes

    with _console_lock:
        kernel32 = ctypes.windll.kernel32
        if not kernel32.AttachConsole(root_pid):
            return False
        try:
            return bool(kernel32.GenerateConsoleCtrlEvent(signal.CTRL_BREAK_EVENT, root_pid))
        finally:
            kernel32.FreeConsole()


def graceful_terminate(proc: subprocess.Popen | None, root_pid: int) -> bool:
    """发送第一次终止信号；返回是否成功发出。"""
    if IS_WINDOWS:
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            return True
        except OSError:
            # 监控自己没有控制台（开机自启跑在 pythonw 上）时句柄无效，
            # 绝不能退化成 proc.terminate() 直接强杀，那样服务连收尾的机会都没有
            return _send_break_via_child_console(root_pid)
    try:
        os.killpg(os.getpgid(root_pid), signal.SIGTERM)
        return True
    except OSError:
        if proc is not None:
            try:
                proc.terminate()
                return True
            except OSError:
                return False
        return False


def force_kill(root_pid: int, proc: subprocess.Popen | None) -> None:
    """强杀整棵树。"""
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(root_pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=HIDDEN_CONSOLE_FLAGS,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        for pid in tree_pids(root_pid):
            _kill_one(pid)
        return
    try:
        os.killpg(os.getpgid(root_pid), signal.SIGKILL)
    except OSError:
        pass
    for pid in tree_pids(root_pid):
        _kill_one(pid)
    if proc is not None:
        try:
            proc.kill()
        except OSError:
            pass


def _kill_one(pid: int) -> None:
    try:
        import psutil

        p = psutil.Process(pid)
        p.kill()
    except Exception:
        pass
