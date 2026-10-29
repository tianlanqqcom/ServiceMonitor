"""开机自启：Windows 写当前用户的 Run 项，Linux 写 XDG autostart 桌面文件。

只影响当前用户，随时可以取消；不使用管理员权限。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_ID = "ServiceMonitor"

# Windows：HKCU\Software\Microsoft\Windows\CurrentVersion\Run 下的一个字符串值
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = APP_ID


class AutostartError(RuntimeError):
    pass


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def supported() -> bool:
    return sys.platform in ("win32", "linux")


def _linux_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / f"{APP_ID.lower()}.desktop"


def config_path() -> Path:
    """当前平台上自启配置的实际落点。"""
    if sys.platform == "win32":
        return Path("HKEY_CURRENT_USER") / _RUN_KEY
    return _linux_config_path()


def launch_command() -> str:
    """用 pythonw（Windows）/ python + monitor.py 的绝对路径，避免开机弹控制台。"""
    root = project_root()
    script = root / "monitor.py"
    if sys.platform == "win32":
        venv = root / ".venv" / "Scripts"
        for name in ("pythonw.exe", "python.exe"):
            candidate = venv / name
            if candidate.exists():
                interpreter = candidate
                break
        else:
            interpreter = Path(sys.executable).with_name("pythonw.exe")
            if not interpreter.exists():
                interpreter = Path(sys.executable)
    else:
        venv_python = root / ".venv" / "bin" / "python"
        interpreter = venv_python if venv_python.exists() else Path(sys.executable)
    return subprocess.list2cmdline([str(interpreter), str(script)])


def _win_exists() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_VALUE)
        return True
    except OSError:
        return False


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_exists()
    if sys.platform == "linux":
        return _linux_config_path().exists()
    return False


def set_enabled(enabled: bool) -> None:
    if not supported():
        raise AutostartError("当前平台不支持开机自启")
    if sys.platform == "win32":
        _win_set_enabled(bool(enabled))
    else:
        _linux_set_enabled(bool(enabled))


def _win_set_enabled(enabled: bool) -> None:
    import winreg

    try:
        # CreateKeyEx 会在键不存在时创建（OpenKey 只读已存在的键）
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise AutostartError(f"写入注册表失败：{exc}") from exc


def _linux_set_enabled(enabled: bool) -> None:
    path = _linux_config_path()
    if not enabled:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise AutostartError(f"删除自启文件失败：{exc}") from exc
        return
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_ID}\n"
        "Comment=跨平台服务状态监控\n"
        f"Exec={launch_command()}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry, encoding="utf-8")
        os.chmod(path, 0o755)
    except OSError as exc:
        raise AutostartError(f"写入自启文件失败：{exc}") from exc
