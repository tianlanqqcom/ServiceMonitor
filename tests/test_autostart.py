"""开机自启：Linux 桌面文件与 Windows Run 项，全部落在隔离位置。"""

from __future__ import annotations

import os
import sys

import pytest

from service_monitor import autostart


def test_project_root_points_at_the_repository():
    assert (autostart.project_root() / "monitor.py").exists()


def test_launch_command_uses_the_project_entry_script():
    command = autostart.launch_command()
    assert command.strip(), "命令行不能为空，否则自启项无效"
    assert "monitor.py" in command


def test_unsupported_platform_refuses_with_a_clear_message(monkeypatch):
    monkeypatch.setattr(autostart, "supported", lambda: False)
    with pytest.raises(autostart.AutostartError, match="不支持"):
        autostart.set_enabled(True)


def test_linux_desktop_entry_round_trip(monkeypatch, tmp_path):
    """只改 XDG_CONFIG_HOME，写到临时目录，不碰用户真实配置。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = autostart._linux_config_path()
    assert path.parent.name == "autostart"
    assert path.suffix == ".desktop"
    assert not path.exists()

    autostart._linux_set_enabled(True)
    content = path.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in content
    assert "Terminal=false" in content
    assert f"Exec={autostart.launch_command()}" in content

    autostart._linux_set_enabled(False)
    assert not path.exists()
    autostart._linux_set_enabled(False)  # 重复取消不应报错


@pytest.mark.skipif(sys.platform != "win32", reason="Run 项只在 Windows 上可写")
def test_windows_run_key_round_trip_in_a_scratch_subkey():
    """只写一个临时子键并在结束时删掉：绝不碰真实的自启键。"""
    import winreg

    scratch = rf"Software\ServiceMonitorTest-{os.getpid()}"
    monkey = autostart._RUN_KEY
    autostart._RUN_KEY = scratch
    try:
        assert not autostart._win_exists()
        autostart._win_set_enabled(True)
        assert autostart._win_exists()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, scratch) as key:
            value, kind = winreg.QueryValueEx(key, autostart._RUN_VALUE)
        assert kind == winreg.REG_SZ
        assert "monitor.py" in value

        autostart._win_set_enabled(False)
        assert not autostart._win_exists()
        autostart._win_set_enabled(False)  # 重复取消不应报错
    finally:
        autostart._RUN_KEY = monkey
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, scratch)
        except OSError:
            pass
