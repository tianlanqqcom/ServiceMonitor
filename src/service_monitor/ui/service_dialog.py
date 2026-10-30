"""添加 / 编辑服务的对话框。"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from ..models import DEFAULT_MAX_RESTARTS
from ..supervisor import ATTEMPT_RESET_AFTER

IS_WINDOWS = sys.platform == "win32"


class ServiceDialog(QDialog):
    def __init__(self, parent=None, service=None, title: str = "添加服务"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(580, 470)
        self.setMinimumWidth(460)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：推理服务")
        self.command_edit = QTextEdit()
        self.command_edit.setPlaceholderText("python train.py --config a.yaml")
        self.command_edit.setMaximumHeight(72)
        self.command_edit.setAcceptRichText(False)
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("可选，用于备注这个服务是做什么的")

        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("可选，留空使用监控工具当前的工作目录")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_dir)
        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.addWidget(self.dir_edit)
        dir_row.addWidget(browse)

        self.daemon_check = QCheckBox("守护模式：进程意外退出后自动重新拉起")
        self.daemon_check.setToolTip("勾选启动器脚本后守护不会启用：脚本退出是正常结局，守护只会反复重建整套服务")
        self.launcher_check = QCheckBox(
            "启动器脚本：脚本把服务拉到后台后就自己退出（不守护，后台进程也不计入统计）"
        )
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(1, 3600)
        self.delay_spin.setValue(5)
        self.delay_spin.setSuffix(" 秒")
        self.restarts_spin = QSpinBox()
        # -1 = 无限重试，0 = 不重试，正数 = 连续失败时最多拉起这么多次
        self.restarts_spin.setRange(-1, 999)
        self.restarts_spin.setValue(DEFAULT_MAX_RESTARTS)
        self.restarts_spin.setSuffix(" 次")
        self.restarts_spin.setSpecialValueText("无限重试")
        self.restarts_hint = QLabel("")
        self.restarts_hint.setWordWrap(True)
        self.restarts_hint.setObjectName("hint")
        self.daemon_check.toggled.connect(self._sync_enabled)
        self.launcher_check.toggled.connect(self._sync_enabled)
        self.restarts_spin.valueChanged.connect(self._sync_restarts_hint)
        self._sync_enabled()
        self._sync_restarts_hint()

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("服务名 *", self.name_edit)
        form.addRow("启动命令 *", self.command_edit)
        form.addRow("描述", self.desc_edit)
        form.addRow("工作目录", dir_row)
        form.addRow("守护", self.daemon_check)
        form.addRow("启动器脚本", self.launcher_check)
        form.addRow("重启等待", self.delay_spin)
        form.addRow("重试上限", self.restarts_spin)
        form.addRow("", self.restarts_hint)

        hint = QLabel(
            "命令通过系统 shell 执行（Windows 为 cmd.exe，Linux 为 /bin/sh），"
            "支持参数、管道与重定向；监控会统计该命令派生的整棵进程树。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self.setLayout(layout)

        if service is not None:
            self.name_edit.setText(service["name"])
            self.command_edit.setPlainText(service["command"])
            self.desc_edit.setText(service["description"] or "")
            self.dir_edit.setText(service["working_dir"] or "")
            self.daemon_check.setChecked(bool(service["daemon"]))
            self.delay_spin.setValue(float(service["restart_delay"] or 5))
            self.restarts_spin.setValue(int(service["max_restarts"]))
            self.launcher_check.setChecked(bool(service["launcher"]))
            self._sync_restarts_hint()

    def _sync_enabled(self) -> None:
        launcher = self.launcher_check.isChecked()
        self.daemon_check.setEnabled(not launcher)
        retrying = self.daemon_check.isChecked() and not launcher
        self.delay_spin.setEnabled(retrying)
        self.restarts_spin.setEnabled(retrying)

    def _sync_restarts_hint(self) -> None:
        limit = self.restarts_spin.value()
        reset = int(ATTEMPT_RESET_AFTER)
        if limit < 0:
            text = "无限重试：进程每次退出后都按上面的等待时间重新拉起。"
        elif limit == 0:
            text = "不重试：进程退出后不再拉起，等同于关闭守护模式。"
        else:
            text = (
                f"连续失败时最多重试 {limit} 次，用完就停止守护（状态列会提示，点“启动”可恢复）。"
                f"服务每存活超过 {reset} 秒会把已用次数清零。"
            )
        self.restarts_hint.setText(text)

    def _browse_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择工作目录", self.dir_edit.text() or "")
        if chosen:
            self.dir_edit.setText(chosen)

    def _on_accept(self) -> None:
        missing = []
        if not self.name_edit.text().strip():
            missing.append("服务名")
        if not self.command_edit.toPlainText().strip():
            missing.append("启动命令")
        if missing:
            QMessageBox.warning(self, "字段缺失", "、".join(missing) + " 为必填项。")
            return
        self.accept()

    def values(self) -> dict:
        command = self.command_edit.toPlainText().strip()
        launcher = self.launcher_check.isChecked()
        daemon = self.daemon_check.isChecked() and not launcher
        return {
            "name": self.name_edit.text().strip(),
            "command": command,
            "description": self.desc_edit.text().strip(),
            "working_dir": self.dir_edit.text().strip(),
            "daemon": 1 if daemon else 0,
            "launcher": 1 if launcher else 0,
            "restart_delay": float(self.delay_spin.value()),
            "max_restarts": int(self.restarts_spin.value()),
        }
