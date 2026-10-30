"""SQLite 持久化层与聚合查询。"""

from __future__ import annotations

import sqlite3
import time

from conftest import SERVICE_BASE

from service_monitor.db import Database


def test_service_crud(db):
    sid = db.create_service({**SERVICE_BASE, "name": "A", "description": "描述"})
    row = db.get_service(sid)
    assert row["name"] == "A"
    assert row["description"] == "描述"
    assert row["daemon"] == 0
    assert row["launcher"] == 0
    assert row["max_restarts"] == 5

    db.update_service(sid, {"command": "run.sh", "daemon": 1, "restart_delay": 9.5})
    row = db.get_service(sid)
    assert row["command"] == "run.sh"
    assert row["daemon"] == 1
    assert row["restart_delay"] == 9.5

    db.update_service(sid, {"bogus": 1})
    assert db.get_service(sid)["command"] == "run.sh"


def test_service_name_is_unique(db):
    db.create_service({**SERVICE_BASE, "name": "dup"})
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db.create_service({**SERVICE_BASE, "name": "dup"})


def test_session_lifecycle_and_status(db):
    sid = db.create_service(SERVICE_BASE)
    session = db.create_session(sid, 4321, time.time(), "manual")
    assert db.open_sessions()[0]["id"] == session
    db.finish_session(session, time.time(), 3, "crashed")
    assert db.open_sessions() == []
    last = db.last_sessions_by_service()[sid]
    assert last["exit_code"] == 3
    assert last["exit_reason"] == "crashed"
    assert last["pid"] == 4321


def test_samples_and_aggregate(db):
    sid = db.create_service(SERVICE_BASE)
    session = db.create_session(sid, 1, time.time(), "manual")
    base = 1_000_000.0
    for offset in range(0, 300, 10):
        db.insert_sample(session, sid, base + offset, 1, offset / 10.0, offset * 1024, None, None)
    db.insert_samples(
            [(session, sid, base + offset, 1, 50.0, 4096, 12.0, 1024) for offset in range(0, 60, 20)]
    )
    points = db.aggregate(service_id=sid, since=base - 1, granularity=60, max_points=100)
    assert len(points) == 6
    assert points[0]["ts"] == 999960.0
    peak = db.peak_memory(session)
    assert peak == 290 * 1024
    latest = db.latest_samples_by_service()[sid]
    assert latest["ts"] == base + 40
    span = db.sample_span(sid)
    assert span == (base, base + 290)


def test_aggregate_respects_max_points_and_session_filter(db):
    sid = db.create_service(SERVICE_BASE)
    first = db.create_session(sid, 1, time.time(), "manual")
    second = db.create_session(sid, 2, time.time(), "manual")
    base = 2_000_000.0
    rows = [(first, sid, base + i * 60, 1, 1.0, 10, None, None) for i in range(20)]
    rows += [(second, sid, base + 1200 + i * 60, 2, 2.0, 20, None, None) for i in range(5)]
    db.insert_samples(rows)
    limited = db.aggregate(service_id=sid, granularity=60, max_points=8)
    assert len(limited) == 8
    assert limited[-1]["ts"] == base + 1420  # 桶按 60 秒向下对齐
    assert limited[0]["ts"] == base + 1000
    scoped = db.aggregate(session_id=second, granularity=60)
    assert [p["cpu_avg"] for p in scoped] == [2.0] * 5


def test_output_paging_and_incremental_read(db):
    sid = db.create_service(SERVICE_BASE)
    session = db.create_session(sid, 1, time.time(), "manual")
    ts = time.time()
    db.append_output_many([(session, ts + i, "stdout", f"line {i}") for i in range(10)])
    db.append_output(session, ts + 10, "stderr", "boom")
    assert db.output_count(session) == 11
    assert db.output_last_id(session) == 11
    page = db.output_page(session, 5, 3)
    assert [r["line"] for r in page] == ["line 5", "line 6", "line 7"]
    tail = db.output_after(session, 8, 100)
    assert [r["line"] for r in tail] == ["line 8", "line 9", "boom"]
    assert db.output_after(session, 11, 100) == []
    assert db.list_sessions_with_counts(sid)[0]["lines"] == 11


def test_delete_service_removes_history(tmp_path):
    db = Database(tmp_path / "cascade.db")
    sid = db.create_service(SERVICE_BASE)
    other = db.create_service({**SERVICE_BASE, "name": "other"})
    session = db.create_session(sid, 1, time.time(), "manual")
    db.insert_sample(session, sid, time.time(), 1, 1.0, 10, None, None)
    db.append_output(session, time.time(), "stdout", "x")
    db.delete_service(sid)
    assert db.get_service(sid) is None
    assert db.output_count(session) == 0
    assert db.aggregate(service_id=sid) == []
    assert db.get_service(other) is not None
    db.close()


def test_prune_only_when_enabled(db):
    sid = db.create_service(SERVICE_BASE)
    old = time.time() - 40 * 86400
    session = db.create_session(sid, 1, old, "manual")
    db.insert_sample(session, sid, old, 1, 1.0, 10, None, None)
    db.finish_session(session, old + 1, 0, "exited")
    assert db.prune(0) == 0
    assert db.prune(30) == 1
    assert db.aggregate(service_id=sid) == []
    assert db.list_sessions(sid) == []


V1_SERVICES_DDL = """
CREATE TABLE services (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    command         TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    working_dir     TEXT NOT NULL DEFAULT '',
    daemon          INTEGER NOT NULL DEFAULT 0,
    restart_delay   REAL NOT NULL DEFAULT 5,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
"""


def test_old_database_gains_the_new_columns(tmp_path):
    """老库只有 v1 的列；CREATE TABLE IF NOT EXISTS 不会补列，必须靠 ALTER 迁移。"""
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.execute(V1_SERVICES_DDL)
    conn.execute(
        "INSERT INTO services(name, command, daemon, created_at, updated_at) "
        "VALUES('legacy', 'run.sh', 1, 1, 1)"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        row = db.get_service(1)
        assert row["name"] == "legacy"
        assert row["daemon"] == 1, "迁移不能动了老配置"
        assert row["launcher"] == 0
        assert row["max_restarts"] == 5
        db.update_service(1, {"launcher": 1, "max_restarts": -1})
        row = db.get_service(1)
        assert (row["launcher"], row["max_restarts"]) == (1, -1)
    finally:
        db.close()


def test_data_survives_reopen(tmp_path):
    path = tmp_path / "persist.db"
    first = Database(path)
    sid = first.create_service({**SERVICE_BASE, "name": "keep"})
    session = first.create_session(sid, 9, time.time(), "manual")
    first.append_output(session, time.time(), "stdout", "important line")
    first.insert_sample(session, sid, time.time(), 9, 3.5, 2048, None, None)
    first.close()

    second = Database(path)
    assert second.get_service(sid)["name"] == "keep"
    assert second.output_after(session, 0, 10)[0]["line"] == "important line"
    assert second.aggregate(service_id=sid)[0]["cpu_avg"] == 3.5
    second.close()
