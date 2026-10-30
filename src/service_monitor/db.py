"""SQLite 持久化层：服务定义、启动会话、指标采样、控制台输出。

所有数据落盘在本机数据目录，重启监控工具后不丢失。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    command         TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    working_dir     TEXT NOT NULL DEFAULT '',
    daemon          INTEGER NOT NULL DEFAULT 0,
    restart_delay   REAL NOT NULL DEFAULT 5,
    launcher        INTEGER NOT NULL DEFAULT 0,
    max_restarts    INTEGER NOT NULL DEFAULT 5,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id   INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    pid          INTEGER,
    started_at   REAL NOT NULL,
    ended_at     REAL,
    exit_code    INTEGER,
    exit_reason  TEXT NOT NULL DEFAULT '',
    launch_kind  TEXT NOT NULL DEFAULT 'manual',
    peak_rss     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_service ON sessions(service_id, started_at DESC);

CREATE TABLE IF NOT EXISTS samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    service_id INTEGER NOT NULL,
    ts         REAL NOT NULL,
    pid        INTEGER,
    cpu        REAL,
    mem        INTEGER,
    gpu        REAL,
    gpu_mem    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_samples_session_ts ON samples(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_samples_service_ts ON samples(service_id, ts);

CREATE TABLE IF NOT EXISTS output_lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts         REAL NOT NULL,
    stream     TEXT NOT NULL,
    line       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_output_session ON output_lines(session_id, id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SERVICE_FIELDS = (
    "name",
    "command",
    "description",
    "working_dir",
    "daemon",
    "restart_delay",
    "launcher",
    "max_restarts",
)

# 老库只有 SCHEMA_VERSION=1 的那几列；CREATE TABLE IF NOT EXISTS 不会补新列，得显式 ALTER。
SERVICE_ADDED_COLUMNS = (
    ("launcher", "INTEGER NOT NULL DEFAULT 0"),
    ("max_restarts", "INTEGER NOT NULL DEFAULT 5"),
)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=20)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate_services()
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def _migrate_services(self) -> None:
        """给已有库补上新增列（SQLite 的 CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
        present = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(services)")
        }
        for column, ddl in SERVICE_ADDED_COLUMNS:
            if column not in present:
                self._conn.execute(f"ALTER TABLE services ADD COLUMN {column} {ddl}")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
            self._conn.close()

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    # ---------------------------------------------------------------- services

    def list_services(self) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM services ORDER BY id")

    def get_service(self, service_id: int) -> sqlite3.Row | None:
        return self._query_one("SELECT * FROM services WHERE id=?", (service_id,))

    def create_service(self, values: dict) -> int:
        now = time.time()
        placeholders = ", ".join("?" for _ in SERVICE_FIELDS)
        columns = ", ".join(SERVICE_FIELDS)
        params = [values.get(f, _default_for(f)) for f in SERVICE_FIELDS]
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO services({columns}, created_at, updated_at) "
                f"VALUES({placeholders}, ?, ?)",
                [*params, now, now],
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_service(self, service_id: int, values: dict) -> None:
        fields = [k for k in values if k in SERVICE_FIELDS]
        if not fields:
            return
        assignments = ", ".join(f"{f}=?" for f in fields)
        params = [values[f] for f in fields]
        with self._lock:
            self._conn.execute(
                f"UPDATE services SET {assignments}, updated_at=? WHERE id=?",
                [*params, time.time(), service_id],
            )
            self._conn.commit()

    def delete_service(self, service_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE service_id=?", (service_id,))
            self._conn.execute("DELETE FROM samples WHERE service_id=?", (service_id,))
            self._conn.execute(
                "DELETE FROM output_lines WHERE session_id IN "
                "(SELECT id FROM sessions WHERE service_id=?)",
                (service_id,),
            )
            self._conn.execute("DELETE FROM services WHERE id=?", (service_id,))
            self._conn.commit()

    # ---------------------------------------------------------------- sessions

    def create_session(
        self, service_id: int, pid: int | None, started_at: float, launch_kind: str = "manual"
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions(service_id, pid, started_at, launch_kind) VALUES(?,?,?,?)",
                (service_id, pid, started_at, launch_kind),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def finish_session(
        self, session_id: int, ended_at: float, exit_code: int | None, reason: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at=?, exit_code=?, exit_reason=? WHERE id=?",
                (ended_at, exit_code, reason, session_id),
            )
            self._conn.commit()

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        return self._query_one("SELECT * FROM sessions WHERE id=?", (session_id,))

    def list_sessions(self, service_id: int, limit: int = 200) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM sessions WHERE service_id=? ORDER BY started_at DESC, id DESC LIMIT ?",
            (service_id, limit),
        )

    def list_sessions_with_counts(self, service_id: int, limit: int = 300) -> list[sqlite3.Row]:
        """会话列表附带输出行数，避免逐条 COUNT。"""
        return self._query(
            "SELECT s.*, (SELECT COUNT(*) FROM output_lines o WHERE o.session_id = s.id) AS lines "
            "FROM sessions s WHERE s.service_id=? ORDER BY s.started_at DESC, s.id DESC LIMIT ?",
            (service_id, limit),
        )

    def open_sessions(self) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id")

    def last_sessions_by_service(self) -> dict[int, sqlite3.Row]:
        rows = self._query(
            "SELECT s.* FROM sessions s JOIN ("
            "  SELECT service_id, MAX(id) AS mid FROM sessions GROUP BY service_id"
            ") t ON t.mid = s.id"
        )
        return {int(r["service_id"]): r for r in rows}

    def set_session_pid(self, session_id: int, pid: int | None) -> None:
        self._execute("UPDATE sessions SET pid=? WHERE id=?", (pid, session_id))

    def session_line_count(self, session_id: int) -> int:
        return self.output_count(session_id)

    def set_session_peak(self, session_id: int, peak_rss: int) -> None:
        self._execute(
            "UPDATE sessions SET peak_rss=? WHERE id=? AND peak_rss<?",
            (peak_rss, session_id, peak_rss),
        )

    def mark_orphans(self, now: float | None = None) -> list[sqlite3.Row]:
        """把上次运行遗留的、进程已经不存在的会话收尾为“监控中断”。"""
        now = now if now is not None else time.time()
        orphans = []
        for row in self.open_sessions():
            if not pid_alive(row["pid"]):
                self.finish_session(row["id"], now, None, "orphan")
                orphans.append(row)
        return orphans

    # ----------------------------------------------------------------- samples

    def insert_sample(
        self,
        session_id: int,
        service_id: int,
        ts: float,
        pid: int | None,
        cpu: float | None,
        mem: int | None,
        gpu: float | None,
        gpu_mem: int | None,
    ) -> None:
        self._execute(
            "INSERT INTO samples(session_id, service_id, ts, pid, cpu, mem, gpu, gpu_mem) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (session_id, service_id, ts, pid, cpu, mem, gpu, gpu_mem),
        )

    def insert_samples(self, rows: Iterable[Sequence[Any]]) -> None:
        payload = list(rows)
        if not payload:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO samples(session_id, service_id, ts, pid, cpu, mem, gpu, gpu_mem) "
                "VALUES(?,?,?,?,?,?,?,?)",
                payload,
            )
            self._conn.commit()

    def latest_sample(self, session_id: int) -> sqlite3.Row | None:
        return self._query_one(
            "SELECT * FROM samples WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,)
        )

    def latest_samples_by_service(self) -> dict[int, sqlite3.Row]:
        rows = self._query(
            "SELECT s.* FROM samples s JOIN ("
            "  SELECT service_id, MAX(id) AS mid FROM samples GROUP BY service_id"
            ") t ON t.mid = s.id"
        )
        return {int(r["service_id"]): r for r in rows}

    def peak_memory(self, session_id: int) -> int:
        row = self._query_one(
            "SELECT MAX(mem) AS peak FROM samples WHERE session_id=?", (session_id,)
        )
        return int(row["peak"] or 0) if row else 0

    def aggregate(
        self,
        service_id: int | None = None,
        session_id: int | None = None,
        since: float | None = None,
        until: float | None = None,
        granularity: float = 60.0,
        max_points: int = 400,
    ) -> list[dict]:
        """按 granularity 秒分桶聚合，返回升序的桶列表。"""
        granularity = max(float(granularity), 1.0)
        where = ["1=1"]
        params: list[Any] = []
        if service_id is not None:
            where.append("service_id=?")
            params.append(service_id)
        if session_id is not None:
            where.append("session_id=?")
            params.append(session_id)
        if since is not None:
            where.append("ts>=?")
            params.append(since)
        if until is not None:
            where.append("ts<=?")
            params.append(until)
        rows = self._query(
            "SELECT CAST(ts/? AS INTEGER)*? AS bucket, "
            "AVG(cpu) AS cpu_avg, MAX(cpu) AS cpu_max, "
            "AVG(gpu) AS gpu_avg, MAX(gpu) AS gpu_max, "
            "AVG(mem) AS mem_avg, MAX(mem) AS mem_max, "
            "MAX(gpu_mem) AS gpu_mem_max, COUNT(*) AS n "
            f"FROM samples WHERE {' AND '.join(where)} "
            "GROUP BY bucket ORDER BY bucket",
            [granularity, granularity, *params],
        )
        points = [
            {
                "ts": float(r["bucket"]),
                "cpu_avg": r["cpu_avg"],
                "cpu_max": r["cpu_max"],
                "gpu_avg": r["gpu_avg"],
                "gpu_max": r["gpu_max"],
                "mem_avg": r["mem_avg"],
                "mem_max": r["mem_max"],
                "gpu_mem_max": r["gpu_mem_max"],
                "samples": r["n"],
            }
            for r in rows
        ]
        if max_points and len(points) > max_points:
            points = points[-max_points:]
        return points

    def sample_span(self, service_id: int) -> tuple[float | None, float | None]:
        row = self._query_one(
            "SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM samples WHERE service_id=?",
            (service_id,),
        )
        if not row or row["first_ts"] is None:
            return (None, None)
        return (float(row["first_ts"]), float(row["last_ts"]))

    # ------------------------------------------------------------------ output

    def append_output(self, session_id: int, ts: float, stream: str, line: str) -> None:
        self._execute(
            "INSERT INTO output_lines(session_id, ts, stream, line) VALUES(?,?,?,?)",
            (session_id, ts, stream, line),
        )

    def append_output_many(self, rows: Iterable[Sequence[Any]]) -> None:
        payload = list(rows)
        if not payload:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO output_lines(session_id, ts, stream, line) VALUES(?,?,?,?)", payload
            )
            self._conn.commit()

    def output_after(self, session_id: int, after_id: int, limit: int = 2000) -> list[sqlite3.Row]:
        return self._query(
            "SELECT id, ts, stream, line FROM output_lines "
            "WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
            (session_id, after_id, limit),
        )

    def output_page(self, session_id: int, offset: int, limit: int) -> list[sqlite3.Row]:
        return self._query(
            "SELECT id, ts, stream, line FROM output_lines "
            "WHERE session_id=? ORDER BY id LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        )

    def output_count(self, session_id: int) -> int:
        row = self._query_one(
            "SELECT COUNT(*) AS n FROM output_lines WHERE session_id=?", (session_id,)
        )
        return int(row["n"]) if row else 0

    def output_last_id(self, session_id: int) -> int:
        row = self._query_one(
            "SELECT MAX(id) AS last FROM output_lines WHERE session_id=?", (session_id,)
        )
        return int(row["last"]) if row and row["last"] is not None else 0

    # ---------------------------------------------------------------- retention

    def prune(self, retention_days: int, now: float | None = None) -> int:
        if retention_days <= 0:
            return 0
        now = now if now is not None else time.time()
        cutoff = now - retention_days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM samples WHERE ts<?", (cutoff,))
            self._conn.execute(
                "DELETE FROM output_lines WHERE session_id IN "
                "(SELECT id FROM sessions WHERE ended_at IS NOT NULL AND ended_at<?)",
                (cutoff,),
            )
            self._conn.execute("DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at<?", (cutoff,))
            self._conn.commit()
            return int(cur.rowcount or 0)


def _default_for(field_name: str):
    if field_name in ("description", "working_dir"):
        return ""
    if field_name in ("daemon", "launcher"):
        return 0
    if field_name == "restart_delay":
        return 5.0
    if field_name == "max_restarts":
        return 5
    if field_name in ("name", "command"):
        return ""
    return None


def pid_alive(pid: int | None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(int(pid))
    except Exception:
        return False
