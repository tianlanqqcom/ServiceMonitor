"""设置对话框与外观：关窗行为、开机自启、字体字号的应用。"""

from __future__ import annotations

import pytest

from service_monitor import autostart
from service_monitor.config import (
    CLOSE_ACTION_QUIT,
    CLOSE_ACTION_TRAY,
    MAX_FONT_POINT,
    MIN_FONT_POINT,
    Settings,
    SettingsStore,
)
from service_monitor.ui import settings_dialog as sd
from service_monitor.ui.main_window import MainWindow, apply_ui_font, build_style
from service_monitor.ui.settings_dialog import SettingsDialog


@pytest.fixture
def dialog(qapp, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    handle = SettingsDialog(store, None)
    yield handle, store
    handle.deleteLater()


def test_close_action_radios_are_exclusive_and_map_to_settings(dialog):
    box, store = dialog
    assert store.values.close_action == CLOSE_ACTION_TRAY
    assert box.close_tray.isChecked() and not box.close_quit.isChecked()

    box.close_quit.setChecked(True)
    assert not box.close_tray.isChecked(), "两个选项必须互斥"
    box._save()
    assert store.values.close_action == CLOSE_ACTION_QUIT

    box.close_tray.setChecked(True)
    box._save()
    assert store.values.close_action == CLOSE_ACTION_TRAY


def test_font_controls_round_trip_through_settings(qapp, tmp_path, monkeypatch):
    """offscreen 平台没有真实字体库，字体列表由测试注入。"""
    monkeypatch.setattr(
        sd,
        "font_choices",
        lambda: [(sd.FOLLOW_SYSTEM_LABEL, ""), ("Fake Sans", "Fake Sans"), ("Fake Serif", "Fake Serif")],
    )
    store = SettingsStore(tmp_path / "settings.json")
    box = SettingsDialog(store, None)
    assert box.font_combo.itemData(0) == "", "第一项必须是“跟随系统默认”"
    assert box.font_combo.currentIndex() == 0

    box.font_combo.setCurrentIndex(1)
    box.size_spin.setValue(13.5)
    box._save()
    assert store.values.font_family == "Fake Sans"
    assert store.values.font_point_size == 13.5

    reopened = SettingsDialog(store, None)
    try:
        assert reopened.font_combo.currentData() == "Fake Sans"
        assert reopened.size_spin.value() == 13.5

        # 设置里存的字体族不在列表内时不能丢，否则一次保存就悄悄改回默认
        store.update(font_family="Ghost Font")
        third = SettingsDialog(store, None)
        assert third.font_combo.currentData() == "Ghost Font"
        third.deleteLater()
    finally:
        reopened.deleteLater()
        box.deleteLater()


def test_font_choices_lead_with_follow_system_default():
    choices = sd.font_choices()
    assert choices[0] == (sd.FOLLOW_SYSTEM_LABEL, "")
    families = [data for _, data in choices[1:]]
    assert all(families), "字体族不能是空串，否则下拉框会出现空白项"
    assert len(families) == len(set(families)), "同一字体不该重复出现"


def test_size_spin_range_matches_the_clamp_bounds(dialog):
    box, _ = dialog
    assert box.size_spin.minimum() == pytest.approx(MIN_FONT_POINT)
    assert box.size_spin.maximum() == pytest.approx(MAX_FONT_POINT)


def test_build_style_follows_the_configured_size():
    assert "font-size: 12pt; }" in build_style(12)
    assert "QLabel#hint { color: #7a828c; font-size: 11pt; }" in build_style(12)
    assert "font-size: 24pt" in build_style(99), "超范围字号必须被夹住"
    assert "font-size: 7pt" in build_style(1)


def test_apply_ui_font_sets_family_and_restores_default(qapp):
    before = qapp.font()
    try:
        apply_ui_font(qapp, Settings(font_family="", font_point_size=10))
        baseline = qapp.font().family()
        apply_ui_font(qapp, Settings(font_family="Arial", font_point_size=14))
        assert qapp.font().family() == "Arial"
        assert qapp.font().pointSizeF() == 14
        apply_ui_font(qapp, Settings(font_family="", font_point_size=9))
        assert qapp.font().family() == baseline, "选回“跟随系统默认”必须真的回到初始字体"
        assert qapp.font().pointSizeF() == 9
    finally:
        qapp.setFont(before)


def test_main_window_reapplies_style_after_settings_change(qapp, tmp_path):
    from conftest import SERVICE_BASE

    from service_monitor.db import Database
    from service_monitor.supervisor import Supervisor

    db = Database(tmp_path / "font.db")
    store = SettingsStore(tmp_path / "settings.json")
    supervisor = Supervisor(db, store)
    db.create_service({**SERVICE_BASE, "name": "font-svc"})
    window = MainWindow(db, supervisor, store)
    try:
        assert "font-size: 10pt" in window.styleSheet()
        store.update(font_point_size=16)
        window.apply_appearance()
        assert "QWidget { font-size: 16pt; }" in window.styleSheet()
    finally:
        window._quitting = True
        window.close()
        supervisor.shutdown(stop_services=True)
        db.close()


class _FakeAutostart:
    """自启写的是注册表/用户配置目录，测试里绝不能碰真实环境。"""

    def __init__(self, enabled: bool = False, fail_with: Exception | None = None):
        self.enabled = enabled
        self.calls: list[bool] = []
        self.fail_with = fail_with

    def supported(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool) -> None:
        self.calls.append(enabled)
        if self.fail_with:
            raise self.fail_with
        self.enabled = enabled

    def config_path(self):
        return "FakeAutostartPath"


def test_autostart_writes_only_when_the_choice_changes(qapp, tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    fake = _FakeAutostart()
    monkeypatch.setattr(sd, "autostart", fake)

    box = SettingsDialog(store, None)
    assert not box.autostart_check.isChecked()
    box._save()
    assert fake.calls == [], "没有改动就不该碰注册表"

    box.autostart_check.setChecked(True)
    box._save()
    assert fake.calls == [True]
    assert fake.enabled is True


def test_autostart_failure_keeps_dialog_state(qapp, tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    fake = _FakeAutostart(fail_with=autostart.AutostartError("注册表被拒绝"))
    monkeypatch.setattr(sd, "autostart", fake)

    box = SettingsDialog(store, None)
    box.autostart_check.setChecked(True)
    box.sample_spin.setValue(4.5)
    warned: list[str] = []
    monkeypatch.setattr(
        sd.QMessageBox, "warning", staticmethod(lambda *args, **kwargs: warned.append(args[2]))
    )
    box._save()

    assert warned and "注册表被拒绝" in warned[0]
    assert not fake.enabled, "写失败时不能假装已开启"
    assert not box.autostart_check.isChecked(), "复选框要退回真实状态"
    assert store.values.sample_interval == 4.5, "自启失败不应吞掉其他设置"
