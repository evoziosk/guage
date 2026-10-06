import json
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6 import QtCore, QtGui, QtWidgets

from src.aggregator import UsageAggregator
from src.cli import render_terminal_table
from src.config import WidgetSettings
from src.models import ActiveSession, AggregatedUsage, ProviderUsage, UsageWindow
from src.sessions import SessionTracker, is_file_locked, is_pid_running

# Ensure a QApplication exists
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])


def test_active_session_model():
    s = ActiveSession(
        provider_id="claude",
        session_id="test-123",
        title="Super Admin Redesign",
        status="shell",
        detail="sample-app (shell)",
        cwd="/home/user/sample-app",
        pid=1234,
    )
    assert s.display_provider == "Claude"
    assert s.project_name == "sample-app"
    assert "Super Admin Redesign" in s.notation
    assert "sample-app" in s.notation


def test_aggregated_usage_with_active_sessions():
    s1 = ActiveSession(provider_id="claude", session_id="1", title="Task 1")
    s2 = ActiveSession(provider_id="antigravity", session_id="2", title="Task 2")
    agg = AggregatedUsage(active_sessions=[s1, s2])

    assert len(agg.get_active_sessions()) == 2
    assert len(agg.get_active_sessions("claude")) == 1
    assert agg.get_active_sessions("claude")[0].title == "Task 1"
    assert len(agg.get_active_sessions("antigravity")) == 1
    assert len(agg.get_active_sessions("codex")) == 0

    d = agg.to_dict()
    assert "active_sessions" in d
    assert len(d["active_sessions"]) == 2
    assert d["active_sessions"][0]["title"] == "Task 1"


def test_session_tracker_scan_claude(tmp_path, monkeypatch):
    claude_dir = tmp_path / ".claude"
    sessions_dir = claude_dir / "sessions"
    sessions_dir.mkdir(parents=True)

    session_file = sessions_dir / "9999.json"
    session_data = {
        "pid": 9999,
        "sessionId": "session-abc",
        "name": "Refactoring Core",
        "cwd": "/workspace/myproject",
        "status": "active",
        "startedAt": 1700000000000,
    }
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    # Mock is_pid_running to True for 9999
    monkeypatch.setattr("src.sessions.is_pid_running", lambda pid: pid == 9999)
    monkeypatch.setattr("src.sessions.SessionTracker._claude_dirs", staticmethod(lambda: [claude_dir]))

    tracker = SessionTracker()
    results = tracker.scan_claude()
    assert len(results) == 1
    assert results[0].provider_id == "claude"
    assert results[0].title == "Refactoring Core"
    assert results[0].project_name == "myproject"
    assert results[0].pid == 9999


def test_session_tracker_scan_codex(tmp_path, monkeypatch):
    codex_dir = tmp_path / ".codex"
    locks_dir = codex_dir / "thread-writer-locks"
    locks_dir.mkdir(parents=True)

    lock_file = locks_dir / "thread-xyz.lock"
    lock_file.touch()

    idx_file = codex_dir / "session_index.jsonl"
    idx_file.write_text(
        json.dumps({"id": "thread-xyz", "thread_name": "Codex Companion Task: Repository /home/user/webapp, branch main"}) + "\n",
        encoding="utf-8",
    )

    # Mock lock check to return True for thread-xyz
    monkeypatch.setattr("src.sessions.is_file_locked", lambda path: "thread-xyz" in path.name)
    monkeypatch.setattr("src.sessions.SessionTracker._codex_dirs", staticmethod(lambda: [codex_dir]))

    tracker = SessionTracker()
    results = tracker.scan_codex()
    assert len(results) == 1
    assert results[0].provider_id == "codex"
    assert "webapp" in results[0].title
    assert results[0].status == "running"


def test_session_tracker_scan_antigravity(tmp_path, monkeypatch):
    ag_dir = tmp_path / ".gemini" / "antigravity-cli"
    presence_dir = ag_dir / "presence"
    presence_dir.mkdir(parents=True)

    lock_file = presence_dir / "conv-456.lock"
    lock_file.touch()

    db_file = ag_dir / "conversation_summaries.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, status TEXT, last_modified_time TEXT, killed INT)"
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES ('conv-456', 'Database Migration Fix', 'CASCADE_RUN_STATUS_RUNNING', '2026-10-06T12:00:00', 0)"
    )
    conn.commit()
    conn.close()

    # Create dummy transcript
    logs_dir = ag_dir / "brain" / "conv-456" / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True)
    t_file = logs_dir / "transcript.jsonl"
    t_entry = {
        "step_index": 1,
        "type": "PLANNER_RESPONSE",
        "tool_calls": [{"name": "run_command", "args": {"toolAction": "Running pytest test suite"}}],
    }
    t_file.write_text(json.dumps(t_entry) + "\n", encoding="utf-8")

    # Create settings.json
    settings_file = ag_dir / "settings.json"
    settings_file.write_text(json.dumps({"model": "Gemini 3.8 Flash (Medium)"}), encoding="utf-8")

    monkeypatch.setattr("src.sessions.is_file_locked", lambda path: "conv-456" in path.name)
    monkeypatch.setattr("src.sessions.SessionTracker._antigravity_dirs", staticmethod(lambda: [ag_dir]))

    tracker = SessionTracker()
    results = tracker.scan_antigravity()
    assert len(results) == 1
    assert results[0].provider_id == "antigravity"
    assert results[0].title == "Database Migration Fix"
    assert results[0].detail == "Running pytest test suite"
    assert results[0].model_id == "Gemini 3.8 Flash (Medium)"
    assert results[0].short_model == "Gemini 3.8 Flash"


def test_widget_rendering_with_active_sessions():
    from src.ui.widget import AIUsageWidget

    s_claude = ActiveSession(
        provider_id="claude",
        session_id="c1",
        title="Redesign Auth Panel",
        detail="sample-app (shell)",
        status="running",
        model_id="claude-opus-5-5",
    )
    s_codex = ActiveSession(
        provider_id="codex",
        session_id="cx1",
        title="Fix API endpoints",
        detail="Running pytest...",
        status="running",
        model_id="gpt-6.1-sol",
    )

    pu_claude = ProviderUsage(
        provider_id="claude",
        display_name="Claude Code",
        primary_window=UsageWindow(name="5h", used_pct=40.0, resets_at=1791168000),
    )
    pu_codex = ProviderUsage(
        provider_id="codex",
        display_name="OpenAI Codex",
        primary_window=UsageWindow(name="5h", used_pct=60.0, resets_at=1791168000),
    )

    usage = AggregatedUsage(
        providers={"claude": pu_claude, "codex": pu_codex},
        active_sessions=[s_claude, s_codex],
    )

    aggregator = MagicMock(spec=UsageAggregator)
    aggregator.get_latest.return_value = usage
    aggregator.get_active_sessions.return_value = [s_claude, s_codex]

    settings = WidgetSettings(show_active_sessions=True)
    widget = AIUsageWidget(aggregator, settings)

    # Test rendering in bars mode with active sessions
    widget.set_view_mode("bars")
    pix_bars = QtGui.QPixmap(widget.size())
    widget.render(pix_bars)
    assert not pix_bars.isNull()

    # Test rendering in rings mode with active sessions
    widget.set_view_mode("rings")
    pix_rings = QtGui.QPixmap(widget.size())
    widget.render(pix_rings)
    assert not pix_rings.isNull()

    # Test rendering in mini mode with active sessions
    widget.set_view_mode("mini")
    pix_mini = QtGui.QPixmap(widget.size())
    widget.render(pix_mini)
    assert not pix_mini.isNull()

    # Toggle show_active_sessions off
    widget.settings.show_active_sessions = False
    widget.set_view_mode("bars")
    pix_bars_no_sessions = QtGui.QPixmap(widget.size())
    widget.render(pix_bars_no_sessions)
    assert not pix_bars_no_sessions.isNull()


def test_cli_active_sessions_output(capsys):
    s = ActiveSession(
        provider_id="claude",
        session_id="123",
        title="Admin Panel Work",
        detail="running tests",
    )
    pu = ProviderUsage(
        provider_id="claude",
        display_name="Claude Code",
        primary_window=UsageWindow(name="5h", used_pct=25.0, resets_at=1791168000),
    )
    usage = AggregatedUsage(providers={"claude": pu}, active_sessions=[s])
    render_terminal_table(usage)

    captured = capsys.readouterr().out
    assert "CURRENTLY RUNNING SESSIONS & TASKS" in captured
    assert "Admin Panel Work" in captured


def test_widget_bars_layout_no_overlap_and_mode_switching():
    from src.ui.widget import AIUsageWidget

    s_claude = ActiveSession(
        provider_id="claude",
        session_id="c1",
        title="Claude Architecture Refactor",
        detail="sample-app (shell)",
        status="running",
        started_at=time.time() - 120,
    )
    s_codex = ActiveSession(
        provider_id="codex",
        session_id="cx1",
        title="OpenAI Codex Tests",
        detail="Running pytest in backend",
        status="running",
        started_at=time.time() - 45,
    )

    usage = AggregatedUsage(
        providers={},
        active_sessions=[s_claude, s_codex],
    )
    aggregator = MagicMock(spec=UsageAggregator)
    aggregator.get_latest.return_value = usage
    aggregator.get_active_sessions.return_value = [s_claude, s_codex]

    settings = WidgetSettings(show_active_sessions=True, session_display_mode="bars")
    widget = AIUsageWidget(aggregator, settings)

    # 1. Test "bars" mode layout
    layout_bars = widget._get_bars_layout(460.0)
    assert len(layout_bars.session_rects) == 2
    # Verify strict vertical progression (no overlap)
    assert layout_bars.last_bar_bottom < layout_bars.section_header_rect.top()
    r0, _ = layout_bars.session_rects[0]
    r1, _ = layout_bars.session_rects[1]
    assert layout_bars.section_header_rect.bottom() < r0.top()
    assert r0.bottom() < r1.top()
    assert layout_bars.active_end_y >= r1.bottom()
    assert layout_bars.active_end_y < layout_bars.trend_label_rect.top()
    assert layout_bars.trend_label_rect.bottom() < layout_bars.sparkline_rect.top()
    assert layout_bars.sparkline_rect.bottom() < layout_bars.divider_y
    assert layout_bars.divider_y < layout_bars.footer_text_rect.top()
    assert layout_bars.footer_text_rect.bottom() <= layout_bars.total_height

    h_bars = widget._calc_bars_height()

    # 2. Test "cycle" mode layout
    widget._set_session_display_mode("cycle")
    layout_cycle = widget._get_bars_layout(460.0)
    assert len(layout_cycle.session_rects) == 1
    assert layout_cycle.last_bar_bottom < layout_cycle.section_header_rect.top()
    rc0, _ = layout_cycle.session_rects[0]
    assert layout_cycle.section_header_rect.bottom() < rc0.top()
    assert layout_cycle.active_end_y >= rc0.bottom()
    assert layout_cycle.active_end_y < layout_cycle.trend_label_rect.top()
    assert layout_cycle.trend_label_rect.bottom() < layout_cycle.sparkline_rect.top()
    assert layout_cycle.sparkline_rect.bottom() < layout_cycle.divider_y

    h_cycle = widget._calc_bars_height()
    assert h_bars > h_cycle

    # 3. Test disabled active sessions
    widget._toggle_active_sessions()
    assert not widget.settings.show_active_sessions
    layout_disabled = widget._get_bars_layout(460.0)
    assert len(layout_disabled.session_rects) == 0
    assert layout_disabled.section_header_rect is None
    assert layout_disabled.active_end_y == layout_disabled.last_bar_bottom
    assert layout_disabled.active_end_y < layout_disabled.trend_label_rect.top()

    # 4. Render all provider themes to guarantee theme matching without errors
    widget._toggle_active_sessions()
    for pid in ("all", "claude", "codex", "antigravity"):
        widget.cycle_provider(pid)
        pix = QtGui.QPixmap(widget.size())
        widget.render(pix)
        assert not pix.isNull()


def test_tray_menu_active_sessions_options():
    from src.ui.tray import build_app_menu
    from src.ui.widget import AIUsageWidget

    aggregator = MagicMock(spec=UsageAggregator)
    aggregator.get_latest.return_value = AggregatedUsage()
    widget = AIUsageWidget(aggregator, WidgetSettings(show_active_sessions=True))

    menu = build_app_menu(widget, lambda: None)
    # Find Active Sessions submenu
    sessions_menu = None
    for action in menu.actions():
        if action.menu() and action.menu().title() == "Active Sessions":
            sessions_menu = action.menu()
            break
    assert sessions_menu is not None

    action_titles = [act.text() for act in sessions_menu.actions()]
    assert "Enabled" in action_titles
    assert "Show Separate Bars for Each" in action_titles
    assert "Cycle in Single Bar" in action_titles

