"""程序入口：组装数据层、调度层与界面。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from . import __version__
from .config import ENV_HOME, SettingsStore, db_path
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.home:
        os.environ[ENV_HOME] = str(Path(args.home).expanduser().resolve())
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("ServiceMonitor")
    app.setOrganizationName("ServiceMonitor")
    app.setApplicationDisplayName("ServiceMonitor")

    store = SettingsStore()
    # 字体在建窗口之前定好，否则首个窗口按系统默认字号排一遍版再跳一次
    apply_ui_font(app, store.values)
    db = Database(db_path())
    if args.demo:
        for name in seed_demo(db):
            print(f"已插入示例服务：{name}")
    supervisor = Supervisor(db, store)
    window = MainWindow(db, supervisor, store)
    window.show()
    supervisor.start()
    if not db.list_services():
        window.statusBar().showMessage(
            "还没有服务，点击“＋ 添加服务”或使用 --demo 启动示例", 12000
        )
    code = app.exec()
    supervisor.shutdown(stop_services=store.values.stop_on_exit)
    db.close()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
