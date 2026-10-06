from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import get_cache_dir
from src.models import ModelAllowance, ProviderUsage, UsageWindow

CACHE_FILE = "claude_cache.json"


def _iso_to_epoch(val: Any) -> int:
    if not isinstance(val, str) or not val:
        return 0
    try:
        dt = datetime.fromisoformat(val)
        return int(dt.timestamp())
    except Exception:
        return 0


def _pct(val: Any) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return max(0.0, min(100.0, f))
    except (TypeError, ValueError):
        return 0.0


class ClaudeCollector:
    """Collects Claude Code usage and model allowances."""

    def __init__(self, cache_ttl_seconds: int = 60):
        self.cache_ttl = cache_ttl_seconds
        self._last_result: Optional[ProviderUsage] = None
        self._rate_limited_until: float = 0.0

    @staticmethod
    def _claude_dirs() -> list:
        """Candidate ~/.claude directories. On Windows this includes WSL homes,
        since Claude Code is often run inside WSL and refreshes its token there."""
        dirs = []
        custom_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        if custom_dir:
            dirs.append(Path(custom_dir))
        dirs.append(Path.home() / ".claude")
        if sys.platform == "win32":
            for root in (r"\\wsl.localhost", r"\\wsl$"):
                try:
                    distros = [d for d in Path(root).iterdir()]
                except Exception:
                    distros = [Path(root) / "Ubuntu"]
                found = False
                for distro in distros:
                    try:
                        for home in (distro / "home").iterdir():
                            d = home / ".claude"
                            if (d / ".credentials.json").exists():
                                dirs.append(d)
                                found = True
                    except Exception:
                        continue
                if found:
                    break
        return dirs

    @classmethod
    def find_account_info(cls) -> tuple[Optional[str], Optional[str]]:
        """Extract user email and display name from ~/.claude.json or candidate directories."""
        candidates = []
        for d in cls._claude_dirs():
            candidates.append(d.parent / ".claude.json")
            candidates.append(d / "settings.json")
            candidates.append(d / ".credentials.json")
        candidates.append(Path.home() / ".claude.json")

        for c in candidates:
            if not c.exists():
                continue
            try:
                data = json.loads(c.read_text(encoding="utf-8", errors="replace"))
                oa = data.get("oauthAccount", {})
                email = oa.get("emailAddress") or data.get("email") or data.get("emailAddress")
                name = oa.get("displayName") or oa.get("fullName") or data.get("name")
                if email or name:
                    return email, name
            except Exception:
                continue
        return None, None

    def find_credentials(self) -> Optional[Dict[str, Any]]:
        """Find the freshest Claude OAuth credentials (env, local, or WSL)."""
        env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if env_token:
            return {"accessToken": env_token, "subscriptionType": "OAuth (Env)"}

        best: Optional[Dict[str, Any]] = None
        best_exp = -1.0
        for d in self._claude_dirs():
            cred_path = d / ".credentials.json"
            try:
                if not cred_path.exists():
                    continue
                with open(cred_path, "r", encoding="utf-8") as f:
                    oauth = json.load(f).get("claudeAiOauth")
                if isinstance(oauth, dict) and oauth.get("accessToken"):
                    exp = float(oauth.get("expiresAt") or 0)
                    if exp > best_exp:
                        best, best_exp = oauth, exp
            except Exception:
                continue
        return best

    def get_local_quota_limits(self) -> Optional[Dict[str, Any]]:
        """
        Inspect recent Claude Code session transcripts (~/.claude/projects/*/*.jsonl)
        for any quotaLimits rejection records.
        """
        now = time.time()
        jsonl_files = []
        for claude_dir in self._claude_dirs():
            projects_dir = claude_dir / "projects"
            try:
                if projects_dir.exists():
                    jsonl_files.extend(projects_dir.glob("*/*.jsonl"))
            except Exception:
                continue
        try:
            jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            return None

        for p in jsonl_files[:15]:
            try:
                if now - p.stat().st_mtime > 86400:
                    continue
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    if "quotaLimits" in line:
                        entry = json.loads(line)
                        ql = entry.get("quotaLimits")
                        if isinstance(ql, dict):
                            return ql
            except Exception:
                continue
        return None

    def fetch_api(self, token: str) -> Dict[str, Any]:
        """Fetch rate limits and allowances from Anthropic OAuth usage API."""
        if time.time() < self._rate_limited_until:
            return {"error": "Rate limited by Anthropic API (cooling down)", "rate_limited": True}

        url = "https://api.anthropic.com/api/oauth/usage"
        req = Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-code/2.1.289",
            },
        )
        try:
            with urlopen(req, timeout=10) as resp:
                raw = resp.read(65536).decode("utf-8", errors="replace")
                return json.loads(raw)
        except HTTPError as e:
            if e.code == 401:
                return {"error": "Credentials expired -- re-authenticate with 'claude'"}
            if e.code == 429:
                self._rate_limited_until = time.time() + 120.0
                return {"error": "Rate limited by Anthropic API", "rate_limited": True}
            return {"error": f"HTTP error {e.code}"}
        except (URLError, OSError, TimeoutError) as e:
            return {"error": f"Network error: {e}"}
        except json.JSONDecodeError:
            return {"error": "Malformed JSON from usage API"}

    def _read_cache(self, ignore_ttl: bool = False) -> Optional[ProviderUsage]:
        cache_path = get_cache_dir() / CACHE_FILE
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            # Reconstruct
            ts = d.get("cached_at", 0)
            if not ignore_ttl and time.time() - ts > self.cache_ttl:
                return None
            pu = ProviderUsage(
                provider_id="claude",
                display_name=d.get("display_name", "Claude Code"),
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
        if not usage.available:
            return
        # Never overwrite valid cache with zeroed values on error
        if usage.error and usage.primary_window and usage.primary_window.used_pct == 0.0:
            return
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

    def collect(self, force: bool = False) -> ProviderUsage:
        """Collect real-time Claude Code usage."""
        local_quota = self.get_local_quota_limits()
        is_local_rejected = False
        local_resets_at = 0
        if local_quota and local_quota.get("status") == "rejected":
            local_resets_at = int(local_quota.get("resetsAt", 0))
            if local_resets_at > time.time():
                is_local_rejected = True

        if not force:
            cached = self._read_cache(ignore_ttl=False)
            if cached:
                if is_local_rejected and cached.primary_window:
                    cached.primary_window.used_pct = 100.0
                    cached.primary_window.resets_at = local_resets_at
                    cached.primary_window.severity = "critical"
                    cached.error = "Session limit reached"
                self._last_result = cached
                return cached

        creds = self.find_credentials()
        if not creds:
            cached = self._read_cache(ignore_ttl=True)
            if cached:
                self._last_result = cached
                return cached
            pu = ProviderUsage(
                provider_id="claude",
                display_name="Claude Code",
                available=False,
                error="No credentials found -- run 'claude' to log in",
            )
            self._last_result = pu
            return pu

        token = creds.get("accessToken", "")
        plan_tier = creds.get("subscriptionType") or creds.get("rateLimitTier")

        api_res = self.fetch_api(token)

        # Handle API Error or 429 Rate Limit
        if "error" in api_res:
            is_rate_limit = bool(api_res.get("rate_limited"))

            # 1. Check if we have an in-memory previous result
            if self._last_result and self._last_result.primary_window:
                if is_local_rejected:
                    self._last_result.primary_window.used_pct = 100.0
                    self._last_result.primary_window.resets_at = local_resets_at
                    self._last_result.primary_window.severity = "critical"
                    self._last_result.error = "Session limit reached"
                else:
                    self._last_result.error = "Rate limited (cached data)" if is_rate_limit else api_res["error"]
                return self._last_result

            # 2. Check disk cache (even expired TTL)
            cached = self._read_cache(ignore_ttl=True)
            if cached and cached.primary_window and (cached.primary_window.used_pct > 0 or (cached.secondary_window and cached.secondary_window.used_pct > 0)):
                if is_local_rejected:
                    cached.primary_window.used_pct = 100.0
                    cached.primary_window.resets_at = local_resets_at
                    cached.primary_window.severity = "critical"
                    cached.error = "Session limit reached"
                else:
                    cached.error = "Rate limited (cached data)" if is_rate_limit else api_res["error"]
                self._last_result = cached
                return cached

            # 3. If local quota is rejected, synthesize ProviderUsage directly
            if is_local_rejected:
                primary_w = UsageWindow(
                    name="5-Hour Session",
                    used_pct=100.0,
                    resets_at=local_resets_at,
                    window_duration_mins=300,
                    severity="critical",
                )
                secondary_w = UsageWindow(
                    name="Weekly Limit",
                    used_pct=67.0,
                    resets_at=local_resets_at + 86400 * 2,
                    window_duration_mins=10080,
                    severity="normal",
                )
                pu = ProviderUsage(
                    provider_id="claude",
                    display_name="Claude Code",
                    available=True,
                    primary_window=primary_w,
                    secondary_window=secondary_w,
                    models=[
                        ModelAllowance(
                            model_name="Claude Code Engine",
                            windows=[primary_w, secondary_w],
                            tier_info=plan_tier or "pro",
                            description="Standard agent session window",
                        )
                    ],
                    plan_tier=plan_tier or "pro",
                    last_updated=time.time(),
                    error="Session limit reached",
                )
                self._last_result = pu
                return pu

            # 4. Fallback error ProviderUsage
            pu = ProviderUsage(
                provider_id="claude",
                display_name="Claude Code",
                available=False,
                error=api_res["error"],
                plan_tier=plan_tier,
            )
            self._last_result = pu
            return pu

        # Parse windows on successful API response
        five = api_res.get("five_hour") or {}
        seven = api_res.get("seven_day") or {}
        extra = api_res.get("extra_usage") or {}
        spend = api_res.get("spend") or {}

        session_pct = _pct(five.get("utilization", 0.0))
        weekly_pct = _pct(seven.get("utilization", 0.0))
        session_reset = five.get("resets_at")
        weekly_reset = seven.get("resets_at")

        # Also check explicit limits array if present
        for limit in api_res.get("limits", []):
            kind = limit.get("kind")
            if kind == "session":
                limit_pct = _pct(limit.get("percent", 0.0))
                if limit_pct > session_pct or not session_reset:
                    session_pct = limit_pct
                if limit.get("resets_at"):
                    session_reset = limit.get("resets_at")
            elif kind in ("weekly", "weekly_all"):
                limit_pct = _pct(limit.get("percent", 0.0))
                if limit_pct > weekly_pct or not weekly_reset:
                    weekly_pct = limit_pct
                if limit.get("resets_at"):
                    weekly_reset = limit.get("resets_at")

        session_resets_at = _iso_to_epoch(session_reset)
        if is_local_rejected:
            session_pct = max(session_pct, 100.0)
            session_resets_at = local_resets_at

        primary_w = UsageWindow(
            name="5-Hour Session",
            used_pct=session_pct,
            resets_at=session_resets_at,
            resets_iso=session_reset,
            window_duration_mins=300,
            severity="critical" if session_pct >= 90.0 else ("warning" if session_pct >= 75.0 else "normal"),
        )

        secondary_w = UsageWindow(
            name="Weekly Limit",
            used_pct=weekly_pct,
            resets_at=_iso_to_epoch(weekly_reset),
            resets_iso=weekly_reset,
            window_duration_mins=10080,
            severity="critical" if weekly_pct >= 90.0 else ("warning" if weekly_pct >= 75.0 else "normal"),
        )

        # Parse model-specific / scoped allowances
        models: list[ModelAllowance] = []

        # Breakdown by product (Claude Code vs Chat vs Cowork)
        breakdown = api_res.get("seven_day_breakdown", {}).get("rows", [])
        breakdown_desc = ", ".join(f"{r.get('display_name')}: {r.get('percent', 0)}%" for r in breakdown)

        models.append(
            ModelAllowance(
                model_name="Claude Code Engine",
                windows=[primary_w, secondary_w],
                tier_info=plan_tier,
                description=breakdown_desc if breakdown_desc else "Standard agent session window",
            )
        )

        # Check for model-scoped limits in `limits` array
        for limit in api_res.get("limits", []):
            scope = limit.get("scope") or {}
            model_info = scope.get("model") or {}
            name = model_info.get("display_name")
            if name:
                models.append(
                    ModelAllowance(
                        model_name=name,
                        windows=[
                            UsageWindow(
                                name=f"{name} Limit",
                                used_pct=_pct(limit.get("percent", 0)),
                                resets_at=_iso_to_epoch(limit.get("resets_at")),
                                resets_iso=limit.get("resets_at"),
                            )
                        ],
                    )
                )

        # Credits or spend
        credits_str = None
        if spend.get("enabled"):
            used = spend.get("used", {}).get("amount_minor", 0) / 100.0
            currency = spend.get("used", {}).get("currency", "USD")
            credits_str = f"{currency} {used:.2f}"
        elif extra.get("is_enabled"):
            credits_str = f"Extra: {extra.get('used_credits', 0)} credits"

        email, name = self.find_account_info()
        acc_tag = f" ({email.split('@')[0]})" if email else (f" ({name})" if name else "")

        usage = ProviderUsage(
            provider_id="claude",
            display_name=f"Claude Code{acc_tag}",
            available=True,
            account_email=email,
            account_name=name,
            primary_window=primary_w,
            secondary_window=secondary_w,
            models=models,
            credits_balance=credits_str,
            plan_tier=plan_tier,
            last_updated=time.time(),
            extra_details={"raw_payload": api_res, "email": email, "name": name},
        )
        self._write_cache(usage)
        self._last_result = usage
        return usage
