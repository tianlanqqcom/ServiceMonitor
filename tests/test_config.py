"""设置持久化：读写同一个文件、坏值回退、不串到全局数据目录。"""

from __future__ import annotations

import json

from service_monitor.config import ENV_HOME, Settings, SettingsStore


def test_settings_roundtrip_through_file(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.update(sample_interval=7.5, retention_days=30, minimize_to_tray=False, gpu_enabled=False)

    reloaded = SettingsStore(path)
    assert reloaded.values.sample_interval == 7.5
    assert reloaded.values.retention_days == 30
    assert reloaded.values.minimize_to_tray is False
    assert reloaded.values.gpu_enabled is False


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
