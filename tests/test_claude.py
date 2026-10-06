from unittest.mock import MagicMock, patch
from src.collectors.claude import ClaudeCollector


def test_claude_collector_parsing(tmp_path):
    collector = ClaudeCollector(cache_ttl_seconds=10)

    sample_api_response = {
        "five_hour": {
            "utilization": 85.0,
            "resets_at": "2026-10-05T02:40:00.000000+00:00",
        },
        "seven_day": {
            "utilization": 45.0,
            "resets_at": "2026-10-06T11:00:00.000000+00:00",
        },
        "seven_day_breakdown": {
            "rows": [
                {"display_name": "Claude Code", "percent": 90},
                {"display_name": "Chats", "percent": 10},
            ]
        },
        "limits": [
            {
                "kind": "weekly_scoped",
                "percent": 30.0,
                "resets_at": "2026-10-07T00:00:00+00:00",
                "scope": {"model": {"display_name": "Claude 3.7 Sonnet"}},
            }
        ],
        "spend": {"enabled": False},
        "extra_usage": {"is_enabled": False},
    }

    with patch("src.collectors.claude.get_cache_dir", return_value=tmp_path):
        with patch.object(collector, "find_credentials", return_value={"accessToken": "test-token", "subscriptionType": "Pro"}):
            with patch.object(collector, "find_account_info", return_value=("dev@example.com", "Dev User")):
                with patch.object(collector, "fetch_api", return_value=sample_api_response):
                    usage = collector.collect(force=True)

    assert usage.available is True
    assert usage.display_name == "Claude Code (dev)"
    assert usage.account_email == "dev@example.com"
    assert usage.plan_tier == "Pro"
    assert usage.primary_window is not None
    assert usage.primary_window.used_pct == 85.0
    assert usage.primary_window.severity == "warning"
    assert usage.secondary_window is not None
    assert usage.secondary_window.used_pct == 45.0

    # Models: Claude Code Engine + Claude 3.7 Sonnet
    assert len(usage.models) == 2
    assert usage.models[0].model_name == "Claude Code Engine"
    assert "Claude Code: 90%" in usage.models[0].description
    assert usage.models[1].model_name == "Claude 3.7 Sonnet"
    assert usage.models[1].windows[0].used_pct == 30.0


def test_claude_no_credentials(tmp_path):
    collector = ClaudeCollector(cache_ttl_seconds=10)
    with patch("src.collectors.claude.get_cache_dir", return_value=tmp_path):
        with patch.object(collector, "find_credentials", return_value=None):
            usage = collector.collect(force=True)
    assert usage.available is False
    assert "No credentials found" in usage.error


def test_claude_rate_limit_with_cached_data(tmp_path):
    import json
    import time
    collector = ClaudeCollector(cache_ttl_seconds=10)

    # Pre-populate cache with 100% usage and reset in future
    future_reset = int(time.time()) + 600
    cache_data = {
        "cached_at": time.time(),
        "available": True,
        "error": None,
        "plan_tier": "Pro",
        "primary_window": {
            "name": "5-Hour Session",
            "used_pct": 100.0,
            "resets_at": future_reset,
            "window_duration_mins": 300,
        },
        "secondary_window": {
            "name": "Weekly Limit",
            "used_pct": 67.0,
            "resets_at": future_reset + 86400,
            "window_duration_mins": 10080,
        },
        "models": [],
    }
    (tmp_path / "claude_cache.json").write_text(json.dumps(cache_data), encoding="utf-8")

    with patch("src.collectors.claude.get_cache_dir", return_value=tmp_path):
        with patch.object(collector, "find_credentials", return_value={"accessToken": "test-token", "subscriptionType": "Pro"}):
            with patch.object(collector, "fetch_api", return_value={"error": "Rate limited by Anthropic API", "rate_limited": True}):
                usage = collector.collect(force=True)

    assert usage.available is True
    assert usage.primary_window is not None
    assert usage.primary_window.used_pct == 100.0
    assert usage.primary_window.resets_at == future_reset
    assert "Rate limited" in usage.error
    # Must NOT report "Ready" while resets_at is 10 minutes in the future
    assert usage.primary_window.formatted_reset != "Ready"
    assert "m" in usage.primary_window.formatted_reset


def test_claude_rate_limit_with_local_quota_rejection(tmp_path):
    import time
    collector = ClaudeCollector(cache_ttl_seconds=10)

    future_reset = int(time.time()) + 900  # 15 minutes left
    mock_local_quota = {
        "status": "rejected",
        "resetsAt": future_reset,
        "rateLimitType": "five_hour",
    }

    with patch("src.collectors.claude.get_cache_dir", return_value=tmp_path):
        with patch.object(collector, "get_local_quota_limits", return_value=mock_local_quota):
            with patch.object(collector, "find_credentials", return_value={"accessToken": "test-token", "subscriptionType": "Pro"}):
                with patch.object(collector, "fetch_api", return_value={"error": "Rate limited by Anthropic API", "rate_limited": True}):
                    usage = collector.collect(force=True)

    assert usage.available is True
    assert usage.primary_window is not None
    assert usage.primary_window.used_pct == 100.0
    assert usage.primary_window.resets_at == future_reset
    assert usage.primary_window.severity == "critical"
    assert usage.primary_window.formatted_reset != "Ready"
    assert "m" in usage.primary_window.formatted_reset


def test_claude_error_does_not_poison_cache(tmp_path):
    import json
    import time
    collector = ClaudeCollector(cache_ttl_seconds=10)

    future_reset = int(time.time()) + 600
    cache_data = {
        "cached_at": time.time(),
        "available": True,
        "error": None,
        "plan_tier": "Pro",
        "primary_window": {
            "name": "5-Hour Session",
            "used_pct": 95.0,
            "resets_at": future_reset,
            "window_duration_mins": 300,
        },
        "secondary_window": None,
        "models": [],
    }
    (tmp_path / "claude_cache.json").write_text(json.dumps(cache_data), encoding="utf-8")

    with patch("src.collectors.claude.get_cache_dir", return_value=tmp_path):
        with patch.object(collector, "find_credentials", return_value={"accessToken": "test-token"}):
            with patch.object(collector, "fetch_api", return_value={"error": "Rate limited by Anthropic API", "rate_limited": True}):
                with patch.object(collector, "get_local_quota_limits", return_value=None):
                    collector.collect(force=True)

    # Verify disk cache still holds 95.0%, not 0.0%
    saved = json.loads((tmp_path / "claude_cache.json").read_text(encoding="utf-8"))
    assert saved["primary_window"]["used_pct"] == 95.0
    assert saved["primary_window"]["resets_at"] == future_reset
