from __future__ import annotations

import json
import time
from typing import Dict, List, Optional, Tuple

from src.config import get_cache_dir
from src.models import AggregatedUsage

SAMPLE_EVERY_SECS = 300  # one point every 5 minutes
KEEP_SECS = 7 * 24 * 3600


class History:
    """Tiny append-only JSONL log of remaining% per provider (about 2k lines for 7 days)."""

    def __init__(self, path=None) -> None:
        self.path = path or (get_cache_dir() / "history.jsonl")
        self._points: List[dict] = []
        self._last_write = 0.0
        self._load()

    def _load(self) -> None:
        try:
            cutoff = time.time() - KEEP_SECS
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("t", 0) >= cutoff:
                        self._points.append(d)
            if self._points:
                self._last_write = self._points[-1]["t"]
                # compact the file so it never grows unbounded
                with open(self.path, "w", encoding="utf-8") as f:
                    for d in self._points:
                        f.write(json.dumps(d) + "\n")
        except OSError:
            pass

    def record(self, usage: AggregatedUsage, now: Optional[float] = None) -> bool:
        now = now or time.time()
        if now - self._last_write < SAMPLE_EVERY_SECS:
            return False
        entry: Dict[str, dict] = {}
        for pid, pu in usage.providers.items():
            if not pu.available:
                continue
            e = {}
            if pu.primary_window:
                e["s"] = round(pu.primary_window.remaining_pct, 1)
            if pu.secondary_window:
                e["w"] = round(pu.secondary_window.remaining_pct, 1)
            if e:
                entry[pid] = e
        if not entry:
            return False
        point = {"t": round(now, 1), "p": entry}
        self._points.append(point)
        self._last_write = now
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(point) + "\n")
        except OSError:
            pass
        return True

    def series(self, pid: str, key: str = "s", hours: float = 24.0) -> List[Tuple[float, float]]:
        cutoff = time.time() - hours * 3600
        return [(d["t"], d["p"][pid][key]) for d in self._points if d["t"] >= cutoff and pid in d["p"] and key in d["p"][pid]]
