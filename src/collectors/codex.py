from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import get_cache_dir
from src.models import ModelAllowance, ProviderUsage, UsageWindow

CACHE_FILE = "codex_cache.json"
RPC_TIMEOUT = 10.0


def find_codex_bin() -> Optional[str]:
    """Find the codex CLI executable across Linux and Windows."""
    # 1. On Windows, check binary .exe candidates first to avoid running via .cmd
    if sys.platform == "win32":
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            p = Path(user_profile)
            exe_candidates = [
                p / "AppData" / "Local" / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe",
                p / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex.exe",
            ]
            for c in exe_candidates:
                if os.path.exists(c):
                    return str(c)

    # 2. Check PATH
    which = shutil.which("codex")
    if which:
        return which

    # 3. Check platform-specific common paths
    candidates = []
    if sys.platform == "win32":
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            p = Path(user_profile)
            candidates.extend([
                p / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex.cmd",
                p / "AppData" / "Roaming" / "npm" / "codex.cmd",
            ])
    else:
        home = Path.home()
        candidates.extend([
            home / ".local" / "bin" / "codex",
            home / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex",
            Path("/usr/local/bin/codex"),
            Path("/opt/homebrew/bin/codex"),
        ])

    for c in candidates:
        if os.path.exists(c):
            return str(c)
    return None


class CodexCollector:
    """Collects OpenAI Codex usage, rate limits, and model allowances."""

    def __init__(self, cache_ttl_seconds: int = 120):
        self.cache_ttl = cache_ttl_seconds
        self._last_result: Optional[ProviderUsage] = None

    @classmethod
    def find_account_info(cls) -> tuple[Optional[str], Optional[str]]:
        """Extract user email and display name from ~/.codex/auth.json."""
        import base64

        candidates = [Path.home() / ".codex" / "auth.json"]
        if sys.platform == "win32":
            user_profile = os.environ.get("USERPROFILE")
            if user_profile:
                candidates.append(Path(user_profile) / ".codex" / "auth.json")
            for root in (r"\\wsl.localhost", r"\\wsl$"):
                try:
                    for distro in Path(root).iterdir():
                        for home in (distro / "home").iterdir():
                            candidates.append(home / ".codex" / "auth.json")
                except Exception:
                    pass

        for c in candidates:
            if not c.exists():
                continue
            try:
                data = json.loads(c.read_text(encoding="utf-8", errors="replace"))
                idt = data.get("tokens", {}).get("id_token")
                if idt and "." in idt:
                    parts = idt.split(".")
                    if len(parts) >= 2:
                        p_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
                        payload = json.loads(base64.urlsafe_b64decode(p_b64.encode()))
                        email = payload.get("email")
                        name = payload.get("name")
                        if email or name:
                            return email, name
            except Exception:
                continue
        return None, None

    def _read_cache(self) -> Optional[ProviderUsage]:
        cache_path = get_cache_dir() / CACHE_FILE
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            ts = d.get("cached_at", 0)
            if time.time() - ts > self.cache_ttl:
                return None
            pu = ProviderUsage(
                provider_id="codex",
                display_name=d.get("display_name", "OpenAI Codex"),
                available=d.get("available", True),
                error=d.get("error"),
                account_email=d.get("account_email"),
                account_name=d.get("account_name"),
                plan_tier=d.get("plan_tier"),
                credits_balance=d.get("credits_balance"),
                last_updated=ts,
            )
            if d.get("primary_window"):
                pw = d["primary_window"]
                pu.primary_window = UsageWindow(
                    name=pw["name"],
                    used_pct=pw["used_pct"],
                    resets_at=pw["resets_at"],
                    window_duration_mins=pw.get("window_duration_mins", 300),
                )
            if d.get("secondary_window"):
                sw = d["secondary_window"]
                pu.secondary_window = UsageWindow(
                    name=sw["name"],
                    used_pct=sw["used_pct"],
                    resets_at=sw["resets_at"],
                    window_duration_mins=sw.get("window_duration_mins", 10080),
                )
            for m in d.get("models", []):
                ma = ModelAllowance(
                    model_name=m["model_name"],
                    tier_info=m.get("tier_info"),
                    description=m.get("description"),
                )
                for w in m.get("windows", []):
                    ma.windows.append(
                        UsageWindow(
                            name=w["name"],
                            used_pct=w["used_pct"],
                            resets_at=w["resets_at"],
                            window_duration_mins=w.get("window_duration_mins"),
                        )
                    )
                pu.models.append(ma)
            return pu
        except Exception:
            return None

    def _write_cache(self, usage: ProviderUsage) -> None:
        cache_path = get_cache_dir() / CACHE_FILE
        try:
            data = {
                "cached_at": usage.last_updated,
                "display_name": usage.display_name,
                "available": usage.available,
                "error": usage.error,
                "account_email": usage.account_email,
                "account_name": usage.account_name,
                "plan_tier": usage.plan_tier,
                "credits_balance": usage.credits_balance,
                "primary_window": {
                    "name": usage.primary_window.name,
                    "used_pct": usage.primary_window.used_pct,
                    "resets_at": usage.primary_window.resets_at,
                    "window_duration_mins": usage.primary_window.window_duration_mins,
                } if usage.primary_window else None,
                "secondary_window": {
                    "name": usage.secondary_window.name,
                    "used_pct": usage.secondary_window.used_pct,
                    "resets_at": usage.secondary_window.resets_at,
                    "window_duration_mins": usage.secondary_window.window_duration_mins,
                } if usage.secondary_window else None,
                "models": [
                    {
                        "model_name": m.model_name,
                        "tier_info": m.tier_info,
                        "description": m.description,
                        "windows": [
                            {
                                "name": w.name,
                                "used_pct": w.used_pct,
                                "resets_at": w.resets_at,
                                "window_duration_mins": w.window_duration_mins,
                            }
                            for w in m.windows
                        ],
                    }
                    for m in usage.models
                ],
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _query_rpc(self, codex_bin: str) -> Optional[Dict[str, Any]]:
        """Perform cross-platform stdio JSON-RPC handshake with codex app-server."""
        # On Windows, need shell=True or proper command resolving if codex is a .cmd
        use_shell = sys.platform == "win32" and codex_bin.lower().endswith(".cmd")
        popen_kwargs: Dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "shell": use_shell,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            popen_kwargs["startupinfo"] = si

        try:
            proc = subprocess.Popen(
                [codex_bin, "app-server"],
                **popen_kwargs,
            )
        except Exception as e:
            sys.stderr.write(f"Failed to spawn codex app-server: {e}\n")
            return None

        line_queue: queue.Queue[str] = queue.Queue()

        def reader():
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if line.strip():
                        line_queue.put(line)
            except Exception:
                pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        def send(msg: Dict[str, Any]):
            try:
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
            except Exception:
                pass

        result: Optional[Dict[str, Any]] = None
        plan_type: Optional[str] = None
        deadline = time.monotonic() + RPC_TIMEOUT

        try:
            # 1. Send initialize
            send({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "desktop-usage-widget", "version": "0.1.0"}},
            })

            while time.monotonic() < deadline and result is None:
                try:
                    raw = line_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                # Capture account plan update notifications
                if msg.get("method") == "account/updated":
                    params = msg.get("params", {})
                    plan_type = params.get("planType")

                if msg.get("id") == 1:
                    # Initialized handshake
                    send({"jsonrpc": "2.0", "method": "initialized"})
                    time.sleep(0.3)
                    send({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}})
                elif msg.get("id") == 2:
                    result = msg.get("result")
                    break
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass

        if result and plan_type and "planType" not in result:
            result["planType"] = plan_type
        return result

    def collect(self, force: bool = False) -> ProviderUsage:
        """Collect real-time Codex usage and limits."""
        if not force:
            cached = self._read_cache()
            if cached:
                self._last_result = cached
                return cached

        codex_bin = find_codex_bin()
        if not codex_bin:
            pu = ProviderUsage(
                provider_id="codex",
                display_name="OpenAI Codex",
                available=False,
                error="Codex CLI not found in PATH or standard directories",
            )
            self._last_result = pu
            return pu

        res = self._query_rpc(codex_bin)
        if not res:
            pu = ProviderUsage(
                provider_id="codex",
                display_name="OpenAI Codex",
                available=False,
                error="Failed to connect to codex app-server RPC",
            )
            self._last_result = pu
            return pu

        rate_limits = res.get("rateLimits", {})
        by_limit_id = res.get("rateLimitsByLimitId", {})
        plan_tier = rate_limits.get("planType") or res.get("planType")

        # Primary & Secondary windows
        primary_data = rate_limits.get("primary") or {}
        secondary_data = rate_limits.get("secondary") or {}

        primary_w = UsageWindow(
            name="5-Hour Limit",
            used_pct=float(primary_data.get("usedPercent", 0)),
            resets_at=int(primary_data.get("resetsAt", 0)),
            window_duration_mins=primary_data.get("windowDurationMins", 300),
        )

        secondary_w = None
        if secondary_data:
            secondary_w = UsageWindow(
                name="Weekly Limit",
                used_pct=float(secondary_data.get("usedPercent", 0)),
                resets_at=int(secondary_data.get("resetsAt", 0)),
                window_duration_mins=secondary_data.get("windowDurationMins", 10080),
            )

        # Parse specific model allowances
        models: list[ModelAllowance] = []

        # Codex main agent allowance
        models.append(
            ModelAllowance(
                model_name="Codex CLI",
                windows=[primary_w] + ([secondary_w] if secondary_w else []),
                tier_info=plan_tier.capitalize() if plan_tier else None,
                description="Core OpenAI Codex coding agent quota",
            )
        )

        # Base model inference limits (e.g., GPT-5.6 Luna / Reserve)
        base_inf = by_limit_id.get("base_model_inference")
        if base_inf:
            model_name = base_inf.get("normalModelSlug") or base_inf.get("limitName") or "Base Model Inference"
            b_prim = base_inf.get("primary") or {}
            w_list = []
            if b_prim:
                w_list.append(
                    UsageWindow(
                        name="Model Inference Window",
                        used_pct=float(b_prim.get("usedPercent", 0)),
                        resets_at=int(b_prim.get("resetsAt", 0)),
                        window_duration_mins=b_prim.get("windowDurationMins", 10080),
                    )
                )
            models.append(
                ModelAllowance(
                    model_name=f"Reserve: {model_name}",
                    windows=w_list,
                    tier_info=base_inf.get("limitName"),
                    description=f"Model-specific allowance for {model_name}",
                )
            )

        # Credits
        credits_data = rate_limits.get("credits") or {}
        credits_str = None
        if credits_data.get("hasCredits"):
            credits_str = f"Balance: {credits_data.get('balance', '0')}"

        email, name = self.find_account_info()
        acc_tag = f" ({email.split('@')[0]})" if email else (f" ({name})" if name else "")

        usage = ProviderUsage(
            provider_id="codex",
            display_name=f"OpenAI Codex{acc_tag}",
            available=True,
            account_email=email,
            account_name=name,
            primary_window=primary_w,
            secondary_window=secondary_w,
            models=models,
            credits_balance=credits_str,
            plan_tier=plan_tier.capitalize() if plan_tier else None,
            last_updated=time.time(),
            extra_details={"raw_rpc": res, "email": email, "name": name},
        )
        self._write_cache(usage)
        self._last_result = usage
        return usage
