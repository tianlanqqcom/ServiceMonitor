"""外层列表页：所有已添加服务的实时状态，可在行内直接启停。"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..models import (
    STATUS_EXITED,
    STATUS_RUNNING,
    STATUS_STOPPING,
    duration_text,
    status_from_session,
    status_label,
)
from .service_dialog import ServiceDialog
from .widgets import TextItem, configure_table, fmt_bytes, fmt_pct, status_color

COLUMNS = ("状态", "服务名", "描述", "PID", "CPU", "GPU", "当前内存", "峰值内存", "运行时长", "操作")
WIDTHS = (96, 168, 0, 74, 70, 70, 94, 94, 90, 118)
LIVE = (STATUS_RUNNING, STATUS_STOPPING)
TEXT_COLOR = QColor("#202327")


class ServiceListPage(QWidget):
    open_detail = Signal(int)

    def __init__(self, db, supervisor, parent=None):
        super().__init__(parent)
        self.db = db
        self.supervisor = supervisor
        self._row_ids: list[int] = []
        self._services: list = []

        self.add_button = QPushButton("＋ 添加服务")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self.open_add_dialog)
        self.edit_button = QPushButton("编辑")
        self.edit_button.clicked.connect(self.edit_selected)
        self.remove_button = QPushButton("删除")
        self.remove_button.clicked.connect(self.remove_selected)
        self.start_all_button = QPushButton("全部启动")
        self.start_all_button.clicked.connect(lambda: self._batch(True))
        self.stop_all_button = QPushButton("全部停止")
        self.stop_all_button.clicked.connect(lambda: self._batch(False))

        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称或描述过滤…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        for widget in (self.add_button, self.edit_button, self.remove_button):
            toolbar.addWidget(widget)
        toolbar.addSpacing(10)
        toolbar.addWidget(self.start_all_button)
        toolbar.addWidget(self.stop_all_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.search)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        configure_table(self.table)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        for index, width in enumerate(WIDTHS):
            if width:
                self.table.setColumnWidth(index, width)
            else:
                header.setSectionResizeMode(index, QHeaderView.Stretch)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.Fixed)

        hint = QLabel("单击任意一行查看该服务的历史曲线与控制台输出；行内按钮可直接启动或停止。")
        hint.setObjectName("hint")
        self.summary = QLabel("")
        bottom = QHBoxLayout()
        bottom.addWidget(hint)
        bottom.addStretch(1)
        bottom.addWidget(self.summary)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)
        layout.addLayout(bottom)

        self.refresh()
        self._update_buttons()

    # ------------------------------------------------------------------ 数据刷新

    def refresh(self) -> None:
        services = self.db.list_services()
        ids = [int(s["id"]) for s in services]
        self._services = services
        if ids != self._row_ids:
            self._row_ids = ids
            self._rebuild_structure()
        self._update_values()

    def _rebuild_structure(self) -> None:
        self.table.setUpdatesEnabled(False)
        selected = self.current_service_id()
        self.table.setRowCount(len(self._row_ids))
        for row, service in enumerate(self._services):
            sid = int(service["id"])
            name = TextItem(service["name"])
            font = QFont()
            font.setBold(True)
            name.setFont(font)
            self.table.setItem(row, 0, QTableWidgetItem("—"))
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, TextItem(service["description"]))
            for col in range(3, 9):
                self.table.setItem(row, col, QTableWidgetItem("—"))
            self.table.setCellWidget(row, 9, _RowActions(self, sid, service["name"]))
        if selected is not None:
            row = self._row_of(selected)
            if row >= 0:
                self.table.selectRow(row)
        self.table.setUpdatesEnabled(True)

    def _update_values(self) -> None:
        last_sessions = self.db.last_sessions_by_service()
        latest_samples = self.db.latest_samples_by_service()
        now = time.time()
        running = 0
        for row, service in enumerate(self._services):
            sid = int(service["id"])
            state = self.supervisor.state_of(sid)
            live = state["status"] in LIVE
            session = last_sessions.get(sid)
            sample = latest_samples.get(sid)
            status = state["status"] or (
                STATUS_RUNNING if session and session["ended_at"] is None
                else status_from_session(session)
            )
            if live:
                running += 1
            item = QTableWidgetItem(status_label(status))
            item.setForeground(status_color(status))
            if state.get("paused"):
                item.setToolTip(state["paused"])
            elif session is not None and session["exit_code"] is not None:
                item.setToolTip(f"上次退出码：{session['exit_code']}")
            self.table.setItem(row, 0, item)

            pid = state.get("pid") if live else (session["pid"] if session else None)
            cpu = fmt_pct(state.get("cpu")) if live else "—"
            gpu = fmt_pct(state.get("gpu")) if live else "—"
            if live:
                current_mem = fmt_bytes(state.get("mem"))
                peak_mem = fmt_bytes(state.get("mem_peak"))
                duration = duration_text(now - (session["started_at"] if session else now))
            else:
                current_mem = fmt_bytes(sample["mem"]) if sample else "—"
                peak_mem = fmt_bytes(session["peak_rss"]) if session and session["peak_rss"] else "—"
                duration = (
                    duration_text(session["ended_at"] - session["started_at"])
                    if session and session["ended_at"]
                    else "—"
                )
            for col, value in zip((3, 4, 5, 6, 7, 8), (pid, cpu, gpu, current_mem, peak_mem, duration)):
                cell = self.table.item(row, col) or QTableWidgetItem()
                cell.setText("—" if value in (None, "") else str(value))
                cell.setForeground(status_color(status) if col == 3 else TEXT_COLOR)
                if not live and col == 6 and sample:
                    cell.setToolTip("上次采样时的内存占用（服务当前未运行）")
                elif not live and col == 8 and session and session["ended_at"]:
                    cell.setToolTip("上次运行的时长")
                self.table.setItem(row, col, cell)
            widget = self.table.cellWidget(row, 9)
            if isinstance(widget, _RowActions):
                widget.set_running(live)
        self.summary.setText(f"共 {len(self._row_ids)} 个服务，运行中 {running} 个")
        self._apply_filter(self.search.text())

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for row, service in enumerate(self._services):
            hay = f"{service['name']} {service['description'] or ''}".lower()
            self.table.setRowHidden(row, bool(needle) and needle not in hay)

    # -------------------------------------------------------------------- 操作

    def current_service_id(self) -> int | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._row_ids):
            return self._row_ids[row]
        return None

    def _row_of(self, service_id: int) -> int:
        try:
            return self._row_ids.index(int(service_id))
        except ValueError:
            return -1

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column != 9 and 0 <= row < len(self._row_ids):
            self.open_detail.emit(self._row_ids[row])

    def _update_buttons(self) -> None:
        has = self.current_service_id() is not None
        self.edit_button.setEnabled(has)
        self.remove_button.setEnabled(has)

    def open_add_dialog(self) -> None:
        dialog = ServiceDialog(self, title="添加服务")
        if dialog.exec() != ServiceDialog.Accepted:
            return
        try:
            self.db.create_service(dialog.values())
        except Exception as exc:
            QMessageBox.critical(self, "添加失败", f"保存服务失败：{exc}")
            return
        self.refresh()

    def edit_selected(self) -> None:
        sid = self.current_service_id()
        service = self.db.get_service(sid) if sid else None
        if service is None:
            return
        dialog = ServiceDialog(self, service=service, title="编辑服务")
        if dialog.exec() != ServiceDialog.Accepted:
            return
        self.db.update_service(sid, dialog.values())
        if self.supervisor.state_of(sid)["status"] in LIVE:
            self.supervisor.sig.log_message.emit(
                f"[monitor] “{service['name']}”的配置已更新，将在下次启动时生效"
            )
        self.refresh()

    def remove_selected(self) -> None:
        sid = self.current_service_id()
        if sid is None:
            return
        service = self.db.get_service(sid)
        if self.supervisor.state_of(sid)["status"] in LIVE:
            QMessageBox.warning(self, "无法删除", f"“{service['name']}”正在运行，请先停止。")
            return
        confirm = QMessageBox.question(
            self,
            "删除服务",
            f"确定删除“{service['name']}”？该服务的历史监控数据与控制台输出会一并删除。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self.db.delete_service(sid)
        self.refresh()

    def _batch(self, start: bool) -> None:
        for service in self._services:
            sid = int(service["id"])
            live = self.supervisor.state_of(sid)["status"] in LIVE
            if start and not live:
                self.supervisor.request_start(sid)
            elif not start and live:
                self.supervisor.request_stop(sid)

    def _context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if not (0 <= row < len(self._row_ids)):
            return
        self.table.selectRow(row)
        sid = self._row_ids[row]
        live = self.supervisor.state_of(sid)["status"] in LIVE
        menu = QMenu(self)
        menu.addAction("查看详情", lambda: self.open_detail.emit(sid))
        menu.addAction("启动" if not live else "停止", lambda: self._toggle(sid))
        menu.addSeparator()
        menu.addAction("编辑", self.edit_selected)
        menu.addAction("删除", self.remove_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _toggle(self, sid: int) -> None:
        if self.supervisor.state_of(sid)["status"] in LIVE:
            self.supervisor.request_stop(sid)
        else:
            self.supervisor.request_start(sid)

    def select_service(self, service_id: int) -> None:
        row = self._row_of(service_id)
        if row >= 0:
            self.table.selectRow(row)


class _RowActions(QWidget):
    """行内启动 / 停止按钮。"""

    def __init__(self, page: ServiceListPage, service_id: int, name: str):
        super().__init__(page)
        self.page = page
        self.service_id = service_id
        self.start_button = QToolButton(self)
        self.start_button.setText("启动")
        self.start_button.setToolTip(f"启动 {name}")
        self.start_button.clicked.connect(lambda: self.page.supervisor.request_start(self.service_id))
        self.stop_button = QToolButton(self)
        self.stop_button.setText("停止")
        self.stop_button.setToolTip(f"停止 {name}")
        self.stop_button.clicked.connect(lambda: self.page.supervisor.request_stop(self.service_id))
        box = QHBoxLayout(self)
        box.setContentsMargins(2, 1, 2, 1)
        box.setSpacing(4)
        box.addWidget(self.start_button)
        box.addWidget(self.stop_button)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
