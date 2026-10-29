"""GPU 指标采集。

优先使用 nvidia-smi（N 卡）；工具不存在时返回 None，界面上显示为不可用，
不影响 CPU / 内存的采集。
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

_LOCK = threading.Lock()
_PROBE: dict[str, object] = {"checked_at": 0.0, "path": None}
_PROBE_TTL = 30.0


@dataclass
class GpuSnapshot:
    """一次 nvidia-smi 查询的结果。"""

    util_percent: float | None = None
    mem_used: int = 0
    mem_total: int = 0
    per_process_mem: dict[int, int] = field(default_factory=dict)
    gpu_count: int = 0

    def mem_for_pids(self, pids) -> int:
        return sum(int(self.per_process_mem.get(int(pid), 0)) for pid in pids if int(self.per_process_mem.get(int(pid), 0)) > 0)

    @property
    def available(self) -> bool:
        return self.util_percent is not None or self.gpu_count > 0


def nvidia_smi_path() -> str | None:
    with _LOCK:
        now = time.monotonic()
        if _PROBE["path"] is not None or now - float(_PROBE["checked_at"]) < _PROBE_TTL:
            return _PROBE["path"] or None
        path = shutil.which("nvidia-smi")
        _PROBE["checked_at"] = now
        _PROBE["path"] = path or ""
        return path or None


def nvidia_available() -> bool:
    return nvidia_smi_path() is not None


def _run(query_flag: str, columns: str, timeout: float = 3.0) -> str | None:
    exe = nvidia_smi_path()
    if not exe:
        return None
    cmd = [exe, f"--{query_flag}={columns}", "--format=csv,noheader,nounits"]
    try:
        out = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, text=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def snapshot(timeout: float = 3.0) -> GpuSnapshot | None:
    """整机 GPU 快照；没有 nvidia-smi 时返回 None。"""
    gpu_out = _run("query-gpu", "index,utilization.gpu,memory.used,memory.total", timeout)
    if gpu_out is None:
        return None
    snap = GpuSnapshot()
    utils: list[float] = []
    for line in gpu_out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            utils.append(float(parts[1]))
            snap.mem_used += int(float(parts[2])) * 1024 * 1024
            snap.mem_total += int(float(parts[3])) * 1024 * 1024
        except ValueError:
            continue
    if utils:
        snap.util_percent = max(utils)
        snap.gpu_count = len(utils)
    proc_out = _run("query-compute-apps", "pid,used_memory", timeout)
    if proc_out is not None:
        for line in proc_out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                snap.per_process_mem[int(parts[0])] = int(float(parts[1])) * 1024 * 1024
            except ValueError:
                continue
    return snap


def util_for_pids(snap: GpuSnapshot | None, pids) -> float | None:
    """进程树正在使用 GPU 时返回其利用率，否则 None（表示该服务不吃 GPU）。"""
    if snap is None:
        return None
    if not any(int(pid) in snap.per_process_mem for pid in pids):
        return None
    return snap.util_percent
