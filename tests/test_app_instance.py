"""单实例守护：第二次启动必须叫醒已有实例，而不是再多一个托盘图标。"""

from __future__ import annotations

import time

from PySide6.QtNetwork import QLocalServer

from service_monitor.app import acquire_single_instance, instance_name, notify_running_instance
from service_monitor.config import ENV_HOME


def _wait_for_connection(server, qapp, seconds: float = 3.0) -> bool:
    received: list[int] = []
    server.newConnection.connect(lambda: received.append(1))
    deadline = time.time() + seconds
    while time.time() < deadline and not received:
        qapp.processEvents()
        time.sleep(0.02)
    return bool(received)


def test_first_launch_listens_and_second_launch_notifies(qapp, tmp_home):
    server = acquire_single_instance()
    assert isinstance(server, QLocalServer)
    try:
        assert notify_running_instance(instance_name()) is True
        assert _wait_for_connection(server, qapp), "已有实例要收到连接，才能把窗口叫出来"
        # 第二个实例不再监听：它只负责通知，然后退出
        assert acquire_single_instance() is None
    finally:
        server.close()


def test_notify_is_quick_when_nothing_is_running(qapp, tmp_home):
    started = time.monotonic()
    assert notify_running_instance(instance_name()) is False
    assert time.monotonic() - started < 2.0, "没有实例时不能拖慢启动"


def test_instance_name_follows_the_data_dir(qapp, tmp_home, monkeypatch):
    first = instance_name()
    monkeypatch.setenv(ENV_HOME, str(tmp_home / "another-data-dir"))
    assert instance_name() != first, "两份数据应能各开一个监控"
