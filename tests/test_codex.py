from unittest.mock import patch
from src.collectors.codex import CodexCollector


def test_codex_collector_parsing(tmp_path):
    collector = CodexCollector(cache_ttl_seconds=10)

    sample_rpc_result = {
        "planType": "plus",
        "rateLimits": {
            "primary": {"usedPercent": 61, "resetsAt": 1791173000, "windowDurationMins": 300},
            "secondary": {"usedPercent": 26, "resetsAt": 1791585000, "windowDurationMins": 10080},
            "credits": {"hasCredits": True, "balance": "100.00"},
        },
        "rateLimitsByLimitId": {
            "base_model_inference": {
                "limitName": "gpt-reserve",
                "normalModelSlug": "gpt-5.6-luna",
                "primary": {"usedPercent": 32, "resetsAt": 1791562000, "windowDurationMins": 10080},
            }
        },
    }

    with patch("src.collectors.codex.get_cache_dir", return_value=tmp_path):
        with patch("src.collectors.codex.find_codex_bin", return_value="/fake/codex"):
            with patch.object(collector, "find_account_info", return_value=("codexdev@example.com", "Codex Dev")):
                with patch.object(collector, "_query_rpc", return_value=sample_rpc_result):
                    usage = collector.collect(force=True)

    assert usage.available is True
    assert usage.display_name == "OpenAI Codex (codexdev)"
    assert usage.account_email == "codexdev@example.com"
    assert usage.plan_tier == "Plus"
    assert usage.credits_balance == "Balance: 100.00"
    assert usage.primary_window is not None
    assert usage.primary_window.used_pct == 61.0
    assert usage.secondary_window is not None
    assert usage.secondary_window.used_pct == 26.0

    # Models: Codex CLI + Reserve: gpt-5.6-luna
    assert len(usage.models) == 2
    assert usage.models[0].model_name == "Codex CLI"
    assert usage.models[1].model_name == "Reserve: gpt-5.6-luna"
    assert usage.models[1].windows[0].used_pct == 32.0


def test_codex_binary_missing(tmp_path):
    collector = CodexCollector(cache_ttl_seconds=10)
    with patch("src.collectors.codex.get_cache_dir", return_value=tmp_path):
        with patch("src.collectors.codex.find_codex_bin", return_value=None):
            usage = collector.collect(force=True)
    assert usage.available is False
    assert "not found" in usage.error
