from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict


def get_cache_dir() -> Path:
    """Return the cross-platform cache directory for the application."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            d = Path(base) / "ai-usage-widget"
            d.mkdir(parents=True, exist_ok=True)
            return d
    # Linux / macOS / fallback
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        d = Path(xdg_cache) / "ai-usage-widget"
    else:
        d = Path.home() / ".cache" / "ai-usage-widget"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_dir() -> Path:
    """Return the cross-platform config directory for the application."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            d = Path(base) / "ai-usage-widget"
            d.mkdir(parents=True, exist_ok=True)
            return d
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        d = Path(xdg_config) / "ai-usage-widget"
    else:
        d = Path.home() / ".config" / "ai-usage-widget"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class WidgetSettings:
    pos_x: int = 100
    pos_y: int = 100
    scale: float = 1.0
    opacity: float = 0.95
    always_on_top: bool = True
    compact_mode: bool = True
    poll_interval_claude: int = 60
    poll_interval_codex: int = 60
    poll_interval_antigravity: int = 60
    poll_interval_opencode: int = 60
    theme: str = "dark"
    show_used: bool = False  # False = "remaining" style, True = "used" style
    show_active_sessions: bool = True  # show notations/updates of currently running sessions
    session_display_mode: str = "bars"  # "bars" (separate bar for each) or "cycle" (single cycling bar)
    session_poll_interval: int = 5  # seconds between fast local active session checks
    notifications: bool = True
    adaptive_polling: bool = True  # back off polling while usage is idle
    fade_when_idle: bool = True
    idle_opacity: float = 0.55
    view_mode: str = ""  # "", "bars", "rings" or "mini"; empty derives from compact_mode

    @classmethod
    def load(cls) -> WidgetSettings:
        cfg_file = get_config_dir() / "settings.json"
        if not cfg_file.exists():
            return cls()
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()

    def save(self) -> None:
        cfg_file = get_config_dir() / "settings.json"
        try:
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except Exception as e:
            sys.stderr.write(f"Failed to save settings: {e}\n")
