from __future__ import annotations

from dataclasses import dataclass
from PySide6.QtGui import QColor, QFont


@dataclass
class ProviderTheme:
    id: str
    name: str
    logo_char: str
    bg_color: QColor
    border_color: QColor
    logo_bg: QColor
    logo_fg: QColor
    accent_color: QColor
    text_white: QColor
    text_muted: QColor
    text_dim: QColor
    progress_track: QColor
    divider_color: QColor
    tab_active_bg: QColor
    tab_active_border: QColor

    def get_color_for_pct(self, pct: float) -> QColor:
        if pct >= 95.0:
            return UITheme.CRITICAL_ACCENT
        if pct >= 75.0:
            return UITheme.WARNING_ACCENT
        return self.accent_color


class UITheme:
    # Brand Colors
    CLAUDE_PRIMARY = QColor(217, 119, 87)       # Claude Clay Orange (#D97757)
    CODEX_PRIMARY = QColor(244, 244, 245)        # Codex Monochrome White
    ANTIGRAVITY_PRIMARY = QColor(59, 130, 246)   # Google AI Electric Blue (#3B82F6)
    OPENCODE_PRIMARY = QColor(16, 185, 129)      # OpenCode Mint Emerald (#10B981)

    CLAUDE_ORANGE = CLAUDE_PRIMARY
    CODEX_GREEN = CODEX_PRIMARY
    ANTIGRAVITY_BLUE = ANTIGRAVITY_PRIMARY

    # Universal Status Alerts
    GREEN_ACCENT = QColor(34, 197, 94)         # Vivid TokenEater green (#22C55E)
    WARNING_ACCENT = QColor(245, 158, 11)      # Amber (#F59E0B)
    CRITICAL_ACCENT = QColor(239, 68, 68)      # Vivid Red (#EF4444) for 100%

    # Active Session Notations & Badges
    ACTIVE_ACCENT = QColor(34, 197, 94)        # Vivid emerald green (#22C55E)
    ACTIVE_BG = QColor(34, 197, 94, 28)        # Translucent green badge bg
    ACTIVE_BORDER = QColor(34, 197, 94, 90)    # Soft badge border
    ACTIVE_TEXT = QColor(74, 222, 128)         # High contrast green (#4ADE80)
    ACTIVE_STRIP_BG = QColor(14, 22, 18, 200)  # Dark emerald tint for live strip

    # Dimensions
    CARD_WIDTH = 460
    PILL_WIDTH = 460
    CARD_HEIGHT_BARS = 410                     # Height for top list design in image.png
    CARD_HEIGHT_RINGS = 250                    # Height for bottom circular rings design
    CARD_HEIGHT_MINI = 176                     # One compact line per provider
    CORNER_RADIUS = 28                         # 28px rounded corners from image.png

    # Default / TokenEater Fallbacks
    BG_CARD = QColor(18, 19, 22, 252)
    BORDER_CARD = QColor(36, 40, 48, 255)
    BORDER_DIVIDER = QColor(30, 33, 40)
    PROGRESS_TRACK = QColor(31, 35, 42)
    TEXT_WHITE = QColor(255, 255, 255)
    TEXT_MUTED = QColor(122, 130, 142)
    TEXT_DIM = QColor(90, 98, 110)

    # Provider Brand Themes
    THEMES: dict[str, ProviderTheme] = {
        "all": ProviderTheme(
            id="all",
            name="Guage",
            logo_char="G",
            bg_color=QColor(14, 16, 22, 252),          # Deep Titanium Obsidian (#0E1016)
            border_color=QColor(38, 44, 58, 255),      # Precision hairline border (#262C3A)
            logo_bg=QColor(139, 92, 246),              # Vivid Violet badge (#8B5CF6)
            logo_fg=QColor(255, 255, 255),
            accent_color=QColor(168, 85, 247),          # Electric Violet Pulse (#A855F7)
            text_white=QColor(255, 255, 255),
            text_muted=QColor(156, 163, 175),          # Slate Silver (#9CA3AF)
            text_dim=QColor(100, 116, 139),            # Cool Slate (#64748B)
            progress_track=QColor(28, 32, 44),         # Refined Deep Track
            divider_color=QColor(32, 38, 52),          # Crisp Divider
            tab_active_bg=QColor(139, 92, 246, 50),
            tab_active_border=QColor(168, 85, 247, 140),
        ),
        "claude": ProviderTheme(
            id="claude",
            name="Claude",
            logo_char="C",
            bg_color=QColor(24, 22, 20, 252),          # Claude Slate Dark (#181614 warm charcoal)
            border_color=QColor(62, 50, 44, 255),      # Warm terracotta hairline border
            logo_bg=QColor(217, 119, 87),              # Claude Clay Terracotta (#D97757)
            logo_fg=QColor(255, 255, 255),             # Crisp White 'C'
            accent_color=QColor(217, 119, 87),         # Claude Clay Orange
            text_white=QColor(250, 249, 245),          # Claude Ivory Light (#FAF9F5)
            text_muted=QColor(176, 168, 160),          # Warm Slate Ivory
            text_dim=QColor(125, 115, 106),
            progress_track=QColor(42, 36, 33),         # Warm Progress Track
            divider_color=QColor(46, 40, 36),
            tab_active_bg=QColor(217, 119, 87, 50),
            tab_active_border=QColor(217, 119, 87, 140),
        ),
        "codex": ProviderTheme(
            id="codex",
            name="Codex",
            logo_char="C",
            bg_color=QColor(13, 14, 16, 252),          # Pure Minimalist Black (#0D0E10)
            border_color=QColor(42, 45, 52, 255),      # Hairline Monochrome Border
            logo_bg=QColor(255, 255, 255),             # Stark Pure White Badge
            logo_fg=QColor(13, 14, 16),                # Deep Black 'C'
            accent_color=QColor(244, 244, 245),        # White & Black Theme Primary
            text_white=QColor(255, 255, 255),          # Stark Crisp White
            text_muted=QColor(161, 161, 170),          # Cool Zinc (#A1A1AA)
            text_dim=QColor(113, 113, 122),            # Muted Zinc Footer
            progress_track=QColor(32, 34, 40),         # Monochrome Track
            divider_color=QColor(36, 38, 44),
            tab_active_bg=QColor(255, 255, 255, 45),
            tab_active_border=QColor(255, 255, 255, 120),
        ),
        "antigravity": ProviderTheme(
            id="antigravity",
            name="Antigravity",
            logo_char="A",
            bg_color=QColor(10, 16, 30, 252),          # Deep Cosmic Navy/Slate (#0A101E)
            border_color=QColor(30, 48, 80, 255),      # Deep Electric Navy Border
            logo_bg=QColor(37, 99, 235),               # Google AI Electric Blue (#2563EB)
            logo_fg=QColor(255, 255, 255),             # Crisp White 'A'
            accent_color=QColor(59, 130, 246),         # Google AI Blue (#3B82F6)
            text_white=QColor(248, 250, 252),          # Ice White (#F8FAFC)
            text_muted=QColor(148, 163, 184),          # Slate Blue (#94A3B8)
            text_dim=QColor(100, 116, 139),
            progress_track=QColor(20, 32, 56),         # Deep Navy Track
            divider_color=QColor(25, 40, 70),
            tab_active_bg=QColor(59, 130, 246, 50),
            tab_active_border=QColor(59, 130, 246, 140),
        ),
        "opencode": ProviderTheme(
            id="opencode",
            name="OpenCode",
            logo_char="O",
            bg_color=QColor(12, 20, 18, 252),          # Deep Emerald Obsidian (#0C1412)
            border_color=QColor(28, 54, 44, 255),      # Emerald hairline border
            logo_bg=QColor(16, 185, 129),              # OpenCode Vivid Emerald (#10B981)
            logo_fg=QColor(255, 255, 255),             # Crisp White 'O'
            accent_color=QColor(16, 185, 129),         # OpenCode Mint Emerald
            text_white=QColor(248, 250, 249),          # Pure White / Mint Tint
            text_muted=QColor(154, 178, 168),          # Slate Mint (#9AB2A8)
            text_dim=QColor(95, 122, 112),
            progress_track=QColor(22, 38, 32),         # Deep Emerald Track
            divider_color=QColor(28, 48, 40),
            tab_active_bg=QColor(16, 185, 129, 45),
            tab_active_border=QColor(16, 185, 129, 130),
        ),
    }

    @classmethod
    def get_theme(cls, provider_id: str) -> ProviderTheme:
        return cls.THEMES.get(provider_id, cls.THEMES["all"])

    @staticmethod
    def get_color_for_pct(pct: float) -> QColor:
        if pct >= 95.0:
            return UITheme.CRITICAL_ACCENT
        if pct >= 75.0:
            return UITheme.WARNING_ACCENT
        return UITheme.GREEN_ACCENT
