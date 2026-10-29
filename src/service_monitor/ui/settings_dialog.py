"""设置对话框：采样间隔、GPU 采集、退出行为、数据保留。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ..config import data_dir
from ..metrics import gpu as gpu_mod


class SettingsDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        values = store.values
        self.setWindowTitle("监控设置")
        self.setModal(True)
        self.resize(430, 300)

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

        form = QFormLayout()
        form.addRow("采样间隔", self.sample_spin)
        form.addRow("GPU 采集", self.gpu_check)
        form.addRow("GPU 刷新间隔", self.gpu_interval_spin)
        form.addRow("退出时", self.stop_on_exit)
        form.addRow("数据保留", self.retention_spin)
        form.addRow("", retention_hint)
        form.addRow("曲线最大点数", self.points_spin)
        form.addRow("默认守护重启等待", self.restart_delay_spin)

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
        layout.addWidget(status)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def _save(self) -> None:
        self.store.update(
            sample_interval=float(self.sample_spin.value()),
            gpu_enabled=bool(self.gpu_check.isChecked()),
            gpu_interval=float(self.gpu_interval_spin.value()),
            stop_on_exit=bool(self.stop_on_exit.isChecked()),
            retention_days=int(self.retention_spin.value()),
            max_chart_points=int(self.points_spin.value()),
            default_restart_delay=float(self.restart_delay_spin.value()),
        )
        self.accept()
