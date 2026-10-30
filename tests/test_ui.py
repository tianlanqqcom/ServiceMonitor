"""界面测试（offscreen Qt）：列表、对话框、详情页曲线与实时输出行为。"""

from __future__ import annotations

import time

import pytest

from conftest import SERVICE_BASE, script_command
from service_monitor.config import CLOSE_ACTION_QUIT, CLOSE_ACTION_TRAY, SettingsStore
from service_monitor.db import Database
from service_monitor.models import STATUS_RUNNING
from service_monitor.supervisor import Supervisor
from service_monitor.ui.main_window import MainWindow
from service_monitor.ui.service_dialog import ServiceDialog


@pytest.fixture
def env(tmp_path, qapp):
    db = Database(tmp_path / "ui.db")
    store = SettingsStore(tmp_path / "settings.json")
    supervisor = Supervisor(db, store)
    window = MainWindow(db, supervisor, store)
    window.resize(1240, 780)
    window.show()
    qapp.processEvents()
    yield window, db, supervisor, store
    window.close()
    supervisor.shutdown(stop_services=True)
    db.close()


def add_service(db, name="svc", **changes) -> int:
    return db.create_service({**SERVICE_BASE, "name": name, **changes})


def test_list_page_renders_rows_with_actions(env, qapp):
    window, db, supervisor, _ = env
    sid = add_service(db, "alpha", description="前端网关")
    window.list_page.refresh()
    qapp.processEvents()
    table = window.list_page.table
    assert table.rowCount() == 1
    assert table.item(0, 1).text() == "alpha"
    assert table.item(0, 2).text() == "前端网关"
    actions = table.cellWidget(0, 9)
    assert actions.start_button.isEnabled() and not actions.stop_button.isEnabled()
    assert "共 1 个服务" in window.list_page.summary.text()


def test_clicking_row_opens_detail_page(env, qapp):
    window, db, supervisor, _ = env
    sid = add_service(db, "beta")
    window.list_page.refresh()
    window.list_page._on_cell_clicked(0, 1)
    qapp.processEvents()
    assert window.stack.currentIndex() == 1
    assert window.detail_page.service_id == sid
    assert "beta" in window.detail_page.title_label.text()
    window.detail_page.back_requested.emit()
    qapp.processEvents()
    assert window.stack.currentIndex() == 0


def test_search_filters_rows(env, qapp):
    window, db, _, _ = env
    add_service(db, "keepme", description="重要服务")
    add_service(db, "dropme", description="临时")
    window.list_page.refresh()
    window.list_page.search.setText("keep")
    qapp.processEvents()
    hidden = [row for row in range(window.list_page.table.rowCount())
              if window.list_page.table.isRowHidden(row)]
    assert hidden == [1]
    window.list_page.search.setText("")
    assert window.list_page.table.isRowHidden(0) is False


def test_service_dialog_requires_name_and_command(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    dialog = ServiceDialog(title="添加服务")
    dialog.name_edit.setText("")
    dialog.command_edit.setPlainText("")
    dialog._on_accept()
    assert dialog.result() != ServiceDialog.Accepted or dialog.result() == 0
    dialog.name_edit.setText("ok")
    dialog.command_edit.setPlainText("python app.py")
    dialog.daemon_check.setChecked(True)
    dialog.delay_spin.setValue(12)
    dialog._on_accept()
    values = dialog.values()
    assert values["name"] == "ok"
    assert values["command"] == "python app.py"
    assert values["daemon"] == 1
    assert values["restart_delay"] == 12.0


def test_service_dialog_roundtrip_into_db(qapp, db):
    dialog = ServiceDialog(title="添加服务")
    dialog.name_edit.setText("round")
    dialog.command_edit.setPlainText("run.sh")
    dialog.desc_edit.setText("描述文字")
    dialog.dir_edit.setText("/tmp")
    values = dialog.values()
    sid = db.create_service(values)
    row = db.get_service(sid)
    assert (row["name"], row["command"], row["description"], row["working_dir"]) == (
        "round",
        "run.sh",
        "描述文字",
        "/tmp",
    )


def test_service_dialog_launcher_keeps_daemon_available(qapp, db):
    dialog = ServiceDialog(title="添加服务")
    dialog.name_edit.setText("stack")
    dialog.command_edit.setPlainText("tools/restart_stack.ps1")
    dialog.daemon_check.setChecked(True)
    dialog.launcher_check.setChecked(True)
    assert dialog.daemon_check.isEnabled(), "启动器模式下守护仍要可用（只对非 0 退出补拉）"
    assert dialog.delay_spin.isEnabled() and dialog.restarts_spin.isEnabled()
    values = dialog.values()
    assert (values["launcher"], values["daemon"]) == (1, 1)

    dialog.restarts_spin.setValue(-1)
    assert dialog.values()["max_restarts"] == -1
    assert "无限" in dialog.restarts_hint.text()
    dialog.restarts_spin.setValue(3)
    assert "3 次" in dialog.restarts_hint.text()

    sid = db.create_service(dialog.values())
    again = ServiceDialog(service=db.get_service(sid), title="编辑服务")
    assert again.values() == dialog.values()


def test_service_dialog_defaults_come_from_settings(qapp):
    from service_monitor.config import Settings

    tuned = ServiceDialog(
        title="添加服务",
        settings=Settings(default_restart_delay=12.0, restart_reset_after=900.0),
    )
    tuned.daemon_check.setChecked(True)
    assert tuned.delay_spin.value() == 12.0, "设置里的默认重启等待要真的落到新增服务上"
    assert "900 秒" in tuned.restarts_hint.text()

    plain = ServiceDialog(title="添加服务")
    assert plain.delay_spin.value() == 5.0
    assert "60 秒" in plain.restarts_hint.text()


def test_list_page_labels_a_launcher_script_exit(env, qapp):
    window, db, _, _ = env
    sid = add_service(db, "stack", launcher=1)
    session = db.create_session(sid, 4321, time.time() - 30, "manual")
    db.finish_session(session, time.time() - 29, 0, "exited")
    window.list_page.refresh()
    qapp.processEvents()
    item = window.list_page.table.item(0, 0)
    assert item.text() == "脚本已退出"
    assert "后台" in item.toolTip()


def test_list_page_shows_why_the_daemon_stopped(env, qapp):
    window, db, supervisor, _ = env
    sid = add_service(db, "flaky")
    with supervisor._lock:
        supervisor._paused[sid] = "已重试 2 次仍未稳定运行，已达重试上限（2），守护已停止（点“启动”可恢复）"
    window.list_page.refresh()
    qapp.processEvents()
    item = window.list_page.table.item(0, 0)
    assert item.text() == "守护等待中"
    assert "已达重试上限" in item.toolTip()


def test_charts_requery_when_granularity_changes(env, qapp):
    window, db, supervisor, _ = env
    sid = add_service(db, "gamma")
    session = db.create_session(sid, 100, time.time() - 600, "manual")
    base = time.time() - 600
    db.insert_samples(
        [(session, sid, base + offset, 100, offset % 50, 1024 * (offset + 1), None, None)
         for offset in range(0, 500, 10)]
    )
    calls = []
    original = db.aggregate

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    db.aggregate = spy
    window.show_detail(sid)
    qapp.processEvents()
    assert calls and calls[-1]["granularity"] == 60
    window.detail_page.granularity_combo.setCurrentIndex(3)
    window.detail_page.reload_charts()
    assert calls[-1]["granularity"] == 3600
    window.detail_page.range_combo.setCurrentIndex(4)
    window.detail_page.reload_charts()
    assert calls[-1]["granularity"] == 3600
    assert calls[-1]["since"] == pytest.approx(time.time() - 604800, abs=5)
    assert window.detail_page.cpu_chart.has_data
    assert window.detail_page.mem_chart.has_data
    assert "数据点" in window.detail_page.chart_info.text()


def test_session_table_marks_running_session(env, qapp, python_exe):
    window, db, supervisor, _ = env
    sid = add_service(db, "delta", command=script_command(python_exe, "sleeper.py"), working_dir="")
    supervisor.start()
    supervisor.request_start(sid)
    deadline = time.time() + 15
    while time.time() < deadline and supervisor.state_of(sid)["status"] != STATUS_RUNNING:
        time.sleep(0.1)
        qapp.processEvents()
    assert supervisor.state_of(sid)["status"] == STATUS_RUNNING

    window.show_detail(sid)
    qapp.processEvents()
    table = window.detail_page.session_table
    assert table.item(0, 3).text() == "服务运行中"
    assert "服务运行中" in window.detail_page.output_info.text()
    supervisor.shutdown(stop_services=True)
    qapp.processEvents()
    window.detail_page.reload_sessions()
    qapp.processEvents()
    assert "0" in window.detail_page.session_table.item(0, 3).text() or "手动停止" in (
        window.detail_page.session_table.item(0, 3).text()
    )


def test_live_output_appends_without_moving_the_view(env, qapp):
    """关键要求：新输出实时刷新，但用户不在末尾时不被拉走。"""
    window, db, supervisor, _ = env
    sid = add_service(db, "echo")
    session = db.create_session(sid, 1, time.time() - 30, "manual")
    ts = time.time() - 30
    db.append_output_many([(session, ts + i, "stdout", f"line-{i}") for i in range(1200)])
    window.show_detail(sid)
    qapp.processEvents()

    page = window.detail_page
    bar = page.output.verticalScrollBar()
    assert bar.maximum() > 0, "内容应足以产生滚动条"
    assert "line-1199" in page.output.toPlainText()

    bar.setValue(0)
    qapp.processEvents()
    assert bar.value() == 0
    db.append_output_many([(session, time.time(), "stderr", f"late-{i}") for i in range(40)])
    new_rows = db.output_after(session, page._output_last_id, 2000)
    page.append_output_rows(new_rows)
    qapp.processEvents()

    assert bar.value() == 0, "用户停在开头，追加新行不应改变视口"
    assert "late-39" in page.output.toPlainText()
    assert page.jump_latest.isVisible() and "40" in page.jump_latest.text()

    page._jump_to_bottom()
    qapp.processEvents()
    assert bar.value() == bar.maximum()
    assert not page.jump_latest.isVisible()


def test_following_bottom_stays_at_bottom(env, qapp):
    window, db, supervisor, _ = env
    sid = add_service(db, "tail")
    session = db.create_session(sid, 1, time.time() - 5, "manual")
    db.append_output_many([(session, time.time(), "stdout", f"v-{i}") for i in range(1500)])
    window.show_detail(sid)
    qapp.processEvents()
    page = window.detail_page
    bar = page.output.verticalScrollBar()
    assert bar.value() == bar.maximum()
    db.append_output_many([(session, time.time(), "stdout", "fresh-line") for _ in range(30)])
    page.append_output_rows(db.output_after(session, page._output_last_id, 2000))
    qapp.processEvents()
    assert bar.value() == bar.maximum()
    assert "fresh-line" in page.output.toPlainText()


def test_errors_only_filter_hides_stdout(env, qapp):
    window, db, supervisor, _ = env
    sid = add_service(db, "mix")
    session = db.create_session(sid, 1, time.time(), "manual")
    db.append_output(session, time.time(), "stdout", "normal talk")
    db.append_output(session, time.time(), "stderr", "problem talk")
    window.show_detail(sid)
    qapp.processEvents()
    page = window.detail_page
    assert "normal talk" in page.output.toPlainText()
    page.show_errors_only.setChecked(True)
    qapp.processEvents()
    text = page.output.toPlainText()
    assert "problem talk" in text and "normal talk" not in text


def test_closing_window_stops_running_service(env, qapp, python_exe):
    window, db, supervisor, store = env
    sid = add_service(db, "zeta", command=script_command(python_exe, "sleeper.py"), working_dir="")
    supervisor.start()
    supervisor.request_start(sid)
    deadline = time.time() + 15
    while time.time() < deadline and supervisor.state_of(sid)["status"] != STATUS_RUNNING:
        time.sleep(0.1)
        qapp.processEvents()
    assert supervisor.state_of(sid)["status"] == STATUS_RUNNING

    window.close()
    qapp.processEvents()
    assert supervisor.state_of(sid)["status"] is None
    session = db.last_sessions_by_service()[sid]
    assert session["ended_at"] is not None
    assert session["exit_reason"] == "stopped"


def test_empty_list_shows_hint(env, qapp):
    window, db, _, _ = env
    window.list_page.refresh()
    qapp.processEvents()
    assert window.list_page.table.rowCount() == 0
    assert "共 0 个服务" in window.list_page.summary.text()


def test_output_text_contrasts_with_background(env, qapp):
    """输出面板是深色背景，浅色文字才可读（曾因 #202327 文字与背景同色而看不见）。"""
    from PySide6.QtGui import QPalette

    from service_monitor.ui import detail_page as dp

    window, _, _, _ = env
    base = window.detail_page.output.palette().color(QPalette.Base)
    assert base.lightness() < 128, "输出面板背景应为深色"
    for name in ("COLOR_STDOUT", "COLOR_STDERR", "COLOR_INFO", "COLOR_TIMESTAMP"):
        color = getattr(dp, name)
        assert color.lightness() - base.lightness() > 60, f"{name} 在深色背景上不可读"


def test_switching_service_replaces_console_output(env, qapp):
    """两个服务的会话可能都落在第 0 行，选择信号不会触发，输出面板必须手动换内容。"""
    window, db, _, _ = env
    first = add_service(db, "first")
    second = add_service(db, "second")
    now = time.time()
    for sid, marker in ((first, "第一条服务的日志"), (second, "第二条服务的日志")):
        session = db.create_session(sid, 900 + sid, now - 60, "manual")
        db.finish_session(session, now - 30, 0, "exited")
        db.append_output(session, now - 40, "stdout", marker)

    window.show_detail(first)
    qapp.processEvents()
    assert "第一条服务的日志" in window.detail_page.output.toPlainText()

    window.show_detail(second)
    qapp.processEvents()
    text = window.detail_page.output.toPlainText()
    assert "第二条服务的日志" in text
    assert "第一条服务的日志" not in text


def test_single_point_chart_still_spans_a_time_axis(qapp):
    from service_monitor.ui.charts import TimeSeriesChart

    chart = TimeSeriesChart("CPU 利用率", "%")
    chart.resize(600, 180)
    now = time.time()
    chart.set_points([(now, 12.0)], [(now, 20.0)])
    assert chart._t1 > chart._t0, "单点时时间轴不应塌缩为一个时刻"
    rect = chart._plot_rect()
    assert chart._x_of(chart._t0, rect) == pytest.approx(rect.left())
    assert chart._x_of(chart._t1, rect) == pytest.approx(rect.right())

    chart.set_points([(now, 12.0)], [(now, 20.0)], domain=(now - 3600, now))
    assert chart._t1 - chart._t0 == pytest.approx(3600, abs=1)
    assert chart._x_of(now - 1800, rect) == pytest.approx(rect.center().x(), abs=2)


class _TrayProbe:
    """offscreen 平台没有系统托盘，用替身控制可用性，才能测关窗分支。"""

    def __init__(self, available: bool):
        self._available = available

    def isSystemTrayAvailable(self) -> bool:
        return self._available


@pytest.fixture
def tray_env(tmp_path, qapp, monkeypatch, request):
    import service_monitor.ui.main_window as mw

    built: list = []

    def build(*, available: bool = True, close_action: str = "tray"):
        monkeypatch.setattr(mw, "QSystemTrayIcon", _TrayProbe(available))
        db = Database(tmp_path / "tray.db")
        store = SettingsStore(tmp_path / "tray-settings.json")
        store.update(close_action=close_action)
        supervisor = Supervisor(db, store)
        window = MainWindow(db, supervisor, store)
        window.show()
        qapp.processEvents()
        built.append((window, supervisor, db))
        return window, db, supervisor, store

    yield build
    for window, supervisor, db in built:
        window._quitting = True
        window.close()
        supervisor.shutdown(stop_services=True)
        db.close()


def start_sleeper(supervisor, db, python_exe, qapp, name="tray-svc") -> int:
    sid = add_service(db, name, command=script_command(python_exe, "sleeper.py"), working_dir="")
    supervisor.start()
    supervisor.request_start(sid)
    deadline = time.time() + 15
    while time.time() < deadline and supervisor.state_of(sid)["status"] != STATUS_RUNNING:
        time.sleep(0.1)
        qapp.processEvents()
    assert supervisor.state_of(sid)["status"] == STATUS_RUNNING
    return sid


def test_closing_window_hides_to_tray_and_keeps_monitoring(tray_env, qapp, python_exe):
    window, db, supervisor, _ = tray_env()
    sid = start_sleeper(supervisor, db, python_exe, qapp)
    assert window.tray is not None

    window.close()
    qapp.processEvents()
    assert not window.isVisible(), "关窗应当只是隐藏"
    assert supervisor.state_of(sid)["status"] == STATUS_RUNNING, "缩到托盘后必须继续监控"
    assert db.last_sessions_by_service()[sid]["ended_at"] is None

    window.quit_application()
    qapp.processEvents()
    assert supervisor.state_of(sid)["status"] is None
    session = db.last_sessions_by_service()[sid]
    assert session["ended_at"] is not None
    assert session["exit_reason"] == "stopped"


def test_close_action_quit_makes_window_really_exit(tray_env, qapp, python_exe):
    window, db, supervisor, _ = tray_env(close_action=CLOSE_ACTION_QUIT)
    sid = start_sleeper(supervisor, db, python_exe, qapp, name="no-tray")

    window.close()
    qapp.processEvents()
    assert supervisor.state_of(sid)["status"] is None


def test_missing_system_tray_falls_back_to_quit(tray_env, qapp, python_exe):
    """没有托盘的桌面环境里关窗必须真的退出，否则应用再也找不到。"""
    window, db, supervisor, _ = tray_env(available=False)
    sid = start_sleeper(supervisor, db, python_exe, qapp, name="no-tray-desktop")
    assert window.tray is None

    window.close()
    qapp.processEvents()
    assert supervisor.state_of(sid)["status"] is None


def test_tray_menu_offers_open_and_quit(qapp):
    from service_monitor.ui.tray import ServiceTray

    tray = ServiceTray()
    opened, quit_requests = [], []
    tray.open_requested.connect(lambda: opened.append(1))
    tray.quit_requested.connect(lambda: quit_requests.append(1))
    by_text = {action.text(): action for action in tray.contextMenu().actions() if action.text()}
    assert list(by_text) == ["打开界面", "退出监控"]

    by_text["打开界面"].trigger()
    by_text["退出监控"].trigger()
    assert opened == [1] and quit_requests == [1]


def test_tray_icon_paints(qapp):
    from service_monitor.ui.tray import build_icon

    icon = build_icon(0)
    assert not icon.isNull()
    pixmap = icon.pixmap(64, 64)
    assert (pixmap.width(), pixmap.height()) == (64, 64)
    assert not build_icon(3).isNull()


def test_tray_tooltip_shows_running_count(qapp):
    from service_monitor.ui.tray import ServiceTray

    tray = ServiceTray()
    tray.set_running_count(2)
    assert "运行中 2 个" in tray.toolTip()
    tray.set_running_count(0)
    assert "运行中" not in tray.toolTip()
