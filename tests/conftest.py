"""测试用的公共夹具：隔离数据目录、临时数据库、无头 Qt。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from service_monitor.config import ENV_HOME, SettingsStore  # noqa: E402
from service_monitor.db import Database  # noqa: E402

SERVICE_BASE = {
    "name": "svc",
    "command": "echo hi",
    "description": "",
    "working_dir": "",
    "daemon": 0,
    "restart_delay": 5.0,
}

ASSETS = Path(__file__).resolve().parent / "assets"


def script_command(python_exe: str, name: str, *args: str) -> str:
    """拼出“用测试脚本启动一个服务”的命令行。"""
    parts = [f'"{python_exe}" -u "{ASSETS / name}"']
    parts.extend(args)
    return " ".join(parts)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_HOME, str(tmp_path))
    return tmp_path


@pytest.fixture
def db(tmp_path):
    handle = Database(tmp_path / "test.db")
    yield handle
    handle.close()


@pytest.fixture
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def python_exe() -> str:
    return sys.executable
