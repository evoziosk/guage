from unittest.mock import MagicMock, patch
from PySide6 import QtCore, QtGui, QtWidgets
from src.aggregator import UsageAggregator
from src.config import WidgetSettings
from src.models import AggregatedUsage, ProviderUsage, UsageWindow
from src.ui.styles import UITheme
from src.ui.tray import create_tray_icon
from src.ui.widget import AIUsageWidget

# Ensure a QApplication exists
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])


def test_tray_icon_generation():
    icon = create_tray_icon()
    assert not icon.isNull()


def test_widget_modes_and_painting():
    aggregator = MagicMock(spec=UsageAggregator)
    pu_claude = ProviderUsage(
        provider_id="claude",
        display_name="Claude Code",
        primary_window=UsageWindow(name="5h", used_pct=85.0, resets_at=1791168000),
        secondary_window=UsageWindow(name="Weekly", used_pct=45.0, resets_at=1791284400),
    )
    aggregator.get_latest.return_value = AggregatedUsage(providers={"claude": pu_claude})

    settings = WidgetSettings()
    widget = AIUsageWidget(aggregator, settings)

    assert widget.compact_mode is True
    assert widget.width() > 0
    assert widget.height() > 0

    # Toggle to expanded mode
    widget.toggle_mode()
    assert widget.compact_mode is False
    assert widget.height() > 100

    # Test paint event without crash
    pixmap = QtGui.QPixmap(widget.size())
    widget.render(pixmap)
    assert not pixmap.isNull()

    # Toggle back to compact
    widget.toggle_mode()
    assert widget.compact_mode is True
    pixmap_compact = QtGui.QPixmap(widget.size())
    widget.render(pixmap_compact)
    assert not pixmap_compact.isNull()


def test_provider_theme_switching():
    aggregator = MagicMock(spec=UsageAggregator)
    aggregator.get_latest.return_value = AggregatedUsage()
    widget = AIUsageWidget(aggregator)

    # Test all tabs
    for pid, expected_char in [("all", "G"), ("claude", "C"), ("codex", "C"), ("antigravity", "A")]:
        widget.cycle_provider(pid)
        assert widget.active_provider == pid
        theme = UITheme.get_theme(pid)
        assert theme.logo_char == expected_char
        pix = QtGui.QPixmap(widget.size())
        widget.render(pix)
        assert not pix.isNull()


def test_remaining_style_percentages():
    aggregator = MagicMock(spec=UsageAggregator)
    pu_claude = ProviderUsage(
        provider_id="claude",
        display_name="Claude Code",
        primary_window=UsageWindow(name="5h", used_pct=20.0, resets_at=1791168000),
        secondary_window=UsageWindow(name="Weekly", used_pct=60.0, resets_at=1791284400),
    )
    aggregator.get_latest.return_value = AggregatedUsage(providers={"claude": pu_claude})
    widget = AIUsageWidget(aggregator)

    # In All view
    widget.cycle_provider("all")
    title, color, rows = widget._get_provider_rows()
    claude_row = rows[0]
    # remaining_pct must be 80.0 (100 - 20)
    assert claude_row[3] == 80.0
    # weekly remaining must be 40.0 (100 - 60)
    assert claude_row[6] == 40.0
    assert "remaining" in claude_row[2]

    # In detailed Claude view
    widget.cycle_provider("claude")
    title, color, rows = widget._get_provider_rows()
    session_row = rows[0]
    weekly_row = rows[1]
    assert session_row[3] == 80.0
    assert weekly_row[3] == 40.0


def test_antigravity_claude_gpt_5h_row():
    from src.models import ModelAllowance
    aggregator = MagicMock(spec=UsageAggregator)
    pu_ag = ProviderUsage(
        provider_id="antigravity",
        display_name="Antigravity",
        primary_window=UsageWindow(name="Five Hour Limit Remaining", used_pct=80.0, resets_at=1791168000, window_duration_mins=300),
        secondary_window=UsageWindow(name="Weekly Limit Remaining", used_pct=15.0, resets_at=1791284400, window_duration_mins=10080),
        models=[
            ModelAllowance(
                model_name="Gemini Models",
                windows=[
                    UsageWindow(name="Weekly Limit Remaining", used_pct=15.0, resets_at=1791284400, window_duration_mins=10080),
                    UsageWindow(name="Five Hour Limit Remaining", used_pct=80.0, resets_at=1791168000, window_duration_mins=300),
                ],
            ),
            ModelAllowance(
                model_name="Claude and GPT models",
                windows=[
                    UsageWindow(name="Weekly Limit Remaining", used_pct=50.0, resets_at=1791284400, window_duration_mins=10080),
                    UsageWindow(name="Five Hour Limit Remaining", used_pct=100.0, resets_at=1791168000, window_duration_mins=300),
                ],
            ),
        ],
    )
    aggregator.get_latest.return_value = AggregatedUsage(providers={"antigravity": pu_ag})
    widget = AIUsageWidget(aggregator)

    widget.cycle_provider("antigravity")
    title, color, rows = widget._get_provider_rows()
    assert len(rows) == 4
    # Row 0: Gemini 5h
    assert rows[0][1] == "Gemini 5h"
    assert rows[0][3] == 20.0
    # Row 1: Gemini Weekly
    assert rows[1][1] == "Gemini Weekly"
    assert rows[1][3] == 85.0
    # Row 2: Claude & GPT 5h
    assert rows[2][1] == "Claude & GPT 5h"
    assert rows[2][3] == 0.0
    assert rows[2][5] == "critical"
    # Row 3: Claude & GPT Weekly
    assert rows[3][1] == "Claude & GPT Wk"
    assert rows[3][3] == 50.0

    # Ensure widget renders without error across all view modes with 4 rows
    for mode in ("bars", "rings", "mini"):
        widget.set_view_mode(mode)
        pix = QtGui.QPixmap(widget.size())
        widget.render(pix)
        assert not pix.isNull()


def test_background_thread_update_reaches_widget():
    """Updates published from the aggregator thread must land in widget.usage."""
    import threading
    aggregator = MagicMock(spec=UsageAggregator)
    aggregator.get_latest.return_value = AggregatedUsage()
    widget = AIUsageWidget(aggregator)
    assert "codex" not in widget.usage.providers

    pu = ProviderUsage(
        provider_id="codex",
        display_name="OpenAI Codex",
        primary_window=UsageWindow(name="5h", used_pct=6.0, resets_at=1791226196),
    )
    t = threading.Thread(target=widget._on_data_updated, args=(AggregatedUsage(providers={"codex": pu}),))
    t.start()
    t.join()
    deadline = QtCore.QDeadlineTimer(2000)
    while "codex" not in widget.usage.providers and not deadline.hasExpired():
        app.processEvents()
    assert widget.usage.providers["codex"].primary_window.remaining_pct == 94.0
