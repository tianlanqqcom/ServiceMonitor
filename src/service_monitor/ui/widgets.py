"""通用格式化与小组件。"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QTableWidgetItem

from ..models import (
    STATUS_CRASHED,
    STATUS_EXITED,
    STATUS_LAUNCH_FAILED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    STATUS_STOPPING,
    STATUS_WAITING,
    status_label,
)

STATUS_COLORS = {
    STATUS_RUNNING: "#2f9e63",
    STATUS_STOPPED: "#8a8f98",
    STATUS_EXITED: "#4a86c5",
    STATUS_CRASHED: "#d9483b",
    STATUS_STOPPING: "#e0862a",
    STATUS_WAITING: "#d9a520",
    STATUS_LAUNCH_FAILED: "#a03030",
}


def fmt_bytes(value, unit_mb: bool = False) -> str:
    if value is None:
        return "—"
    value = float(value)
    if unit_mb:
        return f"{value / 1024 / 1024:.1f} MB"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def fmt_mb(value) -> str:
    if value is None:
        return "—"
    return f"{float(value) / 1024 / 1024:.1f}"


def fmt_pct(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def fmt_ts(ts, with_date: bool = True) -> str:
    if not ts:
        return "—"
    pattern = "%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S"
    return time.strftime(pattern, time.localtime(ts))


def fmt_ts_compact(ts) -> str:
    """省略年份的时间戳，用于表格列。"""
    if not ts:
        return "—"
    local = time.localtime(ts)
    if local.tm_year != time.localtime().tm_year:
        return time.strftime("%Y-%m-%d %H:%M", local)
    return time.strftime("%m-%d %H:%M:%S", local)


def status_color(status: str) -> QColor:
    return QColor(STATUS_COLORS.get(status, "#8a8f98"))


class StatusItem(QTableWidgetItem):
    def __init__(self, status: str):
        super().__init__(status_label(status))
        self.setForeground(status_color(status))
        self.setToolTip(status_label(status))
        self.setData(Qt.UserRole, status)


def configure_table(table) -> None:
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setWordWrap(False)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setHighlightSections(False)


class TextItem(QTableWidgetItem):
    """左对齐纯文本，完整内容放 tooltip。"""

    def __init__(self, text: str = ""):
        super().__init__("" if text is None else str(text))
        self.setToolTip("" if text is None else str(text))
