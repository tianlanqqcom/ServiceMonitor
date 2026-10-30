"""设置持久化：读写同一个文件、坏值回退、不串到全局数据目录。"""

from __future__ import annotations

import json

from service_monitor.config import (
    CLOSE_ACTION_QUIT,
    CLOSE_ACTION_TRAY,
    DEFAULT_FONT_POINT_SIZE,
    ENV_HOME,
    MAX_FONT_POINT,
    MIN_FONT_POINT,
    Settings,
    SettingsStore,
    clamp_point_size,
)
from service_monitor.models import DEFAULT_RETRY_RESET_AFTER


def test_settings_roundtrip_through_file(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.update(
        sample_interval=7.5,
        retention_days=30,
        close_action=CLOSE_ACTION_QUIT,
        gpu_enabled=False,
        font_family="Microsoft YaHei",
        font_point_size=12.5,
    )

    reloaded = SettingsStore(path)
    assert reloaded.values.sample_interval == 7.5
    assert reloaded.values.retention_days == 30
    assert reloaded.values.close_action == CLOSE_ACTION_QUIT
    assert reloaded.values.gpu_enabled is False
    assert reloaded.values.font_family == "Microsoft YaHei"
    assert reloaded.values.font_point_size == 12.5


def test_settings_honour_their_own_file_not_the_global_dir(tmp_path, monkeypatch):
    """SettingsStore 传入路径时必须读这个文件，不能退回 SERVICEMONITOR_HOME。"""
    other = tmp_path / "global"
    other.mkdir()
    monkeypatch.setenv(ENV_HOME, str(other))
    (other / "settings.json").write_text(
        json.dumps({"sample_interval": 1.5}), encoding="utf-8"
    )

    path = tmp_path / "project.json"
    SettingsStore(path).update(sample_interval=9.0)
    assert SettingsStore(path).values.sample_interval == 9.0
    assert SettingsStore(other / "settings.json").values.sample_interval == 1.5


def test_bad_or_missing_values_fall_back_to_defaults(tmp_path):
    defaults = Settings()
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "sample_interval": "abc",
                "retention_days": None,
                "max_chart_points": 480,
                "unknown_key": 1,
            }
        ),
        encoding="utf-8",
    )
    values = Settings.load(path)
    assert values.sample_interval == defaults.sample_interval
    assert values.retention_days == defaults.retention_days
    assert values.max_chart_points == 480


def test_missing_file_uses_defaults(tmp_path):
    values = Settings.load(tmp_path / "does-not-exist.json")
    assert values == Settings()


def test_font_size_is_clamped_to_a_readable_range():
    assert clamp_point_size(2) == MIN_FONT_POINT
    assert clamp_point_size(99) == MAX_FONT_POINT
    assert clamp_point_size(11.5) == 11.5
    assert clamp_point_size(None) == DEFAULT_FONT_POINT_SIZE


def test_legacy_minimize_to_tray_file_still_loads(tmp_path):
    """老版本写过的设置不能把新版本读崩：未知键忽略，缺的新键用默认值。"""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"minimize_to_tray": False, "sample_interval": 3.0}), encoding="utf-8")
    values = Settings.load(path)
    assert values.sample_interval == 3.0
    assert values.close_action == CLOSE_ACTION_TRAY
    assert values.font_family == ""
    assert values.restart_reset_after == DEFAULT_RETRY_RESET_AFTER
