"""自绘时间序列折线图（QPainter，不依赖 QtCharts 扩展模块）。

主曲线是桶内平均值，虚线是桶内峰值；鼠标悬停读取最近的采样点。
"""

from __future__ import annotations

import math
import time
from typing import Sequence

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

MARGIN_LEFT = 52
MARGIN_RIGHT = 12
MARGIN_TOP = 26
MARGIN_BOTTOM = 22
MIN_HEIGHT = 150

GRID = QColor("#e8ebef")
AXIS_TEXT = QColor("#7a828c")
LABEL_TEXT = QColor("#202327")


def _nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10 ** exponent
    for factor in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if value <= factor * base:
            return factor * base
    return 10 * base


class TimeSeriesChart(QWidget):
    def __init__(self, title: str, unit: str = "", color: str = "#2f7bd0", parent=None):
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.color = QColor(color)
        self.max_color = QColor(color).darker(135)
        self._points: list[tuple[float, float]] = []
        self._peaks: list[tuple[float, float]] = []
        self._hover_index: int = -1
        self.empty_hint = "暂无数据（该服务还没有采样记录）"
        self._y_max = 1.0
        self._t0 = 0.0
        self._t1 = 0.0
        self.setMinimumHeight(MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ 数据

    def set_points(
        self,
        points: Sequence[tuple[float, float | None]],
        peaks: Sequence[tuple[float, float | None]] = (),
        domain: tuple[float, float] | None = None,
    ) -> None:
        self._points = [(ts, float(v)) for ts, v in points if v is not None]
        self._peaks = [(ts, float(v)) for ts, v in peaks if v is not None]
        values = [v for _, v in self._points] + [v for _, v in self._peaks]
        top = max(values) if values else 0.0
        if self.unit == "%":
            self._y_max = max(_nice_ceiling(top), 10.0)
            if top <= 100:
                self._y_max = max(self._y_max, min(100.0, _nice_ceiling(top)))
        else:
            self._y_max = _nice_ceiling(top) if top > 0 else 1.0
        times = [ts for ts, _ in self._points] or [ts for ts, _ in self._peaks]
        if domain is not None and domain[1] > domain[0]:
            self._t0, self._t1 = float(domain[0]), float(domain[1])
        elif times:
            self._t0, self._t1 = min(times), max(times)
            if self._t1 - self._t0 <= 0:
                # 单点时给两侧留出时间轴，否则四个刻度会印成同一个时刻
                self._t0 -= 30.0
                self._t1 += 30.0
        else:
            self._t0 = self._t1 = 0.0
        self._hover_index = -1
        self.update()

    @property
    def has_data(self) -> bool:
        return bool(self._points or self._peaks)

    # ------------------------------------------------------------------ 几何

    def _plot_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        return QRectF(MARGIN_LEFT, MARGIN_TOP, max(w - MARGIN_LEFT - MARGIN_RIGHT, 10),
                      max(h - MARGIN_TOP - MARGIN_BOTTOM, 10))

    def _x_of(self, ts: float, rect: QRectF) -> float:
        if not self._points and not self._peaks:
            return rect.left()
        if self._t1 - self._t0 <= 0:
            return rect.center().x()
        ratio = min(max((ts - self._t0) / (self._t1 - self._t0), 0.0), 1.0)
        return rect.left() + ratio * rect.width()

    def _y_of(self, value: float, rect: QRectF) -> float:
        ratio = 0.0 if self._y_max <= 0 else min(max(value / self._y_max, 0.0), 1.0)
        return rect.bottom() - ratio * rect.height()

    def _nearest(self, pos: QPoint) -> int:
        if not self._points:
            return -1
        rect = self._plot_rect()
        best, best_dist = -1, float("inf")
        for index, (ts, value) in enumerate(self._points):
            dist = abs(self._x_of(ts, rect) - pos.x())
            if dist < best_dist:
                best, best_dist = index, dist
        return best if best_dist < 24 else -1

    # ------------------------------------------------------------------ 绘制

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        rect = self._plot_rect()
        metrics = QFontMetrics(self.font())

        title_font = QFont(self.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QPen(LABEL_TEXT))
        painter.drawText(QRectF(MARGIN_LEFT - 40, 4, self.width() - 50, 18),
                         Qt.AlignVCenter | Qt.AlignLeft, self.title)
        if self.unit:
            painter.setFont(self.font())
            painter.setPen(QPen(AXIS_TEXT))
            painter.drawText(rect.left() + rect.width() - 60, 4, 60, 18,
                             Qt.AlignVCenter | Qt.AlignRight, self.unit)

        steps = 4
        painter.setFont(self.font())
        for index in range(steps + 1):
            value = self._y_max * index / steps
            y = self._y_of(value, rect)
            painter.setPen(QPen(GRID))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QPen(AXIS_TEXT))
            label = self._format_value(value)
            painter.drawText(QRectF(0, y - 9, MARGIN_LEFT - 6, 18),
                             Qt.AlignVCenter | Qt.AlignRight, label)

        if not self.has_data:
            painter.setPen(QPen(AXIS_TEXT))
            painter.drawText(rect, Qt.AlignCenter, self.empty_hint)
            painter.setPen(QPen(GRID))
            self._draw_x_labels(painter, rect, metrics)
            return

        # 面积 + 主曲线
        pts = [QPointF(self._x_of(ts, rect), self._y_of(v, rect)) for ts, v in self._points]
        if len(pts) >= 2:
            area = QPolygonF(pts)
            area.append(QPointF(pts[-1].x(), rect.bottom()))
            area.append(QPointF(pts[0].x(), rect.bottom()))
            fill = QColor(self.color)
            fill.setAlpha(38)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawPolygon(area)
        if pts:
            pen = QPen(self.color)
            pen.setWidthF(1.6)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            path = QPainterPath(pts[0])
            for point in pts[1:]:
                path.lineTo(point)
            painter.drawPath(path)
            if len(pts) == 1:
                painter.setBrush(QBrush(self.color))
                painter.drawEllipse(pts[0], 2.5, 2.5)

        # 峰值虚线
        peak_pts = [QPointF(self._x_of(ts, rect), self._y_of(v, rect)) for ts, v in self._peaks]
        if len(peak_pts) >= 2:
            pen = QPen(self.max_color)
            pen.setStyle(Qt.DashLine)
            pen.setWidthF(1.1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            path = QPainterPath(peak_pts[0])
            for point in peak_pts[1:]:
                path.lineTo(point)
            painter.drawPath(path)

        if self._hover_index >= 0 and self._hover_index < len(pts):
            point = pts[self._hover_index]
            painter.setPen(QPen(QColor("#b7bec7")))
            painter.drawLine(QPointF(point.x(), rect.top()), QPointF(point.x(), rect.bottom()))
            painter.setBrush(QBrush(self.color))
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawEllipse(point, 3.4, 3.4)

        self._draw_x_labels(painter, rect, metrics)

    def _draw_x_labels(self, painter: QPainter, rect: QRectF, metrics: QFontMetrics) -> None:
        painter.setPen(QPen(AXIS_TEXT))
        if self._t1 - self._t0 <= 0:
            painter.drawText(QRectF(rect.left(), rect.bottom() + 3, 70, 16),
                             Qt.AlignLeft, time.strftime("%H:%M:%S", time.localtime()))
            return
        first, last = self._t0, self._t1
        count = 4 if rect.width() > 320 else 3
        for index in range(count):
            ts = first + (last - first) * index / (count - 1)
            x = rect.left() + rect.width() * index / (count - 1)
            label = self._format_time(ts, last - first)
            width = min(metrics.horizontalAdvance(label) + 8, rect.width() / count)
            box = QRectF(x - width / 2, rect.bottom() + 3, width, 16)
            if index == 0:
                box.setLeft(rect.left() - 6)
                box.setWidth(width)
            elif index == count - 1:
                box.setLeft(rect.right() - width)
                box.setWidth(width)
            painter.drawText(box, Qt.AlignCenter, label)

    def _format_time(self, ts: float, span: float) -> str:
        if span > 86400 * 2:
            return time.strftime("%m-%d %H:%M", time.localtime(ts))
        if span > 3600:
            return time.strftime("%H:%M", time.localtime(ts))
        return time.strftime("%H:%M:%S", time.localtime(ts))

    def _format_value(self, value: float) -> str:
        if self.unit == "MB":
            if value >= 1024:
                return f"{value / 1024:.1f}G"
            return f"{value:.0f}"
        if self.unit == "%":
            return f"{value:.0f}"
        if value >= 100:
            return f"{value:.0f}"
        return f"{value:.1f}" if value < 10 else f"{value:.0f}"

    # ------------------------------------------------------------------ 交互

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._nearest(event.position().toPoint())
        if index != self._hover_index:
            self._hover_index = index
            self.update()
        if index >= 0:
            ts, value = self._points[index]
            peak = self._peaks[index][1] if index < len(self._peaks) else None
            extra = f"　峰值 {_numfmt(peak)}{self.unit}" if peak is not None else ""
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}\n"
                f"平均 {_numfmt(value)}{self.unit}{extra}",
                self,
            )

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_index != -1:
            self._hover_index = -1
            self.update()


def _numfmt(value: float) -> str:
    value = float(value)
    return f"{value:.1f}" if abs(value) < 10 else f"{value:.0f}"
