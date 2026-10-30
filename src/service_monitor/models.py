"""服务与会话的状态模型。"""

from __future__ import annotations

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_EXITED = "exited"
STATUS_CRASHED = "crashed"
STATUS_STOPPING = "stopping"
STATUS_WAITING = "waiting"
STATUS_LAUNCH_FAILED = "launch_failed"
STATUS_INTERRUPTED = "interrupted"
STATUS_LAUNCHER_EXITED = "launcher_exited"

STATUS_LABELS = {
    STATUS_RUNNING: "运行中",
    STATUS_STOPPED: "未启动",
    STATUS_EXITED: "已退出",
    STATUS_CRASHED: "异常退出",
    STATUS_STOPPING: "停止中",
    STATUS_WAITING: "守护等待中",
    STATUS_LAUNCH_FAILED: "启动失败",
    STATUS_INTERRUPTED: "监控中断",
    STATUS_LAUNCHER_EXITED: "脚本已退出",
}

# 守护重试上限的默认值：-1 = 无限重试，0 = 不重试，正数 = 连续失败时最多重试这么多次
DEFAULT_MAX_RESTARTS = 5

REASON_EXITED = "exited"
REASON_CRASHED = "crashed"
REASON_STOPPED = "stopped"
REASON_FAILED = "failed"
REASON_ORPHAN = "orphan"

REASON_LABELS = {
    REASON_EXITED: "正常退出",
    REASON_CRASHED: "异常退出",
    REASON_STOPPED: "手动停止",
    REASON_FAILED: "启动失败",
    REASON_ORPHAN: "监控中断",
    "": "运行中",
}

STREAM_STDOUT = "stdout"
STREAM_STDERR = "stderr"
STREAM_INFO = "info"

# Windows 下被外力终止的进程会带上 NTSTATUS 退出码，直接显示这串数字容易被误认为程序崩溃
WINDOWS_EXIT_NOTES = {
    0xC000013A: "已被终止",
    0xC0000409: "快速失败检查",
    0xC000027B: "已被强制结束",
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status or "未知")


def code_text(code) -> str:
    """退出码的展示文本；负数按信号解释（Linux 上 SIGKILL 为 -9）。"""
    if code is None:
        return "未知"
    code = int(code)
    if code < 0:
        return f"{code}（信号 {-code}）"
    note = WINDOWS_EXIT_NOTES.get(code)
    return f"{code}（{note}）" if note else str(code)


def duration_text(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = int(max(0, float(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600:02d}h"


def exit_reason_label(reason: str, exit_code: int | None) -> str:
    if reason not in REASON_LABELS:
        return reason or "未知"
    if reason == REASON_EXITED and exit_code not in (None, 0):
        return REASON_LABELS[REASON_CRASHED]
    return REASON_LABELS[reason]


def status_from_exit(reason: str, exit_code: int | None) -> str:
    """会话结束后的展示状态：手动停止/正常退出算已退出，非 0 或信号算异常退出。"""
    if reason == REASON_CRASHED:
        return STATUS_CRASHED
    if reason == REASON_STOPPED:
        return STATUS_EXITED
    if exit_code is None:
        return STATUS_EXITED
    if exit_code == 0:
        return STATUS_EXITED
    return STATUS_CRASHED


def status_from_session(session, launcher: bool = False) -> str:
    """已停止服务的外层状态取自最近一次会话。session 为 sqlite3.Row 或 None。

    launcher=True 时，脚本正常退出后显示"脚本已退出"而不是"已退出"：服务是脚本
    在后台拉起来的，监控跟不到它，显示成退出会让人以为服务挂了。
    """
    if session is None:
        return STATUS_STOPPED
    if session["ended_at"] is None:
        return STATUS_RUNNING
    reason = session["exit_reason"] or ""
    if reason == REASON_ORPHAN:
        return STATUS_INTERRUPTED
    if reason == REASON_FAILED:
        return STATUS_LAUNCH_FAILED
    status = status_from_exit(reason, session["exit_code"])
    if launcher and reason != REASON_STOPPED and status == STATUS_EXITED:
        return STATUS_LAUNCHER_EXITED
    return status
