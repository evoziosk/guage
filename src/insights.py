from __future__ import annotations

import json
import os
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from src.config import get_cache_dir
from src.models import AggregatedUsage, UsageWindow, format_seconds_remaining

WARN_PCT = 25.0
CRIT_PCT = 10.0

PROVIDER_LABELS = {"claude": "Claude", "codex": "Codex", "antigravity": "Antigravity", "opencode": "OpenCode"}
SHORT_LABELS = {"claude": "Claude", "codex": "Codex", "antigravity": "AGY", "opencode": "OpenCode"}

# Burn-rate sampling
_MAX_SAMPLES = 40
_WINDOW_SECS = 45 * 60
_MIN_SPAN_SECS = 180


def _level(remaining: float) -> int:
    """0 = healthy, 1 = warning (<=25%), 2 = critical (<=10%), 3 = depleted (0%)."""
    if remaining <= 0.5:
        return 3
    if remaining <= CRIT_PCT:
        return 2
    if remaining <= WARN_PCT:
        return 1
    return 0


class InsightEngine:
    """Tracks recent readings to estimate burn rate and detect threshold crossings.

    Purely in-memory and O(providers) per update, so it adds no measurable overhead.
    """

    def __init__(self) -> None:
        self._samples: Dict[Tuple[str, str], Deque[Tuple[float, float]]] = {}
        self._levels: Dict[Tuple[str, str], int] = {}
        self._seen_stamp: Dict[Tuple[str, str], float] = {}

    @staticmethod
    def _windows(usage: AggregatedUsage):
        for pid, pu in usage.providers.items():
            if not pu.available:
                continue
            if pu.primary_window:
                yield pid, "session", pu.primary_window, pu.last_updated
            if pu.secondary_window:
                yield pid, "weekly", pu.secondary_window, pu.last_updated
            for m in pu.models:
                w_5h = m.get_window("5h")
                if w_5h and w_5h != pu.primary_window:
                    yield pid, f"{m.model_name} 5h", w_5h, pu.last_updated
                w_wk = m.get_window("weekly")
                if w_wk and w_wk != pu.secondary_window:
                    yield pid, f"{m.model_name} weekly", w_wk, pu.last_updated

    def update(self, usage: AggregatedUsage) -> List[Tuple[str, str]]:
        """Ingest a snapshot. Returns a list of (title, message) alerts to show."""
        alerts: List[Tuple[str, str]] = []
        now = time.time()
        for pid, kind, win, stamp in self._windows(usage):
            key = (pid, kind)
            rem = win.remaining_pct

            # Only record genuinely new readings (cache hits repeat the same stamp)
            if self._seen_stamp.get(key) != stamp:
                self._seen_stamp[key] = stamp
                dq = self._samples.setdefault(key, deque(maxlen=_MAX_SAMPLES))
                if dq and rem > dq[-1][1] + 5.0:  # window reset -> start over
                    dq.clear()
                dq.append((now, rem))

            lvl = _level(rem)
            prev = self._levels.get(key)
            self._levels[key] = lvl
            if prev is None:
                continue  # baseline, never alert on first sight
            name = f"{PROVIDER_LABELS.get(pid, pid)} {kind}"
            if lvl > prev:
                reset = format_seconds_remaining(win.resets_at - now) if win.resets_at else ""
                tail = f" - resets in {reset}" if reset and reset != "Ready" else ""
                if lvl == 3:
                    alerts.append((f"{name} depleted", f"0% left{tail}"))
                else:
                    alerts.append((f"{name} running low", f"{int(rem)}% left{tail}"))
            elif prev >= 2 and lvl == 0:
                alerts.append((f"{name} is back", f"{int(rem)}% available again"))
        return alerts

    def eta_seconds(self, pid: str, kind: str = "session", win: Optional[UsageWindow] = None) -> Optional[float]:
        """Estimated seconds until empty at the recent pace, only if that is before the reset."""
        dq = self._samples.get((pid, kind))
        if not dq or len(dq) < 2:
            return None
        now = dq[-1][0]
        recent = [s for s in dq if now - s[0] <= _WINDOW_SECS]
        if len(recent) < 2:
            return None
        t0, r0 = recent[0]
        t1, r1 = recent[-1]
        span = t1 - t0
        drop = r0 - r1
        if span < _MIN_SPAN_SECS or drop < 0.5:
            return None
        eta = r1 / (drop / span)
        if win is not None and win.resets_at:
            until_reset = win.resets_at - time.time()
            if until_reset > 0 and eta >= until_reset:
                return None  # will reset before running out
        return eta

    def eta_text(self, pid: str, kind: str, win: Optional[UsageWindow]) -> Optional[str]:
        eta = self.eta_seconds(pid, kind, win)
        if eta is None or eta > 24 * 3600:
            return None
        return f"empty in ~{format_seconds_remaining(eta)} at this pace"


def lowest_remaining(usage: AggregatedUsage) -> Optional[Tuple[str, float]]:
    """(provider_id, remaining%) of the most constrained session window."""
    best: Optional[Tuple[str, float]] = None
    for pid, pu in usage.providers.items():
        if pu.available and pu.primary_window:
            rem = pu.primary_window.remaining_pct
            if best is None or rem < best[1]:
                best = (pid, rem)
    return best


def best_provider(usage: AggregatedUsage) -> Optional[Tuple[str, float]]:
    """Provider with the most headroom (limited by both session and weekly windows)."""
    best: Optional[Tuple[str, float]] = None
    for pid, pu in usage.providers.items():
        if not (pu.available and pu.primary_window):
            continue
        headroom = pu.primary_window.remaining_pct
        if pu.secondary_window:
            headroom = min(headroom, pu.secondary_window.remaining_pct)
        if best is None or headroom > best[1]:
            best = (pid, headroom)
    return best if best and best[1] > CRIT_PCT else None


def tray_summary(usage: AggregatedUsage) -> str:
    parts = []
    pids = ["claude", "codex", "antigravity"]
    if "opencode" in usage.providers and usage.providers["opencode"].available:
        pids.append("opencode")
    for pid in pids:
        pu = usage.providers.get(pid)
        if pu and pu.available and pu.primary_window:
            pw = pu.primary_window
            reset = pw.formatted_reset
            tail = "" if reset == "Ready" else f" (resets {reset})"
            parts.append(f"{SHORT_LABELS[pid]}: {int(pw.remaining_pct)}% left{tail}")
        else:
            parts.append(f"{SHORT_LABELS[pid]}: n/a")
    return "Guage\n" + "\n".join(parts)


def write_snapshot(usage: AggregatedUsage) -> None:
    """Write a small JSON file other tools (Waybar, Polybar, scripts) can read."""
    try:
        out = {"updated": usage.last_updated, "providers": {}}
        for pid, pu in usage.providers.items():
            entry = {"available": pu.available, "error": pu.error}
            for label, w in (("session", pu.primary_window), ("weekly", pu.secondary_window)):
                if w:
                    entry[label] = {
                        "remaining_pct": round(w.remaining_pct, 1),
                        "used_pct": round(w.used_pct, 1),
                        "resets_at": w.resets_at,
                    }
            for m in pu.models:
                w_5h = m.get_window("5h")
                w_wk = m.get_window("weekly")
                if w_5h or w_wk:
                    entry.setdefault("models", {})[m.model_name] = {
                        "5h_remaining_pct": round(w_5h.remaining_pct, 1) if w_5h else None,
                        "5h_resets_at": w_5h.resets_at if w_5h else None,
                        "weekly_remaining_pct": round(w_wk.remaining_pct, 1) if w_wk else None,
                        "weekly_resets_at": w_wk.resets_at if w_wk else None,
                    }
            out["providers"][pid] = entry
        path = get_cache_dir() / "snapshot.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f)
        os.replace(tmp, path)
    except Exception:
        pass
