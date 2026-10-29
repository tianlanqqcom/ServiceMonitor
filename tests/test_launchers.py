"""启动脚本自身的回归位：cmd.exe / PowerShell 解析脚本的方式很特别，踩过的坑都要钉住。

- .bat 必须是纯 ASCII：cmd.exe 按字节偏移重读脚本，UTF-8 中文行会被切成半截当成命令执行
  （现象：'w：被监控的服务…' is not recognized as an internal or external command）。
- .bat 必须是 CRLF：纯 LF 时 cmd.exe 会把多行当成一行解析。
- .ps1 必须带 UTF-8 BOM：Windows PowerShell 5.1 没有 BOM 就按本地 ANSI 码页解码，中文变乱码。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> bytes:
    return (ROOT / name).read_bytes()


def test_bat_is_pure_ascii():
    raw = read("start-monitor.bat")
    non_ascii = bytes(byte for byte in raw if byte > 127)
    assert not non_ascii, f"cmd.exe 会错误解析 .bat 里的 UTF-8 中文：{non_ascii[:40]!r}"


def test_windows_launchers_use_crlf():
    for name in ("start-monitor.bat", "run.ps1", "setup.ps1"):
        raw = read(name)
        assert raw.count(b"\r\n") == raw.count(b"\n"), f"{name} 必须整文件 CRLF"


def test_powershell_scripts_carry_a_utf8_bom():
    for name in ("run.ps1", "setup.ps1"):
        assert read(name).startswith(b"\xef\xbb\xbf"), f"{name} 缺 BOM，PowerShell 5.1 会读成乱码"


def test_bat_delegates_to_run_ps1_in_hidden_console_mode():
    text = read("start-monitor.bat").decode("ascii")
    assert "run.ps1" in text, "环境准备与启动统一交给 run.ps1，bat 不再自己建 venv"
    assert "-Hidden" in text, "双击启动要隐藏控制台"
    assert "pythonw.exe" not in text, "pythonw 没有控制台，被监控的服务收不到 CTRL_BREAK"

    ps1 = read("run.ps1").decode("utf-8-sig")
    assert "[switch]$Hidden" in ps1, "run.ps1 要提供隐藏启动的开关"
    assert "WindowStyle Hidden" in ps1, "窗口靠 Start-Process 的 SW_HIDE 压掉"
    assert "pythonw.exe" not in ps1, "-Hidden 也要用控制台版 python.exe，否则优雅停止退化成强杀"
