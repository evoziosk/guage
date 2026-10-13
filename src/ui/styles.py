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
    # Brand Colors matching Stitch Minimalist Modern palette
    CLAUDE_PRIMARY = QColor(249, 115, 22)       # Orange (#F97316)
    CODEX_PRIMARY = QColor(228, 228, 231)        # Zinc White (#E4E4E7)
    ANTIGRAVITY_PRIMARY = QColor(59, 130, 246)   # Blue (#3B82F6)
    OPENCODE_PRIMARY = QColor(16, 185, 129)      # Emerald (#10B981)

    CLAUDE_ORANGE = CLAUDE_PRIMARY
    CODEX_GREEN = CODEX_PRIMARY
    ANTIGRAVITY_BLUE = ANTIGRAVITY_PRIMARY

    # Universal Status Alerts
    GREEN_ACCENT = QColor(16, 185, 129)         # Emerald (#10B981)
    WARNING_ACCENT = QColor(245, 158, 11)       # Amber (#F59E0B)
    CRITICAL_ACCENT = QColor(239, 68, 68)       # Vivid Red (#EF4444)

    # Active Session Notations & Badges
    ACTIVE_ACCENT = QColor(16, 185, 129)        # Emerald (#10B981)
    ACTIVE_BG = QColor(16, 185, 129, 26)
    ACTIVE_BORDER = QColor(16, 185, 129, 80)
    ACTIVE_TEXT = QColor(52, 211, 153)          # (#34D399)
    ACTIVE_STRIP_BG = QColor(17, 18, 21, 240)

    # Dimensions
    CARD_WIDTH = 460
    PILL_WIDTH = 460
    CARD_HEIGHT_BARS = 430
    CARD_HEIGHT_RINGS = 260
    CARD_HEIGHT_MINI = 72
    CORNER_RADIUS = 12                          # Sleek modern rounded card corners

    # Minimalist Modern Dark Tokens
    BG_CANVAS = QColor(12, 13, 14, 252)         # #0C0D0E
    BG_CARD = QColor(17, 18, 21, 255)           # #111215
    BG_ELEVATED = QColor(24, 24, 27, 255)       # #18181B
    BORDER_CARD = QColor(39, 39, 42, 200)       # #27272A
    BORDER_HAIRLINE = QColor(39, 39, 42, 160)   # #27272A/60
    BORDER_DIVIDER = QColor(39, 39, 42, 160)    # #27272A/60
    PROGRESS_TRACK = QColor(39, 39, 42, 230)    # #27272A/90
    TEXT_WHITE = QColor(250, 250, 250)          # #FAFAFA
    TEXT_MUTED = QColor(161, 161, 170)          # #A1A1AA
    TEXT_DIM = QColor(113, 113, 122)            # #71717A
    STATUS_NOMINAL = QColor(52, 211, 153)       # #34D399

    @staticmethod
    def mono_font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        f = QFont("JetBrains Mono")
        f.setFamilies(["JetBrains Mono", "SF Mono", "DejaVu Sans Mono", "Consolas", "monospace"])
        f.setPointSizeF(size)
        f.setWeight(weight)
        return f

    @staticmethod
    def sans_font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        f = QFont("Inter")
        f.setFamilies(["-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Inter", "Segoe UI", "DejaVu Sans", "sans-serif"])
        f.setPointSizeF(size)
        f.setWeight(weight)
        return f

    # Provider Brand Themes
    THEMES: dict[str, ProviderTheme] = {
        "all": ProviderTheme(
            id="all",
            name="Guage",
            logo_char="G",
            bg_color=QColor(12, 13, 14, 252),          # #0C0D0E Canvas
            border_color=QColor(39, 39, 42, 200),      # #27272A Border
            logo_bg=QColor(24, 24, 27),                # #18181B Zinc-900
            logo_fg=QColor(212, 212, 216),             # #D4D4D8 Zinc-300
            accent_color=QColor(16, 185, 129),         # Emerald
            text_white=QColor(250, 250, 250),
            text_muted=QColor(161, 161, 170),
            text_dim=QColor(113, 113, 122),
            progress_track=QColor(39, 39, 42, 230),
            divider_color=QColor(39, 39, 42, 160),
            tab_active_bg=QColor(39, 39, 42, 255),
            tab_active_border=QColor(63, 63, 70, 200),
        ),
        "claude": ProviderTheme(
            id="claude",
            name="Claude",
            logo_char="C",
            bg_color=QColor(12, 13, 14, 252),
            border_color=QColor(39, 39, 42, 200),
            logo_bg=QColor(24, 24, 27),
            logo_fg=QColor(249, 115, 22),
            accent_color=QColor(249, 115, 22),
            text_white=QColor(250, 250, 250),
            text_muted=QColor(161, 161, 170),
            text_dim=QColor(113, 113, 122),
            progress_track=QColor(39, 39, 42, 230),
            divider_color=QColor(39, 39, 42, 160),
            tab_active_bg=QColor(39, 39, 42, 255),
            tab_active_border=QColor(63, 63, 70, 200),
        ),
        "codex": ProviderTheme(
            id="codex",
            name="Codex",
            logo_char="C",
            bg_color=QColor(12, 13, 14, 252),
            border_color=QColor(39, 39, 42, 200),
            logo_bg=QColor(24, 24, 27),
            logo_fg=QColor(228, 228, 231),
            accent_color=QColor(228, 228, 231),
            text_white=QColor(250, 250, 250),
            text_muted=QColor(161, 161, 170),
            text_dim=QColor(113, 113, 122),
            progress_track=QColor(39, 39, 42, 230),
            divider_color=QColor(39, 39, 42, 160),
            tab_active_bg=QColor(39, 39, 42, 255),
            tab_active_border=QColor(63, 63, 70, 200),
        ),
        "antigravity": ProviderTheme(
            id="antigravity",
            name="Antigravity",
            logo_char="A",
            bg_color=QColor(12, 13, 14, 252),
            border_color=QColor(39, 39, 42, 200),
            logo_bg=QColor(24, 24, 27),
            logo_fg=QColor(59, 130, 246),
            accent_color=QColor(59, 130, 246),
            text_white=QColor(250, 250, 250),
            text_muted=QColor(161, 161, 170),
            text_dim=QColor(113, 113, 122),
            progress_track=QColor(39, 39, 42, 230),
            divider_color=QColor(39, 39, 42, 160),
            tab_active_bg=QColor(39, 39, 42, 255),
            tab_active_border=QColor(63, 63, 70, 200),
        ),
        "opencode": ProviderTheme(
            id="opencode",
            name="OpenCode",
            logo_char="O",
            bg_color=QColor(12, 13, 14, 252),
            border_color=QColor(39, 39, 42, 200),
            logo_bg=QColor(24, 24, 27),
            logo_fg=QColor(16, 185, 129),
            accent_color=QColor(16, 185, 129),
            text_white=QColor(250, 250, 250),
            text_muted=QColor(161, 161, 170),
            text_dim=QColor(113, 113, 122),
            progress_track=QColor(39, 39, 42, 230),
            divider_color=QColor(39, 39, 42, 160),
            tab_active_bg=QColor(39, 39, 42, 255),
            tab_active_border=QColor(63, 63, 70, 200),
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
