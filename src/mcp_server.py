from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from src.aggregator import UsageAggregator
from src.insights import InsightEngine, best_provider, PROVIDER_LABELS
from src.models import AggregatedUsage, ProviderUsage


MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "guage-limits"
SERVER_VERSION = "1.0.0"


class MCPServer:
    """Standard Model Context Protocol (MCP) server over stdio for AI agent quota inspection."""

    def __init__(self, aggregator: Optional[UsageAggregator] = None):
        self.aggregator = aggregator or UsageAggregator()
        self.insights = InsightEngine()

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_remaining_limits",
                "description": (
                    "Get current remaining quota percentages, reset times, and allowance windows for all connected "
                    "AI coding providers (Claude Code, OpenAI Codex, Antigravity, OpenCode). Returns session & weekly limits, "
                    "severity status, and recommendations on which provider has the most headroom."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Filter by provider ID ('claude', 'codex', 'antigravity', 'opencode', or 'all')",
                            "enum": ["all", "claude", "codex", "antigravity", "opencode"],
                            "default": "all",
                        },
                        "force_refresh": {
                            "type": "boolean",
                            "description": "If true, bypass cached usage data and perform a live query",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "get_active_sessions",
                "description": (
                    "Get list of currently running coding sessions, background rollout tasks, and autonomous agents across "
                    "Claude, Codex, Antigravity, and OpenCode. Returns task title, active model (e.g. Sonnet 5.5, GPT-6.1), "
                    "status, duration, and working directory."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Filter by provider ID ('claude', 'codex', 'antigravity', 'opencode', or 'all')",
                            "enum": ["all", "claude", "codex", "antigravity", "opencode"],
                            "default": "all",
                        },
                        "force_refresh": {
                            "type": "boolean",
                            "description": "If true, scan running processes and session locks fresh",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "get_quota_recommendation",
                "description": (
                    "Get optimal provider recommendation based on available headroom, burn rate (%/hr), "
                    "and estimated time until quota reset or exhaustion."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "get_model_allowances",
                "description": (
                    "Get granular per-model allowances and dedicated quotas (e.g. Sonnet vs Opus limits, "
                    "Codex model allowances, Gemini Flash vs Pro allowances)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Filter by provider ('claude', 'codex', 'antigravity', 'opencode', or 'all')",
                            "enum": ["all", "claude", "codex", "antigravity", "opencode"],
                            "default": "all",
                        },
                    },
                },
            },
        ]

    def get_resource_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "uri": "limits://current",
                "name": "Current AI Quota Limits",
                "description": "Snapshot of remaining allowances, reset times, and status across all AI providers",
                "mimeType": "application/json",
            },
            {
                "uri": "limits://sessions",
                "name": "Currently Active Sessions",
                "description": "Snapshot of currently active tasks, models, and running sessions",
                "mimeType": "application/json",
            },
        ]

    def _execute_get_remaining_limits(self, args: Dict[str, Any]) -> str:
        provider_filter = args.get("provider", "all")
        force = bool(args.get("force_refresh", False))

        if force:
            usage = self.aggregator.refresh_all(force=True)
        else:
            usage = self.aggregator.get_latest()
            # If empty, do an initial refresh
            if not usage.providers:
                usage = self.aggregator.refresh_all(force=False)

        lines = ["# Current AI Model Limits & Remaining Quota\n"]

        best = best_provider(usage)
        if best:
            pid, headroom = best
            lines.append(f"**Recommended Provider Right Now**: `{PROVIDER_LABELS.get(pid, pid)}` ({headroom:.1f}% remaining headroom)\n")

        target_pids = [provider_filter] if provider_filter != "all" else ["claude", "codex", "antigravity", "opencode"]

        for pid in target_pids:
            p = usage.providers.get(pid)
            if not p:
                continue

            display_name = p.display_name
            tier_str = f" (Plan: {p.plan_tier})" if p.plan_tier else ""
            lines.append(f"## {display_name}{tier_str}")

            if not p.available:
                lines.append(f"- **Status**: Unavailable ({p.error or 'Not configured or credentials missing'})")
                lines.append("")
                continue

            if p.account_email:
                name_extra = f" ({p.account_name})" if p.account_name else ""
                lines.append(f"- **Account**: `{p.account_email}`{name_extra}")

            if p.credits_balance:
                lines.append(f"- **Credits Balance**: {p.credits_balance}")

            if p.primary_window:
                pw = p.primary_window
                status_icon = "🟢" if pw.severity == "normal" else ("🟡" if pw.severity == "warning" else "🔴")
                lines.append(
                    f"- {status_icon} **{pw.name}**: **{pw.remaining_pct:.1f}% remaining** "
                    f"({pw.used_pct:.1f}% used) • Resets: **{pw.formatted_reset}**"
                )

            if p.secondary_window:
                sw = p.secondary_window
                status_icon = "🟢" if sw.severity == "normal" else ("🟡" if sw.severity == "warning" else "🔴")
                lines.append(
                    f"- {status_icon} **{sw.name}**: **{sw.remaining_pct:.1f}% remaining** "
                    f"({sw.used_pct:.1f}% used) • Resets: **{sw.formatted_reset}**"
                )

            if p.models:
                lines.append("- **Model Allowances**:")
                for m in p.models:
                    tier = f" [{m.tier_info}]" if m.tier_info else ""
                    sub_wins = []
                    for w in m.windows:
                        sub_wins.append(f"{w.name}: {w.remaining_pct:.1f}% (reset {w.formatted_reset})")
                    wins_str = " | ".join(sub_wins) if sub_wins else "Default allowance"
                    lines.append(f"  - `{m.model_name}`{tier}: {wins_str}")

            lines.append("")

        return "\n".join(lines).strip()

    def _execute_get_active_sessions(self, args: Dict[str, Any]) -> str:
        provider_filter = args.get("provider", "all")
        force = bool(args.get("force_refresh", False))

        sessions = self.aggregator.get_active_sessions(provider_filter, force=force)
        if not sessions:
            return "No active AI coding sessions or rollout tasks currently detected."

        lines = [f"# Currently Active Sessions ({len(sessions)} running)\n"]
        for s in sessions:
            prov = s.display_provider
            model_tag = f" `[{s.short_model}]`" if s.short_model else ""
            dur_str = ""
            if s.started_at:
                elapsed = int(time.time() - s.started_at)
                if elapsed < 60:
                    dur_str = f" • running for {elapsed}s"
                elif elapsed < 3600:
                    dur_str = f" • running for {elapsed // 60}m"
                else:
                    dur_str = f" • running for {elapsed // 3600}h {(elapsed % 3600) // 60}m"

            lines.append(f"- **[{prov}]**{model_tag} **{s.title}**{dur_str}")
            if s.detail and s.detail != s.title:
                lines.append(f"  - Detail: {s.detail}")
            if s.cwd:
                lines.append(f"  - Working Directory: `{s.cwd}`")
            if s.pid:
                lines.append(f"  - PID: {s.pid}")

        return "\n".join(lines).strip()

    def _execute_get_quota_recommendation(self) -> str:
        usage = self.aggregator.get_latest()
        if not usage.providers:
            usage = self.aggregator.refresh_all(force=False)

        best = best_provider(usage)
        lines = ["# AI Quota & Headroom Recommendation\n"]
        if best:
            pid, headroom = best
            lines.append(f"### Best Option: **{PROVIDER_LABELS.get(pid, pid)}**")
            lines.append(f"- Available Headroom: **{headroom:.1f}% remaining**")
            pu = usage.providers.get(pid)
            if pu and pu.primary_window:
                lines.append(f"- Primary Window Reset: **{pu.primary_window.formatted_reset}**")
        else:
            lines.append("No active providers currently reporting quota headroom.")

        # Comparison Table
        lines.append("\n### Provider Comparison")
        lines.append("| Provider | Session Left | Reset Time | Weekly Pool | Status |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for pid in ("claude", "codex", "antigravity", "opencode"):
            pu = usage.providers.get(pid)
            if not pu or not pu.available:
                continue
            sess_str = f"{pu.primary_window.remaining_pct:.1f}%" if pu.primary_window else "N/A"
            reset_str = pu.primary_window.formatted_reset if pu.primary_window else "Ready"
            wk_str = f"{pu.secondary_window.remaining_pct:.1f}%" if pu.secondary_window else "N/A"
            sev = pu.highest_severity.upper()
            lines.append(f"| {pu.display_name} | {sess_str} | {reset_str} | {wk_str} | {sev} |")

        return "\n".join(lines).strip()

    def _execute_get_model_allowances(self, args: Dict[str, Any]) -> str:
        provider_filter = args.get("provider", "all")
        usage = self.aggregator.get_latest()
        if not usage.providers:
            usage = self.aggregator.refresh_all(force=False)

        lines = ["# Detailed Model Allowances & Sub-Limits\n"]
        target_pids = [provider_filter] if provider_filter != "all" else ["claude", "codex", "antigravity", "opencode"]

        for pid in target_pids:
            pu = usage.providers.get(pid)
            if not pu or not pu.available:
                continue
            lines.append(f"## {pu.display_name}")
            if not pu.models:
                lines.append("- Operates on unified shared pool.")
            else:
                for m in pu.models:
                    tier_desc = f" ({m.tier_info})" if m.tier_info else ""
                    lines.append(f"### `{m.model_name}`{tier_desc}")
                    if m.description:
                        lines.append(f"{m.description}")
                    for w in m.windows:
                        lines.append(f"- **{w.name}**: {w.remaining_pct:.1f}% remaining (resets in {w.formatted_reset})")
            lines.append("")

        return "\n".join(lines).strip()

    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process incoming JSON-RPC 2.0 message and return response dict."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        # Notifications (no reply expected)
        if req_id is None:
            return None

        # Ping
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        # Initialize
        if method == "initialize":
            client_version = params.get("protocolVersion")
            negotiated_version = client_version if client_version else MCP_PROTOCOL_VERSION
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": negotiated_version,
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                    "capabilities": {
                        "tools": {},
                        "resources": {},
                    },
                },
            }

        # Tools List
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": self.get_tool_definitions(),
                },
            }

        # Tools Call
        if method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments") or {}

            try:
                if tool_name == "get_remaining_limits":
                    text = self._execute_get_remaining_limits(tool_args)
                elif tool_name == "get_active_sessions":
                    text = self._execute_get_active_sessions(tool_args)
                elif tool_name == "get_quota_recommendation":
                    text = self._execute_get_quota_recommendation()
                elif tool_name == "get_model_allowances":
                    text = self._execute_get_model_allowances(tool_args)
                else:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {tool_name}",
                        },
                    }

                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                            }
                        ],
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error executing tool {tool_name}: {str(e)}",
                            }
                        ],
                    },
                }

        # Resources List
        if method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": self.get_resource_definitions(),
                },
            }

        # Resources Read
        if method == "resources/read":
            uri = params.get("uri")
            if uri == "limits://current":
                usage = self.aggregator.get_latest()
                data_json = json.dumps(usage.to_dict(), indent=2)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": data_json,
                            }
                        ],
                    },
                }
            elif uri == "limits://sessions":
                sessions = self.aggregator.get_active_sessions()
                s_data = [
                    {
                        "provider_id": s.provider_id,
                        "display_provider": s.display_provider,
                        "session_id": s.session_id,
                        "title": s.title,
                        "status": s.status,
                        "detail": s.detail,
                        "cwd": s.cwd,
                        "started_at": s.started_at,
                        "model_id": s.model_id,
                        "short_model": s.short_model,
                    }
                    for s in sessions
                ]
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": json.dumps(s_data, indent=2),
                            }
                        ],
                    },
                }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": f"Resource not found: {uri}",
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Unsupported MCP method: {method}",
            },
        }

    def run_stdio(self) -> None:
        """Run the stdio message processing loop."""
        # Ensure utf-8 stdio
        if hasattr(sys.stdin, "reconfigure"):
            try:
                sys.stdin.reconfigure(encoding="utf-8")
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    req = json.loads(line)
                except json.JSONDecodeError as err:
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {str(err)}"},
                    }
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()
                    continue

                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                # Log internal server exception to stderr without breaking protocol on stdout
                sys.stderr.write(f"[modelpulse-mcp] Error: {e}\n")
                sys.stderr.flush()


def generate_configs() -> Dict[str, Any]:
    """Generate configuration snippets for Claude, Antigravity, OpenCode, and Cursor."""
    base_dir = str(Path(__file__).resolve().parent.parent)
    python_bin = sys.executable

    server_entry = {
        "command": python_bin,
        "args": ["-m", "src.mcp_server"],
        "cwd": base_dir,
        "env": {
            "PYTHONPATH": base_dir,
        },
    }

    return {
        "mcpServers": {
            "guage": server_entry,
            "modelpulse": server_entry,
        }
    }


def install_antigravity_config() -> bool:
    """Register Guage & ModelPulse into ~/.gemini/config/mcp_config.json."""
    config_dir = Path.home() / ".gemini" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = config_dir / "mcp_config.json"

    data = {}
    if mcp_file.exists():
        try:
            content = mcp_file.read_text(encoding="utf-8").strip()
            if content:
                data = json.loads(content)
        except Exception:
            data = {}

    mcp_servers = data.setdefault("mcpServers", {})
    cfg = generate_configs()
    mcp_servers["guage"] = cfg["mcpServers"]["guage"]
    mcp_servers["modelpulse"] = cfg["mcpServers"]["modelpulse"]

    mcp_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def install_claude_config() -> bool:
    """Register Guage & ModelPulse into ~/.claude.json."""
    claude_file = Path.home() / ".claude.json"
    data = {}
    if claude_file.exists():
        try:
            content = claude_file.read_text(encoding="utf-8").strip()
            if content:
                data = json.loads(content)
        except Exception:
            data = {}

    mcp_servers = data.setdefault("mcpServers", {})
    cfg = generate_configs()
    mcp_servers["guage"] = cfg["mcpServers"]["guage"]
    mcp_servers["modelpulse"] = cfg["mcpServers"]["modelpulse"]

    claude_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def install_opencode_config() -> bool:
    """Register Guage & ModelPulse into current opencode.json or ~/.config/opencode/opencode.json."""
    cfg = generate_configs()
    server_spec = cfg["mcpServers"]["guage"]

    # Target root of workspace or home config
    target_files = [
        Path.cwd() / "opencode.json",
        Path.home() / ".config" / "opencode" / "opencode.json",
    ]
    installed_any = False
    for tf in target_files:
        if tf.parent.exists():
            data = {}
            if tf.exists():
                try:
                    c = tf.read_text(encoding="utf-8").strip()
                    if c:
                        data = json.loads(c)
                except Exception:
                    data = {}
            mcp_sec = data.setdefault("mcp", {})
            mcp_sec["guage"] = server_spec
            mcp_sec["modelpulse"] = server_spec
            tf.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            installed_any = True
            break
    return installed_any


def main() -> None:
    parser = argparse.ArgumentParser(description="ModelPulse MCP Server & Limits Connector for AI Agents")
    parser.add_argument("--summary", "--inspect", dest="summary", action="store_true", help="Print current remaining limits summary and exit")
    parser.add_argument("--sessions", action="store_true", help="Print currently active sessions across all providers and exit")
    parser.add_argument("--json", action="store_true", help="Output full aggregated usage as JSON and exit")
    parser.add_argument("--config", action="store_true", help="Print MCP server JSON configuration snippet")
    parser.add_argument("--install-all", action="store_true", help="Install ModelPulse MCP into Antigravity, Claude Code, and OpenCode configs")
    parser.add_argument("--install-antigravity", action="store_true", help="Install into ~/.gemini/config/mcp_config.json")
    parser.add_argument("--install-claude", action="store_true", help="Install into ~/.claude.json")
    parser.add_argument("--install-opencode", action="store_true", help="Install into opencode.json")
    parser.add_argument("--stdio", action="store_true", help="Run MCP JSON-RPC 2.0 stdio loop (default when no flags)")

    args = parser.parse_args()
    server = MCPServer()

    if args.summary:
        print(server._execute_get_remaining_limits({"provider": "all", "force_refresh": False}))
        print("\n" + server._execute_get_quota_recommendation())
        return

    if args.sessions:
        print(server._execute_get_active_sessions({"provider": "all", "force_refresh": False}))
        return

    if args.json:
        usage = server.aggregator.get_latest()
        if not usage.providers:
            usage = server.aggregator.refresh_all(force=False)
        print(json.dumps(usage.to_dict(), indent=2))
        return

    if args.config:
        print(json.dumps(generate_configs(), indent=2))
        return

    if args.install_all:
        ag_ok = install_antigravity_config()
        c_ok = install_claude_config()
        oc_ok = install_opencode_config()
        print(f"Antigravity config updated: {ag_ok} (~/.gemini/config/mcp_config.json)")
        print(f"Claude Code config updated: {c_ok} (~/.claude.json)")
        print(f"OpenCode config updated:    {oc_ok}")
        return

    if args.install_antigravity:
        ag_ok = install_antigravity_config()
        print(f"Antigravity config updated: {ag_ok} (~/.gemini/config/mcp_config.json)")
        return

    if args.install_claude:
        c_ok = install_claude_config()
        print(f"Claude Code config updated: {c_ok} (~/.claude.json)")
        return

    if args.install_opencode:
        oc_ok = install_opencode_config()
        print(f"OpenCode config updated:    {oc_ok}")
        return

    # Default to running MCP stdio loop
    server.run_stdio()


if __name__ == "__main__":
    main()
