"""设置对话框：采样、GPU、外观、关窗行为、开机自启、数据保留。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QRadioButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import autostart
from ..autostart import AutostartError
from ..config import (
    CLOSE_ACTION_QUIT,
    CLOSE_ACTION_TRAY,
    DEFAULT_FONT_POINT_SIZE,
    MAX_FONT_POINT,
    MIN_FONT_POINT,
    data_dir,
)
from ..metrics import gpu as gpu_mod

FOLLOW_SYSTEM_LABEL = "（跟随系统默认）"


def font_choices() -> list[tuple[str, str]]:
    """字体下拉项：（显示文字, 写入设置的字体族），首项表示不覆盖系统默认。"""
    families = [f for f in QFontDatabase.families(QFontDatabase.WritingSystem.Latin) if f]
    return [(FOLLOW_SYSTEM_LABEL, "")] + [(family, family) for family in families]


class SettingsDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        values = store.values
        self.setWindowTitle("监控设置")
        self.setModal(True)
        self.resize(470, 470)

        self.sample_spin = QDoubleSpinBox()
        self.sample_spin.setRange(0.5, 60)
        self.sample_spin.setSingleStep(0.5)
        self.sample_spin.setSuffix(" 秒")
        self.sample_spin.setValue(values.sample_interval)

        self.gpu_check = QCheckBox("采集 GPU 利用率与显存（需要 nvidia-smi）")
        self.gpu_check.setChecked(values.gpu_enabled)
        self.gpu_interval_spin = QDoubleSpinBox()
        self.gpu_interval_spin.setRange(1, 60)
        self.gpu_interval_spin.setSuffix(" 秒")
        self.gpu_interval_spin.setValue(values.gpu_interval)

        self.font_combo = QComboBox()
        base_size = self.font_combo.font().pointSizeF()
        for index, (label, family) in enumerate(font_choices()):
            self.font_combo.addItem(label, family)
            if family:
                preview = QFont(family)
                preview.setPointSizeF(base_size)
                self.font_combo.setItemData(index, preview, Qt.ItemDataRole.FontRole)
        self._select_font(values.font_family)
        self.font_combo.activated.connect(self._sync_font_preview)

        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(MIN_FONT_POINT, MAX_FONT_POINT)
        self.size_spin.setSingleStep(0.5)
        self.size_spin.setDecimals(1)
        self.size_spin.setSuffix(" 磅")
        self.size_spin.setValue(float(values.font_point_size or DEFAULT_FONT_POINT_SIZE))

        self.close_tray = QRadioButton("缩到系统托盘（监控继续，右键图标可打开界面或退出）")
        self.close_quit = QRadioButton("完全退出（按设置决定是否停止服务）")
        self.close_tray.setChecked(values.close_action != CLOSE_ACTION_QUIT)
        self.close_quit.setChecked(values.close_action == CLOSE_ACTION_QUIT)
        for radio in (self.close_tray, self.close_quit):
            radio.toggled.connect(self._update_close_hint)
        close_box = QGroupBox("点击关闭按钮时")
        close_layout = QVBoxLayout(close_box)
        close_layout.addWidget(self.close_tray)
        close_layout.addWidget(self.close_quit)
        self.close_hint = QLabel("")
        self.close_hint.setObjectName("hint")
        self.close_hint.setWordWrap(True)
        close_layout.addWidget(self.close_hint)
        self._update_close_hint()

        self.autostart_check = QCheckBox("开机后自动启动监控（仅当前用户，随时可取消）")
        self.autostart_hint = QLabel("")
        self.autostart_hint.setObjectName("hint")
        self.autostart_hint.setWordWrap(True)
        if not autostart.supported():
            self.autostart_check.setEnabled(False)
            self.autostart_hint.setText("当前平台不提供开机自启接口，请在系统设置中配置登录项。")
        else:
            self.autostart_check.setChecked(autostart.is_enabled())
            self.autostart_hint.setText(f"自启配置位置：{autostart.config_path()}")

        self.stop_on_exit = QCheckBox("退出监控工具时停止由它启动的服务")
        self.stop_on_exit.setChecked(values.stop_on_exit)

        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(0, 3650)
        self.retention_spin.setSuffix(" 天")
        self.retention_spin.setValue(values.retention_days)
        retention_hint = QLabel("0 表示永久保留（默认）")
        retention_hint.setObjectName("hint")

        self.points_spin = QSpinBox()
        self.points_spin.setRange(60, 2000)
        self.points_spin.setValue(values.max_chart_points)

        self.restart_delay_spin = QDoubleSpinBox()
        self.restart_delay_spin.setRange(1, 3600)
        self.restart_delay_spin.setSuffix(" 秒")
        self.restart_delay_spin.setValue(values.default_restart_delay)

        self.reset_after_spin = QDoubleSpinBox()
        self.reset_after_spin.setRange(10, 86400)
        self.reset_after_spin.setDecimals(0)
        self.reset_after_spin.setSuffix(" 秒")
        self.reset_after_spin.setValue(values.restart_reset_after)

        form = QFormLayout()
        form.addRow("采样间隔", self.sample_spin)
        form.addRow("GPU 采集", self.gpu_check)
        form.addRow("GPU 刷新间隔", self.gpu_interval_spin)
        form.addRow("界面字体", self._with_hint(self.font_combo, "改完点“保存”立即应用到所有界面"))
        form.addRow("界面字号", self._with_hint(self.size_spin, f"有效范围 {MIN_FONT_POINT:g}–{MAX_FONT_POINT:g} 磅"))
        form.addRow("数据保留", self.retention_spin)
        form.addRow("", retention_hint)
        form.addRow("曲线最大点数", self.points_spin)
        form.addRow("默认守护重启等待", self.restart_delay_spin)
        form.addRow(
            "重试计数清零",
            self._with_hint(self.reset_after_spin, "服务每存活超过这个时长，守护的重试次数归零"),
        )

        status = QLabel(
            "检测到的 GPU 工具：" + ("nvidia-smi 可用" if gpu_mod.nvidia_available() else "未找到 nvidia-smi")
            + f"　数据目录：{data_dir()}"
        )
        status.setWordWrap(True)
        status.setObjectName("hint")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(close_box)
        autostart_panel = QWidget()
        autostart_box = QVBoxLayout(autostart_panel)
        autostart_box.setContentsMargins(0, 0, 0, 0)
        autostart_box.addWidget(self.autostart_check)
        autostart_box.addWidget(self.autostart_hint)
        layout.addWidget(autostart_panel)
        layout.addWidget(self.stop_on_exit)
        layout.addWidget(status)
        layout.addStretch(1)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ 交互

    def _with_hint(self, widget: QWidget, text: str) -> QWidget:
        """QFormLayout 一行放两个控件：控件 + 下方灰色说明。"""
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        box.addWidget(widget)
        hint = QLabel(text)
        hint.setObjectName("hint")
        box.addWidget(hint)
        return panel

    def _select_font(self, family) -> None:
        wanted = (family or "").strip()
        if not wanted:
            self.font_combo.setCurrentIndex(0)
            return
        index = self.font_combo.findData(wanted)
        if index < 0:
            # 设置里的字体族不在系统列表内（例如只含中文的族名），补一项，避免保存时被抹掉
            self.font_combo.insertItem(1, wanted, wanted)
            index = 1
        self.font_combo.setCurrentIndex(index)

    def _update_close_hint(self, *_ignored) -> None:
        if self.close_quit.isChecked():
            self.close_hint.setText("关闭窗口即退出，任务栏不会留下图标。")
        elif QSystemTrayIcon.isSystemTrayAvailable():
            self.close_hint.setText("没有托盘图标时（部分 Linux 桌面）会自动退回为直接退出，避免找不回窗口。")
        else:
            self.close_hint.setText("当前桌面检测不到系统托盘，关窗会直接退出。")

    def _sync_font_preview(self, index: int) -> None:
        """下拉框自身字体不能跟着预览变，否则整行说明文字都会换成选中项的字体。"""
        family = self.font_combo.itemData(index) or ""
        if family:
            font = self.font_combo.font()
            font.setFamily(family)
            self.font_combo.setFont(font)

    # ------------------------------------------------------------------ 保存

    def _apply_autostart(self) -> None:
        if not self.autostart_check.isEnabled():
            return
        wanted = self.autostart_check.isChecked()
        if wanted == autostart.is_enabled():
            return
        try:
            autostart.set_enabled(wanted)
        except AutostartError as exc:
            QMessageBox.warning(self, "开机自启", f"{exc}\n其他设置仍会保存。")
            self.autostart_check.setChecked(autostart.is_enabled())
            return
        self.autostart_hint.setText(
            f"已{'开启' if wanted else '关闭'}，配置位置：{autostart.config_path()}"
        )

    def _save(self) -> None:
        self._apply_autostart()
        self.store.update(
            sample_interval=float(self.sample_spin.value()),
            gpu_enabled=bool(self.gpu_check.isChecked()),
            gpu_interval=float(self.gpu_interval_spin.value()),
            font_family=str(self.font_combo.currentData() or ""),
            font_point_size=float(self.size_spin.value()),
            close_action=CLOSE_ACTION_QUIT if self.close_quit.isChecked() else CLOSE_ACTION_TRAY,
            stop_on_exit=bool(self.stop_on_exit.isChecked()),
            retention_days=int(self.retention_spin.value()),
            max_chart_points=int(self.points_spin.value()),
            default_restart_delay=float(self.restart_delay_spin.value()),
            restart_reset_after=float(self.reset_after_spin.value()),
        )
        self.accept()
