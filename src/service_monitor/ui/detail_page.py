"""服务详情页：一段时间内的 CPU/GPU/内存折线 + 每次启动的控制台输出记录。"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import (
    STATUS_RUNNING,
    STATUS_STOPPING,
    STREAM_INFO,
    STREAM_STDERR,
    code_text,
    duration_text,
    exit_reason_label,
    status_label,
)
from .charts import TimeSeriesChart
from .widgets import (
    TextItem,
    configure_table,
    fmt_bytes,
    fmt_pct,
    fmt_ts,
    fmt_ts_compact,
    status_color,
)

GRANULARITIES = (("10 秒", 10), ("1 分钟", 60), ("10 分钟", 600), ("1 小时", 3600))
RANGES = (("10 分钟", 600), ("1 小时", 3600), ("6 小时", 21600), ("24 小时", 86400),
          ("7 天", 604800), ("30 天", 2592000), ("全部", 0))
DEFAULT_GRANULARITY = 1
DEFAULT_RANGE = 1
IS_WINDOWS = sys.platform == "win32"

# 输出面板是深色背景（见 main_window 的 QPlainTextEdit 样式），文字必须用浅色
COLOR_STDOUT = QColor("#e6e8eb")
COLOR_STDERR = QColor("#ff8f8f")
COLOR_INFO = QColor("#79b8ff")
COLOR_TIMESTAMP = QColor("#7f8691")
MAX_VISIBLE_LINES = 8000
INITIAL_LINES = 2000


class ServiceDetailPage(QWidget):
    back_requested = Signal()

    def __init__(self, db, supervisor, parent=None):
        super().__init__(parent)
        self.db = db
        self.supervisor = supervisor
        self.service_id: int | None = None
        self.session_id: int | None = None
        self._output_last_id = 0
        self._output_line_count = 0
        self._pending_new_lines = 0
        self._output_paused = False

        self.back_button = QPushButton("← 返回列表")
        self.back_button.clicked.connect(self.back_requested.emit)
        self.title_label = QLabel("服务详情")
        font = QFont()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 2)
        self.title_label.setFont(font)
        self.status_label = QLabel("—")
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("hint")
        self.start_button = QPushButton("启动")
        self.start_button.clicked.connect(lambda: self._command("start"))
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(lambda: self._command("stop"))
        self.restart_button = QPushButton("重启")
        self.restart_button.clicked.connect(lambda: self._command("restart"))

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.back_button)
        header.addWidget(self.title_label)
        header.addWidget(self.status_label)
        header.addStretch(1)
        header.addWidget(self.start_button)
        header.addWidget(self.stop_button)
        header.addWidget(self.restart_button)
        header_line = QHBoxLayout()
        header_line.addSpacing(0)
        sub = QVBoxLayout()
        sub.setSpacing(2)
        sub.addLayout(header)
        sub.addWidget(self.meta_label)

        self.granularity_combo = QComboBox()
        for label, _ in GRANULARITIES:
            self.granularity_combo.addItem(label)
        self.granularity_combo.setCurrentIndex(DEFAULT_GRANULARITY)
        self.range_combo = QComboBox()
        for label, _ in RANGES:
            self.range_combo.addItem(label)
        self.range_combo.setCurrentIndex(DEFAULT_RANGE)
        self.refresh_button = QPushButton("刷新曲线")
        self.refresh_button.clicked.connect(self.reload_charts)
        self.auto_refresh = QCheckBox("自动刷新")
        self.auto_refresh.setChecked(True)
        granularity_hint = QLabel("粒度")
        range_hint = QLabel("范围")
        chart_bar = QHBoxLayout()
        chart_bar.setSpacing(6)
        chart_bar.addWidget(granularity_hint)
        chart_bar.addWidget(self.granularity_combo)
        chart_bar.addWidget(range_hint)
        chart_bar.addWidget(self.range_combo)
        chart_bar.addWidget(self.refresh_button)
        chart_bar.addWidget(self.auto_refresh)
        chart_bar.addStretch(1)
        self.chart_info = QLabel("")
        self.chart_info.setObjectName("hint")
        chart_bar.addWidget(self.chart_info)

        self.cpu_chart = TimeSeriesChart("CPU 利用率", "%", "#2f7bd0")
        self.gpu_chart = TimeSeriesChart("GPU 利用率", "%", "#7b5cd0")
        self.mem_chart = TimeSeriesChart("内存占用", "MB", "#2f9e63")
        charts = QSplitter(Qt.Vertical)
        charts.addWidget(self.cpu_chart)
        charts.addWidget(self.gpu_chart)
        charts.addWidget(self.mem_chart)
        charts.setCollapsible(0, False)

        # 会话列表
        self.session_table = QTableWidget(0, 6)
        self.session_table.setHorizontalHeaderLabels(
            ("开始时间", "时长", "PID", "退出码 / 状态", "峰值内存", "输出行数")
        )
        configure_table(self.session_table)
        self.session_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((112, 46, 46, 100, 60)):
            self.session_table.setColumnWidth(column, width)
        self.session_table.itemSelectionChanged.connect(self._on_session_selected)
        session_box = QVBoxLayout()
        session_title = QLabel("启动记录（点击某一条查看其控制台输出）")
        session_title.setObjectName("hint")
        session_box.addWidget(session_title)
        session_box.addWidget(self.session_table)
        session_panel = QWidget()
        session_panel.setLayout(session_box)

        # 输出视图
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(MAX_VISIBLE_LINES)
        mono = QFont("Consolas" if IS_WINDOWS else "Monospace")
        mono.setStyleHint(QFont.Monospace)
        # 字号交给全局样式（跟随设置的界面字号），这里只固定等宽字体族
        self.output.setFont(mono)
        self.follow = QCheckBox("跟随最新输出")
        self.follow.setChecked(True)
        self.show_errors_only = QCheckBox("只看 stderr")
        self.show_errors_only.toggled.connect(lambda _=False: self._reload_output())
        self.copy_button = QPushButton("复制")
        self.copy_button.clicked.connect(self._copy_output)
        self.export_button = QPushButton("导出…")
        self.export_button.clicked.connect(self._export_output)
        self.output_info = QLabel("")
        self.output_info.setObjectName("hint")
        self.jump_latest = QPushButton("↓ 回到最新")
        self.jump_latest.setVisible(False)
        self.jump_latest.clicked.connect(self._jump_to_bottom)
        output_bar = QHBoxLayout()
        output_bar.setSpacing(6)
        output_bar.addWidget(self.output_info)
        output_bar.addWidget(self.jump_latest)
        output_bar.addStretch(1)
        output_bar.addWidget(self.follow)
        output_bar.addWidget(self.show_errors_only)
        output_bar.addWidget(self.copy_button)
        output_bar.addWidget(self.export_button)
        output_box = QVBoxLayout()
        output_box.setContentsMargins(0, 0, 0, 0)
        output_title = QLabel("控制台输出")
        output_title.setObjectName("hint")
        output_box.addWidget(output_title)
        output_box.addLayout(output_bar)
        output_box.addWidget(self.output)
        output_panel = QWidget()
        output_panel.setLayout(output_box)

        lower = QSplitter(Qt.Horizontal)
        lower.addWidget(session_panel)
        lower.addWidget(output_panel)
        lower.setStretchFactor(0, 0)
        lower.setStretchFactor(1, 1)
        lower.setSizes([440, 700])

        body = QSplitter(Qt.Vertical)
        body.addWidget(charts)
        body.addWidget(lower)
        body.setCollapsible(0, False)
        body.setSizes([380, 360])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addLayout(sub)
        layout.addLayout(chart_bar)
        layout.addWidget(body)

        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._auto_refresh_tick)

    # -------------------------------------------------------------------- 打开

    def open_service(self, service_id: int) -> None:
        service = self.db.get_service(service_id)
        if service is None:
            return
        self.service_id = int(service_id)
        # 换一个服务时必须丢弃上一个服务的会话选择：行号可能恰好相同，
        # 选择信号不会触发，输出面板会残留上个服务的日志。
        self.session_id = None
        self.title_label.setText(service["name"])
        command = service["command"]
        self.meta_label.setText(
            f"命令：{command}"
            + (f"　工作目录：{service['working_dir']}" if service["working_dir"] else "")
            + (f"　描述：{service['description']}" if service["description"] else "")
        )
        self.meta_label.setToolTip(command)
        self.reload_sessions()
        self._on_session_selected()
        self.reload_charts()
        self.update_header()
        self._timer.start()

    def close_page(self) -> None:
        self._timer.stop()

    # ------------------------------------------------------------------ 头部

    def update_header(self) -> None:
        if self.service_id is None:
            return
        state = self.supervisor.state_of(self.service_id)
        status = state["status"]
        if status:
            self.status_label.setText(status_label(status))
            self.status_label.setStyleSheet(f"color: {status_color(status).name()}; font-weight: bold;")
        live = status in (STATUS_RUNNING, STATUS_STOPPING)
        if live:
            self.status_label.setText(
                f"{status_label(status)}　PID {state.get('pid')}　"
                f"CPU {fmt_pct(state.get('cpu'))}　内存 {fmt_bytes(state.get('mem'))}　"
                f"峰值 {fmt_bytes(state.get('mem_peak'))}"
            )
        elif state.get("cached"):
            self.status_label.setText(
                f"{status_label(status) if status else '未启动'}　"
                f"上次 PID {state.get('pid')}　最后采样 CPU {fmt_pct(state.get('cpu'))}"
            )
        else:
            self.status_label.setText(status_label(status) if status else "未启动")
        self.start_button.setEnabled(not live)
        self.stop_button.setEnabled(live)

    # ------------------------------------------------------------------ 曲线

    def reload_charts(self) -> None:
        if self.service_id is None:
            return
        granularity = GRANULARITIES[self.granularity_combo.currentIndex()][1]
        span = RANGES[self.range_combo.currentIndex()][1]
        values = self.supervisor.settings.values
        now = time.time()
        since = None if span == 0 else now - span
        domain = None if span == 0 else (since, now)
        points = self.db.aggregate(
            service_id=self.service_id,
            since=since,
            granularity=granularity,
            max_points=values.max_chart_points,
        )
        self.cpu_chart.set_points(
            [(p["ts"], p["cpu_avg"]) for p in points],
            [(p["ts"], p["cpu_max"]) for p in points],
            domain,
        )
        self.gpu_chart.set_points(
            [(p["ts"], p["gpu_avg"]) for p in points],
            [(p["ts"], p["gpu_max"]) for p in points],
            domain,
        )
        self.mem_chart.set_points(
            [(p["ts"], _to_mb(p["mem_avg"])) for p in points],
            [(p["ts"], _to_mb(p["mem_max"])) for p in points],
            domain,
        )
        if not values.gpu_enabled:
            self.gpu_chart.empty_hint = "GPU 采集已在设置中关闭"
        elif points:
            self.gpu_chart.empty_hint = "该服务没有 GPU 占用（未出现在 nvidia-smi 计算进程列表中）"
        else:
            self.gpu_chart.empty_hint = "暂无数据（该服务还没有采样记录）"
        window = "全部历史" if span == 0 else RANGES[self.range_combo.currentIndex()][0]
        self.chart_info.setText(f"{len(points)} 个数据点　范围 {window}　粒度 {GRANULARITIES[self.granularity_combo.currentIndex()][0]}")

    def _auto_refresh_tick(self) -> None:
        if not self.isVisible() or not self.auto_refresh.isChecked():
            return
        self.reload_charts()
        self.reload_sessions(keep_selection=True)
        self.update_header()

    # ------------------------------------------------------------------ 会话

    def reload_sessions(self, keep_selection: bool = True) -> None:
        if self.service_id is None:
            return
        sessions = self.db.list_sessions_with_counts(self.service_id, limit=300)
        selected = self.session_id if keep_selection else None
        self.session_table.setUpdatesEnabled(False)
        self.session_table.setRowCount(len(sessions))
        now = time.time()
        for row, session in enumerate(sessions):
            running = session["ended_at"] is None
            duration = (now - session["started_at"]) if running else (
                session["ended_at"] - session["started_at"]
            )
            if running:
                exit_cell = "服务运行中"
            elif session["exit_reason"]:
                exit_cell = (
                    f"{code_text(session['exit_code'])}"
                    f"（{exit_reason_label(session['exit_reason'], session['exit_code'])}）"
                )
            else:
                exit_cell = code_text(session["exit_code"])
            cells = (
                TextItem(fmt_ts_compact(session["started_at"])),
                TextItem(duration_text(duration)),
                TextItem("—" if session["pid"] is None else str(session["pid"])),
                TextItem(exit_cell),
                TextItem(fmt_bytes(session["peak_rss"]) if session["peak_rss"] else "—"),
                TextItem(str(session["lines"])),
            )
            for col, cell in enumerate(cells):
                self.session_table.setItem(row, col, cell)
            color = QColor("#2f9e63") if running else status_color(
                "crashed" if (session["exit_code"] or 0) != 0 else "exited"
            )
            status_cell = self.session_table.item(row, 3)
            status_cell.setForeground(color)
            self.session_table.item(row, 0).setData(Qt.UserRole, session["id"])
        self.session_table.setUpdatesEnabled(True)
        if selected is not None:
            for row in range(self.session_table.rowCount()):
                item = self.session_table.item(row, 0)
                if item and item.data(Qt.UserRole) == selected:
                    self.session_table.selectRow(row)
                    return
        if sessions:
            self.session_table.selectRow(0)
        else:
            self.session_id = None
            self._clear_output()

    def _on_session_selected(self) -> None:
        row = self.session_table.currentRow()
        if row < 0:
            return
        item = self.session_table.item(row, 0)
        if item is None:
            return
        session_id = int(item.data(Qt.UserRole))
        if session_id != self.session_id:
            self.session_id = session_id
            self._reload_output()

    # ------------------------------------------------------------------ 输出

    def _clear_output(self) -> None:
        self.output.clear()
        self._output_last_id = 0
        self._output_line_count = 0
        self.output_info.setText("未选择启动记录")

    def _reload_output(self) -> None:
        if self.session_id is None:
            self._clear_output()
            return
        session = self.db.get_session(self.session_id)
        total = self.db.output_count(self.session_id)
        limit = max(0, min(total, INITIAL_LINES))
        offset = max(0, total - limit)
        rows = self.db.output_page(self.session_id, offset, limit)
        self._output_paused = False
        self.output.setPlainText("")
        cursor = self.output.textCursor()
        cursor.beginEditBlock()
        for row in rows:
            self._insert_line(cursor, row["ts"], row["stream"], row["line"])
        cursor.endEditBlock()
        self._output_last_id = rows[-1]["id"] if rows else self.db.output_last_id(self.session_id)
        self._output_line_count = total
        self._pending_new_lines = 0
        self.jump_latest.setVisible(False)
        running = session is not None and session["ended_at"] is None
        label = "服务运行中" if running else (
            f"已退出（{code_text(session['exit_code'] if session else None)}）"
        )
        self.output_info.setText(
            f"共 {total} 行（显示最近 {len(rows)} 行）　{label}"
            + ("　实时刷新中" if running else "")
        )
        self._jump_to_bottom()

    def _insert_line(self, cursor: QTextCursor, ts: float, stream: str, text: str) -> None:
        if self.show_errors_only.isChecked() and stream != STREAM_STDERR:
            return
        fmt = QTextCharFormat()
        fmt.setForeground(
            COLOR_STDERR if stream == STREAM_STDERR
            else COLOR_INFO if stream == STREAM_INFO
            else COLOR_STDOUT
        )
        stamp = QTextCharFormat()
        stamp.setForeground(COLOR_TIMESTAMP)
        cursor.insertText(f"[{fmt_ts(ts, with_date=False)}] ", stamp)
        cursor.insertText(f"{text}\n", fmt)

    def append_output_rows(self, rows) -> None:
        """实时追加；若用户不在末尾，保持他正在看的位置不动。"""
        if not rows:
            return
        bar = self.output.verticalScrollBar()
        was_bottom = bar.value() >= bar.maximum() - 2
        if not was_bottom and self.output.document().blockCount() + len(rows) > MAX_VISIBLE_LINES:
            # 显示缓冲已到上限：不再追加，避免顶部被裁掉时把他正在看的内容顶走
            self._output_paused = True
            self.jump_latest.setVisible(True)
            self.jump_latest.setText("↓ 显示已达上限，点击跳到最新（数据仍在完整记录）")
            return
        previous = bar.value()
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        for row in rows:
            self._insert_line(cursor, row["ts"], row["stream"], row["line"])
        cursor.endEditBlock()
        self._output_last_id = rows[-1]["id"]
        self._output_line_count += len(rows)
        if was_bottom and self.follow.isChecked():
            self._jump_to_bottom()
            self.jump_latest.setVisible(False)
            self._pending_new_lines = 0
        else:
            bar.setValue(previous)
            self._pending_new_lines += len(rows)
            self.jump_latest.setVisible(True)
            self.jump_latest.setText(f"↓ 回到最新（{self._pending_new_lines} 条新输出）")
        if self.service_id is not None:
            running = self.supervisor.running_session_id(self.service_id) == self.session_id
            if running:
                self.output_info.setText(
                    f"共 {self._output_line_count} 行　服务运行中　实时刷新中"
                )

    def _jump_to_bottom(self) -> None:
        if self._output_paused:
            self._output_paused = False
            self._reload_output()
            return
        bar = self.output.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._pending_new_lines = 0
        self.jump_latest.setVisible(False)

    def on_output_signal(self, session_id: int) -> None:
        if self.session_id is None or int(session_id) != int(self.session_id):
            return
        rows = self.db.output_after(self.session_id, self._output_last_id, 2000)
        if rows:
            self.append_output_rows(rows)

    def _copy_output(self) -> None:
        self.output.selectAll()
        self.output.copy()
        self.output.textCursor().clearSelection()

    def _export_output(self) -> None:
        if self.session_id is None:
            QMessageBox.information(self, "导出", "请先选择一条启动记录。")
            return
        from PySide6.QtWidgets import QFileDialog

        default = f"session-{self.session_id}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        path, _ = QFileDialog.getSaveFileName(self, "导出控制台输出", default, "日志 (*.log *.txt)")
        if not path:
            return
        total = self.db.output_count(self.session_id)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                offset = 0
                while offset < total:
                    rows = self.db.output_page(self.session_id, offset, 5000)
                    if not rows:
                        break
                    for row in rows:
                        prefix = "" if row["stream"] == "stdout" else f"[{row['stream']}] "
                        handle.write(f"[{fmt_ts(row['ts'])}] {prefix}{row['line']}\n")
                    offset += len(rows)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.supervisor.sig.log_message.emit(f"[monitor] 已导出 {total} 行到 {path}")

    # -------------------------------------------------------------------- 命令

    def _command(self, action: str) -> None:
        if self.service_id is None:
            return
        if action == "start":
            self.supervisor.request_start(self.service_id)
        elif action == "stop":
            self.supervisor.request_stop(self.service_id)
        else:
            self.supervisor.request_restart(self.service_id)


def _to_mb(value) -> float | None:
    if value is None:
        return None
    return float(value) / 1024 / 1024
