from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Callable, Dict, List, Optional

from src.collectors.antigravity import AntigravityCollector
from src.collectors.claude import ClaudeCollector
from src.collectors.codex import CodexCollector
from src.collectors.opencode import OpenCodeCollector
from src.config import WidgetSettings
from src.history import History
from src.localstats import LocalStats
from src.models import ActiveSession, AggregatedUsage, ProviderUsage
from src.sessions import SessionTracker


class UsageAggregator:
    """Coordinates polling, caching, and timer freshness across all AI providers."""

    def __init__(self, settings: Optional[WidgetSettings] = None):
        self.settings = settings or WidgetSettings.load()
        self.claude = ClaudeCollector(cache_ttl_seconds=self.settings.poll_interval_claude)
        self.codex = CodexCollector(cache_ttl_seconds=self.settings.poll_interval_codex)
        self.antigravity = AntigravityCollector(cache_ttl_seconds=self.settings.poll_interval_antigravity)
        self.opencode = OpenCodeCollector(cache_ttl_seconds=getattr(self.settings, "poll_interval_opencode", 60))

        self.history = History()
        self.stats = LocalStats()
        self.sessions = SessionTracker(cache_ttl=float(getattr(self.settings, "session_poll_interval", 5)))
        self._unchanged: Dict[str, int] = {"claude": 0, "codex": 0, "antigravity": 0, "opencode": 0}
        self._last_pct: Dict[str, float] = {}

        self._usage = AggregatedUsage()
        self._lock = threading.Lock()
        self._subscribers: List[Callable[[AggregatedUsage], None]] = []
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

    def subscribe(self, callback: Callable[[AggregatedUsage], None]) -> None:
        """Register a callback for data updates."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[AggregatedUsage], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def _notify(self) -> None:
        try:
            self.history.record(self.get_latest())
            self.stats.maybe_refresh_async()
        except Exception:
            pass
        with self._lock:
            active_s = self.sessions.scan_all()
            snapshot = AggregatedUsage(
                providers=dict(self._usage.providers),
                active_sessions=list(active_s),
                last_updated=self._usage.last_updated,
            )
        for cb in self._subscribers:
            try:
                cb(snapshot)
            except Exception:
                pass

    def get_latest(self) -> AggregatedUsage:
        """Get the current aggregated usage snapshot."""
        with self._lock:
            active_s = self.sessions.scan_all()
            return AggregatedUsage(
                providers=dict(self._usage.providers),
                active_sessions=list(active_s),
                last_updated=self._usage.last_updated,
            )

    def get_active_sessions(self, provider_id: Optional[str] = None, force: bool = False) -> List[ActiveSession]:
        """Return active sessions filtered by provider_id."""
        sessions = self.sessions.scan_all(force=force)
        if not provider_id or provider_id == "all":
            return sessions
        return [s for s in sessions if s.provider_id == provider_id]

    def refresh_provider(self, provider_id: str, force: bool = False) -> Optional[ProviderUsage]:
        """Fetch a single provider's data."""
        res: Optional[ProviderUsage] = None
        if provider_id == "claude":
            res = self.claude.collect(force=force)
        elif provider_id == "codex":
            res = self.codex.collect(force=force)
        elif provider_id == "antigravity":
            res = self.antigravity.collect(force=force)
        elif provider_id == "opencode":
            res = self.opencode.collect(force=force)

        if res:
            with self._lock:
                self._usage.providers[provider_id] = res
                self._usage.last_updated = time.time()
        return res

    def refresh_all(self, force: bool = False) -> AggregatedUsage:
        """Fetch data from all providers concurrently."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_id = {
                executor.submit(self.claude.collect, force): "claude",
                executor.submit(self.codex.collect, force): "codex",
                executor.submit(self.antigravity.collect, force): "antigravity",
                executor.submit(self.opencode.collect, force): "opencode",
            }
            results = {}
            for future in concurrent.futures.as_completed(future_to_id):
                pid = future_to_id[future]
                try:
                    res = future.result()
                    if res:
                        results[pid] = res
                except Exception as e:
                    results[pid] = ProviderUsage(
                        provider_id=pid,
                        display_name=pid.capitalize(),
                        available=False,
                        error=str(e),
                    )

        with self._lock:
            self._usage.providers.update(results)
            self._usage.last_updated = time.time()

        self._notify()
        return self.get_latest()

    def refresh_all_async(self, force: bool = False, on_complete: Optional[Callable[[AggregatedUsage], None]] = None) -> None:
        """Fetch data from all providers concurrently in a background thread without blocking the UI thread."""
        def _worker():
            usage = self.refresh_all(force=force)
            if on_complete:
                try:
                    on_complete(usage)
                except Exception:
                    pass
        t = threading.Thread(target=_worker, daemon=True, name="AsyncRefreshWorker")
        t.start()

    def _interval(self, pid: str) -> float:
        """Poll interval; backs off up to 4x while a provider's usage is not changing."""
        base = float(getattr(self.settings, f"poll_interval_{pid}"))
        if not getattr(self.settings, "adaptive_polling", True):
            return base
        return min(base * (1 + min(self._unchanged.get(pid, 0), 3)), 300.0)

    def _track_change(self, pid: str) -> None:
        pu = self._usage.providers.get(pid)
        if not pu or not pu.primary_window:
            return
        pct = round(pu.primary_window.used_pct, 1)
        if self._last_pct.get(pid) == pct:
            self._unchanged[pid] = self._unchanged.get(pid, 0) + 1
        else:
            self._unchanged[pid] = 0
        self._last_pct[pid] = pct

    def _loop(self) -> None:
        """Background worker loop managing polling schedules every 60s."""
        last_poll = {
            "claude": time.time(),
            "codex": time.time(),
            "antigravity": time.time(),
            "opencode": time.time(),
        }

        # Initial live fetch
        self.refresh_all(force=True)

        while self._running:
            now = time.time()
            to_refresh = []

            if now - last_poll["claude"] >= self._interval("claude"):
                to_refresh.append("claude")
                last_poll["claude"] = now

            if now - last_poll["codex"] >= self._interval("codex"):
                to_refresh.append("codex")
                last_poll["codex"] = now

            if now - last_poll["antigravity"] >= self._interval("antigravity"):
                to_refresh.append("antigravity")
                last_poll["antigravity"] = now

            if now - last_poll["opencode"] >= self._interval("opencode"):
                to_refresh.append("opencode")
                last_poll["opencode"] = now

            if to_refresh:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(to_refresh)) as executor:
                    futures = {
                        executor.submit(self.refresh_provider, pid, True): pid
                        for pid in to_refresh
                    }
                    concurrent.futures.wait(futures.keys())
                for pid in to_refresh:
                    self._track_change(pid)
                self._notify()

            # Sleep short intervals to stay responsive to stop signals
            for _ in range(10):
                if not self._running:
                    break
                time.sleep(0.5)

    def start(self) -> None:
        """Start the background refresh loop."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop the background refresh loop."""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None
