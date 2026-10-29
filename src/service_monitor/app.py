"""程序入口：组装数据层、调度层与界面。"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import __version__
from .config import ENV_HOME, SettingsStore, data_dir, db_path
from .db import Database
from .supervisor import Supervisor
from .ui.main_window import MainWindow, apply_ui_font


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="service-monitor", description="跨平台服务状态监控工具（Qt 界面）"
    )
    parser.add_argument("--home", help="数据目录（覆盖默认的 APPDATA/XDG数据目录）")
    parser.add_argument("--version", action="version", version=f"ServiceMonitor {__version__}")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="首次运行时插入两个示例服务，便于直接体验启动/停止与曲线",
    )
    return parser


def demo_dir() -> Path | None:
    for candidate in (
        Path(__file__).resolve().parents[2] / "demo",
        Path(__file__).resolve().parent / "demo",
    ):
        if candidate.is_dir():
            return candidate
    return None


def seed_demo(db: Database) -> list[str]:
    root = demo_dir()
    if root is None:
        print("未找到 demo 目录，无法插入示例服务")
        return []
    python = sys.executable
    samples = [
        {
            "name": "示例·心跳服务",
            "command": f'"{python}" -u "{root / "heartbeat.py"}"',
            "description": "持续打印并占用少量 CPU，用来观察实时输出与曲线",
            "daemon": 1,
            "restart_delay": 5.0,
        },
        {
            "name": "示例·异常退出",
            "command": f'"{python}" -u "{root / "crash.py"}"',
            "description": "启动后以退出码 3 结束，用来验证异常退出状态",
            "daemon": 0,
            "restart_delay": 5.0,
        },
    ]
    existing = {s["name"] for s in db.list_services()}
    created = []
    for item in samples:
        if item["name"] in existing:
            continue
        db.create_service(item)
        created.append(item["name"])
    return created


def instance_name() -> str:
    """按数据目录区分实例：用 --home 打开两份数据时允许各开一个监控。"""
    digest = hashlib.sha1(str(data_dir()).encode("utf-8")).hexdigest()[:12]
    return f"ServiceMonitor-{digest}"


def notify_running_instance(name: str, timeout_ms: int = 500) -> bool:
    """通知已在运行的实例把界面调到前台；连不上说明本机没有别的实例。"""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def acquire_single_instance() -> QLocalServer | None:
    """抢到返回本进程的监听服务器；已有实例在跑（或抢注失败）时返回 None。

    没有这一步的话，开机自启 + 手动双击会同时跑两份监控：两个托盘图标、
    两份采样写同一个数据库，守护重启还会互相抢进程。
    """
    name = instance_name()
    if notify_running_instance(name):
        return None
    server = QLocalServer()
    if server.listen(name):
        return server
    # Linux 上崩溃会留下 socket 文件，清掉后重试一次
    QLocalServer.removeServer(name)
    return server if server.listen(name) else None


def _drain_connections(server: QLocalServer) -> None:
    while True:
        connection = server.nextPendingConnection()
        if connection is None:
            return
        connection.abort()
        connection.deleteLater()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.home:
        os.environ[ENV_HOME] = str(Path(args.home).expanduser().resolve())
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("ServiceMonitor")
    app.setOrganizationName("ServiceMonitor")
    app.setApplicationDisplayName("ServiceMonitor")

    instance = acquire_single_instance()
    if instance is None:
        print("已有一个 ServiceMonitor 在运行，界面已调到前台，本次启动退出")
        return 0

    store = SettingsStore()
    # 字体在建窗口之前定好，否则首个窗口按系统默认字号排一遍版再跳一次
    apply_ui_font(app, store.values)
    db = Database(db_path())
    if args.demo:
        for name in seed_demo(db):
            print(f"已插入示例服务：{name}")
    supervisor = Supervisor(db, store)
    window = MainWindow(db, supervisor, store)
    # 第二次启动只负责把已有实例的窗口叫出来，不再开第二份监控
    instance.setParent(window)
    instance.newConnection.connect(lambda: _drain_connections(instance))
    instance.newConnection.connect(window.show_from_tray)
    window.show()
    supervisor.start()
    if not db.list_services():
        window.statusBar().showMessage(
            "还没有服务，点击“＋ 添加服务”或使用 --demo 启动示例", 12000
        )
    code = app.exec()
    supervisor.shutdown(stop_services=store.values.stop_on_exit)
    instance.close()
    db.close()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
