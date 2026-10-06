from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import get_cache_dir
from src.models import ModelAllowance, ProviderUsage, UsageWindow

CACHE_FILE = "antigravity_cache.json"


def _iso_to_epoch(val: Any) -> int:
    if not isinstance(val, str) or not val:
        return 0
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def find_agy_bin() -> Optional[str]:
    """Find the agy CLI binary across Linux and Windows."""
    which = shutil.which("agy")
    if which:
        return which

    candidates = []
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            p = Path(local_app)
            candidates.extend([
                p / "agy" / "bin" / "agy.exe",
                p / "antigravity" / "agy.exe",
                p / "Programs" / "antigravity" / "agy.exe",
            ])
    else:
        home = Path.home()
        candidates.extend([
            home / ".local" / "bin" / "agy",
            Path("/usr/local/bin/agy"),
            home / ".gemini" / "antigravity-cli" / "bin" / "agy",
        ])

    for c in candidates:
        if os.path.exists(c):
            return str(c)
    return None


class AntigravityCollector:
    """Collects Google Antigravity usage, model quotas, and credits."""

    def __init__(self, cache_ttl_seconds: int = 120):
        self.cache_ttl = cache_ttl_seconds
        self._last_result: Optional[ProviderUsage] = None

    @classmethod
    def find_account_info(cls) -> tuple[Optional[str], Optional[str]]:
        """Extract user email and display name from antigravity-oauth-token."""
        import base64

        candidates = [
            Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token",
            Path.home() / ".gemini" / "antigravity-cli" / "settings.json",
        ]
        if sys.platform == "win32":
            user_profile = os.environ.get("USERPROFILE")
            if user_profile:
                candidates.append(Path(user_profile) / ".gemini" / "antigravity-cli" / "antigravity-oauth-token")
            for root in (r"\\wsl.localhost", r"\\wsl$"):
                try:
                    for distro in Path(root).iterdir():
                        for home in (distro / "home").iterdir():
                            candidates.append(home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token")
                except Exception:
                    pass

        for c in candidates:
            if not c.exists():
                continue
            try:
                data = json.loads(c.read_text(encoding="utf-8", errors="replace"))
                idt = data.get("id_token") or data.get("token", {}).get("id_token")
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
                provider_id="antigravity",
                display_name=d.get("display_name", "Antigravity"),
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

    def _run_cmd(self, agy_bin: str, slash_cmd: str) -> Optional[Dict[str, Any]]:
        """Run non-interactive agy print command."""
        try:
            home = str(Path.home())
            kwargs: Dict[str, Any] = {
                "cwd": home,
                "stdin": subprocess.DEVNULL,
                "capture_output": True,
                "text": True,
                "timeout": 20,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = subprocess.SW_HIDE
                kwargs["startupinfo"] = si

            res = subprocess.run(
                [agy_bin, "-p", slash_cmd, "--output-format", "json"],
                **kwargs,
            )
            if res.returncode == 0 and res.stdout.strip():
                return json.loads(res.stdout.strip())
        except Exception as e:
            sys.stderr.write(f"Error running agy {slash_cmd}: {e}\n")
        return None

    def collect(self, force: bool = False) -> ProviderUsage:
        """Collect Antigravity usage for all model groups and allowances."""
        if not force:
            cached = self._read_cache()
            if cached:
                self._last_result = cached
                return cached

        agy_bin = find_agy_bin()
        if not agy_bin:
            pu = ProviderUsage(
                provider_id="antigravity",
                display_name="Antigravity",
                available=False,
                error="Antigravity CLI (agy) not found in PATH",
            )
            self._last_result = pu
            return pu

        usage_payload = self._run_cmd(agy_bin, "/usage")
        credits_payload = self._run_cmd(agy_bin, "/credits") if usage_payload else None

        if not usage_payload:
            pu = ProviderUsage(
                provider_id="antigravity",
                display_name="Antigravity",
                available=False,
                error="Failed to query Antigravity /usage",
            )
            self._last_result = pu
            return pu

        cmd_data = usage_payload.get("command", {}).get("data", {})
        groups = cmd_data.get("groups", [])

        models: List[ModelAllowance] = []
        primary_w: Optional[UsageWindow] = None
        secondary_w: Optional[UsageWindow] = None

        for group in groups:
            g_name = group.get("name", "Model Group")
            g_desc = group.get("description", "")
            buckets = group.get("buckets", [])

            group_windows: List[UsageWindow] = []
            for b in buckets:
                rem_frac = float(b.get("remaining_fraction", 1.0))
                used_pct = max(0.0, min(100.0, (1.0 - rem_frac) * 100.0))
                window_type = b.get("window", "")
                dur_mins = 300 if window_type == "5h" else 10080
                w = UsageWindow(
                    name=b.get("name", "Limit"),
                    used_pct=used_pct,
                    resets_at=_iso_to_epoch(b.get("reset_time")),
                    resets_iso=b.get("reset_time"),
                    window_duration_mins=dur_mins,
                )
                group_windows.append(w)

                # Set primary/secondary from the primary group (Gemini Models)
                if "Gemini" in g_name:
                    if window_type == "5h":
                        primary_w = w
                    elif window_type == "weekly":
                        secondary_w = w

            models.append(
                ModelAllowance(
                    model_name=g_name,
                    windows=group_windows,
                    description=g_desc,
                )
            )

        # Credits
        credits_str = "0 Credits"
        if credits_payload:
            c_data = credits_payload.get("command", {}).get("data", {})
            rem_credits = c_data.get("remaining_credits", 0)
            credits_str = f"{rem_credits} Credits"

        email, name = self.find_account_info()
        acc_tag = f" ({email.split('@')[0]})" if email else (f" ({name})" if name else "")

        usage = ProviderUsage(
            provider_id="antigravity",
            display_name=f"Antigravity{acc_tag}",
            available=True,
            account_email=email,
            account_name=name,
            primary_window=primary_w,
            secondary_window=secondary_w,
            models=models,
            credits_balance=credits_str,
            plan_tier="Antigravity Pro",
            last_updated=time.time(),
            extra_details={"raw_usage": usage_payload, "raw_credits": credits_payload, "email": email, "name": name},
        )
        self._write_cache(usage)
        self._last_result = usage
        return usage
