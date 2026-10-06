import time
from unittest.mock import MagicMock

from PySide6 import QtWidgets

from src.aggregator import UsageAggregator
from src.config import WidgetSettings
from src.insights import InsightEngine, lowest_remaining, tray_summary, write_snapshot
from src.models import AggregatedUsage, ProviderUsage, UsageWindow

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])


def _usage(used, stamp=None, resets_in=3600):
    pu = ProviderUsage(
        provider_id="claude",
        display_name="Claude Code",
        primary_window=UsageWindow(name="5h", used_pct=used, resets_at=int(time.time()) + resets_in),
        last_updated=stamp or time.time(),
    )
    return AggregatedUsage(providers={"claude": pu}, last_updated=pu.last_updated)


def test_alerts_on_threshold_crossing_only():
    eng = InsightEngine()
    assert eng.update(_usage(50, 1)) == []  # baseline never alerts
    alerts = eng.update(_usage(80, 2))  # 20% left -> warning
    assert len(alerts) == 1 and "running low" in alerts[0][0]
    assert eng.update(_usage(82, 3)) == []  # same level, no repeat
    assert "running low" in eng.update(_usage(95, 4))[0][0]  # critical
    assert "depleted" in eng.update(_usage(100, 5))[0][0]
    assert "back" in eng.update(_usage(5, 6))[0][0]


def test_burn_rate_eta(monkeypatch):
    eng = InsightEngine()
    t = [1000.0]
    monkeypatch.setattr(time, "time", lambda: t[0])
    for i, used in enumerate([10, 20, 30]):
        eng.update(_usage(used, stamp=i + 1, resets_in=100000))
        t[0] += 300  # 5 min between samples -> 10% per 5 min
    win = UsageWindow(name="5h", used_pct=30, resets_at=int(t[0]) + 100000)
    eta = eng.eta_seconds("claude", "session", win)
    assert eta is not None and 1500 < eta < 2300  # 70% left at 2%/min ~ 35 min


def test_no_eta_when_reset_comes_first(monkeypatch):
    eng = InsightEngine()
    t = [1000.0]
    monkeypatch.setattr(time, "time", lambda: t[0])
    for i, used in enumerate([10, 20, 30]):
        eng.update(_usage(used, stamp=i + 1, resets_in=600))
        t[0] += 300
    win = UsageWindow(name="5h", used_pct=30, resets_at=int(t[0]) + 60)
    assert eng.eta_seconds("claude", "session", win) is None


def test_summary_and_lowest():
    u = _usage(77)
    assert lowest_remaining(u)[1] == 23.0
    assert "Claude: 23% left" in tray_summary(u)


def test_snapshot_written(tmp_path, monkeypatch):
    monkeypatch.setattr("src.insights.get_cache_dir", lambda: tmp_path)
    write_snapshot(_usage(40))
    import json
    data = json.loads((tmp_path / "snapshot.json").read_text())
    assert data["providers"]["claude"]["session"]["remaining_pct"] == 60.0


def test_used_style_and_mini_view(tmp_path, monkeypatch):
    from PySide6 import QtGui
    from src.ui.widget import AIUsageWidget

    monkeypatch.setattr(WidgetSettings, "save", lambda self: None)
    agg = MagicMock(spec=UsageAggregator)
    agg.get_latest.return_value = _usage(20)
    w = AIUsageWidget(agg, WidgetSettings())
    w.set_view_mode("mini")
    assert w.height() < 250
    w.toggle_style()
    assert w._disp(80.0) == 20.0
    pix = QtGui.QPixmap(w.size())
    w.render(pix)
    assert not pix.isNull()
    _, _, rows = w._get_provider_rows()
    assert "remaining" not in rows[0][2]
