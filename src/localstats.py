from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _midnight() -> float:
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _claude_dir() -> Path:
    custom = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(custom) if custom else Path.home() / ".claude"


def _codex_dir() -> Path:
    custom = os.environ.get("CODEX_HOME")
    return Path(custom) if custom else Path.home() / ".codex"


class LocalStats:
    """Today's token usage read from the CLIs' own local logs (zero network, zero cost).

    Files are re-parsed only when their (mtime, size) changed, so repeated refreshes are cheap.
    """

    def __init__(self, min_interval: float = 300.0) -> None:
        self.min_interval = min_interval
        self._last_run = 0.0
        self._running = False
        self._file_cache: Dict[str, Tuple[Tuple[float, int], int]] = {}
        self.today: Dict[str, Tuple[int, int]] = {}  # provider -> (tokens, sessions)

    # -- Claude -------------------------------------------------------------
    @staticmethod
    def _parse_claude_file(path: Path, midnight: float) -> int:
        total = 0
        seen = set()
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line or '"timestamp"' not in line:
                    continue
                try:
                    d = json.loads(line)
                    msg = d.get("message")
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
                    if ts < midnight:
                        continue
                    mid = msg.get("id")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    total += int(usage.get("input_tokens") or 0)
                    total += int(usage.get("output_tokens") or 0)
                    total += int(usage.get("cache_creation_input_tokens") or 0)
                except (ValueError, KeyError, TypeError):
                    continue
        return total

    # -- Codex --------------------------------------------------------------
    @staticmethod
    def _parse_codex_file(path: Path) -> int:
        last = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "total_token_usage" not in line:
                    continue
                try:
                    info = json.loads(line).get("payload", {}).get("info") or {}
                    last = int(info.get("total_token_usage", {}).get("total_tokens") or last)
                except (ValueError, TypeError, AttributeError):
                    continue
        return last

    def _scan(self, pid: str, root: Path, parse) -> Tuple[int, int]:
        midnight = _midnight()
        tokens = sessions = 0
        if not root.exists():
            return 0, 0
        for path in root.rglob("*.jsonl"):
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < midnight:
                continue
            key = str(path)
            sig = (st.st_mtime, st.st_size)
            cached = self._file_cache.get(key)
            if cached and cached[0] == sig:
                n = cached[1]
            else:
                try:
                    n = parse(path, midnight) if pid == "claude" else parse(path)
                except OSError:
                    continue
                self._file_cache[key] = (sig, n)
            if n > 0:
                tokens += n
                sessions += 1
        return tokens, sessions

    def refresh(self) -> None:
        result = {
            "claude": self._scan("claude", _claude_dir() / "projects", self._parse_claude_file),
            "codex": self._scan("codex", _codex_dir() / "sessions", self._parse_codex_file),
        }
        self.today = result
        self._last_run = time.time()

    def maybe_refresh_async(self) -> None:
        if self._running or time.time() - self._last_run < self.min_interval:
            return
        self._running = True

        def _work():
            try:
                self.refresh()
            except Exception:
                pass
            finally:
                self._running = False

        threading.Thread(target=_work, daemon=True, name="LocalStatsWorker").start()

    def summary(self, pid: str) -> Optional[str]:
        tokens, sessions = self.today.get(pid, (0, 0))
        if tokens <= 0:
            return None
        s = "session" if sessions == 1 else "sessions"
        return f"Today {format_tokens(tokens)} tokens, {sessions} {s}"
