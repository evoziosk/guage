import time
from unittest.mock import MagicMock, patch
from src.aggregator import UsageAggregator
from src.models import ProviderUsage, UsageWindow


def test_aggregator_refresh_all():
    aggregator = UsageAggregator()

    mock_claude = ProviderUsage(
        provider_id="claude",
        display_name="Claude Code",
        primary_window=UsageWindow(name="5h", used_pct=50.0, resets_at=int(time.time() + 3600)),
    )
    mock_codex = ProviderUsage(
        provider_id="codex",
        display_name="OpenAI Codex",
        primary_window=UsageWindow(name="5h", used_pct=20.0, resets_at=int(time.time() + 3600)),
    )
    mock_agy = ProviderUsage(
        provider_id="antigravity",
        display_name="Antigravity",
        primary_window=UsageWindow(name="5h", used_pct=10.0, resets_at=int(time.time() + 3600)),
    )

    with patch.object(aggregator.claude, "collect", return_value=mock_claude):
        with patch.object(aggregator.codex, "collect", return_value=mock_codex):
            with patch.object(aggregator.antigravity, "collect", return_value=mock_agy):
                snapshot = aggregator.refresh_all(force=True)

    assert "claude" in snapshot.providers
    assert "codex" in snapshot.providers
    assert "antigravity" in snapshot.providers
    assert snapshot.providers["claude"].primary_window.used_pct == 50.0
    assert snapshot.providers["codex"].primary_window.used_pct == 20.0
    assert snapshot.providers["antigravity"].primary_window.used_pct == 10.0


def test_aggregator_subscription():
    aggregator = UsageAggregator()
    received = []

    def callback(data):
        received.append(data)

    aggregator.subscribe(callback)

    mock_pu = ProviderUsage(provider_id="claude", display_name="Claude Code")
    with patch.object(aggregator.claude, "collect", return_value=mock_pu):
        with patch.object(aggregator.codex, "collect", return_value=None):
            with patch.object(aggregator.antigravity, "collect", return_value=None):
                aggregator.refresh_all(force=True)

    assert len(received) == 1
    assert "claude" in received[0].providers
