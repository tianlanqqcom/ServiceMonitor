"""主窗口：外层列表页与详情页的切换、全局刷新、设置与数据目录入口。"""

from __future__ import annotations

import time

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..config import data_dir
from ..models import STATUS_RUNNING, STATUS_STOPPING
from .detail_page import ServiceDetailPage
from .service_list_page import ServiceListPage
from .settings_dialog import SettingsDialog

STYLE = """
QWidget { font-size: 13px; }
QMainWindow, QStackedWidget, QWidget#central { background: #f5f6f8; }
QTableWidget { background: #ffffff; alternate-background-color: #fafbfc;
               gridline-color: #eceef1; selection-background-color: #dfe9f5;
               selection-color: #1b1d20; }
QPushButton { padding: 4px 12px; }
QPushButton#primary { background: #2f7bd0; color: #ffffff; border: 0; border-radius: 4px;
                      padding: 5px 14px; }
QPushButton#primary:hover { background: #276ab5; }
QLabel#hint { color: #7a828c; font-size: 12px; }
QPlainTextEdit { background: #1e2126; color: #e6e8eb; border: 1px solid #d5d9de; }
QComboBox, QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox { background: #ffffff;
    border: 1px solid #cdd3da; border-radius: 3px; padding: 2px 6px; }
QSplitter::handle { background: #dfe3e8; }
"""


class MainWindow(QMainWindow):
    def __init__(self, db, supervisor, settings_store):
        super().__init__()
        self.db = db
        self.supervisor = supervisor
        self.settings_store = settings_store
        self.setWindowTitle("ServiceMonitor · 服务状态监控")
        self.resize(1240, 780)
        self.setStyleSheet(STYLE)

        self.list_page = ServiceListPage(db, supervisor)
        self.detail_page = ServiceDetailPage(db, supervisor)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.detail_page)

        central = QWidget(self)
        central.setObjectName("central")
        box = QVBoxLayout(central)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.stack)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪")

        self._build_menu()
        self.list_page.open_detail.connect(self.show_detail)
        self.detail_page.back_requested.connect(self.show_list)

        signals = supervisor.sig
        signals.output_lines.connect(self.detail_page.on_output_signal)
        signals.session_started.connect(self._on_session_change)
        signals.session_finished.connect(self._on_session_finish)
        signals.restart_scheduled.connect(self._on_restart_scheduled)
        signals.log_message.connect(lambda text: self.statusBar().showMessage(text, 8000))
        signals.stats_changed.connect(self._on_stats)

        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._refresh_current_page)
        self._timer.start()

    # -------------------------------------------------------------------- 菜单

    def _build_menu(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("文件")
        add = QAction("添加服务…", self)
        add.setShortcut(QKeySequence("Ctrl+N"))
        add.triggered.connect(self.list_page.open_add_dialog)
        settings = QAction("设置…", self)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.triggered.connect(self.open_settings)
        open_dir = QAction("打开数据目录", self)
        open_dir.triggered.connect(self._open_data_dir)
        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addActions([add, settings, open_dir])
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        control = bar.addMenu("控制")
        start_all = QAction("启动全部", self)
        start_all.triggered.connect(lambda: self.list_page._batch(True))
        stop_all = QAction("停止全部", self)
        stop_all.triggered.connect(lambda: self.list_page._batch(False))
        prune = QAction("按保留策略清理历史数据", self)
        prune.triggered.connect(self._prune)
        control.addActions([start_all, stop_all])
        control.addSeparator()
        control.addAction(prune)

        view = bar.addMenu("视图")
        back = QAction("返回服务列表", self)
        back.setShortcut(QKeySequence("Esc"))
        back.triggered.connect(self.show_list)
        refresh = QAction("立即刷新", self)
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self._refresh_current_page)
        view.addActions([back, refresh])

        help_menu = bar.addMenu("帮助")
        about = QAction("关于", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    # -------------------------------------------------------------------- 页面

    def show_detail(self, service_id: int) -> None:
        self.stack.setCurrentIndex(1)
        self.detail_page.open_service(service_id)

    def show_list(self) -> None:
        self.detail_page.close_page()
        self.stack.setCurrentIndex(0)
        self.list_page.refresh()

    def _refresh_current_page(self) -> None:
        if self.stack.currentIndex() == 0:
            self.list_page.refresh()
        else:
            self.detail_page.update_header()

    def _on_stats(self, service_id: int) -> None:
        if self.stack.currentIndex() == 1 and service_id in (0, self.detail_page.service_id):
            self.detail_page.update_header()

    def _on_session_change(self, service_id: int, session_id: int) -> None:
        if self.stack.currentIndex() == 1 and self.detail_page.service_id == service_id:
            self.detail_page.reload_sessions()
            self.detail_page.reload_charts()
        else:
            self.list_page.refresh()

    def _on_session_finish(self, service_id: int, session_id: int, code, reason: str) -> None:
        self.list_page.refresh()
        if self.stack.currentIndex() == 1 and self.detail_page.service_id == service_id:
            self.detail_page.reload_sessions()
            self.detail_page.reload_charts()

    def _on_restart_scheduled(self, service_id: int, when: float) -> None:
        service = self.db.get_service(service_id)
        name = service["name"] if service else f"#{service_id}"
        self.statusBar().showMessage(
            f"{name} 意外退出，守护将在 {time.strftime('%H:%M:%S', time.localtime(when))} 重新拉起", 8000
        )

    # -------------------------------------------------------------------- 动作

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings_store, self)
        if dialog.exec() == SettingsDialog.Accepted:
            self.statusBar().showMessage("设置已保存，采样间隔等参数立即生效", 5000)

    def _open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir())))

    def _prune(self) -> None:
        days = int(self.settings_store.values.retention_days)
        if days <= 0:
            QMessageBox.information(
                self, "清理历史数据", "当前保留策略是“永久保留”。请先在设置里填写保留天数。"
            )
            return
        removed = self.db.prune(days)
        self.list_page.refresh()
        self.statusBar().showMessage(f"已清理 {days} 天前的数据（采样点 {removed} 条）", 6000)

    def _about(self) -> None:
        from .. import __version__

        QMessageBox.about(
            self,
            "关于 ServiceMonitor",
            f"<b>ServiceMonitor {__version__}</b><br>"
            "跨平台服务状态监控（Windows / Linux）。<br>"
            f"监控数据与控制台输出保存在：{data_dir()}",
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        live = [
            int(service["id"])
            for service in self.db.list_services()
            if self.supervisor.state_of(int(service["id"]))["status"]
            in (STATUS_RUNNING, STATUS_STOPPING)
        ]
        stop_on_exit = bool(self.settings_store.values.stop_on_exit)
        if live and stop_on_exit:
            self.statusBar().showMessage(f"正在停止 {len(live)} 个服务…")
        self._timer.stop()
        # 断开信号，避免关闭过程中排队过来的输出回调碰到已关闭的数据库
        try:
            self.supervisor.sig.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self.supervisor.shutdown(stop_services=stop_on_exit)
        except Exception as exc:
            self.statusBar().showMessage(f"退出时清理异常：{exc}", 8000)
        if live and not stop_on_exit:
            confirm = QMessageBox.question(
                self,
                "服务仍在运行",
                "有服务正在运行，且设置中未勾选“退出时停止服务”。它们会继续运行，"
                "但下次启动本工具后无法再读取其控制台输出。确定退出？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if confirm != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()
