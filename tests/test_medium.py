import json
import os
import time
from datetime import datetime, timezone

from src.aggregator import UsageAggregator
from src.config import WidgetSettings
from src.history import History
from src.insights import best_provider
from src.localstats import LocalStats, format_tokens
from src.models import AggregatedUsage, ProviderUsage, UsageWindow


def _pu(pid, used_s, used_w=10):
    return ProviderUsage(
        provider_id=pid, display_name=pid,
        primary_window=UsageWindow(name="s", used_pct=used_s, resets_at=int(time.time()) + 3600),
        secondary_window=UsageWindow(name="w", used_pct=used_w, resets_at=int(time.time()) + 90000),
        last_updated=time.time(),
    )


def test_history_throttle_and_series(tmp_path):
    h = History(tmp_path / "h.jsonl")
    u = AggregatedUsage(providers={"claude": _pu("claude", 30)})
    now = time.time()
    assert h.record(u, now=now) is True
    assert h.record(u, now=now + 60) is False  # throttled
    assert h.record(u, now=now + 400) is True
    assert len(h.series("claude")) == 2
    # reload from disk
    assert len(History(tmp_path / "h.jsonl").series("claude")) == 2


def test_best_provider_uses_weekly_headroom():
    u = AggregatedUsage(providers={
        "claude": _pu("claude", 10, used_w=95),  # session fine, week nearly gone
        "codex": _pu("codex", 40, used_w=20),
    })
    assert best_provider(u)[0] == "codex"


def test_format_tokens():
    assert format_tokens(999) == "999"
    assert format_tokens(12_000) == "12K"
    assert format_tokens(1_250_000) == "1.2M"


def test_local_stats_claude_and_codex(tmp_path, monkeypatch):
    ts = datetime.now(timezone.utc).isoformat()
    cdir = tmp_path / "claude" / "projects" / "proj"
    cdir.mkdir(parents=True)
    line = {"timestamp": ts, "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5,
                                                              "cache_creation_input_tokens": 100,
                                                              "cache_read_input_tokens": 9999}}}
    (cdir / "s.jsonl").write_text(json.dumps(line) + "\n" + json.dumps(line) + "\n")  # duplicate id
    xdir = tmp_path / "codex" / "sessions" / "2026" / "10" / "05"
    xdir.mkdir(parents=True)
    ev = {"payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 777}}}}
    (xdir / "r.jsonl").write_text(json.dumps(ev) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    s = LocalStats()
    s.refresh()
    assert s.today["claude"] == (115, 1)  # cache reads excluded, duplicate message de-duplicated
    assert s.today["codex"] == (777, 1)
    assert "Today 115 tokens, 1 session" == s.summary("claude")


def test_adaptive_polling_backs_off_and_resets():
    agg = UsageAggregator(WidgetSettings())
    base = agg._interval("claude")
    agg._usage.providers["claude"] = _pu("claude", 30)
    for _ in range(5):
        agg._track_change("claude")
    assert agg._interval("claude") == base * 4
    agg._usage.providers["claude"] = _pu("claude", 35)
    agg._track_change("claude")
    assert agg._interval("claude") == base
