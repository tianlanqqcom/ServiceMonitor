"""调度层：真实进程的启动、采样、输出捕获、守护重启与状态记录。

测试不启动采样线程，而是手工驱动 `_drain_commands` / `_sample_one` /
`_reap_finished`，让断言可重复。
"""

from __future__ import annotations

import sys
import time

import pytest

from service_monitor import supervisor as sup_mod
from service_monitor.models import (
    REASON_ORPHAN,
    REASON_STOPPED,
    STATUS_RUNNING,
    STATUS_STOPPING,
    STATUS_WAITING,
)
from service_monitor.supervisor import Supervisor

from conftest import ASSETS, script_command


def make_service(
    db,
    name: str,
    command: str,
    daemon: int = 0,
    delay: float = 5.0,
    launcher: int = 0,
    max_restarts: int = 5,
) -> int:
    return db.create_service(
        {
            "name": name,
            "command": command,
            "description": "",
            "working_dir": str(ASSETS),
            "daemon": daemon,
            "restart_delay": delay,
            "launcher": launcher,
            "max_restarts": max_restarts,
        }
    )


@pytest.fixture
def sup(db, store):
    handle = Supervisor(db, store)
    yield handle
    handle.shutdown(stop_services=True)


def pump(sup, seconds: float, sid: int | None = None):
    """驱动若干轮 tick：采样 + 回收 + 守护。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        sup._drain_commands()
        for live_id in list(sup._live.keys()):
            sup._sample_one(live_id)
        sup._reap_finished()
        sup._enforce_kill_deadlines()
        sup._handle_pending_restarts()
        sup._pump_output()
        time.sleep(0.2)
    return sup.state_of(sid) if sid is not None else None


def wait_gone(sup, sid: int, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        sup._drain_commands()
        sup._reap_finished()
        sup._enforce_kill_deadlines()
        sup._handle_pending_restarts()
        sup._pump_output()
        state = sup.state_of(sid)
        if state["status"] is None:
            return state
        time.sleep(0.2)
    raise AssertionError(f"服务仍在运行：{sup.state_of(sid)}")


def test_start_stop_writes_session_samples_and_output(sup, db, python_exe):
    sid = make_service(db, "tick", script_command(python_exe, "talker.py"))
    sup.request_start(sid)
    sup._drain_commands()
    assert sup.state_of(sid)["status"] == STATUS_RUNNING

    pump(sup, 2.0)
    state = sup.state_of(sid)
    assert state["pid"] > 0
    assert state["mem"] > 0
    assert state["mem_peak"] >= state["mem"]
    assert db.aggregate(service_id=sid), "采样应已落库"

    session_id = sup.running_session_id(sid)
    lines = [r["line"] for r in db.output_after(session_id, 0, 200)]
    assert any("out-0" in line for line in lines)

    sup.request_stop(sid)
    wait_gone(sup, sid)
    session = db.last_sessions_by_service()[sid]
    assert session["ended_at"] is not None
    assert session["exit_reason"] == REASON_STOPPED
    tail = [r["line"] for r in db.output_after(session["id"], 0, 500)]
    assert any("进程退出" in line for line in tail)
    assert session["peak_rss"] > 0


def test_stderr_and_info_streams_are_labeled(sup, db, python_exe):
    sid = make_service(db, "loud", script_command(python_exe, "talker.py"))
    sup.request_start(sid)
    pump(sup, 1.5)
    rows = db.output_after(sup.running_session_id(sid), 0, 200)
    assert any(r["stream"] == "stderr" and "err-oops" in r["line"] for r in rows)
    assert any(r["stream"] == "info" and "启动成功" in r["line"] for r in rows)


def test_chinese_output_survives_the_pipe(sup, db, python_exe):
    """子进程重定向时按本地编码（Windows 为 GBK）写中文，管道必须原样入库。"""
    sid = make_service(db, "cjk", script_command(python_exe, "cjk.py"))
    sup.request_start(sid)
    pump(sup, 1.5)
    rows = db.output_after(sup.running_session_id(sid), 0, 200)
    lines = [r["line"] for r in rows]
    assert "警告：心跳变慢" in lines, lines
    assert "中文标准错误" in lines, lines
    assert not any("\ufffd" in line for line in lines), lines


def test_nonzero_exit_is_recorded_as_crash(sup, db, python_exe):
    sid = make_service(db, "fails", script_command(python_exe, "exiter.py", "7"))
    sup.request_start(sid)
    wait_gone(sup, sid)
    session = db.last_sessions_by_service()[sid]
    assert session["exit_code"] == 7
    assert session["exit_reason"] == "crashed"


def wait_session_closed(sup, db, sid: int, timeout: float = 30.0):
    """驱动到最近一次会话在库里收尾（守护服务会马上安排下一次拉起）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        sup._drain_commands()
        sup._reap_finished()
        sup._enforce_kill_deadlines()
        sup._pump_output()
        session = db.last_sessions_by_service().get(sid)
        if session is not None and session["ended_at"] is not None:
            return session
        time.sleep(0.2)
    raise AssertionError(f"会话没有结束：{db.last_sessions_by_service().get(sid)}")


def test_daemon_restarts_after_unexpected_exit(sup, db, python_exe):
    sid = make_service(db, "guarded", script_command(python_exe, "exiter.py", "4"), daemon=1, delay=1.0)
    sup.request_start(sid)
    first = wait_session_closed(sup, db, sid)
    assert first["exit_code"] == 4
    assert first["exit_reason"] == "crashed"
    assert sid in sup._pending_restart, "守护应安排重新拉起"
    assert sup.state_of(sid)["status"] == STATUS_WAITING

    time.sleep(1.1)
    sup._handle_pending_restarts()
    sessions = db.list_sessions(sid)
    assert len(sessions) == 2
    assert sessions[0]["launch_kind"] == "daemon"
    wait_session_closed(sup, db, sid)


def test_manual_stop_does_not_trigger_daemon(sup, db, python_exe):
    sid = make_service(db, "guarded-stop", script_command(python_exe, "sleeper.py"), daemon=1, delay=1.0)
    sup.request_start(sid)
    sup._drain_commands()
    sup.request_stop(sid)
    wait_gone(sup, sid)
    assert sid not in sup._pending_restart
    assert sup.state_of(sid)["status"] is None


def test_retry_limit_stops_the_daemon_once_used_up(sup, db, python_exe, monkeypatch):
    """上限 2 就是重试两次；用完之后状态列要能看出为什么不再拉起。"""
    monkeypatch.setattr(sup_mod, "ATTEMPT_RESET_AFTER", 3600.0)
    sid = make_service(
        db, "flaky", script_command(python_exe, "exiter.py", "1"),
        daemon=1, delay=1.0, max_restarts=2,
    )
    sup.request_start(sid)
    for round_no in (1, 2):
        wait_session_closed(sup, db, sid)
        assert sid in sup._pending_restart, f"第 {round_no} 次失败后应继续重试"
        time.sleep(1.05)
        sup._handle_pending_restarts()
    wait_session_closed(sup, db, sid)
    assert sid not in sup._pending_restart
    state = sup.state_of(sid)
    assert state["status"] == STATUS_WAITING
    assert "已达重试上限（2）" in state["paused"], state
    assert len(db.list_sessions(sid)) == 3, "首次拉起 + 两次重试"


def test_zero_retry_limit_never_pulls_the_service_back(sup, db, python_exe):
    sid = make_service(
        db, "once", script_command(python_exe, "exiter.py", "1"),
        daemon=1, delay=1.0, max_restarts=0,
    )
    sup.request_start(sid)
    wait_session_closed(sup, db, sid)
    assert sid not in sup._pending_restart
    assert "重试上限为 0" in sup.state_of(sid)["paused"]


def test_infinite_retry_never_gives_up(sup):
    with sup._lock:
        for _ in range(10):
            sup._schedule_retry(1, 1.0, -1)
        assert 1 in sup._pending_restart
        assert 1 not in sup._paused
        assert sup._attempts[1] == 10


def test_run_longer_than_the_threshold_resets_the_counter(sup, db, python_exe, monkeypatch):
    """活过阈值就当作新一轮问题，否则偶发故障会慢慢吃光配额。"""
    monkeypatch.setattr(sup_mod, "ATTEMPT_RESET_AFTER", 0.0)
    sid = make_service(
        db, "resety", script_command(python_exe, "exiter.py", "1"),
        daemon=1, delay=1.0, max_restarts=1,
    )
    sup.request_start(sid)
    for round_no in (1, 2):
        wait_session_closed(sup, db, sid)
        assert sid in sup._pending_restart, f"计数应已清零，第 {round_no} 轮照旧重试"
        time.sleep(1.05)
        sup._handle_pending_restarts()


def test_launcher_script_exit_is_a_normal_ending(sup, db, python_exe):
    """启动器脚本把服务拉到后台后自己退出：不重启，也不要把这说成守护失败。"""
    sid = make_service(
        db, "launcher", script_command(python_exe, "exiter.py", "0"),
        daemon=1, delay=1.0, launcher=1,
    )
    sup.request_start(sid)
    session = wait_session_closed(sup, db, sid)
    assert session["exit_code"] == 0
    assert session["exit_reason"] == "exited"
    assert sid not in sup._pending_restart
    assert sid not in sup._paused
    lines = [r["line"] for r in db.output_after(session["id"], 0, 200)]
    assert any("启动器脚本模式" in line for line in lines), lines


def test_launcher_script_that_fails_is_still_a_crash(sup, db, python_exe):
    sid = make_service(
        db, "launcher-bad", script_command(python_exe, "exiter.py", "5"),
        daemon=1, delay=1.0, launcher=1,
    )
    sup.request_start(sid)
    session = wait_session_closed(sup, db, sid)
    assert session["exit_code"] == 5
    assert session["exit_reason"] == "crashed"
    assert sid not in sup._pending_restart


def test_reconcile_closes_orphan_sessions(sup, db, python_exe):
    sid = make_service(db, "orphan", script_command(python_exe, "sleeper.py"))
    session = db.create_session(sid, 999999, time.time() - 50, "manual")
    sup.reconcile_after_restart()
    row = db.get_session(session)
    assert row["ended_at"] is not None
    assert row["exit_reason"] == REASON_ORPHAN
    assert db.output_after(session, 0, 10)[0]["line"].startswith("[monitor]")


def test_reload_last_stats_exposes_last_frame(db, store):
    sup = Supervisor(db, store)
    sid = make_service(db, "gone", "cmd /c echo x" if sys.platform == "win32" else "true")
    session = db.create_session(sid, 4242, time.time() - 20, "manual")
    db.insert_sample(session, sid, time.time() - 19, 4242, 12.5, 3_000_000, 40.0, 512_000)
    db.finish_session(session, time.time() - 18, 0, "exited")
    sup.reload_last_stats()
    state = sup.state_of(sid)
    assert state["cached"] is True
    assert (state["cpu"], state["mem"], state["gpu"]) == (12.5, 3_000_000, 40.0)
    sup.shutdown(stop_services=True)


def test_split_lines_handles_progress_bars():
    assert sup_mod._split_lines("a\nb\r\n") == ["a\nb"]
    assert sup_mod._split_lines("50%\r100%\n") == ["50%", "100%"]
    assert sup_mod._split_lines("single") == ["single"]


def test_decode_falls_back_to_locale_encoding():
    assert sup_mod._decode("中文".encode("utf-8"), "gbk") == "中文"
    assert sup_mod._decode("中文".encode("gbk"), "gbk") == "中文"
    assert sup_mod._decode(b"plain ascii", "utf-8") == "plain ascii"
    assert isinstance(sup_mod._decode(b"\xfa\xfb\xfc\xfd", "utf-8"), str)


def test_state_reports_stopping_after_stop_request(sup, db, python_exe):
    sid = make_service(db, "slow-stop", script_command(python_exe, "sleeper.py"))
    sup.request_start(sid)
    sup._drain_commands()
    sup.request_stop(sid)
    sup._drain_commands()
    assert sup.state_of(sid)["status"] == STATUS_STOPPING


def test_restart_command_stops_then_starts(sup, db, python_exe):
    sid = make_service(db, "cycle", script_command(python_exe, "sleeper.py"))
    sup.request_start(sid)
    pump(sup, 0.5)
    first = sup.running_session_id(sid)
    assert first

    sup.request_restart(sid)
    deadline = time.time() + 40
    second = None
    while time.time() < deadline:
        sup._drain_commands()
        sup._reap_finished()
        sup._enforce_kill_deadlines()
        sup._handle_pending_restarts()
        sup._pump_output()
        second = sup.running_session_id(sid)
        if second and second != first:
            break
        time.sleep(0.2)

    assert second and second != first
    assert len(db.list_sessions(sid)) == 2
    assert db.get_session(first)["ended_at"] is not None
