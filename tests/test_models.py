import time
from src.models import (
    AggregatedUsage,
    ModelAllowance,
    ProviderUsage,
    UsageWindow,
    format_reset_label,
    format_seconds_remaining,
)


def test_format_seconds_remaining():
    assert format_seconds_remaining(-10) == "Ready"
    assert format_seconds_remaining(0) == "Ready"
    assert format_seconds_remaining(45) == "45s"
    assert format_seconds_remaining(150) == "2m"
    assert format_seconds_remaining(3700) == "1h 1m"
    assert format_seconds_remaining(90000) == "1d 1h"


def test_format_reset_label():
    assert format_reset_label("Ready") == "Ready"
    assert format_reset_label("ready") == "Ready"
    assert format_reset_label("Reset ready") == "Ready"
    assert format_reset_label("") == "Ready"
    assert format_reset_label("1h 45m") == "Reset 1h 45m"
    assert format_reset_label("Reset 30m") == "Reset 30m"


def test_usage_window_severity():
    w_norm = UsageWindow(name="Norm", used_pct=40.0, resets_at=int(time.time() + 3600))
    assert w_norm.severity == "normal"
    assert w_norm.remaining_pct == 60.0

    w_warn = UsageWindow(name="Warn", used_pct=80.0, resets_at=int(time.time() + 3600))
    assert w_warn.severity == "warning"

    w_crit = UsageWindow(name="Crit", used_pct=96.0, resets_at=int(time.time() + 3600))
    assert w_crit.severity == "critical"


def test_provider_usage_highest_severity():
    w1 = UsageWindow(name="Session", used_pct=50.0, resets_at=int(time.time() + 3600))
    w2 = UsageWindow(name="Weekly", used_pct=92.0, resets_at=int(time.time() + 7200))
    pu = ProviderUsage(
        provider_id="test",
        display_name="Test Provider",
        primary_window=w1,
        secondary_window=w2,
    )
    assert pu.highest_severity == "critical"


def test_aggregated_usage_serialization():
    w1 = UsageWindow(name="Session", used_pct=50.0, resets_at=1791168000)
    pu = ProviderUsage(
        provider_id="claude",
        display_name="Claude Code",
        primary_window=w1,
        plan_tier="pro",
        account_email="alice@company.org",
        account_name="Alice Smith",
    )
    agg = AggregatedUsage(providers={"claude": pu}, last_updated=1791165000)
    d = agg.to_dict()
    assert d["last_updated"] == 1791165000
    assert "claude" in d["providers"]
    assert d["providers"]["claude"]["primary_window"]["used_pct"] == 50.0
    assert d["providers"]["claude"]["plan_tier"] == "pro"
    assert d["providers"]["claude"]["account_email"] == "alice@company.org"
    assert d["providers"]["claude"]["account_name"] == "Alice Smith"
    assert d["providers"]["claude"]["account_identifier"] == "alice"


def test_account_identifier():
    pu1 = ProviderUsage(provider_id="c", display_name="C", account_email="bob@example.com")
    assert pu1.account_identifier == "bob"

    pu2 = ProviderUsage(provider_id="c", display_name="C", account_name="Charlie")
    assert pu2.account_identifier == "Charlie"

    pu3 = ProviderUsage(provider_id="c", display_name="C")
    assert pu3.account_identifier is None


def test_active_session_short_model_and_notation():
    from src.models import ActiveSession

    # Claude models
    s1 = ActiveSession(provider_id="claude", session_id="s1", title="Refactor", model_id="claude-opus-5-5")
    assert s1.short_model == "Opus 5.5"
    assert "[Opus 5.5]" in s1.notation

    s2 = ActiveSession(provider_id="claude", session_id="s2", title="Task", model_id="claude-3-7-sonnet-20250219")
    assert s2.short_model == "Sonnet 3.7"

    # Codex / OpenAI models
    s3 = ActiveSession(provider_id="codex", session_id="s3", title="Feature", model_id="gpt-6.1-sol")
    assert s3.short_model == "GPT-6.1"

    # OpenCode provider-prefixed model
    s4 = ActiveSession(provider_id="opencode", session_id="s4", title="Bugfix", model_id="openai/gpt-6.1-sol")
    assert s4.short_model == "GPT-6.1"

    # Antigravity models
    s5 = ActiveSession(provider_id="antigravity", session_id="s5", title="Analysis", model_id="Gemini 3.8 Flash (Medium)")
    assert s5.short_model == "Gemini 3.8 Flash"

    s6 = ActiveSession(provider_id="antigravity", session_id="s6", title="Analysis", model_id="gemini-3.1-pro-preview")
    assert s6.short_model == "Gemini 3.1 Pro"

