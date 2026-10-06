from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

from src.aggregator import UsageAggregator
from src.config import WidgetSettings
from src.insights import InsightEngine, best_provider, PROVIDER_LABELS
from src.models import ActiveSession, AggregatedUsage, ProviderUsage, UsageWindow, format_reset_label
from src.ui.styles import ProviderTheme, UITheme


@dataclass
class BarsLayout:
    last_bar_bottom: float
    session_rects: List[Tuple[QRectF, ActiveSession]]
    section_header_rect: Optional[QRectF]
    active_end_y: float
    trend_label_rect: QRectF
    sparkline_rect: QRectF
    divider_y: float
    footer_text_rect: QRectF
    footer_dot_center: QPointF
    footer_status_rect: QRectF
    total_height: int


class AIUsageWidget(QtWidgets.QWidget):
    """TokenEater-themed desktop usage widget matching image.png with ModelPulse overview."""

    data_applied = QtCore.Signal()
    _data_signal = QtCore.Signal(object)
    _refresh_done_signal = QtCore.Signal()

    def __init__(self, aggregator: UsageAggregator, settings: Optional[WidgetSettings] = None):
        super().__init__()
        self.aggregator = aggregator
        self.settings = settings or WidgetSettings.load()
        self.usage: AggregatedUsage = self.aggregator.get_latest()
        self._data_signal.connect(self._apply_new_data, Qt.ConnectionType.QueuedConnection)
        self._refresh_done_signal.connect(self._finish_manual_refresh, Qt.ConnectionType.QueuedConnection)

        # Frameless always-on-top window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.createWinId()

        # Modes: "rings" (compact bottom card in image.png) vs "bars" (detailed top card in image.png)
        if self.settings.view_mode in ("bars", "rings", "mini"):
            self.view_mode = self.settings.view_mode
        else:
            self.view_mode = "rings" if self.settings.compact_mode else "bars"
        self.insights = InsightEngine()
        self.alert_callback = None  # set by the tray: (title, message) -> None
        self.active_provider = "all"  # "all", "claude", "codex", "antigravity"

        self._drag_pos: Optional[QPoint] = None
        self._is_dragging = False
        self._is_refreshing = False

        self._cycle_tick = 0
        self._last_session_check = 0.0

        # Restore geometry
        self.move(self.settings.pos_x, self.settings.pos_y)
        self._update_geometry()

        # Subscribe to aggregator updates
        self.aggregator.subscribe(self._on_data_updated)

        # 1-second UI refresh timer for countdown tick & session rotations
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._on_tick)
        self.tick_timer.start(1000)

        self.setWindowOpacity(self.settings.opacity)

    def _on_tick(self) -> None:
        self._cycle_tick += 1
        now = time.time()
        if self.settings.show_active_sessions and (now - self._last_session_check >= self.settings.session_poll_interval):
            self._last_session_check = now
            if hasattr(self.aggregator, "get_active_sessions"):
                prev_count = len(self.usage.active_sessions)
                self.usage.active_sessions = self.aggregator.get_active_sessions()
                if len(self.usage.active_sessions) != prev_count and self.view_mode == "bars":
                    self._update_geometry()
        self.update()

    def _get_bars_layout(self, w: float = UITheme.CARD_WIDTH) -> BarsLayout:
        try:
            _, _, rows = self._get_provider_rows()
            n = len(rows)
        except Exception:
            n = 4 if self.active_provider in ("antigravity", "opencode") else 3
        if n > 3:
            start_y = 62.0
            row_gap = 65.0
            bar_offset = 35.0
            bar_h = 6.0
        else:
            start_y = 70.0
            row_gap = 92.0
            bar_offset = 46.0
            bar_h = 7.5

        last_bar_bottom = start_y + (n - 1) * row_gap + bar_offset + bar_h

        sessions: List[ActiveSession] = []
        if self.settings.show_active_sessions:
            sessions = self.usage.get_active_sessions(self.active_provider if self.active_provider != "all" else None)

        session_rects: List[Tuple[QRectF, ActiveSession]] = []
        section_header_rect: Optional[QRectF] = None

        if sessions:
            section_header_rect = QRectF(26.0, last_bar_bottom + 14.0, w - 52.0, 14.0)
            bars_start_y = section_header_rect.bottom() + 6.0
            bar_height = 26.0
            bar_gap = 6.0

            if getattr(self.settings, "session_display_mode", "bars") == "bars":
                for idx, s in enumerate(sessions):
                    sy = bars_start_y + idx * (bar_height + bar_gap)
                    session_rects.append((QRectF(26.0, sy, w - 52.0, bar_height), s))
                active_end_y = bars_start_y + len(sessions) * bar_height + max(0, len(sessions) - 1) * bar_gap
            else:
                idx = (self._cycle_tick // 3) % len(sessions)
                s_rect = QRectF(26.0, bars_start_y, w - 52.0, bar_height)
                session_rects.append((s_rect, sessions[idx]))
                active_end_y = bars_start_y + bar_height
        else:
            active_end_y = last_bar_bottom

        trend_gap = 22.0 if sessions else 18.0
        trend_label_top = active_end_y + trend_gap
        trend_label_rect = QRectF(26.0, trend_label_top, w - 52.0, 14.0)
        sparkline_top = trend_label_rect.bottom() + 6.0
        sparkline_height = 20.0
        sparkline_rect = QRectF(26.0, sparkline_top, w - 52.0, sparkline_height)
        sparkline_bottom = sparkline_rect.bottom()

        divider_y = sparkline_bottom + 16.0
        footer_text_rect = QRectF(26.0, divider_y + 8.0, w - 100.0, 20.0)
        footer_dot_center = QPointF(w - 64.0, divider_y + 18.0)
        footer_status_rect = QRectF(w - 56.0, divider_y + 8.0, 40.0, 20.0)
        total_height = int(divider_y + 36.0)

        return BarsLayout(
            last_bar_bottom=last_bar_bottom,
            session_rects=session_rects,
            section_header_rect=section_header_rect,
            active_end_y=active_end_y,
            trend_label_rect=trend_label_rect,
            sparkline_rect=sparkline_rect,
            divider_y=divider_y,
            footer_text_rect=footer_text_rect,
            footer_dot_center=footer_dot_center,
            footer_status_rect=footer_status_rect,
            total_height=max(total_height, UITheme.CARD_HEIGHT_BARS),
        )

    def _calc_bars_height(self) -> int:
        return self._get_bars_layout(UITheme.CARD_WIDTH).total_height

    def _update_geometry(self) -> None:
        scale = self.settings.scale
        w = int(UITheme.CARD_WIDTH * scale)
        if self.view_mode == "bars":
            h = int(self._calc_bars_height() * scale)
        elif self.view_mode == "mini":
            h = int(UITheme.CARD_HEIGHT_MINI * scale)
        else:
            h = int(UITheme.CARD_HEIGHT_RINGS * scale)
        if self.size() != QtCore.QSize(w, h):
            self.setFixedSize(w, h)
        self.update()

    @property
    def compact_mode(self) -> bool:
        return self.view_mode == "rings"

    @compact_mode.setter
    def compact_mode(self, val: bool) -> None:
        self.view_mode = "rings" if val else "bars"
        self._update_geometry()

    def toggle_mode(self) -> None:
        self.set_view_mode("bars" if self.view_mode == "rings" else "rings")

    def set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        self.settings.compact_mode = (mode != "bars")
        self.settings.view_mode = mode
        self.settings.save()
        self._update_geometry()

    def toggle_style(self) -> None:
        """Switch between 'remaining' and 'used' presentation."""
        self.settings.show_used = not self.settings.show_used
        self.settings.save()
        self.update()

    def _disp(self, remaining: float) -> float:
        """Convert a remaining percentage to what the user wants displayed."""
        return 100.0 - remaining if self.settings.show_used else remaining

    def enterEvent(self, event) -> None:
        self.setWindowOpacity(self.settings.opacity)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.settings.fade_when_idle and not self._is_dragging:
            self.setWindowOpacity(min(self.settings.opacity, self.settings.idle_opacity))
        super().leaveEvent(event)

    def cycle_provider(self, provider_id: Optional[str] = None) -> None:
        providers = ["all", "claude", "codex", "antigravity"]
        if "opencode" in self.usage.providers and self.usage.providers["opencode"].available:
            providers.append("opencode")
        if provider_id and provider_id in providers:
            self.active_provider = provider_id
        else:
            idx = providers.index(self.active_provider) if self.active_provider in providers else 0
            self.active_provider = providers[(idx + 1) % len(providers)]
        self._update_geometry()

    def manual_refresh(self) -> None:
        """Trigger an asynchronous, non-blocking refresh of all providers."""
        if self._is_refreshing:
            return
        self._is_refreshing = True
        self.update()
        self.aggregator.refresh_all_async(force=True, on_complete=self._on_manual_refresh_done)

    def _on_manual_refresh_done(self, usage: AggregatedUsage) -> None:
        self._refresh_done_signal.emit()

    @QtCore.Slot()
    def _finish_manual_refresh(self) -> None:
        self._is_refreshing = False
        self.update()

    def _on_data_updated(self, new_usage: AggregatedUsage) -> None:
        # Called from the aggregator thread; a queued signal hops to the GUI thread.
        self._data_signal.emit(new_usage)

    @QtCore.Slot(object)
    def _apply_new_data(self, new_usage: AggregatedUsage) -> None:
        self.usage = new_usage
        try:
            alerts = self.insights.update(new_usage)
            if alerts and self.settings.notifications and self.alert_callback:
                for title, msg in alerts:
                    self.alert_callback(title, msg)
        except Exception:
            pass
        self._update_geometry()
        self.data_applied.emit()

    def _get_tab_rects(self, w: float) -> List[Tuple[str, str, QRectF]]:
        """Return (provider_id, label, rect) for header switcher tabs."""
        has_opencode = "opencode" in self.usage.providers and self.usage.providers["opencode"].available
        if has_opencode:
            tabs = [
                ("all", "All", 34.0),
                ("claude", "Claude", 52.0),
                ("codex", "Codex", 50.0),
                ("antigravity", "AGY", 38.0),
                ("opencode", "OpenCode", 66.0),
            ]
            tx = 150.0
            rects = []
            for pid, label, tw in tabs:
                rects.append((pid, label, QRectF(tx, 17, tw, 22)))
                tx += tw + 4.0
            return rects
        else:
            tabs = [("all", "All", 40.0), ("claude", "Claude", 62.0), ("codex", "Codex", 56.0), ("antigravity", "AGY", 46.0)]
            tx = 172.0
            rects = []
            for pid, label, tw in tabs:
                rects.append((pid, label, QRectF(tx, 17, tw, 22)))
                tx += tw + 6.0
            return rects

    def _get_toggle_rect(self, w: float) -> QRectF:
        """Return rect for view mode toggle icon button."""
        return QRectF(w - 38, 17, 22, 22)

    def _get_active_session_rects(self, w: float) -> List[Tuple[QRectF, ActiveSession]]:
        """Return (rect, session) for all rendered active session bars in bars mode."""
        if self.view_mode != "bars" or not self.settings.show_active_sessions:
            return []
        return self._get_bars_layout(w).session_rects

    # --- Mouse & Native Dragging ---
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            w = self.width()

            # Check if clicked on view mode toggle icon
            if self._get_toggle_rect(w).contains(pos):
                self.toggle_mode()
                event.accept()
                return

            # Check if clicked on provider switcher pills
            for pid, _, rect in self._get_tab_rects(w):
                if rect.contains(pos):
                    self.cycle_provider(pid)
                    event.accept()
                    return

            # Check if clicked on an active session bar
            for s_rect, s in self._get_active_session_rects(w):
                if s_rect.contains(pos):
                    if self.active_provider == s.provider_id:
                        self.cycle_provider("all")
                    else:
                        self.cycle_provider(s.provider_id)
                    event.accept()
                    return

            # Start native Wayland / Windows system move
            handle = self.windowHandle()
            if handle and hasattr(handle, "startSystemMove") and handle.startSystemMove():
                event.accept()
                return

            # Fallback manual drag
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._is_dragging = True
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        pos = event.position()
        w = self.width()

        # Check if hovering clickable header elements or active session bars
        session_rects = self._get_active_session_rects(w)
        is_clickable = (
            self._get_toggle_rect(w).contains(pos)
            or any(rect.contains(pos) for _, _, rect in self._get_tab_rects(w))
            or any(s_rect.contains(pos) for s_rect, _ in session_rects)
        )
        if is_clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)

        if self._is_dragging and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self._snap_to_edge()
            self.settings.pos_x = self.x()
            self.settings.pos_y = self.y()
            self.settings.save()
            event.accept()

    def _snap_to_edge(self, margin: int = 24) -> None:
        """Snap to the nearest screen edge when dropped close to it (X11 / Windows only)."""
        if QtGui.QGuiApplication.platformName() in ("wayland", "offscreen"):
            return
        screen = self.screen()
        if not screen:
            return
        g = screen.availableGeometry()
        x, y = self.x(), self.y()
        if abs(x - g.left()) < margin:
            x = g.left()
        elif abs(x + self.width() - g.right()) < margin:
            x = g.right() - self.width() + 1
        if abs(y - g.top()) < margin:
            y = g.top()
        elif abs(y + self.height() - g.bottom()) < margin:
            y = g.bottom() - self.height() + 1
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_mode()
            event.accept()

    def moveEvent(self, event: QtGui.QMoveEvent) -> None:
        super().moveEvent(event)
        self.settings.pos_x = self.x()
        self.settings.pos_y = self.y()
        self.settings.save()

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #121316;
                color: #e2e8f0;
                border: 1px solid #282c35;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #20242d;
            }
        """)

        v_menu = menu.addMenu("View")
        for mode, label in [("bars", "Bars (detailed)"), ("rings", "Rings"), ("mini", "Mini (one line each)")]:
            act = v_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.view_mode == mode)
            act.triggered.connect(lambda checked=False, m=mode: self.set_view_mode(m))

        style_action = menu.addAction("Show Used % instead" if not self.settings.show_used else "Show Remaining % instead")
        style_action.triggered.connect(self.toggle_style)

        fade_action = menu.addAction("Fade when idle")
        fade_action.setCheckable(True)
        fade_action.setChecked(self.settings.fade_when_idle)

        def _toggle_fade():
            self.settings.fade_when_idle = not self.settings.fade_when_idle
            self.settings.save()
            if not self.settings.fade_when_idle:
                self.setWindowOpacity(self.settings.opacity)

        fade_action.triggered.connect(_toggle_fade)

        notif_action = menu.addAction("Low-quota notifications")
        notif_action.setCheckable(True)
        notif_action.setChecked(self.settings.notifications)

        def _toggle_notif():
            self.settings.notifications = not self.settings.notifications
            self.settings.save()

        notif_action.triggered.connect(_toggle_notif)

        sessions_menu = menu.addMenu("Active Sessions")
        act_toggle = sessions_menu.addAction("Enabled")
        act_toggle.setCheckable(True)
        act_toggle.setChecked(self.settings.show_active_sessions)
        act_toggle.triggered.connect(self._toggle_active_sessions)

        sessions_menu.addSeparator()
        act_bars = sessions_menu.addAction("Show Separate Bars for Each")
        act_bars.setCheckable(True)
        act_bars.setChecked(getattr(self.settings, "session_display_mode", "bars") == "bars")
        act_bars.triggered.connect(lambda: self._set_session_display_mode("bars"))

        act_cycle = sessions_menu.addAction("Cycle in Single Bar")
        act_cycle.setCheckable(True)
        act_cycle.setChecked(getattr(self.settings, "session_display_mode", "bars") == "cycle")
        act_cycle.triggered.connect(lambda: self._set_session_display_mode("cycle"))

        active_list = self.usage.get_active_sessions()
        if active_list:
            sessions_menu.addSeparator()
            for s in active_list:
                item = sessions_menu.addAction(f"[{s.display_provider}] {s.notation}")
                item.setEnabled(False)

        menu.addSeparator()
        p_menu = menu.addMenu("Select Active Tab")
        choices = [
            ("all", "All (Overview)"),
            ("claude", "Claude Code"),
            ("codex", "OpenAI Codex"),
            ("antigravity", "Google Antigravity"),
        ]
        if "opencode" in self.usage.providers and self.usage.providers["opencode"].available:
            choices.append(("opencode", "OpenCode (OpenAI)"))
        for pid, name in choices:
            act = p_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(self.active_provider == pid)
            act.triggered.connect(lambda checked=False, p=pid: self.cycle_provider(p))

        menu.addSeparator()

        refresh_action = menu.addAction("Refresh Now")
        refresh_action.triggered.connect(self.manual_refresh)

        top_action = menu.addAction("Always on Top")
        top_action.setCheckable(True)
        top_action.setChecked(self.settings.always_on_top)

        def _toggle_top():
            self.settings.always_on_top = not self.settings.always_on_top
            self.settings.save()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.settings.always_on_top)
            self.show()

        top_action.triggered.connect(_toggle_top)

        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(QtWidgets.QApplication.instance().quit)

        menu.exec(global_pos)

    def _toggle_active_sessions(self) -> None:
        self.settings.show_active_sessions = not self.settings.show_active_sessions
        self.settings.save()
        self._update_geometry()

    def _set_session_display_mode(self, mode: str) -> None:
        self.settings.session_display_mode = mode
        self.settings.save()
        self._update_geometry()

    # --- Data Extraction Helpers ---
    def _get_provider_rows(self) -> Tuple[str, QColor, List[Tuple[str, str, str, float, str, str, Optional[float], Optional[QColor]]]]:
        """Rows with burn-rate ETA subtitles and the used/remaining wording applied."""
        title, brand, rows = self._get_provider_rows_raw()
        out = []
        for idx, row in enumerate(rows):
            icon, r_title, r_sub, pct, reset, sev, wk, color = row
            pid, kind = None, "session"
            if self.active_provider == "all":
                all_pids = ("claude", "codex", "antigravity", "opencode")
                pid = all_pids[idx] if idx < len(all_pids) else None
            elif idx == 0:
                pid = self.active_provider
            if pid:
                pu = self.usage.providers.get(pid)
                eta = self.insights.eta_text(pid, kind, pu.primary_window if pu else None)
                if eta:
                    acc = pu.account_identifier if pu else None
                    r_sub = f"{eta} • {acc}" if acc else eta
            if self.settings.show_used:
                r_sub = r_sub.replace("remaining", "used")
            out.append((icon, r_title, r_sub, pct, reset, sev, wk, color))
        return title, brand, out

    def _get_provider_rows_raw(self) -> Tuple[str, QColor, List[Tuple[str, str, str, float, str, str, Optional[float], Optional[QColor]]]]:
        """
        Returns (display_title, brand_color, rows)
        Each row: (icon_type, title, subtitle, used_pct, reset_str, severity, weekly_pct, row_accent_color)
        """
        if self.active_provider == "all":
            brand_color = QColor(168, 85, 247)  # Violet / Gradient Pulse
            title = "Guage"

            c_data = self.usage.providers.get("claude")
            cx_data = self.usage.providers.get("codex")
            ag_data = self.usage.providers.get("antigravity")
            oc_data = self.usage.providers.get("opencode")

            # Row 1: Claude Code
            cp = c_data.primary_window.remaining_pct if (c_data and c_data.primary_window) else 100.0
            cwk = c_data.secondary_window.remaining_pct if (c_data and c_data.secondary_window) else 33.0
            cr = c_data.primary_window.formatted_reset if (c_data and c_data.primary_window) else "Ready"
            c_sev = "critical" if cp <= 10.0 else ("warning" if cp <= 25.0 else "normal")

            # Row 2: OpenAI Codex
            cxp = cx_data.primary_window.remaining_pct if (cx_data and cx_data.primary_window) else 36.0
            cxwk = cx_data.secondary_window.remaining_pct if (cx_data and cx_data.secondary_window) else 74.0
            cxr = cx_data.primary_window.formatted_reset if (cx_data and cx_data.primary_window) else "1h 55m"
            cx_sev = "critical" if cxp <= 10.0 else ("warning" if cxp <= 25.0 else "normal")

            # Row 3: Google Antigravity
            agp = ag_data.primary_window.remaining_pct if (ag_data and ag_data.primary_window) else 26.9
            agwk = ag_data.secondary_window.remaining_pct if (ag_data and ag_data.secondary_window) else 87.2
            agr = ag_data.primary_window.formatted_reset if (ag_data and ag_data.primary_window) else "2h 15m"
            ag_sev = "critical" if agp <= 10.0 else ("warning" if agp <= 25.0 else "normal")

            c_acc = c_data.account_identifier if c_data else None
            cx_acc = cx_data.account_identifier if cx_data else None
            ag_acc = ag_data.account_identifier if ag_data else None
            oc_acc = oc_data.account_identifier if oc_data else None

            c_sub = f"5h session • {c_acc}" if c_acc else "5h session remaining"
            cx_sub = f"5h session • {cx_acc}" if cx_acc else "5h session remaining"

            ag_sub = "Gemini 5h remaining"
            if ag_data:
                m_3p = ag_data.get_model("claude") or ag_data.get_model("gpt") or (ag_data.models[1] if len(ag_data.models) >= 2 else None)
                if m_3p:
                    w5_3p = m_3p.get_window("5h")
                    if w5_3p and w5_3p.remaining_pct <= 10.0:
                        ag_sub = f"Gemini 5h • Claude/GPT 5h: {int(w5_3p.remaining_pct)}%"
            if ag_acc:
                ag_sub = f"Gemini 5h • {ag_acc}" if ag_sub == "Gemini 5h remaining" else f"{ag_sub} • {ag_acc}"

            rows = [
                ("timer", "Claude Code", c_sub, cp, format_reset_label(cr), c_sev, cwk, UITheme.CLAUDE_PRIMARY),
                ("chart", "OpenAI Codex", cx_sub, cxp, format_reset_label(cxr), cx_sev, cxwk, UITheme.CODEX_PRIMARY),
                ("sparkle", "Antigravity", ag_sub, agp, format_reset_label(agr), ag_sev, agwk, UITheme.ANTIGRAVITY_PRIMARY),
            ]

            oc_data = self.usage.providers.get("opencode")
            if oc_data and oc_data.available:
                ocp = oc_data.primary_window.remaining_pct if oc_data.primary_window else 53.0
                ocwk = oc_data.secondary_window.remaining_pct if oc_data.secondary_window else 93.0
                ocr = oc_data.primary_window.formatted_reset if oc_data.primary_window else "Ready"
                oc_sev = "critical" if ocp <= 10.0 else ("warning" if ocp <= 25.0 else "normal")
                oc_sub = f"OpenAI Sub • {oc_acc}" if oc_acc else "OpenAI 5h remaining"
                rows.append(
                    ("timer", "OpenCode", oc_sub, ocp, format_reset_label(ocr), oc_sev, ocwk, UITheme.OPENCODE_PRIMARY)
                )

            return title, brand_color, rows

        data = self.usage.providers.get(self.active_provider)

        if self.active_provider == "claude":
            brand_color = UITheme.CLAUDE_ORANGE
            title = "Claude"
            p1 = data.primary_window.remaining_pct if (data and data.primary_window) else 100.0
            r1 = data.primary_window.formatted_reset if (data and data.primary_window) else "Ready"
            p2 = data.secondary_window.remaining_pct if (data and data.secondary_window) else 33.0
            r2 = data.secondary_window.formatted_reset if (data and data.secondary_window) else "1d 8h"

            p3 = p2
            r3 = r2
            sub3 = "Shares the weekly pool"
            if data:
                raw = data.extra_details.get("raw_payload", {}) if data.extra_details else {}
                scoped = (raw.get("seven_day_sonnet") or {}).get("utilization")
                if scoped is not None:
                    p3 = max(0.0, 100.0 - float(scoped))
                    sub3 = "Dedicated Sonnet limit"
                elif data.models and len(data.models) > 1 and data.models[1].windows:
                    p3 = data.models[1].windows[0].remaining_pct
                    r3 = data.models[1].windows[0].formatted_reset
                    sub3 = "Dedicated model limit"

            sev1 = "critical" if p1 <= 10.0 else ("warning" if p1 <= 25.0 else "normal")
            sev2 = "critical" if p2 <= 10.0 else ("warning" if p2 <= 25.0 else "normal")
            sev3 = "critical" if p3 <= 10.0 else ("warning" if p3 <= 25.0 else "normal")

            rows = [
                ("timer", "Session", "5h sliding window", p1, format_reset_label(r1), sev1, None, None),
                ("chart", "Weekly", "Opus + Sonnet + Haiku", p2, format_reset_label(r2), sev2, None, None),
                ("sparkle", "Sonnet", sub3, p3, format_reset_label(r3), sev3, None, None),
            ]
            return title, brand_color, rows

        elif self.active_provider == "codex":
            brand_color = UITheme.CODEX_GREEN
            title = "Codex"
            p1 = data.primary_window.remaining_pct if (data and data.primary_window) else 36.0
            r1 = data.primary_window.formatted_reset if (data and data.primary_window) else "1h 55m"
            p2 = data.secondary_window.remaining_pct if (data and data.secondary_window) else 74.0
            r2 = data.secondary_window.formatted_reset if (data and data.secondary_window) else "4d 20h"

            p3 = 68.0
            r3 = "4d 14h"
            if data:
                for m in data.models:
                    if "gpt" in m.model_name.lower() and m.windows:
                        p3 = m.windows[0].remaining_pct
                        r3 = m.windows[0].formatted_reset

            sev1 = "critical" if p1 <= 10.0 else ("warning" if p1 <= 25.0 else "normal")
            sev2 = "critical" if p2 <= 10.0 else ("warning" if p2 <= 25.0 else "normal")
            sev3 = "critical" if p3 <= 10.0 else ("warning" if p3 <= 25.0 else "normal")

            rows = [
                ("timer", "Session", "5h sliding window", p1, format_reset_label(r1), sev1, None, None),
                ("chart", "Weekly", "Plus tier allowance", p2, format_reset_label(r2), sev2, None, None),
                ("sparkle", "Reserve", "GPT-5.6 Luna inference", p3, format_reset_label(r3), sev3, None, None),
            ]
            return title, brand_color, rows

        elif self.active_provider == "opencode":
            brand_color = UITheme.OPENCODE_PRIMARY
            title = "OpenCode"
            p1 = data.primary_window.remaining_pct if (data and data.primary_window) else 53.0
            r1 = data.primary_window.formatted_reset if (data and data.primary_window) else "Ready"
            p2 = data.secondary_window.remaining_pct if (data and data.secondary_window) else 93.0
            r2 = data.secondary_window.formatted_reset if (data and data.secondary_window) else "6d 22h"

            p3 = 100.0
            r3 = "Ready"
            sub3 = "ChatGPT Plus connected"
            if data and data.extra_details and data.extra_details.get("email"):
                sub3 = f"Connected: {data.extra_details['email']}"
            if data and data.models:
                m_model = data.models[0]
                if m_model and m_model.windows:
                    p3 = m_model.windows[0].remaining_pct
                    r3 = m_model.windows[0].formatted_reset

            sev1 = "critical" if p1 <= 10.0 else ("warning" if p1 <= 25.0 else "normal")
            sev2 = "critical" if p2 <= 10.0 else ("warning" if p2 <= 25.0 else "normal")
            sev3 = "critical" if p3 <= 10.0 else ("warning" if p3 <= 25.0 else "normal")

            rows = [
                ("timer", "Session", "5h sliding window", p1, format_reset_label(r1), sev1, None, None),
                ("chart", "Weekly", "Plus tier allowance", p2, format_reset_label(r2), sev2, None, None),
                ("sparkle", "OpenAI Sub", sub3, p3, format_reset_label(r3), sev3, None, None),
            ]
            return title, brand_color, rows

        else:  # Antigravity
            brand_color = UITheme.ANTIGRAVITY_BLUE
            title = "Antigravity"
            p_gem_5h = data.primary_window.remaining_pct if (data and data.primary_window) else 26.9
            r_gem_5h = data.primary_window.formatted_reset if (data and data.primary_window) else "2h 15m"
            p_gem_wk = data.secondary_window.remaining_pct if (data and data.secondary_window) else 87.2
            r_gem_wk = data.secondary_window.formatted_reset if (data and data.secondary_window) else "17h 34m"

            p_3p_5h = 100.0
            r_3p_5h = "Ready"
            p_3p_wk = 100.0
            r_3p_wk = "Ready"

            if data:
                m_3p = data.get_model("claude") or data.get_model("gpt") or (data.models[1] if len(data.models) >= 2 else None)
                if m_3p:
                    w5 = m_3p.get_window("5h")
                    wwk = m_3p.get_window("weekly")
                    if w5:
                        p_3p_5h = w5.remaining_pct
                        r_3p_5h = w5.formatted_reset
                    if wwk:
                        p_3p_wk = wwk.remaining_pct
                        r_3p_wk = wwk.formatted_reset

            sev_gem_5h = "critical" if p_gem_5h <= 10.0 else ("warning" if p_gem_5h <= 25.0 else "normal")
            sev_gem_wk = "critical" if p_gem_wk <= 10.0 else ("warning" if p_gem_wk <= 25.0 else "normal")
            sev_3p_5h = "critical" if p_3p_5h <= 10.0 else ("warning" if p_3p_5h <= 25.0 else "normal")
            sev_3p_wk = "critical" if p_3p_wk <= 10.0 else ("warning" if p_3p_wk <= 25.0 else "normal")

            rows = [
                ("timer", "Gemini 5h", "Flash + Pro 5h remaining", p_gem_5h, format_reset_label(r_gem_5h), sev_gem_5h, None, None),
                ("chart", "Gemini Weekly", "Antigravity Pro weekly pool", p_gem_wk, format_reset_label(r_gem_wk), sev_gem_wk, None, None),
                ("timer", "Claude & GPT 5h", "3P models 5h sliding window", p_3p_5h, format_reset_label(r_3p_5h), sev_3p_5h, None, None),
                ("sparkle", "Claude & GPT Wk", "Shared 3P weekly allowance", p_3p_wk, format_reset_label(r_3p_wk), sev_3p_wk, None, None),
            ]
            return title, brand_color, rows

    # --- Painting ---
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w = self.width()
        h = self.height()
        r = UITheme.CORNER_RADIUS

        theme = UITheme.get_theme(self.active_provider)

        # 1. Main Matte Dark Background adapted to active model theme
        card_path = QPainterPath()
        card_path.addRoundedRect(QRectF(1, 1, w - 2, h - 2), r, r)
        p.fillPath(card_path, theme.bg_color)

        p.setPen(QPen(theme.border_color, 1.2))
        p.drawPath(card_path)

        # 2. Header (Logo + Brand + Tabs + Mode Toggle)
        title, _, rows = self._get_provider_rows()
        self._paint_header(p, w, title, theme)

        # 3. Content View (Bars vs Rings from image.png)
        if self.view_mode == "bars":
            self._paint_bars_view(p, w, h, rows, theme)
        elif self.view_mode == "mini":
            self._paint_mini_view(p, w, h, rows, theme)
        else:
            self._paint_rings_view(p, w, h, rows, theme)

    def _paint_header(self, p: QPainter, w: float, title: str, theme: ProviderTheme) -> None:
        # Brand Logo: First Alphabet Badge (C, C, A, M)
        logo_rect = QRectF(20, 16, 22, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(theme.logo_bg))
        p.drawRoundedRect(logo_rect, 6, 6)

        p.setFont(QFont("Segoe UI", 10.5, QFont.Weight.Black))
        p.setPen(QPen(theme.logo_fg))
        p.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter, theme.logo_char)

        # Brand Title
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        p.setPen(QPen(theme.text_white))
        title_w = 95 if title == "Guage" else 92
        p.drawText(QRectF(48, 13, title_w, 28), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)

        # Provider Switcher Pills
        for pid, t_label, pill_rect in self._get_tab_rects(w):
            is_active = (self.active_provider == pid)
            if is_active:
                p.setBrush(QBrush(theme.tab_active_bg))
                p.setPen(QPen(theme.tab_active_border, 1.2))
                p.drawRoundedRect(pill_rect, 11, 11)
                p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                p.setPen(QPen(theme.text_white))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(Qt.PenStyle.NoPen)
                p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
                p.setPen(QPen(theme.text_muted))
            p.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, t_label)

        # Mode Toggle Icon (Top Right)
        toggle_box = self._get_toggle_rect(w)
        p.setBrush(QBrush(QColor(255, 255, 255, 12)))
        p.setPen(QPen(theme.border_color, 1))
        p.drawRoundedRect(toggle_box, 6, 6)
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QPen(theme.text_muted))
        toggle_symbol = "≡" if self.view_mode == "rings" else "◎"
        p.drawText(toggle_box, Qt.AlignmentFlag.AlignCenter, toggle_symbol)

    def _paint_bars_view(self, p: QPainter, w: float, h: float, rows: List[Tuple[str, str, str, float, str, str, Optional[float], Optional[QColor]]], theme: ProviderTheme) -> None:
        """Top card design from image.png with weekly % beneath main % in overview."""
        n = len(rows)
        if n > 3:
            start_y = 62
            row_gap = 65
            bar_offset = 35
            bar_h = 6.0
            title_font = QFont("Segoe UI", 10.5, QFont.Weight.Bold)
            sub_font = QFont("Segoe UI", 8.0)
            pct_font = QFont("Segoe UI", 12.0, QFont.Weight.Bold)
            meta_font = QFont("Segoe UI", 8.0)
            icon_y_off = 0
            sub_y_off = 15
        else:
            start_y = 70
            row_gap = 92
            bar_offset = 46
            bar_h = 7.5
            title_font = QFont("Segoe UI", 11.5, QFont.Weight.Bold)
            sub_font = QFont("Segoe UI", 9.0)
            pct_font = QFont("Segoe UI", 13.5, QFont.Weight.Bold)
            meta_font = QFont("Segoe UI", 8.5)
            icon_y_off = 2
            sub_y_off = 18

        for i, (icon_type, r_title, r_sub, remaining_pct, reset_str, severity, weekly_pct, r_color) in enumerate(rows):
            ry = start_y + i * row_gap
            row_accent = r_color or theme.accent_color

            # Icon
            self._paint_row_icon(p, 26, ry + icon_y_off, icon_type, severity, row_accent)

            # Title
            p.setFont(title_font)
            p.setPen(QPen(theme.text_white))
            p.drawText(QRectF(56, ry - 4, 210, 22), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, r_title)

            # Draw small active session notation badge if session is running
            row_pid = None
            if self.active_provider == "all":
                row_pid = ("claude", "codex", "antigravity", "opencode")[i] if i < len(rows) else None
            elif i == 0:
                row_pid = self.active_provider

            if self.settings.show_active_sessions and row_pid:
                p_sessions = self.usage.get_active_sessions(row_pid)
                if p_sessions:
                    fm = QtGui.QFontMetrics(title_font)
                    tw = fm.horizontalAdvance(r_title)
                    badge_rect = QRectF(56 + tw + 8, ry - 1, 48, 16)
                    badge_bg = QColor(row_accent.red(), row_accent.green(), row_accent.blue(), 26)
                    badge_border = QColor(row_accent.red(), row_accent.green(), row_accent.blue(), 85)
                    p.setBrush(QBrush(badge_bg))
                    p.setPen(QPen(badge_border, 1))
                    p.drawRoundedRect(badge_rect, 4, 4)

                    # Accent dot + text
                    p.setBrush(QBrush(row_accent))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(QPointF(badge_rect.left() + 7, badge_rect.center().y()), 2.5, 2.5)

                    p.setFont(QFont("Segoe UI", 7.5, QFont.Weight.Bold))
                    p.setPen(QPen(row_accent))
                    p.drawText(
                        QRectF(badge_rect.left() + 12, badge_rect.top(), badge_rect.width() - 14, badge_rect.height()),
                        Qt.AlignmentFlag.AlignCenter,
                        "LIVE",
                    )

            # Subtitle
            p.setFont(sub_font)
            p.setPen(QPen(theme.text_muted))
            p.drawText(QRectF(56, ry + sub_y_off, 220, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, r_sub)

            # Percentage (Large bold status color - Remaining style)
            if remaining_pct <= 10.0 or severity == "critical":
                color = UITheme.CRITICAL_ACCENT
            elif remaining_pct <= 25.0 or severity == "warning":
                color = UITheme.WARNING_ACCENT
            else:
                color = row_accent

            p.setFont(pct_font)
            p.setPen(QPen(color))
            p.drawText(QRectF(w - 140, ry - 6, 114, 24), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{int(self._disp(remaining_pct))}%")

            # Subtitle beneath percentage (Weekly % + Reset in All view, or clean Reset countdown / Ready in detailed view)
            p.setFont(meta_font)
            p.setPen(QPen(theme.text_muted))
            clean_time = reset_str.replace("Reset ", "").strip()
            if clean_time.lower() == "ready":
                clean_time = "Ready"

            if weekly_pct is not None:
                sub_meta = f"Wk: {int(self._disp(weekly_pct))}% • {clean_time}"
            else:
                sub_meta = "Ready" if clean_time == "Ready" else reset_str
            p.drawText(QRectF(w - 210, ry + sub_y_off, 184, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, sub_meta)

            # Progress Bar Track
            bar_y = ry + bar_offset
            bar_w = w - 52

            p.setBrush(QBrush(theme.progress_track))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(26, bar_y, bar_w, bar_h), bar_h / 2, bar_h / 2)

            # Progress Bar Fill (Remaining style: full when 100%, drains down as used)
            fill_w = max(0.0, min(bar_w, (self._disp(remaining_pct) / 100.0) * bar_w))
            if fill_w > 0:
                p.setBrush(QBrush(color))
                p.drawRoundedRect(QRectF(26, bar_y, fill_w, bar_h), bar_h / 2, bar_h / 2)

        layout = self._get_bars_layout(w)

        # Active Sessions live section (separate bars or cycled bar)
        self._paint_active_sessions_section(p, layout, theme)

        # Trendline sparkline
        self._paint_trend_strip(p, layout, rows, theme)

        # Footer
        p.setPen(QPen(theme.divider_color, 1))
        p.drawLine(26, int(layout.divider_y), int(w - 26), int(layout.divider_y))

        p.setFont(QFont("Segoe UI", 8.5))
        p.setPen(QPen(theme.text_dim))
        if self._is_refreshing:
            upd_text = "Refreshing usage data..."
        else:
            secs = int(time.time() - self.usage.last_updated)
            upd_text = "Updated just now" if secs < 60 else f"Updated {secs // 60}m ago"
            hint = self._best_hint()
            if hint:
                upd_text += f"  |  {hint}"
        p.drawText(layout.footer_text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, upd_text)

        # Right dot + time
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(UITheme.WARNING_ACCENT if self._is_refreshing else theme.accent_color))
        p.drawEllipse(layout.footer_dot_center, 3, 3)

        p.setPen(QPen(theme.text_dim))
        status_tail = "..." if self._is_refreshing else "60s"
        p.drawText(layout.footer_status_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, status_tail)

    def _best_hint(self) -> Optional[str]:
        if self.active_provider != "all":
            curr = self.usage.providers.get(self.active_provider)
            if curr and (curr.account_email or curr.account_name):
                return curr.account_email or curr.account_name
            return None
        best = best_provider(self.usage)
        if not best:
            return None
        pid, headroom = best
        return f"Best now: {PROVIDER_LABELS.get(pid, pid)} ({int(self._disp(headroom))}%{' used' if self.settings.show_used else ''})"

    def _paint_active_sessions_section(self, p: QPainter, layout: BarsLayout, theme: ProviderTheme) -> None:
        """Paint running sessions section with theme matching and separate or cycled bars."""
        if not layout.session_rects or not self.settings.show_active_sessions:
            return

        sessions = self.usage.get_active_sessions(self.active_provider if self.active_provider != "all" else None)
        if not sessions:
            return

        # 1. Section Header
        if layout.section_header_rect:
            p.setFont(QFont("Segoe UI", 7.5, QFont.Weight.Bold))
            p.setPen(QPen(theme.text_dim))
            header_title = "ACTIVE SESSIONS" if len(sessions) > 1 else "ACTIVE SESSION"
            p.drawText(layout.section_header_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, header_title)

            # Counter on right of header
            if getattr(self.settings, "session_display_mode", "bars") == "cycle":
                idx = (self._cycle_tick // 3) % len(sessions)
                count_lbl = f"{idx + 1} of {len(sessions)}"
            else:
                count_lbl = f"{len(sessions)} active"
            p.setFont(QFont("Segoe UI", 7.5))
            p.drawText(layout.section_header_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, count_lbl)

        # 2. Render each active session bar
        is_cycle = getattr(self.settings, "session_display_mode", "bars") == "cycle"
        for idx, (s_rect, s) in enumerate(layout.session_rects):
            counter_tag = f"({idx + 1}/{len(sessions)})" if is_cycle and len(sessions) > 1 else None
            self._paint_single_session_bar(p, s_rect, s, theme, counter_tag)

    def _paint_single_session_bar(
        self,
        p: QPainter,
        rect: QRectF,
        s: ActiveSession,
        theme: ProviderTheme,
        counter_tag: Optional[str] = None,
    ) -> None:
        """Paint an individual active session bar styled seamlessly to the active provider theme."""
        # Container background and hairline border matching theme
        p.setBrush(QBrush(theme.progress_track))
        p.setPen(QPen(theme.border_color, 1.0))
        p.drawRoundedRect(rect, 6.0, 6.0)

        # Provider Brand color (Claude terracotta, Codex monochrome white, AGY electric blue, OpenCode mint emerald)
        if s.provider_id == "claude":
            brand_color = UITheme.CLAUDE_PRIMARY
        elif s.provider_id == "codex":
            brand_color = UITheme.CODEX_PRIMARY
        elif s.provider_id == "opencode":
            brand_color = UITheme.OPENCODE_PRIMARY
        else:
            brand_color = UITheme.ANTIGRAVITY_PRIMARY

        # Provider Badge Pill
        pill_font = QFont("Segoe UI", 7.5, QFont.Weight.Bold)
        fm_p = QtGui.QFontMetrics(pill_font)
        pill_text = s.display_provider
        pill_w = fm_p.horizontalAdvance(pill_text) + 12.0
        pill_rect = QRectF(rect.left() + 6.0, rect.top() + 4.0, pill_w, 18.0)

        pill_bg = QColor(brand_color.red(), brand_color.green(), brand_color.blue(), 26)
        pill_border = QColor(brand_color.red(), brand_color.green(), brand_color.blue(), 75)
        p.setBrush(QBrush(pill_bg))
        p.setPen(QPen(pill_border, 1.0))
        p.drawRoundedRect(pill_rect, 4.0, 4.0)

        p.setFont(pill_font)
        p.setPen(QPen(brand_color))
        p.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, pill_text)

        next_x = pill_rect.right() + 4.0

        # Model Badge Pill (Short model identifier)
        model_name = s.short_model
        if model_name:
            m_font = QFont("Segoe UI", 7.0, QFont.Weight.DemiBold)
            fm_m = QtGui.QFontMetrics(m_font)
            m_w = fm_m.horizontalAdvance(model_name) + 10.0
            m_rect = QRectF(next_x, rect.top() + 4.0, m_w, 18.0)

            # Subtly tinted dark glass badge
            m_bg = QColor(255, 255, 255, 14)
            m_border = QColor(255, 255, 255, 36)
            p.setBrush(QBrush(m_bg))
            p.setPen(QPen(m_border, 1.0))
            p.drawRoundedRect(m_rect, 4.0, 4.0)

            p.setFont(m_font)
            p.setPen(QPen(QColor("#e2e8f0")))
            p.drawText(m_rect, Qt.AlignmentFlag.AlignCenter, model_name)

            next_x = m_rect.right() + 4.0

        # Pulse indicator dot beside pill(s)
        dot_x = next_x + 3.0
        dot_y = rect.center().y()
        p.setBrush(QBrush(brand_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(dot_x, dot_y), 2.5, 2.5)

        # Status / elapsed / counter tag on right
        if counter_tag:
            right_text = counter_tag
        else:
            if s.started_at is not None and s.started_at > 0:
                elapsed = int(time.time() - s.started_at)
                if elapsed < 60:
                    dur_str = f"{elapsed}s"
                elif elapsed < 3600:
                    dur_str = f"{elapsed // 60}m"
                else:
                    dur_str = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"
                right_text = f"{dur_str} • {s.status}"
            else:
                right_text = s.status.capitalize()

        p.setFont(QFont("Segoe UI", 7.5))
        fm_rt = QtGui.QFontMetrics(p.font())
        right_w = fm_rt.horizontalAdvance(right_text) + 6.0
        right_rect = QRectF(rect.right() - right_w - 8.0, rect.top(), right_w, rect.height())
        p.setPen(QPen(theme.text_dim))
        p.drawText(right_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, right_text)

        # Main notation / title in the middle
        content_left = dot_x + 6.0
        content_right = right_rect.left() - 6.0
        avail_w = max(10.0, content_right - content_left)

        p.setFont(QFont("Segoe UI", 8.0))
        p.setPen(QPen(theme.text_white))

        if model_name:
            # Build title without repeating model tag
            t_parts = []
            if s.title:
                t_parts.append(s.title)
            if s.detail and s.detail != s.title:
                det = s.detail
                if model_name.lower() in det.lower():
                    clean_d = [p_str.strip() for p_str in det.split("•") if model_name.lower() not in p_str.lower()]
                    det = " • ".join(clean_d) if clean_d else None
                if det:
                    t_parts.append(det)
            elif s.project_name and s.project_name not in (s.title or ""):
                t_parts.append(f"in {s.project_name}")
            display_note = " • ".join(t_parts) if t_parts else (s.title or "Active Session")
        else:
            display_note = s.notation or s.title or "Active Session"

        fm_note = QtGui.QFontMetrics(p.font())
        elided = fm_note.elidedText(display_note, Qt.TextElideMode.ElideRight, int(avail_w))
        p.drawText(QRectF(content_left, rect.top(), avail_w, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

    def _paint_trend_strip(self, p: QPainter, layout: BarsLayout, rows, theme: ProviderTheme) -> None:
        """24h sparkline of remaining quota plus today's local token count."""
        history = getattr(self.aggregator, "history", None)
        stats = getattr(self.aggregator, "stats", None)
        if history is None:
            return

        label = "24h trend"
        if self.active_provider in ("claude", "codex") and stats is not None and hasattr(stats, "summary"):
            extra = stats.summary(self.active_provider)
            if extra:
                label = f"{extra}  |  24h trend"

        p.setFont(QFont("Segoe UI", 7.5))
        p.setPen(QPen(theme.text_dim))
        p.drawText(layout.trend_label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

        top = layout.sparkline_rect.top()
        height = layout.sparkline_rect.height()
        x0 = layout.sparkline_rect.left()
        width = layout.sparkline_rect.width()

        if self.active_provider == "all":
            series = [
                ("claude", UITheme.CLAUDE_PRIMARY),
                ("codex", UITheme.CODEX_PRIMARY),
                ("antigravity", UITheme.ANTIGRAVITY_PRIMARY),
            ]
            if "opencode" in self.usage.providers and self.usage.providers["opencode"].available:
                series.append(("opencode", UITheme.OPENCODE_PRIMARY))
        else:
            series = [(self.active_provider, theme.accent_color)]

        now = time.time()
        span = 24 * 3600.0
        drew = False
        for pid, color in series:
            pts = history.series(pid, "s", 24.0)
            if len(pts) < 2:
                continue
            drew = True
            path = QPainterPath()
            for i, (t, rem) in enumerate(pts):
                x = x0 + width * (1.0 - (now - t) / span)
                val = self._disp(rem)
                y = top + height * (1.0 - val / 100.0)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)
        if not drew:
            p.setFont(QFont("Segoe UI", 8))
            p.setPen(QPen(theme.text_dim))
            p.drawText(QRectF(x0, top, width, height), Qt.AlignmentFlag.AlignCenter, "Collecting history...")

    def _paint_mini_view(self, p: QPainter, w: float, h: float, rows, theme: ProviderTheme) -> None:
        """Ultra compact view: one line per row (name, thin bar, percentage, reset)."""
        n = len(rows)
        start_y = 56 if n > 3 else 64
        gap = 26 if n > 3 else 34
        for i, (_, r_title, _, remaining_pct, reset_str, severity, weekly_pct, r_color) in enumerate(rows):
            y = start_y + i * gap
            row_accent = r_color or theme.accent_color
            if remaining_pct <= 10.0 or severity == "critical":
                color = UITheme.CRITICAL_ACCENT
            elif remaining_pct <= 25.0 or severity == "warning":
                color = UITheme.WARNING_ACCENT
            else:
                color = row_accent

            p.setFont(QFont("Segoe UI", 8.5 if n > 3 else 9.5, QFont.Weight.Bold))
            p.setPen(QPen(theme.text_white))
            p.drawText(QRectF(24, y, 110, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, r_title)

            # Miniature live indicator dot
            row_pid = ("claude", "codex", "antigravity", "opencode")[i] if (self.active_provider == "all" and i < len(rows)) else self.active_provider
            if self.settings.show_active_sessions and row_pid:
                s_active = self.usage.get_active_sessions(row_pid)
                if s_active:
                    p.setBrush(QBrush(row_accent))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(QPointF(14, y + 10), 2.5, 2.5)

            bar_x, bar_w = 138.0, w - 138.0 - 150.0
            p.setBrush(QBrush(theme.progress_track))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(bar_x, y + 7, bar_w, 6), 3, 3)
            fill = max(0.0, min(bar_w, self._disp(remaining_pct) / 100.0 * bar_w))
            if fill > 0:
                p.setBrush(QBrush(color))
                p.drawRoundedRect(QRectF(bar_x, y + 7, fill, 6), 3, 3)

            p.setFont(QFont("Segoe UI", 9.5 if n > 3 else 10, QFont.Weight.Bold))
            p.setPen(QPen(color))
            p.drawText(QRectF(w - 144, y, 44, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{int(self._disp(remaining_pct))}%")

            p.setFont(QFont("Segoe UI", 8))
            p.setPen(QPen(theme.text_muted))
            clean = reset_str.replace("Reset ", "").strip()
            p.drawText(QRectF(w - 96, y, 74, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, clean)

    def _paint_rings_view(self, p: QPainter, w: float, h: float, rows: List[Tuple[str, str, str, float, str, str, Optional[float], Optional[QColor]]], theme: ProviderTheme) -> None:
        """Bottom card design from image.png with circular gauges."""
        n = max(1, len(rows))
        center_y = 112 if n > 3 else 114
        circle_r = 27.0 if n > 3 else 36.0
        ring_stroke = 5.0 if n > 3 else 6.0

        # Dynamic columns
        col_w = (w - 32) / n

        for i, (_, r_title, _, remaining_pct, reset_str, severity, weekly_pct, r_color) in enumerate(rows):
            cx = 16 + col_w * i + col_w / 2
            cy = center_y
            row_accent = r_color or theme.accent_color

            ring_rect = QRectF(cx - circle_r, cy - circle_r, circle_r * 2, circle_r * 2)

            # Track Circle
            p.setPen(QPen(theme.progress_track, ring_stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(ring_rect)

            # Active Progress Arc (Remaining style)
            if remaining_pct <= 10.0 or severity == "critical":
                color = UITheme.CRITICAL_ACCENT
            elif remaining_pct <= 25.0 or severity == "warning":
                color = UITheme.WARNING_ACCENT
            else:
                color = row_accent

            p.setPen(QPen(color, ring_stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))

            span_angle = -int((self._disp(remaining_pct) / 100.0) * 360 * 16)
            if span_angle != 0:
                p.drawArc(ring_rect, 90 * 16, span_angle)

            # Center % text
            p.setFont(QFont("Segoe UI", 9.5 if n > 3 else 11.5, QFont.Weight.Bold))
            p.setPen(QPen(theme.text_white))
            p.drawText(ring_rect, Qt.AlignmentFlag.AlignCenter, f"{int(self._disp(remaining_pct))}%")

            # Label below gauge
            p.setFont(QFont("Segoe UI", 8.0 if n > 3 else 9.5, QFont.Weight.Bold))
            p.setPen(QPen(theme.text_white))
            p.drawText(QRectF(cx - col_w / 2, cy + circle_r + (7 if n > 3 else 9), col_w, 16), Qt.AlignmentFlag.AlignCenter, r_title)

            # Live indicator dot for active session
            row_pid = ("claude", "codex", "antigravity", "opencode")[i] if (self.active_provider == "all" and i < len(rows)) else self.active_provider
            if self.settings.show_active_sessions and row_pid:
                s_active = self.usage.get_active_sessions(row_pid)
                if s_active:
                    p.setBrush(QBrush(UITheme.ACTIVE_ACCENT))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(QPointF(cx + col_w / 3.4, cy - circle_r - 2), 3, 3)

            # Reset & Weekly % below label
            clean_reset = reset_str.replace("Reset ", "").strip()
            if clean_reset.lower() == "ready":
                clean_reset = "Ready"

            if weekly_pct is not None:
                sub_label = f"Wk: {int(self._disp(weekly_pct))}% • {clean_reset}"
            else:
                sub_label = clean_reset

            p.setFont(QFont("Segoe UI", 7.0 if n > 3 else 8.0))
            p.setPen(QPen(theme.text_muted))
            p.drawText(QRectF(cx - col_w / 2 - 4, cy + circle_r + (24 if n > 3 else 28), col_w + 8, 14), Qt.AlignmentFlag.AlignCenter, sub_label)

        # Footer (Centered)
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QPen(theme.text_dim))
        if self._is_refreshing:
            upd_text = "Refreshing usage data..."
        else:
            secs = int(time.time() - self.usage.last_updated)
            upd_text = "Updated just now" if secs < 60 else f"Updated {secs // 60}m ago"
            if self.settings.show_active_sessions:
                all_active = self.usage.get_active_sessions()
                if all_active:
                    n_act = len(all_active)
                    upd_text = f"● {n_act} active session{'s' if n_act > 1 else ''}  |  {upd_text}"
        p.drawText(QRectF(16, h - 25, w - 32, 16), Qt.AlignmentFlag.AlignCenter, upd_text)

    def _paint_row_icon(self, p: QPainter, x: float, y: float, icon_type: str, severity: str, color: Optional[QColor] = None) -> None:
        """Draw clean vector icons in brand accent color."""
        icon_color = color or UITheme.GREEN_ACCENT
        p.setPen(QPen(icon_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)

        if icon_type == "timer":
            # Stopwatch
            p.drawEllipse(QRectF(x, y + 2, 14, 14))
            p.drawLine(QPointF(x + 7, y), QPointF(x + 7, y + 2))
            p.drawLine(QPointF(x + 7, y + 9), QPointF(x + 7, y + 5))
            p.drawLine(QPointF(x + 7, y + 9), QPointF(x + 10, y + 9))
        elif icon_type == "chart":
            # Bar chart
            p.setBrush(QBrush(icon_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x, y + 9, 3.5, 7), 1, 1)
            p.drawRoundedRect(QRectF(x + 5, y + 5, 3.5, 11), 1, 1)
            p.drawRoundedRect(QRectF(x + 10, y + 1, 3.5, 15), 1, 1)
        else:
            # Sparkle / Magic wand
            p.drawLine(QPointF(x + 1, y + 15), QPointF(x + 11, y + 5))
            p.drawPoint(QPointF(x + 14, y + 2))
            p.drawPoint(QPointF(x + 6, y + 1))
            p.drawPoint(QPointF(x + 15, y + 9))
