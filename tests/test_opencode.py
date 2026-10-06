from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.opencode import OpenCodeCollector
from src.sessions import SessionTracker


def test_opencode_collector_disabled_when_no_db(tmp_path):
    collector = OpenCodeCollector(db_path=str(tmp_path / "nonexistent.db"))
    assert not collector.is_available()
    usage = collector.get_usage()
    assert not usage.available
    assert "not found" in (usage.error or "").lower()


def test_opencode_collector_parse_usage(tmp_path):
    cache_path = tmp_path / "cache.json"
    collector = OpenCodeCollector(cache_path=str(cache_path))

    mock_resp = {
        "plan_type": "plus",
        "email": "test@example.com",
        "rate_limit": {
            "primary_window": {
                "used_percent": 45,
                "limit_window_seconds": 18000,
                "reset_after_seconds": 7200,
            },
            "secondary_window": {
                "used_percent": 12,
                "limit_window_seconds": 604800,
                "reset_after_seconds": 360000,
            },
        },
        "model_usage": {
            "gpt-6-astra": {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 45,
                        "reset_after_seconds": 7200,
                    }
                }
            }
        },
    }

    mock_creds = {
        "access": "mock_token",
        "refresh": "mock_refresh",
        "expires": 9999999999999,
        "metadata": {"accountID": "acc_123"},
    }

    dummy_db = tmp_path / "dummy.db"
    dummy_db.touch()
    collector.db_path = dummy_db

    with patch.object(collector, "is_available", return_value=True), \
         patch.object(collector, "_read_credentials", return_value=mock_creds), \
         patch.object(collector, "_fetch_wham_usage", return_value=mock_resp):
        usage = collector.get_usage(force=True)

        assert usage.available
        assert usage.plan_tier == "Plus"
        assert "test" in usage.display_name
        assert usage.primary_window is not None
        assert usage.primary_window.remaining_pct == 55.0
        assert usage.secondary_window is not None
        assert usage.secondary_window.remaining_pct == 88.0
        assert len(usage.models) == 2  # Overall + gpt-6-astra


def test_scan_opencode_detects_processes(tmp_path):
    scanner = SessionTracker()
    dummy_db = tmp_path / "opencode.db"

    now_ms = int(time.time() * 1000.0)
    # Create dummy database with session_v2 schema
    import sqlite3
    conn = sqlite3.connect(dummy_db)
    conn.execute(
        "CREATE TABLE session_v2 (id TEXT, title TEXT, directory TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, time_idle INTEGER, time_archived INTEGER, time_suspended INTEGER, idle_outcome TEXT)"
    )
    conn.execute(
        "INSERT INTO session_v2 VALUES ('sess-1', 'Frontend Redesign', '/workspace/app', '{\"name\": \"gpt-5\"}', ?, ?, NULL, NULL, NULL, NULL)",
        (now_ms - 10000, now_ms - 5000),
    )
    conn.commit()
    conn.close()

    mock_proc = MagicMock()
    mock_proc.info = {"pid": 99999, "name": "opencode", "cmdline": ["opencode", "run"]}

    with patch("src.collectors.opencode.find_opencode_db", return_value=dummy_db), \
         patch("subprocess.check_output", return_value="opencode.exe 99999"):
        sessions = scanner.scan_opencode()
        assert isinstance(sessions, list)
