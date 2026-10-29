"""系统托盘：关闭窗口后常驻，右键菜单提供"打开界面"与"退出监控"。

图标用 QPainter 现场绘制，避免依赖各平台的资源打包方式（Windows 上
QSystemTrayIcon 没有图标就不会显示）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

EDGE = 64
BASE = QColor("#2f7bd0")
EDGE_DARK = QColor("#1f5c99")
BADGE = QColor("#2f9e63")
TOOLTIP_IDLE = "ServiceMonitor · 服务状态监控"


def build_icon(running: int = 0) -> QIcon:
    """绘制一个带心跳折线的显示器图标；running>0 时右上角加绿色运行数角标。"""
    pixmap = QPixmap(EDGE, EDGE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    stand = QRectF(EDGE / 2 - 7, EDGE - 16, 14, 8)
    painter.fillRect(stand, EDGE_DARK)
    body = QRectF(4, 8, EDGE - 8, EDGE - 30)
    painter.setPen(QPen(EDGE_DARK, 2))
    painter.setBrush(BASE)
    painter.drawRoundedRect(body, 8, 8)

    middle = body.center().y()
    path = QPainterPath()
    path.moveTo(body.left() + 7, middle)
    path.lineTo(body.left() + 15, middle)
    path.lineTo(body.left() + 21, middle - 9)
    path.lineTo(body.left() + 27, middle + 9)
    path.lineTo(body.left() + 33, middle)
    path.lineTo(body.right() - 7, middle)
    painter.setPen(QPen(QColor("#ffffff"), 3))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)

    if running > 0:
        painter.setPen(Qt.NoPen)
        painter.setBrush(BADGE)
        painter.drawEllipse(QPointF(EDGE - 13.0, 13.0), 11.0, 11.0)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPixelSize(14)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(
            QRectF(EDGE - 25, 1, 24, 24), Qt.AlignCenter, str(min(int(running), 99))
        )
    painter.end()
    return QIcon(pixmap)


class ServiceTray(QSystemTrayIcon):
    """托盘图标 + 右键菜单；只负责显示与转发意图，退出流程由主窗口决定。"""

    open_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(build_icon(0), parent)
        self.setToolTip(TOOLTIP_IDLE)
        menu = QMenu()
        menu.addAction("打开界面", self.open_requested.emit)
        menu.addSeparator()
        menu.addAction("退出监控", self.quit_requested.emit)
        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def set_running_count(self, running: int) -> None:
        running = max(0, int(running))
        self.setIcon(build_icon(running))
        self.setToolTip(
            TOOLTIP_IDLE if not running else f"{TOOLTIP_IDLE}　运行中 {running} 个"
        )

    def notify_hidden(self) -> None:
        """第一次缩到托盘时说明怎么找回来（部分桌面环境不支持通知，忽略即可）。"""
        try:
            self.showMessage(
                "已缩到托盘",
                "监控仍在继续。右键托盘图标可打开界面或退出监控。",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
        except Exception:
            pass
