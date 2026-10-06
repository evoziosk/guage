from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def format_seconds_remaining(seconds: float) -> str:
    """Format seconds into human-readable countdown like '2h 15m' or '3d 4h'."""
    if seconds <= 0:
        return "Ready"
    secs = int(seconds)
    days = secs // 86400
    hours = (secs % 86400) // 3600
    mins = (secs % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    if mins > 0:
        return f"{mins}m"
    return f"{secs}s"


def format_reset_label(reset_val: str) -> str:
    """
    Format countdown with 'Reset ' prefix unless there is no time left (Ready).
    e.g. '1h 45m'      -> 'Reset 1h 45m'
         'Ready'       -> 'Ready'
         'ready'       -> 'Ready'
         'Reset ready' -> 'Ready'
         ''            -> 'Ready'
    """
    if not reset_val:
        return "Ready"
    clean = reset_val.replace("Reset ", "").replace("Reset", "").strip()
    if not clean or clean.lower() == "ready":
        return "Ready"
    return f"Reset {clean}"


@dataclass
class UsageWindow:
    name: str  # e.g., "5-Hour Session", "Weekly Limit"
    used_pct: float  # 0.0 - 100.0
    resets_at: int  # Unix timestamp in seconds
    resets_iso: Optional[str] = None
    window_duration_mins: Optional[int] = None
    severity: str = "normal"  # "normal", "warning" (>75%), "critical" (>90%)

    @property
    def remaining_pct(self) -> float:
        return max(0.0, 100.0 - self.used_pct)

    @property
    def formatted_reset(self) -> str:
        if self.resets_at <= 0:
            return "Ready"
        remaining = self.resets_at - time.time()
        return format_seconds_remaining(remaining)

    def calculate_severity(self) -> str:
        if self.used_pct >= 90.0:
            return "critical"
        if self.used_pct >= 75.0:
            return "warning"
        return "normal"

    def __post_init__(self):
        self.severity = self.calculate_severity()


@dataclass
class ModelAllowance:
    model_name: str  # e.g., "Gemini Flash / Pro", "Claude Opus", "GPT-5.6 Luna"
    windows: List[UsageWindow] = field(default_factory=list)
    tier_info: Optional[str] = None
    description: Optional[str] = None
    extra_data: Dict[str, Any] = field(default_factory=dict)

    def get_window(self, kind: str) -> Optional[UsageWindow]:
        """Find window by kind: '5h' (or 300) or 'weekly' (or 10080)."""
        kind_lower = kind.lower()
        for w in self.windows:
            if kind_lower in ("5h", "session", "300"):
                if w.window_duration_mins == 300 or "5" in w.name.lower():
                    return w
            elif kind_lower in ("weekly", "week", "10080"):
                if w.window_duration_mins == 10080 or "week" in w.name.lower():
                    return w
        return None


@dataclass
class ProviderUsage:
    provider_id: str  # "claude", "codex", "antigravity", "opencode"
    display_name: str  # "Claude Code", "OpenAI Codex", "Antigravity", "OpenCode"
    available: bool = True
    error: Optional[str] = None
    account_email: Optional[str] = None
    account_name: Optional[str] = None
    primary_window: Optional[UsageWindow] = None  # Typically 5-hour rolling session window
    secondary_window: Optional[UsageWindow] = None  # Typically weekly window
    models: List[ModelAllowance] = field(default_factory=list)
    credits_balance: Optional[str] = None
    plan_tier: Optional[str] = None
    last_updated: float = field(default_factory=time.time)
    extra_details: Dict[str, Any] = field(default_factory=dict)

    @property
    def account_identifier(self) -> Optional[str]:
        """Returns the username/handle prefix from email (e.g. 'alice' from 'alice@example.com') or account name."""
        if self.account_email:
            return self.account_email.split("@")[0]
        if self.account_name:
            return self.account_name
        return None

    def get_model(self, name_sub: str) -> Optional[ModelAllowance]:
        sub = name_sub.lower()
        for m in self.models:
            if sub in m.model_name.lower():
                return m
        return None

    @property
    def highest_severity(self) -> str:
        severities = ["normal"]
        if self.primary_window:
            severities.append(self.primary_window.severity)
        if self.secondary_window:
            severities.append(self.secondary_window.severity)
        for m in self.models:
            for w in m.windows:
                severities.append(w.severity)
        if "critical" in severities:
            return "critical"
        if "warning" in severities:
            return "warning"
        return "normal"


@dataclass
class ActiveSession:
    provider_id: str  # "claude", "codex", "antigravity", "opencode"
    session_id: str
    title: str
    status: str = "running"
    detail: Optional[str] = None
    cwd: Optional[str] = None
    started_at: Optional[float] = None
    updated_at: Optional[float] = None
    pid: Optional[int] = None
    model_id: Optional[str] = None

    @property
    def display_provider(self) -> str:
        names = {"claude": "Claude", "codex": "Codex", "antigravity": "AGY", "opencode": "OpenCode"}
        return names.get(self.provider_id, self.provider_id.capitalize())

    @property
    def short_model(self) -> Optional[str]:
        """Compact model name suitable for badges and ticker displays."""
        if not self.model_id:
            return None
        import re
        m = self.model_id.lower().strip()
        # Clean provider prefixes (e.g. "openai/gpt-6.1-sol" or "anthropic/claude-3.5-sonnet")
        if "/" in m:
            m = m.split("/")[-1]
        # Strip trailing annotations like (medium), (high), (low), (preview), etc.
        m = re.sub(r"\(.*?\)", "", m).strip()
        m = re.sub(r"-\d{8}$", "", m)  # Strip date stamps like -20241022 or -20250219
        if m.startswith("claude-"):
            m = m[len("claude-"):]

        mapping = {
            "sonnet-5-5": "Sonnet 5.5",
            "sonnet-5.5": "Sonnet 5.5",
            "sonnet-5": "Sonnet 5",
            "sonnet-4-6": "Sonnet 4.6",
            "sonnet-4.6": "Sonnet 4.6",
            "sonnet-4-5": "Sonnet 4.5",
            "sonnet-4.5": "Sonnet 4.5",
            "sonnet-3-7": "Sonnet 3.7",
            "sonnet-3.7": "Sonnet 3.7",
            "3-7-sonnet": "Sonnet 3.7",
            "3.7-sonnet": "Sonnet 3.7",
            "sonnet-3-5": "Sonnet 3.5",
            "sonnet-3.5": "Sonnet 3.5",
            "3-5-sonnet": "Sonnet 3.5",
            "3.5-sonnet": "Sonnet 3.5",
            "opus-5-5": "Opus 5.5",
            "opus-5.5": "Opus 5.5",
            "opus-4-6": "Opus 4.6",
            "opus-4.6": "Opus 4.6",
            "opus-5": "Opus 5",
            "opus-4-5": "Opus 4.5",
            "opus-4.5": "Opus 4.5",
            "haiku-4-5": "Haiku 4.5",
            "haiku-4.5": "Haiku 4.5",
            "haiku-3-5": "Haiku 3.5",
            "haiku-3.5": "Haiku 3.5",
            "gpt-6.1-sol": "GPT-6.1",
            "gpt-6.1": "GPT-6.1",
            "gpt-6-astra": "GPT-6",
            "gpt-6": "GPT-6",
            "gpt-5.6-luna": "GPT-5.6",
            "gpt-5.6-sol": "GPT-5.6",
            "gpt-5.6": "GPT-5.6",
            "gpt-5.5": "GPT-5.5",
            "gpt-4o": "GPT-4o",
            "gpt-4o-mini": "GPT-4o mini",
            "gemini 3.8 flash": "Gemini 3.8 Flash",
            "gemini-3.8-flash": "Gemini 3.8 Flash",
            "gemini 3.1 pro": "Gemini 3.1 Pro",
            "gemini-3.1-pro": "Gemini 3.1 Pro",
            "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
            "gemini-2.5-pro": "Gemini 2.5 Pro",
            "gemini-2.5-flash": "Gemini 2.5 Flash",
            "gemini-pro": "Gemini Pro",
            "gemini-flash": "Gemini Flash",
        }
        if m in mapping:
            return mapping[m]
        clean = m.replace("-preview", "").replace("-latest", "").strip()
        if clean in mapping:
            return mapping[clean]
        if clean.startswith("gpt-"):
            return "GPT-" + clean[4:].upper()
        if clean.startswith("gemini"):
            words = clean.replace("-", " ").split()
            return " ".join(w.capitalize() for w in words)
        return clean.capitalize()

    @property
    def project_name(self) -> Optional[str]:
        if self.cwd:
            from pathlib import Path
            return Path(self.cwd).name
        return None

    @property
    def notation(self) -> str:
        """Concise one-line notation for widget tickers and status strips."""
        parts = []
        if self.title:
            parts.append(self.title)
        if self.short_model:
            parts.append(f"[{self.short_model}]")
        if self.detail and self.detail != self.title:
            # If detail already has model, avoid duplicating
            det = self.detail
            if self.short_model and self.short_model.lower() in det.lower():
                det_parts = [p.strip() for p in det.split("•") if self.short_model.lower() not in p.lower()]
                if det_parts:
                    det = " • ".join(det_parts)
                else:
                    det = None
            if det:
                parts.append(det)
        elif self.project_name:
            parts.append(f"in {self.project_name}")
        return " • ".join(parts)


@dataclass
class AggregatedUsage:
    providers: Dict[str, ProviderUsage] = field(default_factory=dict)
    active_sessions: List[ActiveSession] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)

    def get_active_sessions(self, provider_id: Optional[str] = None) -> List[ActiveSession]:
        if not provider_id or provider_id == "all":
            return list(self.active_sessions)
        return [s for s in self.active_sessions if s.provider_id == provider_id]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        out = {
            "last_updated": self.last_updated,
            "active_sessions": [
                {
                    "provider_id": s.provider_id,
                    "session_id": s.session_id,
                    "title": s.title,
                    "status": s.status,
                    "detail": s.detail,
                    "cwd": s.cwd,
                    "started_at": s.started_at,
                    "updated_at": s.updated_at,
                    "pid": s.pid,
                    "notation": s.notation,
                }
                for s in self.active_sessions
            ],
            "providers": {},
        }
        for pid, p in self.providers.items():
            out["providers"][pid] = {
                "display_name": p.display_name,
                "available": p.available,
                "error": p.error,
                "account_email": p.account_email,
                "account_name": p.account_name,
                "account_identifier": p.account_identifier,
                "plan_tier": p.plan_tier,
                "credits_balance": p.credits_balance,
                "highest_severity": p.highest_severity,
                "primary_window": {
                    "name": p.primary_window.name,
                    "used_pct": p.primary_window.used_pct,
                    "remaining_pct": p.primary_window.remaining_pct,
                    "resets_at": p.primary_window.resets_at,
                    "formatted_reset": p.primary_window.formatted_reset,
                    "severity": p.primary_window.severity,
                } if p.primary_window else None,
                "secondary_window": {
                    "name": p.secondary_window.name,
                    "used_pct": p.secondary_window.used_pct,
                    "remaining_pct": p.secondary_window.remaining_pct,
                    "resets_at": p.secondary_window.resets_at,
                    "formatted_reset": p.secondary_window.formatted_reset,
                    "severity": p.secondary_window.severity,
                } if p.secondary_window else None,
                "models": [
                    {
                        "model_name": m.model_name,
                        "tier_info": m.tier_info,
                        "description": m.description,
                        "windows": [
                            {
                                "name": w.name,
                                "used_pct": w.used_pct,
                                "remaining_pct": w.remaining_pct,
                                "resets_at": w.resets_at,
                                "formatted_reset": w.formatted_reset,
                                "severity": w.severity,
                            }
                            for w in m.windows
                        ],
                    }
                    for m in p.models
                ],
            }
        return out
