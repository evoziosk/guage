from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.mcp_server import MCPServer, generate_configs
from src.models import ActiveSession, AggregatedUsage, ProviderUsage, UsageWindow


def test_mcp_initialize():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = server.handle_request(req)

    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "guage-limits"
    assert "tools" in resp["result"]["capabilities"]
    assert "resources" in resp["result"]["capabilities"]


def test_mcp_ping():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}
    resp = server.handle_request(req)
    assert resp["result"] == {}


def test_mcp_tools_list():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
    resp = server.handle_request(req)

    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "get_remaining_limits" in tool_names
    assert "get_active_sessions" in tool_names
    assert "get_quota_recommendation" in tool_names
    assert "get_model_allowances" in tool_names


def test_mcp_tool_calls():
    mock_agg = MagicMock()
    pw = UsageWindow(name="5h Session", used_pct=25.0, resets_at=1791168000)
    pu = ProviderUsage(
        provider_id="claude",
        display_name="Claude Code",
        primary_window=pw,
        plan_tier="Pro",
    )
    mock_agg.get_latest.return_value = AggregatedUsage(
        providers={"claude": pu},
        active_sessions=[
            ActiveSession(
                provider_id="claude",
                session_id="c1",
                title="Auth Refactor",
                status="running",
                model_id="claude-opus-5-5",
            )
        ],
    )
    mock_agg.get_active_sessions.return_value = [
        ActiveSession(
            provider_id="claude",
            session_id="c1",
            title="Auth Refactor",
            status="running",
            model_id="claude-opus-5-5",
        )
    ]

    server = MCPServer(aggregator=mock_agg)

    # 1. get_remaining_limits
    call_limits = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {"name": "get_remaining_limits", "arguments": {"provider": "claude"}},
    }
    resp = server.handle_request(call_limits)
    text = resp["result"]["content"][0]["text"]
    assert "Claude Code" in text
    assert "75.0% remaining" in text

    # 2. get_active_sessions
    call_sessions = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {"name": "get_active_sessions", "arguments": {}},
    }
    resp_s = server.handle_request(call_sessions)
    text_s = resp_s["result"]["content"][0]["text"]
    assert "Auth Refactor" in text_s
    assert "Opus 5.5" in text_s

    # 3. get_quota_recommendation
    call_rec = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {"name": "get_quota_recommendation", "arguments": {}},
    }
    resp_r = server.handle_request(call_rec)
    text_r = resp_r["result"]["content"][0]["text"]
    assert "Claude" in text_r


def test_mcp_resources():
    mock_agg = MagicMock()
    mock_agg.get_latest.return_value = AggregatedUsage()
    mock_agg.get_active_sessions.return_value = []

    server = MCPServer(aggregator=mock_agg)

    # List resources
    resp = server.handle_request({"jsonrpc": "2.0", "id": 20, "method": "resources/list"})
    uris = [r["uri"] for r in resp["result"]["resources"]]
    assert "limits://current" in uris
    assert "limits://sessions" in uris

    # Read current
    resp_read = server.handle_request({"jsonrpc": "2.0", "id": 21, "method": "resources/read", "params": {"uri": "limits://current"}})
    content_text = resp_read["result"]["contents"][0]["text"]
    parsed = json.loads(content_text)
    assert "providers" in parsed


def test_mcp_unknown_method():
    server = MCPServer()
    resp = server.handle_request({"jsonrpc": "2.0", "id": 99, "method": "unknown/operation"})
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_generate_configs():
    cfg = generate_configs()
    assert "mcpServers" in cfg
    assert "modelpulse" in cfg["mcpServers"]
    assert cfg["mcpServers"]["modelpulse"]["args"] == ["-m", "src.mcp_server"]
