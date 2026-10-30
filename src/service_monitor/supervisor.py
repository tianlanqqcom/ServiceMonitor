"""调度核心：启动/停止服务、采样指标、捕获输出、守护重启。

线程模型：
  - GUI 线程只调用 request_* 方法（把命令压入队列）并接收 Qt 信号；
  - loop 线程串行执行启停命令、采样、守护重启判定；
  - output 线程消费输出队列，批量写库并转发给界面。
"""

from __future__ import annotations

import locale
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Signal

from .config import SettingsStore
from .db import Database
from .metrics import gpu as gpu_mod
from .models import (
    DEFAULT_MAX_RESTARTS,
    REASON_CRASHED,
    REASON_EXITED,
    REASON_FAILED,
    REASON_ORPHAN,
    REASON_STOPPED,
    STATUS_RUNNING,
    STATUS_STOPPING,
    STATUS_WAITING,
    STREAM_INFO,
    STREAM_STDERR,
    STREAM_STDOUT,
    code_text,
    duration_text,
)
from .process import control

MAX_LINE_CHARS = 6000
# 连续失败计数在服务活着超过这么久之后归零：短暂抖动不该把配额吃光。
ATTEMPT_RESET_AFTER = 60.0


class SupervisorSignals(QObject):
    services_changed = Signal()
    stats_changed = Signal(int)
    session_started = Signal(int, int)
    session_finished = Signal(int, int, object, str)
    output_lines = Signal(int)
    restart_scheduled = Signal(int, float)
    log_message = Signal(str)


@dataclass
class RunningSession:
    service_id: int
    service_name: str
    session_id: int
    root_pid: int
    main_pid: int
    proc: subprocess.Popen
    started_at: float
    daemon: bool = False
    restart_delay: float = 5.0
    launcher: bool = False
    max_restarts: int = DEFAULT_MAX_RESTARTS
    peak_rss: int = 0
    cpu: float | None = None
    rss: int | None = None
    gpu: float | None = None
    gpu_mem: int | None = None
    tree: list[int] = field(default_factory=list)
    procs: dict = field(default_factory=dict)
    stop_requested_at: float | None = None
    kill_deadline: float | None = None
    readers: list = field(default_factory=list)
    sample_count: int = 0

    @property
    def status(self) -> str:
        return STATUS_STOPPING if self.stop_requested_at is not None else STATUS_RUNNING


class Supervisor:
    def __init__(self, db: Database, settings: SettingsStore):
        self.db = db
        self.settings = settings
        self.sig = SupervisorSignals()
        self._live: dict[int, RunningSession] = {}
        self._last: dict[int, dict] = {}
        self._pending_restart: dict[int, float] = {}
        self._attempts: dict[int, int] = {}
        self._paused: dict[int, str] = {}
        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._output_q: "queue.Queue[tuple]" = queue.Queue()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._shutdown_done = False
        self._loop: threading.Thread | None = None
        self._writer: threading.Thread | None = None
        self._gpu_snap: gpu_mod.GpuSnapshot | None = None
        self._gpu_at = 0.0
        self._locale_enc = locale.getpreferredencoding(False) or "utf-8"

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> None:
        self.reconcile_after_restart()
        self._writer = threading.Thread(target=self._output_loop, name="sm-output", daemon=True)
        self._loop = threading.Thread(target=self._main_loop, name="sm-loop", daemon=True)
        self._writer.start()
        self._loop.start()
        self.reload_last_stats()

    def shutdown(self, stop_services: bool | None = None) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        if stop_services is None:
            stop_services = self.settings.values.stop_on_exit
        self._stop.set()
        if stop_services:
            with self._lock:
                targets = list(self._live.keys())
            for sid in targets:
                self._stop_session(sid, by_user=False)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                # loop 线程已停，这里代替它完成回收与残余输出落库
                self._reap_finished()
                self._enforce_kill_deadlines()
                self._pump_output()
                with self._lock:
                    if not self._live:
                        break
                time.sleep(0.15)
            with self._lock:
                leftovers = [(s.root_pid, s.proc) for s in self._live.values()]
            for pid, proc in leftovers:
                control.force_kill(pid, proc)
            self._reap_finished()
            self._pump_output()
        if self._writer:
            self._writer.join(timeout=3)
        if self._loop:
            self._loop.join(timeout=3)

    def reconcile_after_restart(self) -> None:
        """上次运行遗留的会话：输出管道已丢，统一收尾并记录原因。"""
        now = time.time()
        rows = self.db.open_sessions()
        for row in rows:
            alive = control._alive(int(row["pid"] or 0))
            reason = REASON_ORPHAN
            self.db.finish_session(row["id"], now, None, reason)
            note = (
                f"[monitor] 监控中断：会话结束于 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"
                + (f"，进程 PID {row['pid']} 可能仍在运行，但 Monitor 已无法读取其输出" if alive else "")
            )
            self.db.append_output(row["id"], now, STREAM_INFO, note)
            self.sig.log_message.emit(note)

    def reload_last_stats(self) -> None:
        """把已停止服务的最后一帧指标读回内存，供外层列表展示。"""
        latest = self.db.latest_samples_by_service()
        with self._lock:
            for sid, row in latest.items():
                self._last[sid] = {
                    "cpu": row["cpu"],
                    "rss": row["mem"],
                    "gpu": row["gpu"],
                    "gpu_mem": row["gpu_mem"],
                    "pid": row["pid"],
                    "ts": row["ts"],
                }
        self.sig.stats_changed.emit(0)

    # ---------------------------------------------------------------------- API

    def request_start(self, service_id: int, kind: str = "manual") -> None:
        self._commands.put(("start", service_id, kind))

    def request_stop(self, service_id: int) -> None:
        self._commands.put(("stop", service_id))

    def request_restart(self, service_id: int) -> None:
        self._commands.put(("restart", service_id))

    def state_of(self, service_id: int) -> dict:
        with self._lock:
            session = self._live.get(service_id)
            if session is not None:
                peak = max(session.peak_rss, session.rss or 0)
                return {
                    "status": session.status,
                    "pid": session.main_pid,
                    "root_pid": session.root_pid,
                    "cpu": session.cpu,
                    "mem": session.rss,
                    "mem_peak": peak,
                    "gpu": session.gpu,
                    "gpu_mem": session.gpu_mem,
                    "session_id": session.session_id,
                }
            if service_id in self._paused:
                return {"status": STATUS_WAITING, "pid": None, "cpu": None, "mem": None,
                        "mem_peak": None, "gpu": None, "gpu_mem": None, "session_id": None,
                        "paused": self._paused[service_id]}
            if service_id in self._pending_restart:
                return {"status": STATUS_WAITING, "pid": None, "cpu": None, "mem": None,
                        "mem_peak": None, "gpu": None, "gpu_mem": None, "session_id": None}
            cached = self._last.get(service_id)
        return {
            "status": None,
            "pid": (cached or {}).get("pid"),
            "cpu": (cached or {}).get("cpu"),
            "mem": (cached or {}).get("rss"),
            "mem_peak": None,
            "gpu": (cached or {}).get("gpu"),
            "gpu_mem": (cached or {}).get("gpu_mem"),
            "session_id": None,
            "cached": bool(cached),
        }

    def running_session_id(self, service_id: int) -> int | None:
        with self._lock:
            s = self._live.get(service_id)
            return s.session_id if s else None

    # -------------------------------------------------------------- 主循环线程

    def _main_loop(self) -> None:
        while not self._stop.is_set():
            interval = max(float(self.settings.values.sample_interval), 0.5)
            self._drain_commands()
            self._refresh_gpu()
            for sid in list(self._live.keys()):
                try:
                    self._sample_one(sid)
                except Exception as exc:  # 采集异常不应终止循环
                    self.sig.log_message.emit(f"[monitor] 采样失败 service={sid}: {exc}")
            self._reap_finished()
            self._enforce_kill_deadlines()
            self._handle_pending_restarts()
            self._stop.wait(interval)

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            action = cmd[0]
            if action == "start":
                self._start_service(cmd[1], cmd[2] if len(cmd) > 2 else "manual")
            elif action == "stop":
                self._stop_service(cmd[1])
            elif action == "restart":
                sid = cmd[1]
                if sid in self._live:
                    self._stop_service(sid)
                    self._pending_restart[sid] = time.time() + 0.5
                else:
                    self._start_service(sid, "manual")

    def _start_service(self, service_id: int, kind: str) -> None:
        with self._lock:
            if service_id in self._live:
                return
            self._pending_restart.pop(service_id, None)
            self._paused.pop(service_id, None)
        row = self.db.get_service(service_id)
        if row is None:
            self.sig.log_message.emit(f"[monitor] 服务 #{service_id} 不存在，无法启动")
            return
        started_at = time.time()
        # 启动器脚本跑完就退出是正常结局，守护会把整套服务反复拆掉重装，这里直接关掉。
        launcher = bool(row["launcher"])
        daemon = bool(row["daemon"]) and not launcher
        restart_delay = float(row["restart_delay"] or 5)
        max_restarts = _int_or(row["max_restarts"], DEFAULT_MAX_RESTARTS)
        session_id = self.db.create_session(service_id, None, started_at, kind)
        try:
            proc = control.spawn(row["command"], row["working_dir"] or None)
        except OSError as exc:
            self.db.finish_session(session_id, time.time(), None, REASON_FAILED)
            self.db.append_output(
                session_id, time.time(), STREAM_INFO, f"[monitor] 启动失败：{exc}"
            )
            self.sig.session_finished.emit(service_id, session_id, None, REASON_FAILED)
            self.sig.log_message.emit(f"[monitor] {row['name']} 启动失败：{exc}")
            with self._lock:
                self._last[service_id] = {"cpu": None, "rss": None, "gpu": None,
                                          "gpu_mem": None, "pid": None, "ts": time.time()}
                if daemon:
                    self._schedule_retry(service_id, restart_delay, max_restarts)
            return
        root_pid = int(proc.pid)
        main_pid = control.resolve_main_pid(root_pid)
        session = RunningSession(
            service_id=service_id,
            service_name=row["name"],
            session_id=session_id,
            root_pid=root_pid,
            main_pid=main_pid,
            proc=proc,
            started_at=started_at,
            daemon=daemon,
            restart_delay=restart_delay,
            launcher=launcher,
            max_restarts=max_restarts,
        )
        session.tree = control.tree_pids(root_pid)
        self.db.set_session_pid(session_id, main_pid)
        self.db.append_output(
            session_id,
            started_at,
            STREAM_INFO,
            f"[monitor] 启动成功 PID={main_pid} 命令={row['command']}"
            + (f" 工作目录={row['working_dir']}" if row["working_dir"] else ""),
        )
        if launcher:
            self.db.append_output(
                session_id,
                started_at,
                STREAM_INFO,
                "[monitor] 启动器脚本模式：脚本把服务拉到后台后自己退出属正常结局，监控不会重启它，"
                "脚本留在后台的进程也不在监控的统计范围内",
            )
        readers = [
            threading.Thread(
                target=self._read_stream,
                args=(session_id, stream_name, stream, self._locale_enc),
                name=f"sm-{stream_name}-{service_id}",
                daemon=True,
            )
            for stream_name, stream in (
                (STREAM_STDOUT, proc.stdout),
                (STREAM_STDERR, proc.stderr),
            )
            if stream is not None
        ]
        for r in readers:
            r.start()
        session.readers = readers
        with self._lock:
            self._live[service_id] = session
            if kind != "daemon":
                self._attempts[service_id] = 0
        self.sig.session_started.emit(service_id, session_id)
        self.sig.stats_changed.emit(service_id)
        self.sig.log_message.emit(f"[monitor] {row['name']} 已启动（PID {main_pid}）")

    def _stop_service(self, service_id: int) -> None:
        with self._lock:
            self._pending_restart.pop(service_id, None)
            self._paused.pop(service_id, None)
            session = self._live.get(service_id)
        if session is None:
            return
        self._stop_session(service_id, by_user=True, session=session)

    def _stop_session(self, service_id: int, by_user: bool, session: RunningSession | None = None) -> None:
        with self._lock:
            session = session or self._live.get(service_id)
            if session is None:
                return
            if session.stop_requested_at is not None:
                return
            session.stop_requested_at = time.time()
        grace = max(float(getattr(self.settings.values, "stop_grace", 5.0)), 1.0)
        control.graceful_terminate(session.proc, session.root_pid)
        session.kill_deadline = time.time() + grace
        if by_user:
            self.db.append_output(
                session.session_id, session.stop_requested_at, STREAM_INFO,
                f"[monitor] 已发送停止信号（{grace:.0f} 秒后仍未退出将强制结束）",
            )

    def _enforce_kill_deadlines(self) -> None:
        now = time.time()
        with self._lock:
            due = [s for s in self._live.values() if s.kill_deadline and now >= s.kill_deadline]
        for session in due:
            control.force_kill(session.root_pid, session.proc)
            session.kill_deadline = None
            self.db.append_output(
                session.session_id, now, STREAM_INFO, "[monitor] 已强制结束进程树"
            )

    def _reap_finished(self) -> None:
        with self._lock:
            candidates = list(self._live.values())
        for session in candidates:
            code = session.proc.poll()
            if code is not None:
                self._finalize(session, code)

    def _finalize(self, session: RunningSession, exit_code: int | None) -> None:
        with self._lock:
            if session.service_id not in self._live or self._live[session.service_id] is not session:
                return
            del self._live[session.service_id]
        ended_at = time.time()
        manual = session.stop_requested_at is not None
        reason = REASON_STOPPED if manual else (REASON_EXITED if exit_code == 0 else REASON_CRASHED)
        # 输出管道可能还有残余数据，给读线程一点时间读到 EOF（超时后残余行仍会被写出）
        for r in session.readers:
            r.join(timeout=0.6)
        self._pump_output()
        peak = self.db.peak_memory(session.session_id) or session.peak_rss
        self.db.finish_session(session.session_id, ended_at, exit_code, reason)
        self.db.set_session_peak(session.session_id, peak)
        note = (
            f"[monitor] 进程退出：退出码={code_text(exit_code)} "
            f"原因={('手动停止' if manual else ('正常退出' if exit_code == 0 else '异常退出'))} "
            f"运行时长={duration_text(ended_at - session.started_at)}"
        )
        self.db.append_output(session.session_id, ended_at, STREAM_INFO, note)
        with self._lock:
            self._last[session.service_id] = {
                "cpu": session.cpu,
                "rss": session.rss,
                "gpu": session.gpu,
                "gpu_mem": session.gpu_mem,
                "pid": session.main_pid,
                "ts": ended_at,
            }
            if ended_at - session.started_at > ATTEMPT_RESET_AFTER:
                self._attempts[session.service_id] = 0
            if session.daemon and not manual:
                self._schedule_retry(
                    session.service_id, session.restart_delay, session.max_restarts
                )
        self.sig.session_finished.emit(
            session.service_id, session.session_id, exit_code, reason
        )
        self.sig.stats_changed.emit(session.service_id)
        self.sig.log_message.emit(f"[monitor] {session.service_name} {note}")

    def _schedule_retry(self, service_id: int, delay: float, limit: int) -> None:
        """limit：-1 无限重试，0 不重试，正数 = 连续失败时最多拉起这么多次。

        计数在服务活着超过 ATTEMPT_RESET_AFTER 后归零，所以偶发抖动不会吃光配额。
        """
        attempts = self._attempts.get(service_id, 0)
        self._pending_restart.pop(service_id, None)
        if limit >= 0 and attempts >= limit:
            detail = (
                "重试上限为 0"
                if limit == 0
                else f"已重试 {attempts} 次仍未稳定运行，已达重试上限（{limit}）"
            )
            self._paused[service_id] = f"{detail}，守护已停止（点“启动”可恢复）"
            self.sig.log_message.emit(f"[monitor] 服务 #{service_id} {detail}，守护已停止")
            self.sig.stats_changed.emit(service_id)
            return
        self._attempts[service_id] = attempts + 1
        when = time.time() + max(float(delay), 1.0)
        self._pending_restart[service_id] = when
        self.sig.restart_scheduled.emit(service_id, when)
        self.sig.stats_changed.emit(service_id)

    def _handle_pending_restarts(self) -> None:
        now = time.time()
        with self._lock:
            due = [sid for sid, when in self._pending_restart.items() if now >= when]
            for sid in due:
                self._pending_restart.pop(sid, None)
        for sid in due:
            self.sig.log_message.emit(f"[monitor] 守护重新拉起服务 #{sid}")
            self._start_service(sid, "daemon")

    # ---------------------------------------------------------------- 采样

    def _refresh_gpu(self) -> None:
        values = self.settings.values
        if not values.gpu_enabled:
            self._gpu_snap = None
            return
        now = time.time()
        if now - self._gpu_at < max(float(values.gpu_interval), 1.0):
            return
        self._gpu_at = now
        self._gpu_snap = gpu_mod.snapshot()

    def _sample_one(self, service_id: int) -> None:
        with self._lock:
            session = self._live.get(service_id)
        if session is None:
            return
        session.tree = control.tree_pids(session.root_pid)
        if len(session.tree) > 1 or session.main_pid == session.root_pid:
            resolved = control.resolve_main_pid(session.root_pid)
            if resolved != session.main_pid:
                session.main_pid = resolved
        cpu_total = 0.0
        rss_total = 0
        alive: dict[int, Any] = {}
        try:
            import psutil

            for pid in session.tree:
                try:
                    proc = session.procs.get(pid)
                    if proc is None:
                        proc = psutil.Process(pid)
                        proc.cpu_percent(None)  # 预热，下一 tick 才有增量
                    else:
                        cpu_total += proc.cpu_percent(None)
                    alive[pid] = proc
                    rss_total += proc.memory_info().rss
                except Exception:
                    continue
            session.procs = alive
        except ImportError:
            return
        if not alive:
            # 进程树已全部消失，本轮不写采样：否则会留下一条 CPU/内存归零的假数据点
            return
        gpu_util = gpu_mod.util_for_pids(self._gpu_snap, session.tree)
        gpu_mem = self._gpu_snap.mem_for_pids(session.tree) if self._gpu_snap else 0
        ts = time.time()
        session.cpu = round(cpu_total, 2)
        session.rss = rss_total
        session.peak_rss = max(session.peak_rss, rss_total)
        session.gpu = gpu_util
        session.gpu_mem = gpu_mem or None
        session.sample_count += 1
        self.db.insert_sample(
            session.session_id, service_id, ts, session.main_pid,
            session.cpu, session.rss, session.gpu, session.gpu_mem,
        )
        if session.peak_rss:
            self.db.set_session_peak(session.session_id, session.peak_rss)
        self.sig.stats_changed.emit(service_id)

    # ---------------------------------------------------------------- 输出

    def _read_stream(self, session_id: int, stream: str, fh, encoding: str) -> None:
        """按行读取管道；Windows 控制台常见 GBK，按 utf-8 -> 本地编码 -> replace 回退。"""
        try:
            while True:
                raw = fh.readline()
                if not raw:
                    break
                if isinstance(raw, bytes):
                    text = _decode(raw, encoding)
                else:
                    text = raw
                for part in _split_lines(text):
                    if len(part) > MAX_LINE_CHARS:
                        for i in range(0, len(part), MAX_LINE_CHARS):
                            self._output_q.put((session_id, stream, part[i : i + MAX_LINE_CHARS], time.time()))
                    else:
                        self._output_q.put((session_id, stream, part, time.time()))
        except (ValueError, OSError):
            pass
        finally:
            try:
                fh.close()
            except OSError:
                pass

    def _output_loop(self) -> None:
        """攒批写库并通知界面：最多等 0.3 秒，或攒满 500 行；退出时写完残余。"""
        buf: list[tuple] = []
        deadline: float | None = None
        while True:
            try:
                item = self._output_q.get(timeout=0.1)
            except queue.Empty:
                item = None
            if item is not None:
                buf.append(item)
                if deadline is None:
                    deadline = time.monotonic() + 0.3
            if len(buf) >= 500 or (buf and deadline is not None and time.monotonic() >= deadline):
                self._flush(buf)
                buf, deadline = [], None
            if self._stop.is_set() and not buf and self._output_q.empty():
                return

    def _pump_output(self) -> None:
        batch: list[tuple] = []
        while True:
            try:
                batch.append(self._output_q.get_nowait())
            except queue.Empty:
                break
        self._flush(batch)

    def _flush(self, batch: list[tuple]) -> None:
        if not batch:
            return
        self.db.append_output_many(
            [(sid, ts, stream, line) for (sid, stream, line, ts) in batch]
        )
        for sid in dict.fromkeys(item[0] for item in batch):
            self.sig.output_lines.emit(sid)


def _int_or(value, fallback: int) -> int:
    """老库里可能是 NULL；顺便挡掉写坏的浮点/字符串。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _decode(raw: bytes, encoding: str) -> str:
    """优先按 UTF-8 解码；失败时回退到系统本地编码（Windows 中文环境为 GBK）。"""
    candidates = ["utf-8", encoding]
    if os.name == "nt":
        candidates.append("gb18030")
    seen = set()
    for enc in candidates:
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _split_lines(text: str) -> list[str]:
    """进度条式的 \\r 输出拆成多行；去掉行尾换行。"""
    text = text.rstrip("\n")
    if "\r" in text:
        parts = [p for p in text.split("\r") if p != ""]
        return parts or [""]
    return [text]

