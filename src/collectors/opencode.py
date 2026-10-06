from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import get_cache_dir
from src.models import ModelAllowance, ProviderUsage, UsageWindow

CACHE_FILE = "opencode_cache.json"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def find_opencode_db() -> Optional[Path]:
    """Find the OpenCode SQLite database across Linux, macOS, WSL, and Windows."""
    candidates: List[Path] = []

    # 1. Custom env var
    custom = os.environ.get("OPENCODE_DB_PATH")
    if custom:
        candidates.append(Path(custom))

    # 2. Local standard home
    home = Path.home()
    candidates.append(home / ".local" / "share" / "opencode" / "opencode.db")

    # 3. Windows & WSL paths
    if sys.platform == "win32":
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            p = Path(user_profile)
            candidates.append(p / ".local" / "share" / "opencode" / "opencode.db")
            candidates.append(p / "AppData" / "Local" / "opencode" / "opencode.db")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "opencode" / "opencode.db")
    else:
        # Running inside Linux / WSL - check mounted Windows drive
        for win_mnt in Path("/mnt").glob("*/Users/*"):
            candidates.append(win_mnt / ".local" / "share" / "opencode" / "opencode.db")
            candidates.append(win_mnt / "AppData" / "Local" / "opencode" / "opencode.db")

    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


class OpenCodeCollector:
    """
    Collects usage and rate limits from OpenCode AI.
    Specifically checks for connected ChatGPT / OpenAI OAuth subscriptions
    inside OpenCode's credential store and queries real-time allowance windows.
    """

    def __init__(
        self,
        cache_ttl_seconds: int = 60,
        db_path: Optional[str | Path] = None,
        cache_path: Optional[str | Path] = None,
    ):
        self.cache_ttl = cache_ttl_seconds
        self.db_path = Path(db_path) if db_path else None
        self.cache_path = Path(cache_path) if cache_path else (get_cache_dir() / CACHE_FILE)
        self._last_result: Optional[ProviderUsage] = None

    def is_available(self) -> bool:
        """Check if OpenCode DB is available."""
        p = self.db_path or find_opencode_db()
        return p is not None and p.exists()

    def _read_cache(self, ignore_ttl: bool = False) -> Optional[ProviderUsage]:
        if not self.cache_path.exists():
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            ts = d.get("cached_at", 0)
            if not ignore_ttl and (time.time() - ts > self.cache_ttl):
                return None
            pu = ProviderUsage(
                provider_id="opencode",
                display_name=d.get("display_name", "OpenCode (OpenAI)"),
                available=d.get("available", True),
                error=d.get("error"),
                account_email=d.get("account_email") or d.get("extra_details", {}).get("email"),
                account_name=d.get("account_name"),
                plan_tier=d.get("plan_tier"),
                credits_balance=d.get("credits_balance"),
                last_updated=ts,
                extra_details=d.get("extra_details", {}),
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
            if d.get("models"):
                pu.models = []
                for m in d["models"]:
                    windows = [
                        UsageWindow(
                            name=w["name"],
                            used_pct=w["used_pct"],
                            resets_at=w["resets_at"],
                            window_duration_mins=w.get("window_duration_mins"),
                        )
                        for w in m.get("windows", [])
                    ]
                    pu.models.append(
                        ModelAllowance(
                            model_name=m["model_name"],
                            windows=windows,
                            tier_info=m.get("tier_info"),
                            description=m.get("description"),
                        )
                    )
            return pu
        except Exception:
            return None

    def _write_cache(self, pu: ProviderUsage) -> None:
        cache_path = self.cache_path
        try:
            d: Dict[str, Any] = {
                "cached_at": pu.last_updated,
                "display_name": pu.display_name,
                "available": pu.available,
                "error": pu.error,
                "account_email": pu.account_email,
                "account_name": pu.account_name,
                "plan_tier": pu.plan_tier,
                "credits_balance": pu.credits_balance,
                "extra_details": pu.extra_details,
            }
            if pu.primary_window:
                d["primary_window"] = {
                    "name": pu.primary_window.name,
                    "used_pct": pu.primary_window.used_pct,
                    "resets_at": pu.primary_window.resets_at,
                    "window_duration_mins": pu.primary_window.window_duration_mins,
                }
            if pu.secondary_window:
                d["secondary_window"] = {
                    "name": pu.secondary_window.name,
                    "used_pct": pu.secondary_window.used_pct,
                    "resets_at": pu.secondary_window.resets_at,
                    "window_duration_mins": pu.secondary_window.window_duration_mins,
                }
            if pu.models:
                d["models"] = [
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
                    for m in pu.models
                ]
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
        except Exception:
            pass

    def _read_credentials(self, db_path: Path, integration_id: str = "openai") -> Optional[Dict[str, Any]]:
        """Safely read active credential for a given integration from OpenCode's sqlite database."""
        import sqlite3

        temp_dir = tempfile.gettempdir()
        temp_db = Path(temp_dir) / f"opencode_read_{os.getpid()}.db"
        try:
            shutil.copy2(db_path, temp_db)
            wal_file = db_path.parent / (db_path.name + "-wal")
            if wal_file.exists():
                shutil.copy2(wal_file, Path(temp_dir) / f"{temp_db.name}-wal")
        except Exception:
            temp_db = db_path

        try:
            conn = sqlite3.connect(str(temp_db), timeout=3.0)
            c = conn.cursor()
            c.execute(
                "SELECT value FROM credential WHERE integration_id=? AND active=1 ORDER BY time_updated DESC LIMIT 1;",
                (integration_id,),
            )
            row = c.fetchone()
            conn.close()
            if not row or not row[0]:
                return None
            data = json.loads(row[0])
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None
        finally:
            if temp_db != db_path:
                try:
                    temp_db.unlink(missing_ok=True)
                    (Path(temp_dir) / f"{temp_db.name}-wal").unlink(missing_ok=True)
                    (Path(temp_dir) / f"{temp_db.name}-shm").unlink(missing_ok=True)
                except Exception:
                    pass

    def _read_all_active_integrations(self, db_path: Path) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Read all active credentials and their integration types from OpenCode."""
        import sqlite3

        temp_dir = tempfile.gettempdir()
        temp_db = Path(temp_dir) / f"opencode_all_{os.getpid()}.db"
        try:
            shutil.copy2(db_path, temp_db)
            wal_file = db_path.parent / (db_path.name + "-wal")
            if wal_file.exists():
                shutil.copy2(wal_file, Path(temp_dir) / f"{temp_db.name}-wal")
        except Exception:
            temp_db = db_path

        integrations: List[Tuple[str, str, Dict[str, Any]]] = []
        try:
            conn = sqlite3.connect(str(temp_db), timeout=3.0)
            c = conn.cursor()
            c.execute("SELECT integration_id, label, value FROM credential WHERE active=1 ORDER BY time_updated DESC;")
            for row in c.fetchall():
                integ_id, label, val_raw = row
                try:
                    d = json.loads(val_raw)
                    if isinstance(d, dict):
                        integrations.append((integ_id, label, d))
                except Exception:
                    pass
            conn.close()
        except Exception:
            pass
        finally:
            if temp_db != db_path:
                try:
                    temp_db.unlink(missing_ok=True)
                    (Path(temp_dir) / f"{temp_db.name}-wal").unlink(missing_ok=True)
                    (Path(temp_dir) / f"{temp_db.name}-shm").unlink(missing_ok=True)
                except Exception:
                    pass

        return integrations

    def _refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        """Refresh an expired OAuth token with auth.openai.com."""
        body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OPENAI_CLIENT_ID,
        }
        import urllib.parse

        data = urllib.parse.urlencode(body).encode("utf-8")
        req = Request(
            OPENAI_TOKEN_URL,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def _fetch_wham_usage(self, access_token: str, account_id: Optional[str]) -> Dict[str, Any]:
        """Query OpenAI ChatGPT wham usage endpoint for real-time quota allowance."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id

        req = Request(OPENAI_USAGE_URL, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            return {"error": f"HTTP {e.code}", "status_code": e.code}
        except URLError as e:
            return {"error": f"Network error: {e.reason}"}
        except Exception as e:
            return {"error": str(e)}

    def collect(self, force: bool = False) -> ProviderUsage:
        """Collect current rate limits, quotas and allowances for OpenCode's connected OpenAI sub."""
        db_path = self.db_path or find_opencode_db()
        if not db_path or not db_path.exists():
            pu = ProviderUsage(
                provider_id="opencode",
                display_name="OpenCode",
                available=False,
                error="OpenCode database not found",
            )
            self._last_result = pu
            return pu

        if not force:
            cached = self._read_cache()
            if cached:
                self._last_result = cached
                return cached

        creds = self._read_credentials(db_path)
        if not creds:
            pu = ProviderUsage(
                provider_id="opencode",
                display_name="OpenCode",
                available=False,
                error="No connected OpenAI subscription in OpenCode",
            )
            self._last_result = pu
            return pu

        access = creds.get("access")
        refresh = creds.get("refresh")
        expires = creds.get("expires")
        metadata = creds.get("metadata") or {}
        account_id = metadata.get("accountID")

        # Check if access token is expired or expiring within 60 seconds
        if expires and refresh:
            now_ms = time.time() * 1000.0
            if expires < now_ms + 60000:
                ref_res = self._refresh_token(refresh)
                if ref_res and "access_token" in ref_res:
                    access = ref_res["access_token"]

        if not access:
            pu = ProviderUsage(
                provider_id="opencode",
                display_name="OpenCode (OpenAI)",
                available=False,
                error="Missing access token for OpenCode provider",
            )
            self._last_result = pu
            return pu

        res = self._fetch_wham_usage(access, account_id)
        if "error" in res:
            # Check cached result fallback
            cached = self._read_cache(ignore_ttl=True)
            if cached and cached.primary_window:
                cached.error = f"Rate limited: {res['error']}"
                self._last_result = cached
                return cached
            pu = ProviderUsage(
                provider_id="opencode",
                display_name="OpenCode (OpenAI)",
                available=False,
                error=res["error"],
            )
            self._last_result = pu
            return pu

        rate_limit = res.get("rate_limit") or {}
        plan_type = (res.get("plan_type") or "plus").capitalize()
        email = res.get("email")

        # 1. Primary rolling 5h session window
        primary_w: Optional[UsageWindow] = None
        pw_data = rate_limit.get("primary_window")
        if pw_data:
            used_pct = float(pw_data.get("used_percent", 0.0))
            resets_at = int(pw_data.get("reset_at", 0))
            if resets_at <= 0 and pw_data.get("reset_after_seconds"):
                resets_at = int(time.time() + float(pw_data["reset_after_seconds"]))
            win_duration = int(pw_data.get("limit_window_seconds", 18000) / 60)
            primary_w = UsageWindow(
                name="5-Hour Session",
                used_pct=used_pct,
                resets_at=resets_at,
                window_duration_mins=win_duration,
            )

        # 2. Secondary weekly pool window
        secondary_w: Optional[UsageWindow] = None
        sw_data = rate_limit.get("secondary_window")
        if sw_data:
            used_pct = float(sw_data.get("used_percent", 0.0))
            resets_at = int(sw_data.get("reset_at", 0))
            if resets_at <= 0 and sw_data.get("reset_after_seconds"):
                resets_at = int(time.time() + float(sw_data["reset_after_seconds"]))
            win_duration = int(sw_data.get("limit_window_seconds", 604800) / 60)
            secondary_w = UsageWindow(
                name="Weekly Limit",
                used_pct=used_pct,
                resets_at=resets_at,
                window_duration_mins=win_duration,
            )

        # Discover all connected integrations
        all_integrations = self._read_all_active_integrations(db_path)
        connected_labels = []
        for integ_id, label, val in all_integrations:
            if integ_id != "openai":
                connected_labels.append(label or integ_id)

        # 3. Model allowances & other connected providers in OpenCode
        models: List[ModelAllowance] = []
        if primary_w and secondary_w:
            models.append(
                ModelAllowance(
                    model_name="OpenCode OpenAI",
                    windows=[primary_w, secondary_w],
                    tier_info=plan_type,
                    description=f"{plan_type} subscription allowance via OpenCode",
                )
            )

        # Check if Claude / Anthropic is connected in OpenCode credentials
        claude_cred = self._read_credentials(db_path, integration_id="anthropic") or self._read_credentials(db_path, integration_id="claude")
        if claude_cred:
            # Query Anthropic usage if token available
            token = claude_cred.get("access") or claude_cred.get("accessToken") or claude_cred.get("key")
            if token:
                try:
                    c_req = Request(
                        "https://api.anthropic.com/api/oauth/usage",
                        headers={"Authorization": f"Bearer {token}", "User-Agent": "Claude-Code/2.1"},
                    )
                    with urlopen(c_req, timeout=5) as c_resp:
                        c_data = json.loads(c_resp.read().decode("utf-8"))
                        c_five = c_data.get("five_hour") or {}
                        c_week = c_data.get("seven_day") or {}
                        c_pw = UsageWindow(
                            name="Claude 5h Session",
                            used_pct=float(c_five.get("used_percent", 0.0)),
                            resets_at=int(time.time() + float(c_five.get("reset_after_seconds", 18000))),
                        )
                        c_sw = UsageWindow(
                            name="Claude Weekly Limit",
                            used_pct=float(c_week.get("used_percent", 0.0)),
                            resets_at=int(time.time() + float(c_week.get("reset_after_seconds", 604800))),
                        )
                        models.append(
                            ModelAllowance(
                                model_name="OpenCode Claude",
                                windows=[c_pw, c_sw],
                                tier_info="Connected Sub",
                                description="Claude Code / Anthropic subscription via OpenCode",
                            )
                        )
                except Exception:
                    pass

        model_usage = res.get("model_usage") or {}
        for m_name, m_info in model_usage.items():
            if isinstance(m_info, dict):
                models.append(
                    ModelAllowance(
                        model_name=m_name,
                        windows=[primary_w] if primary_w else [],
                        tier_info=plan_type,
                        description=f"Model allowance: {m_name}",
                    )
                )

        credits_obj = res.get("credits") or {}
        credits_bal = credits_obj.get("balance")

        # Build clean provider display name including connected accounts
        disp = f"OpenCode ({email.split('@')[0]})" if email else "OpenCode"
        if connected_labels:
            disp += f" + {', '.join(connected_labels[:2])}"

        pu = ProviderUsage(
            provider_id="opencode",
            display_name=disp,
            available=True,
            account_email=email,
            account_name=None,
            plan_tier=plan_type,
            primary_window=primary_w,
            secondary_window=secondary_w,
            models=models,
            credits_balance=str(credits_bal) if credits_bal is not None else None,
            last_updated=time.time(),
            extra_details={
                "email": email,
                "account_id": res.get("account_id"),
                "user_id": res.get("user_id"),
                "connected_integrations": [i[0] for i in all_integrations],
            },
        )
        self._last_result = pu
        self._write_cache(pu)
        return pu

    def get_usage(self, force: bool = False) -> ProviderUsage:
        """Alias for collect."""
        return self.collect(force=force)
