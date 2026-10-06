from __future__ import annotations

import argparse
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.aggregator import UsageAggregator
from src.models import AggregatedUsage


def _ansi_color(text: str, severity: str) -> str:
    """Add ANSI colors based on threshold severity."""
    # Check if stdout is a tty
    if not sys.stdout.isatty():
        return text
    if severity == "critical":
        return f"\033[1;31m{text}\033[0m"  # Bold Red
    if severity == "warning":
        return f"\033[1;33m{text}\033[0m"  # Bold Yellow
    return f"\033[1;32m{text}\033[0m"  # Bold Green


def render_terminal_table(usage: AggregatedUsage) -> None:
    """Print a clean, structured terminal table of all allowances."""
    print("=" * 72)
    print("                 GUAGE: AI QUOTA & USAGE MONITOR")
    print("=" * 72)

    for pid, p in usage.providers.items():
        print(f"\n[{p.display_name}]" + (f" (Plan: {p.plan_tier})" if p.plan_tier else ""))
        if not p.available:
            print(f"  Status: UNAVAILABLE ({p.error or 'unknown error'})")
            continue

        if p.account_email:
            name_str = f" ({p.account_name})" if p.account_name else ""
            print(f"  Account: {p.account_email}{name_str}")

        if p.credits_balance:
            print(f"  Credits / Balance: {p.credits_balance}")

        # Primary & Secondary Windows
        if p.primary_window:
            pw = p.primary_window
            bar_len = 20
            rem = pw.remaining_pct
            filled = int((rem / 100.0) * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            status_text = f"{rem:5.1f}% remaining [{bar}] (resets {pw.formatted_reset})"
            colored_status = _ansi_color(status_text, pw.severity)
            print(f"  • {pw.name:<18}: {colored_status}")

        if p.secondary_window:
            sw = p.secondary_window
            bar_len = 20
            rem = sw.remaining_pct
            filled = int((rem / 100.0) * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            status_text = f"{rem:5.1f}% remaining [{bar}] (resets {sw.formatted_reset})"
            colored_status = _ansi_color(status_text, sw.severity)
            print(f"  • {sw.name:<18}: {colored_status}")

        # Model allowances
        if p.models:
            print("  Models / Sub-allowances:")
            for m in p.models:
                tier_badge = f" [{m.tier_info}]" if m.tier_info else ""
                print(f"    - {m.model_name}{tier_badge}")
                if m.description:
                    print(f"      Description: {m.description}")
                for w in m.windows:
                    bar_len = 16
                    rem = w.remaining_pct
                    filled = int((rem / 100.0) * bar_len)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    status_text = f"{rem:5.1f}% remaining [{bar}] (resets {w.formatted_reset})"
                    colored_status = _ansi_color(status_text, w.severity)
                    print(f"        {w.name:<22}: {colored_status}")

    # Currently Running Sessions & Tasks
    if usage.active_sessions:
        print("\n" + "-" * 72)
        print("           CURRENTLY RUNNING SESSIONS & TASKS")
        print("-" * 72)
        for s in usage.active_sessions:
            prov_tag = f"[{s.display_provider}]"
            print(f"  • {prov_tag:<10} {s.notation}")

    print("\n" + "=" * 72)


def main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    # Bare command with no arguments launches the desktop GUI widget
    if not argv:
        from src.main import main as gui_main
        gui_main()
        return

    cmd = argv[0].lower()

    if cmd in ("ui", "app", "widget", "gui"):
        from src.main import main as gui_main
        gui_main()
        return

    if cmd in ("-v", "--version"):
        print("Guage 0.1.0")
        return

    parser = argparse.ArgumentParser(
        prog="guage",
        description="Guage: Cross-platform AI quota & session usage monitor (Claude, Codex, Antigravity, OpenCode)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # guage status
    p_status = subparsers.add_parser("status", help="Show AI quota allowances, burn rates and running sessions")
    p_status.add_argument("--json", action="store_true", help="Output usage as JSON")
    p_status.add_argument("--force", action="store_true", help="Bypass cache and force live refresh")
    p_status.add_argument("--limits", "--markdown", dest="limits", action="store_true", help="Output agent markdown summary")

    # guage sessions
    p_sessions = subparsers.add_parser("sessions", help="Show currently running coding sessions & agents")
    p_sessions.add_argument("--json", action="store_true", help="Output sessions as JSON")
    p_sessions.add_argument("--force", action="store_true", help="Scan processes and locks fresh")

    # guage limits
    p_limits = subparsers.add_parser("limits", help="Show remaining limits markdown summary & recommendations")
    p_limits.add_argument("--provider", default="all", choices=["all", "claude", "codex", "antigravity", "opencode"])
    p_limits.add_argument("--force", action="store_true", help="Force live refresh")

    # guage mcp
    p_mcp = subparsers.add_parser("mcp", help="Run MCP stdio server or configure AI agent tools")
    p_mcp.add_argument("--stdio", action="store_true", help="Run MCP stdio JSON-RPC loop (default)")
    p_mcp.add_argument("--config", action="store_true", help="Print MCP client configuration snippet")
    p_mcp.add_argument("--install-all", action="store_true", help="Install Guage MCP into Antigravity, Claude, and OpenCode")
    p_mcp.add_argument("--install-antigravity", action="store_true", help="Install into ~/.gemini/config/mcp_config.json")
    p_mcp.add_argument("--install-claude", action="store_true", help="Install into ~/.claude.json")
    p_mcp.add_argument("--install-opencode", action="store_true", help="Install into opencode.json")

    # If flags like --json, --force, --status are passed directly
    if argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        args = parser.parse_args(["status"] + argv)
    else:
        args = parser.parse_args(argv)

    if args.command == "status":
        aggregator = UsageAggregator()
        usage = aggregator.refresh_all(force=args.force)
        if args.limits:
            from src.mcp_server import MCPServer
            server = MCPServer(aggregator=aggregator)
            print(server._execute_get_remaining_limits({"provider": "all", "force_refresh": False}))
            print("\n" + server._execute_get_quota_recommendation())
        elif args.json:
            print(json.dumps(usage.to_dict(), indent=2))
        else:
            render_terminal_table(usage)

    elif args.command == "sessions":
        from src.mcp_server import MCPServer
        server = MCPServer()
        if args.json:
            sessions = server.aggregator.get_active_sessions(force=args.force)
            s_data = [
                {
                    "provider_id": s.provider_id,
                    "display_provider": s.display_provider,
                    "session_id": s.session_id,
                    "title": s.title,
                    "status": s.status,
                    "detail": s.detail,
                    "cwd": s.cwd,
                    "model_id": s.model_id,
                    "short_model": s.short_model,
                }
                for s in sessions
            ]
            print(json.dumps(s_data, indent=2))
        else:
            print(server._execute_get_active_sessions({"provider": "all", "force_refresh": args.force}))

    elif args.command == "limits":
        from src.mcp_server import MCPServer
        server = MCPServer()
        print(server._execute_get_remaining_limits({"provider": args.provider, "force_refresh": args.force}))
        print("\n" + server._execute_get_quota_recommendation())

    elif args.command == "mcp":
        from src.mcp_server import (
            MCPServer,
            generate_configs,
            install_antigravity_config,
            install_claude_config,
            install_opencode_config,
        )
        if args.install_all:
            ag_ok = install_antigravity_config()
            c_ok = install_claude_config()
            oc_ok = install_opencode_config()
            print(f"Antigravity config updated: {ag_ok} (~/.gemini/config/mcp_config.json)")
            print(f"Claude Code config updated: {c_ok} (~/.claude.json)")
            print(f"OpenCode config updated:    {oc_ok}")
        elif args.install_antigravity:
            ag_ok = install_antigravity_config()
            print(f"Antigravity config updated: {ag_ok} (~/.gemini/config/mcp_config.json)")
        elif args.install_claude:
            c_ok = install_claude_config()
            print(f"Claude Code config updated: {c_ok} (~/.claude.json)")
        elif args.install_opencode:
            oc_ok = install_opencode_config()
            print(f"OpenCode config updated:    {oc_ok}")
        elif args.config:
            print(json.dumps(generate_configs(), indent=2))
        else:
            MCPServer().run_stdio()

    else:
        parser.print_help()


def cli_status() -> None:
    """Entry point for ai-widget-cli."""
    main(["status"] + sys.argv[1:])


def cli_limits() -> None:
    """Entry point for ai-limits."""
    main(["limits"] + sys.argv[1:])
