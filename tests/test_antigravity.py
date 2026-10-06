from unittest.mock import patch
from src.collectors.antigravity import AntigravityCollector


def test_antigravity_collector_parsing(tmp_path):
    collector = AntigravityCollector(cache_ttl_seconds=10)

    sample_usage_payload = {
        "command": {
            "name": "usage",
            "data": {
                "groups": [
                    {
                        "name": "Gemini Models",
                        "description": "Models within this group: Gemini Flash, Gemini Pro",
                        "buckets": [
                            {
                                "id": "gemini-weekly",
                                "name": "Weekly Limit Remaining",
                                "window": "weekly",
                                "remaining_fraction": 0.90,
                                "reset_time": "2026-10-05T19:41:28Z",
                            },
                            {
                                "id": "gemini-5h",
                                "name": "Five Hour Limit Remaining",
                                "window": "5h",
                                "remaining_fraction": 0.70,
                                "reset_time": "2026-10-05T04:24:30Z",
                            },
                        ],
                    },
                    {
                        "name": "Claude and GPT models",
                        "description": "Models within this group: Claude Opus, Claude Sonnet, GPT-OSS",
                        "buckets": [
                            {
                                "id": "3p-weekly",
                                "name": "Weekly Limit Remaining",
                                "window": "weekly",
                                "remaining_fraction": 1.0,
                                "reset_time": "2026-10-12T01:57:32Z",
                            },
                            {
                                "id": "3p-5h",
                                "name": "Five Hour Limit Remaining",
                                "window": "5h",
                                "remaining_fraction": 1.0,
                                "reset_time": "2026-10-05T06:57:32Z",
                            },
                        ],
                    },
                ]
            },
        }
    }

    sample_credits_payload = {
        "command": {
            "name": "credits",
            "data": {"remaining_credits": 250, "upgrade_uri": "https://antigravity.google"},
        }
    }

    def fake_run_cmd(agy_bin, cmd):
        if cmd == "/usage":
            return sample_usage_payload
        if cmd == "/credits":
            return sample_credits_payload
        return None

    with patch("src.collectors.antigravity.get_cache_dir", return_value=tmp_path):
        with patch("src.collectors.antigravity.find_agy_bin", return_value="/fake/agy"):
            with patch.object(collector, "_run_cmd", side_effect=fake_run_cmd):
                with patch.object(collector, "find_account_info", return_value=("dev@example.com", "Dev")):
                    usage = collector.collect(force=True)

    assert usage.available is True
    assert usage.display_name == "Antigravity (dev)"
    assert usage.account_email == "dev@example.com"
    assert usage.credits_balance == "250 Credits"

    # Primary (5h) used = (1.0 - 0.70) * 100 = 30.0%
    assert usage.primary_window is not None
    assert round(usage.primary_window.used_pct, 1) == 30.0

    # Secondary (weekly) used = (1.0 - 0.90) * 100 = 10.0%
    assert usage.secondary_window is not None
    assert round(usage.secondary_window.used_pct, 1) == 10.0

    # Models: Gemini Models + Claude and GPT models
    assert len(usage.models) == 2
    assert usage.models[0].model_name == "Gemini Models"
    assert usage.models[1].model_name == "Claude and GPT models"
    assert usage.models[1].windows[0].used_pct == 0.0
