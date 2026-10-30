"""状态判定与格式化的边界。"""

import pytest

from service_monitor import models


@pytest.mark.parametrize(
    "reason,code,expected",
    [
        (models.REASON_EXITED, 0, models.STATUS_EXITED),
        (models.REASON_EXITED, 3, models.STATUS_CRASHED),
        (models.REASON_CRASHED, 1, models.STATUS_CRASHED),
        (models.REASON_CRASHED, -9, models.STATUS_CRASHED),
        (models.REASON_STOPPED, 0, models.STATUS_EXITED),
        (models.REASON_STOPPED, 1, models.STATUS_EXITED),
        (models.REASON_EXITED, None, models.STATUS_EXITED),
    ],
)
def test_status_from_exit(reason, code, expected):
    assert models.status_from_exit(reason, code) == expected


def test_status_from_session_variants():
    class Row(dict):
        def __getitem__(self, key):
            return self.get(key)

    assert models.status_from_session(None) == models.STATUS_STOPPED
    assert models.status_from_session(Row(ended_at=None, exit_reason="", exit_code=None)) == (
        models.STATUS_RUNNING
    )
    assert models.status_from_session(Row(ended_at=1, exit_reason=models.REASON_ORPHAN, exit_code=None)) == (
        models.STATUS_INTERRUPTED
    )
    assert models.status_from_session(Row(ended_at=1, exit_reason=models.REASON_FAILED, exit_code=None)) == (
        models.STATUS_LAUNCH_FAILED
    )
    assert models.status_from_session(Row(ended_at=1, exit_reason=models.REASON_CRASHED, exit_code=3)) == (
        models.STATUS_CRASHED
    )


def test_launcher_script_exit_reads_as_a_normal_ending():
    """启动器脚本退出码 0 不该显示成“已退出”：服务是它在后台拉起来的，还活得好好的。"""

    class Row(dict):
        def __getitem__(self, key):
            return self.get(key)

    clean = Row(ended_at=1, exit_reason=models.REASON_EXITED, exit_code=0)
    assert models.status_from_session(clean) == models.STATUS_EXITED
    assert models.status_from_session(clean, launcher=True) == models.STATUS_LAUNCHER_EXITED
    # 脚本自己失败了仍然算异常，不能被启动器模式洗白
    failed = Row(ended_at=1, exit_reason=models.REASON_CRASHED, exit_code=1)
    assert models.status_from_session(failed, launcher=True) == models.STATUS_CRASHED
    stopped = Row(ended_at=1, exit_reason=models.REASON_STOPPED, exit_code=0)
    assert models.status_from_session(stopped, launcher=True) == models.STATUS_EXITED


def test_labels_and_colors_are_defined_for_every_status():
    for status in models.STATUS_LABELS:
        assert models.status_label(status)
    assert models.status_label("nope") == "nope"
    assert models.exit_reason_label(models.REASON_CRASHED, 3) == "异常退出"
    assert models.exit_reason_label("", 0) == "运行中"
    # 退出码非 0 时，即使标记为正常退出也要显示成异常
    assert models.exit_reason_label(models.REASON_EXITED, 2) == models.STATUS_LABELS[models.STATUS_CRASHED]


@pytest.mark.parametrize(
    "code,text",
    [(None, "未知"), (0, "0"), (3, "3"), (-9, "-9（信号 9）")],
)
def test_code_text(code, text):
    assert models.code_text(code) == text


@pytest.mark.parametrize(
    "seconds,text",
    [(None, "—"), (0, "0s"), (59, "59s"), (60, "1m00s"), (3600, "1h00m"), (90000, "1d01h")],
)
def test_duration_text(seconds, text):
    assert models.duration_text(seconds) == text
