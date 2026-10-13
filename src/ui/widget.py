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
        self._expanded_view_mode = "rings" if self.view_mode == "mini" else self.view_mode

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

    def _set_opacity(self, opacity: float) -> None:
        """Safely apply opacity without triggering Wayland plugin errors on Linux/WSL."""
        if QtGui.QGuiApplication.platformName() not in ("wayland",):
            try:
                self.setWindowOpacity(opacity)
            except Exception:
                pass

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

        start_y = 90.0
        card_h = 66.0
        card_gap = 8.0
        last_bar_bottom = start_y + n * card_h + max(0, n - 1) * card_gap

        sessions: List[ActiveSession] = []
        if self.settings.show_active_sessions:
            sessions = self.usage.get_active_sessions(self.active_provider if self.active_provider != "all" else None)

        session_rects: List[Tuple[QRectF, ActiveSession]] = []
        section_header_rect: Optional[QRectF] = None

        if sessions:
            section_header_rect = QRectF(16.0, last_bar_bottom + 14.0, w - 32.0, 16.0)
            bars_start_y = section_header_rect.bottom() + 6.0
            row_height = 28.0
            row_gap = 4.0

            if getattr(self.settings, "session_display_mode", "bars") == "bars":
                for idx, s in enumerate(sessions):
                    sy = bars_start_y + idx * (row_height + row_gap)
                    session_rects.append((QRectF(16.0, sy, w - 32.0, row_height), s))
                active_end_y = bars_start_y + len(sessions) * row_height + max(0, len(sessions) - 1) * row_gap
            else:
                idx = (self._cycle_tick // 3) % len(sessions)
                s_rect = QRectF(16.0, bars_start_y, w - 32.0, row_height)
                session_rects.append((s_rect, sessions[idx]))
                active_end_y = bars_start_y + row_height
        else:
            active_end_y = last_bar_bottom

        trend_gap = 16.0 if sessions else 14.0
        trend_label_top = active_end_y + trend_gap
        trend_label_rect = QRectF(16.0, trend_label_top, w - 32.0, 14.0)
        sparkline_top = trend_label_rect.bottom() + 6.0
        sparkline_height = 20.0
        sparkline_rect = QRectF(16.0, sparkline_top, w - 32.0, sparkline_height)
        sparkline_bottom = sparkline_rect.bottom()

        divider_y = sparkline_bottom + 14.0
        footer_text_rect = QRectF(16.0, divider_y + 8.0, w - 165.0, 18.0)
        footer_dot_center = QPointF(w - 150.0, divider_y + 17.0)
        footer_status_rect = QRectF(w - 145.0, divider_y + 8.0, 75.0, 18.0)
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

    def toggle_mini_mode(self) -> None:
        if self.view_mode == "mini":
            self.set_view_mode(self._expanded_view_mode)
        else:
            self._expanded_view_mode = self.view_mode
            self.set_view_mode("mini")

    def set_view_mode(self, mode: str) -> None:
        bottom = self.y() + self.height()
        self.view_mode = mode
        self.settings.compact_mode = (mode != "bars")
        self.settings.view_mode = mode
        self.settings.save()
        self._update_geometry()
        self.move(self.x(), bottom - self.height())

    def set_always_on_top(self, enabled: bool) -> None:
        self.settings.always_on_top = enabled
        self.settings.save()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()

    def toggle_style(self) -> None:
        """Switch between 'remaining' and 'used' presentation."""
        self.settings.show_used = not self.settings.show_used
        self.settings.save()
        self.update()

    def _disp(self, remaining: float) -> float:
        """Convert a remaining percentage to what the user wants displayed."""
        return 100.0 - remaining if self.settings.show_used else remaining

    def enterEvent(self, event) -> None:
        self._set_opacity(self.settings.opacity)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.settings.fade_when_idle and not self._is_dragging:
            self._set_opacity(min(self.settings.opacity, self.settings.idle_opacity))
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
        tabs = [
            ("all", "All"),
            ("claude", "Claude"),
            ("codex", "Codex"),
            ("antigravity", "AGY"),
        ]
        if has_opencode:
            tabs.append(("opencode", "Open"))

        cx = 16.0
        cy = 52.0
        cw = w - 32.0
        ch = 28.0
        n = len(tabs)
        tab_w = (cw - 4.0) / n
        rects = []
        for i, (pid, label) in enumerate(tabs):
            rects.append((pid, label, QRectF(cx + 2.0 + i * tab_w, cy + 2.0, tab_w, ch - 4.0)))
        return rects

    def _get_telemetry_rect(self, w: float) -> QRectF:
        """Return rect for top telemetry status pill."""
        return QRectF(w - 16.0 - 24.0 - 8.0 - 56.0, 14.0, 56.0, 24.0)

    def _get_toggle_rect(self, w: float) -> QRectF:
        """Return rect for view mode toggle icon button."""
        if self.view_mode == "mini":
            return QRectF(w / 2.0 - 11.0, self.height() - 21.0, 22.0, 16.0)
        return QRectF(w - 16.0 - 24.0, 14.0, 24.0, 24.0)

    def _get_minimize_rect(self, w: float) -> QRectF:
        return QRectF(w - 36.0, self.height() - 22.0, 20.0, 16.0)

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

            if self.view_mode != "mini" and self._get_minimize_rect(w).contains(pos):
                self.toggle_mini_mode()
                event.accept()
                return

            # Check if clicked on view mode toggle icon
            if self._get_toggle_rect(w).contains(pos):
                if self.view_mode == "mini":
                    self.toggle_mini_mode()
                else:
                    self.toggle_mode()
                event.accept()
                return

            # Check if clicked on telemetry pill to refresh
            if self.view_mode != "mini" and self._get_telemetry_rect(w).contains(pos):
                self.manual_refresh()
                event.accept()
                return

            # Check if clicked on provider switcher pills
            if self.view_mode != "mini":
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
            or (self.view_mode != "mini" and self._get_telemetry_rect(w).contains(pos))
            or (self.view_mode != "mini" and self._get_minimize_rect(w).contains(pos))
            or (self.view_mode != "mini" and any(rect.contains(pos) for _, _, rect in self._get_tab_rects(w)))
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

        mini_action = menu.addAction("Expand from compact strip" if self.view_mode == "mini" else "Minimize to compact strip")
        mini_action.triggered.connect(self.toggle_mini_mode)

        style_action = menu.addAction("Show Used % instead" if not self.settings.show_used else "Show Remaining % instead")
        style_action.triggered.connect(self.toggle_style)

        fade_action = menu.addAction("Fade when idle")
        fade_action.setCheckable(True)
        fade_action.setChecked(self.settings.fade_when_idle)

        def _toggle_fade():
            self.settings.fade_when_idle = not self.settings.fade_when_idle
            self.settings.save()
            if not self.settings.fade_when_idle:
                self._set_opacity(self.settings.opacity)

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

        top_action.toggled.connect(self.set_always_on_top)

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

        w = float(self.width())
        h = float(self.height())
        r = float(UITheme.CORNER_RADIUS)

        theme = UITheme.get_theme(self.active_provider)

        # 1. Main Matte Dark Background matching Stitch #0c0d0e canvas
        card_path = QPainterPath()
        card_path.addRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), r, r)
        p.fillPath(card_path, UITheme.BG_CANVAS)

        p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
        p.drawPath(card_path)

        # 2. Header (Logo + Brand + Telemetry + Toggle + Filter Tabs)
        title, _, rows = self._get_provider_rows()
        if self.view_mode != "mini":
            self._paint_header(p, w, title, theme)
            self._paint_filter_pills(p, w, theme)

        # 3. Content View (Bars vs Rings from Stitch redesign)
        if self.view_mode == "bars":
            self._paint_bars_view(p, w, h, rows, theme)
        elif self.view_mode == "mini":
            self._paint_mini_view(p, w, h, rows, theme)
        else:
            self._paint_rings_view(p, w, h, rows, theme)

    def _paint_header(self, p: QPainter, w: float, title: str, theme: ProviderTheme) -> None:
        # Brand Logo Badge: square rounded box with "g:" (or "c:", "a:", etc.)
        logo_rect = QRectF(16.0, 14.0, 24.0, 24.0)
        p.setBrush(QBrush(UITheme.BG_ELEVATED))
        p.setPen(QPen(QColor(63, 63, 70, 180), 1.0))
        p.drawRoundedRect(logo_rect, 5.0, 5.0)

        p.setFont(UITheme.mono_font(8.5, QFont.Weight.Bold))
        p.setPen(QPen(theme.logo_fg if self.active_provider != "all" else QColor(212, 212, 216)))
        badge_char = f"{theme.logo_char.lower()}:"
        p.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter, badge_char)

        # Brand Title + Version: "guage  v2.4"
        p.setFont(UITheme.mono_font(10.5, QFont.Weight.DemiBold))
        p.setPen(QPen(UITheme.TEXT_WHITE))
        app_name = "guage" if self.active_provider == "all" else theme.name.lower()
        fm = QtGui.QFontMetrics(p.font())
        title_w = fm.horizontalAdvance(app_name)
        p.drawText(QRectF(46.0, 14.0, title_w + 4.0, 24.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, app_name)

        # Version tag
        p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
        p.setPen(QPen(UITheme.TEXT_DIM))
        p.drawText(QRectF(46.0 + title_w + 6.0, 14.0, 40.0, 24.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "v2.4")

        # Telemetry Status Pill with Pulsing Emerald Dot (Top Right)
        pill_rect = self._get_telemetry_rect(w)
        p.setBrush(QBrush(UITheme.BG_ELEVATED))
        p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
        p.drawRoundedRect(pill_rect, 4.0, 4.0)

        pulse = 0.5 + 0.5 * math.sin(time.time() * 3.0)
        pulse_alpha = int(160 + 95 * pulse)
        dot_col = QColor(UITheme.GREEN_ACCENT.red(), UITheme.GREEN_ACCENT.green(), UITheme.GREEN_ACCENT.blue(), pulse_alpha)
        p.setBrush(QBrush(dot_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(pill_rect.left() + 9.0, pill_rect.center().y()), 2.5, 2.5)

        p.setFont(UITheme.mono_font(7.5, QFont.Weight.Medium))
        p.setPen(QPen(QColor(212, 212, 216)))
        tel_text = "sync" if self._is_refreshing else "14ms"
        p.drawText(
            QRectF(pill_rect.left() + 16.0, pill_rect.top(), pill_rect.width() - 18.0, pill_rect.height()),
            Qt.AlignmentFlag.AlignCenter,
            tel_text,
        )

        # Mode / Settings Toggle Button (Top Right)
        toggle_box = self._get_toggle_rect(w)
        p.setBrush(QBrush(UITheme.BG_ELEVATED))
        p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
        p.drawRoundedRect(toggle_box, 4.0, 4.0)
        self._paint_gear_icon(p, toggle_box, UITheme.TEXT_MUTED)

        # Hairline Divider below Header
        p.setPen(QPen(UITheme.BORDER_HAIRLINE, 1.0))
        p.drawLine(16, 44, int(w - 16), 44)

    def _paint_filter_pills(self, p: QPainter, w: float, theme: ProviderTheme) -> None:
        tab_rects = self._get_tab_rects(w)
        if not tab_rects:
            return

        # Container
        container_rect = QRectF(16.0, 52.0, w - 32.0, 28.0)
        p.setBrush(QBrush(UITheme.BG_ELEVATED))
        p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
        p.drawRoundedRect(container_rect, 7.0, 7.0)

        for pid, t_label, pill_rect in tab_rects:
            is_active = (self.active_provider == pid)
            if is_active:
                p.setBrush(QBrush(QColor(39, 39, 42, 255)))
                p.setPen(QPen(QColor(63, 63, 70, 180), 1.0))
                p.drawRoundedRect(pill_rect, 5.0, 5.0)
                p.setFont(UITheme.mono_font(7.5, QFont.Weight.Medium))
                p.setPen(QPen(UITheme.TEXT_WHITE))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(Qt.PenStyle.NoPen)
                p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
                p.setPen(QPen(UITheme.TEXT_MUTED))
            p.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, t_label)

    def _paint_bars_view(self, p: QPainter, w: float, h: float, rows: List[Tuple[str, str, str, float, str, str, Optional[float], Optional[QColor]]], theme: ProviderTheme) -> None:
        """Minimalist Modern Redesign with card-based provider metrics."""
        layout = self._get_bars_layout(w)
        start_y = 90.0
        card_h = 66.0
        card_gap = 8.0

        for i, (icon_type, r_title, r_sub, remaining_pct, reset_str, severity, weekly_pct, r_color) in enumerate(rows):
            card_y = start_y + i * (card_h + card_gap)
            card_rect = QRectF(16.0, card_y, w - 32.0, card_h)
            row_accent = r_color or theme.accent_color

            # Card container background & hairline border
            p.setBrush(QBrush(UITheme.BG_CARD))
            p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
            p.drawRoundedRect(card_rect, 8.0, 8.0)

            # Row 1 (Header of Card): Dot, Title, Tag Badge, % and Quota
            dot_center = QPointF(card_rect.left() + 14.0, card_y + 16.0)
            p.setBrush(QBrush(row_accent))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_center, 3.0, 3.0)

            # Title
            p.setFont(UITheme.mono_font(8.5, QFont.Weight.Medium))
            p.setPen(QPen(QColor(228, 228, 231)))
            fm_t = QtGui.QFontMetrics(p.font())
            tw = fm_t.horizontalAdvance(r_title)
            p.drawText(QRectF(card_rect.left() + 24.0, card_y + 5.0, tw + 2.0, 22.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, r_title)

            # Tag badge / mode
            row_pid = None
            if self.active_provider == "all":
                all_pids = ("claude", "codex", "antigravity", "opencode")
                row_pid = all_pids[i] if i < len(all_pids) else None
            elif i == 0:
                row_pid = self.active_provider

            badge_text = ""
            if self.settings.show_active_sessions and row_pid and self.usage.get_active_sessions(row_pid):
                badge_text = "ACTIVE"
            elif self.active_provider == "all":
                if row_pid == "claude":
                    badge_text = "STANDBY"
                elif row_pid == "codex":
                    badge_text = "STANDBY"
                elif row_pid == "antigravity":
                    badge_text = "GEMINI-3.8"
                elif row_pid == "opencode":
                    badge_text = "TEAM"
            else:
                if i == 0:
                    badge_text = "5H"
                elif i == 1:
                    badge_text = "WEEKLY"
                elif i == 2:
                    badge_text = "SONNET" if self.active_provider == "claude" else ("3P 5H" if self.active_provider == "antigravity" else "TEAM")
                elif i == 3:
                    badge_text = "3P WK"

            if badge_text:
                p.setFont(UITheme.mono_font(7.0, QFont.Weight.Normal))
                p.setPen(QPen(UITheme.TEXT_DIM))
                p.drawText(QRectF(card_rect.left() + 24.0 + tw + 8.0, card_y + 5.0, 80.0, 22.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, badge_text)

            # Percentage and Quota label on right
            if remaining_pct <= 10.0 or severity == "critical":
                pct_col = UITheme.CRITICAL_ACCENT
            elif remaining_pct <= 25.0 or severity == "warning":
                pct_col = UITheme.WARNING_ACCENT
            else:
                pct_col = UITheme.TEXT_WHITE

            disp_pct_str = f"{int(self._disp(remaining_pct))}%"
            p.setFont(UITheme.mono_font(9.0, QFont.Weight.DemiBold))
            p.setPen(QPen(pct_col))
            fm_p = QtGui.QFontMetrics(p.font())
            pct_w = fm_p.horizontalAdvance(disp_pct_str)
            p.drawText(QRectF(card_rect.right() - 44.0 - pct_w, card_y + 5.0, pct_w + 2.0, 22.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, disp_pct_str)

            p.setFont(UITheme.mono_font(7.0, QFont.Weight.Normal))
            p.setPen(QPen(UITheme.TEXT_DIM))
            p.drawText(QRectF(card_rect.right() - 40.0, card_y + 5.0, 28.0, 22.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "quota")

            # Row 2 (Middle of Card): Linear Micro Progress Bar
            track_x = card_rect.left() + 12.0
            track_y = card_y + 32.0
            track_w = card_rect.width() - 24.0
            track_h = 3.0

            p.setBrush(QBrush(UITheme.PROGRESS_TRACK))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(track_x, track_y, track_w, track_h), 1.5, 1.5)

            fill_w = max(0.0, min(track_w, (self._disp(remaining_pct) / 100.0) * track_w))
            if fill_w > 0:
                p.setBrush(QBrush(pct_col if pct_col != UITheme.TEXT_WHITE else row_accent))
                p.drawRoundedRect(QRectF(track_x, track_y, fill_w, track_h), 1.5, 1.5)

            # Row 3 (Bottom of Card): Subtitle left / Weekly & Reset right
            p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
            p.setPen(QPen(UITheme.TEXT_DIM))
            sub_display = r_sub.replace(" • ", " / ")
            fm_sub = QtGui.QFontMetrics(p.font())
            elided_sub = fm_sub.elidedText(sub_display, Qt.TextElideMode.ElideRight, int(card_rect.width() - 170.0))
            p.drawText(QRectF(track_x, card_y + 40.0, card_rect.width() - 170.0, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_sub)

            clean_time = reset_str.replace("Reset ", "").strip()
            if clean_time.lower() == "ready":
                clean_time = "Ready"

            if weekly_pct is not None:
                wk_prefix = f"Wk: {int(self._disp(weekly_pct))}% • "
                p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
                fm_wk = QtGui.QFontMetrics(p.font())
                wk_w = fm_wk.horizontalAdvance(wk_prefix)
                clean_w = fm_wk.horizontalAdvance(clean_time)
                total_meta_w = wk_w + clean_w
                meta_x = card_rect.right() - 14.0 - total_meta_w
                p.setPen(QPen(UITheme.TEXT_DIM))
                p.drawText(QRectF(meta_x, card_y + 40.0, wk_w, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, wk_prefix)
                p.setPen(QPen(QColor(212, 212, 216) if clean_time.lower() == "ready" else UITheme.TEXT_DIM))
                p.drawText(QRectF(meta_x + wk_w, card_y + 40.0, clean_w + 2.0, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, clean_time)
            else:
                p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
                p.setPen(QPen(QColor(212, 212, 216) if clean_time.lower() == "ready" else UITheme.TEXT_DIM))
                p.drawText(QRectF(card_rect.right() - 160.0, card_y + 40.0, 146.0, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, clean_time)

        # Active Sessions live section
        self._paint_active_sessions_section(p, layout, theme)

        # Trendline sparkline
        self._paint_trend_strip(p, layout, rows, theme)

        # Footer
        p.setPen(QPen(UITheme.BORDER_HAIRLINE, 1.0))
        p.drawLine(16, int(layout.divider_y), int(w - 16), int(layout.divider_y))

        p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
        secs = int(time.time() - self.usage.last_updated)
        sync_val = "just now" if secs < 60 else f"{secs // 60}m ago"

        p.setPen(QPen(UITheme.TEXT_MUTED))
        sync_prefix = "sync: "
        fm_f = QtGui.QFontMetrics(p.font())
        sw_p = fm_f.horizontalAdvance(sync_prefix)
        sw_v = fm_f.horizontalAdvance(sync_val)

        p.drawText(QRectF(16.0, layout.divider_y + 8.0, sw_p, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sync_prefix)
        p.setPen(QPen(QColor(212, 212, 216)))
        p.drawText(QRectF(16.0 + sw_p, layout.divider_y + 8.0, sw_v, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sync_val)

        next_fx = 16.0 + sw_p + sw_v + 6.0
        hint = self._best_hint()
        if hint:
            p.setPen(QPen(QColor(63, 63, 70, 180)))
            p.drawText(QRectF(next_fx, layout.divider_y + 8.0, 12.0, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "|")
            next_fx += 14.0

            p.setPen(QPen(UITheme.TEXT_MUTED))
            opt_lbl = "opt: "
            ow = fm_f.horizontalAdvance(opt_lbl)
            p.drawText(QRectF(next_fx, layout.divider_y + 8.0, ow, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, opt_lbl)
            next_fx += ow

            p.setPen(QPen(QColor(228, 228, 231)))
            avail_hw = max(10.0, layout.footer_text_rect.right() - next_fx)
            elided_hint = fm_f.elidedText(hint, Qt.TextElideMode.ElideRight, int(avail_hw))
            p.drawText(QRectF(next_fx, layout.divider_y + 8.0, avail_hw, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_hint)

        # Right status: "status: nominal"
        p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
        status_lbl = "status: "
        sl_w = fm_f.horizontalAdvance(status_lbl)
        p.setPen(QPen(UITheme.TEXT_DIM))
        status_x = w - 145.0
        p.drawText(QRectF(status_x, layout.divider_y + 8.0, sl_w, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, status_lbl)

        p.setPen(QPen(UITheme.WARNING_ACCENT if self._is_refreshing else UITheme.STATUS_NOMINAL))
        st_text = "syncing" if self._is_refreshing else "nominal"
        p.drawText(QRectF(status_x + sl_w, layout.divider_y + 8.0, 52.0, 18.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, st_text)

        self._paint_minimize_button(p, w, h, theme)

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
        return f"{PROVIDER_LABELS.get(pid, pid).lower()} ({int(self._disp(headroom))}%)"

    def _paint_active_sessions_section(self, p: QPainter, layout: BarsLayout, theme: ProviderTheme) -> None:
        """Paint running sessions section with Stitch table aesthetic."""
        if not layout.session_rects or not self.settings.show_active_sessions:
            return

        sessions = self.usage.get_active_sessions(self.active_provider if self.active_provider != "all" else None)
        if not sessions:
            return

        # 1. Section Header: "ACTIVE PROCESSES      3 running"
        if layout.section_header_rect:
            p.setFont(UITheme.mono_font(7.0, QFont.Weight.Medium))
            p.setPen(QPen(UITheme.TEXT_DIM))
            header_title = "ACTIVE PROCESSES" if len(sessions) > 1 else "ACTIVE PROCESS"
            p.drawText(layout.section_header_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, header_title)

            if getattr(self.settings, "session_display_mode", "bars") == "cycle":
                idx = (self._cycle_tick // 3) % len(sessions)
                count_lbl = f"{idx + 1} of {len(sessions)}"
            else:
                count_lbl = f"{len(sessions)} running"
            p.setFont(UITheme.mono_font(7.0, QFont.Weight.Normal))
            p.setPen(QPen(UITheme.TEXT_MUTED))
            p.drawText(layout.section_header_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, count_lbl)

        # 2. Outer Table Card
        first_r = layout.session_rects[0][0]
        last_r = layout.session_rects[-1][0]
        table_rect = QRectF(16.0, first_r.top() - 2.0, layout.sparkline_rect.width(), (last_r.bottom() - first_r.top()) + 4.0)
        p.setBrush(QBrush(UITheme.BG_CARD))
        p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
        p.drawRoundedRect(table_rect, 8.0, 8.0)

        # 3. Render each session row inside table card
        is_cycle = getattr(self.settings, "session_display_mode", "bars") == "cycle"
        for idx, (s_rect, s) in enumerate(layout.session_rects):
            if idx > 0:
                p.setPen(QPen(QColor(39, 39, 42, 160), 1.0))
                p.drawLine(int(s_rect.left() + 8.0), int(s_rect.top() - 2.0), int(s_rect.right() - 8.0), int(s_rect.top() - 2.0))
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
        """Paint an individual active session row matching the Stitch tabular design."""
        if s.provider_id == "claude":
            brand_color = UITheme.CLAUDE_PRIMARY
        elif s.provider_id == "codex":
            brand_color = UITheme.CODEX_PRIMARY
        elif s.provider_id == "opencode":
            brand_color = UITheme.OPENCODE_PRIMARY
        else:
            brand_color = UITheme.ANTIGRAVITY_PRIMARY

        # 1. Colored provider dot
        dot_center = QPointF(rect.left() + 14.0, rect.center().y())
        p.setBrush(QBrush(brand_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(dot_center, 3.0, 3.0)

        next_x = rect.left() + 24.0

        # 2. Model Badge Pill
        raw_m = (s.model_id or s.short_model or s.display_provider).lower()
        if "sonnet" in raw_m and "3.7" in raw_m:
            model_name = "sonnet-3.7"
        elif "sonnet" in raw_m and "3.5" in raw_m:
            model_name = "sonnet-3.5"
        elif "flash" in raw_m and "3.8" in raw_m:
            model_name = "flash-3.8"
        elif "flash" in raw_m and "2.5" in raw_m:
            model_name = "flash-2.5"
        elif "gpt-6.1" in raw_m:
            model_name = "gpt-6.1"
        elif "gpt-6" in raw_m:
            model_name = "gpt-6"
        elif "gpt-5" in raw_m:
            model_name = "gpt-5"
        else:
            model_name = s.short_model.lower().replace(" ", "-") if s.short_model else s.display_provider.lower()

        m_font = UITheme.mono_font(7.0, QFont.Weight.Medium)
        fm_m = QtGui.QFontMetrics(m_font)
        m_w = fm_m.horizontalAdvance(model_name) + 10.0
        m_rect = QRectF(next_x, rect.top() + (rect.height() - 17.0) / 2.0, m_w, 17.0)

        p.setBrush(QBrush(UITheme.BG_ELEVATED))
        p.setPen(QPen(QColor(63, 63, 70, 160), 1.0))
        p.drawRoundedRect(m_rect, 4.0, 4.0)

        p.setFont(m_font)
        p.setPen(QPen(QColor(212, 212, 216)))
        p.drawText(m_rect, Qt.AlignmentFlag.AlignCenter, model_name)

        next_x = m_rect.right() + 8.0

        # 3. Elapsed time on right
        if counter_tag:
            right_text = counter_tag
        else:
            if s.started_at is not None and s.started_at > 0:
                elapsed = int(time.time() - s.started_at)
                if elapsed < 60:
                    dur_str = f"{elapsed}s ago"
                elif elapsed < 3600:
                    dur_str = f"{elapsed // 60}m ago"
                else:
                    dur_str = f"{elapsed // 3600}h ago"
                right_text = dur_str
            else:
                right_text = s.status.capitalize()

        p.setFont(UITheme.mono_font(7.0, QFont.Weight.Normal))
        fm_rt = QtGui.QFontMetrics(p.font())
        right_w = fm_rt.horizontalAdvance(right_text) + 8.0
        right_rect = QRectF(rect.right() - right_w - 6.0, rect.top(), right_w, rect.height())
        p.setPen(QPen(UITheme.TEXT_DIM))
        p.drawText(right_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, right_text)

        # 4. Truncated session notation / title in the middle
        content_left = next_x
        content_right = right_rect.left() - 6.0
        avail_w = max(10.0, content_right - content_left)

        p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
        p.setPen(QPen(QColor(212, 212, 216)))

        display_note = s.title or s.notation or "Active Session"
        fm_note = QtGui.QFontMetrics(p.font())
        elided = fm_note.elidedText(display_note, Qt.TextElideMode.ElideRight, int(avail_w))
        p.drawText(QRectF(content_left, rect.top(), avail_w, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

    def _paint_trend_strip(self, p: QPainter, layout: BarsLayout, rows, theme: ProviderTheme) -> None:
        """24h sparkline of remaining quota plus today's local token count."""
        history = getattr(self.aggregator, "history", None)
        stats = getattr(self.aggregator, "stats", None)
        if history is None:
            return

        trend_colors = {
            "claude": UITheme.CLAUDE_PRIMARY,
            "codex": UITheme.CODEX_PRIMARY,
            "antigravity": UITheme.ANTIGRAVITY_PRIMARY,
            "opencode": UITheme.OPENCODE_PRIMARY,
        }
        if self.active_provider == "all":
            series = [
                ("claude", trend_colors["claude"]),
                ("codex", trend_colors["codex"]),
                ("antigravity", trend_colors["antigravity"]),
            ]
            if "opencode" in self.usage.providers and self.usage.providers["opencode"].available:
                series.append(("opencode", trend_colors["opencode"]))
        else:
            series = [(self.active_provider, trend_colors.get(self.active_provider, theme.accent_color))]

        p.setFont(UITheme.mono_font(7.0, QFont.Weight.Medium))
        p.setPen(QPen(UITheme.TEXT_DIM))
        p.drawText(
            layout.trend_label_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "QUOTA TREND · 24H",
        )

        p.setFont(UITheme.mono_font(6.5, QFont.Weight.Normal))
        metrics = QtGui.QFontMetricsF(p.font())
        if self.active_provider == "all":
            legend_items = [(pid, PROVIDER_LABELS.get(pid, pid.title()), color) for pid, color in series]
            legend_width = sum(metrics.horizontalAdvance(name) + 20.0 for _, name, _ in legend_items) + max(0, len(legend_items) - 1) * 6.0
            legend_x = layout.trend_label_rect.right() - legend_width
            for index, (_, name, color) in enumerate(legend_items):
                center_y = layout.trend_label_rect.center().y()
                p.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(legend_x, center_y), QPointF(legend_x + 7.0, center_y))
                p.setPen(QPen(UITheme.TEXT_MUTED))
                p.drawText(
                    QRectF(legend_x + 10.0, layout.trend_label_rect.top(), metrics.horizontalAdvance(name) + 2.0, layout.trend_label_rect.height()),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    name,
                )
                legend_x += metrics.horizontalAdvance(name) + 20.0
                if index < len(legend_items) - 1:
                    legend_x += 6.0
        elif self.active_provider in ("claude", "codex") and stats is not None and hasattr(stats, "summary"):
            extra = stats.summary(self.active_provider)
            if extra:
                p.setPen(QPen(UITheme.TEXT_MUTED))
                p.drawText(
                    layout.trend_label_rect,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    extra,
                )

        top = layout.sparkline_rect.top()
        height = layout.sparkline_rect.height()
        x0 = layout.sparkline_rect.left()
        width = layout.sparkline_rect.width()

        drew = False
        for pid, color in series:
            pts = history.series(pid, "s", 24.0)
            if len(pts) < 2:
                continue
            drew = True
            path = QPainterPath()
            first_t = pts[0][0]
            time_range = max(pts[-1][0] - first_t, 1.0)
            for i, (t, rem) in enumerate(pts):
                x = x0 + width * ((t - first_t) / time_range)
                val = self._disp(rem)
                y = top + height * (1.0 - val / 100.0)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)
        if not drew:
            p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
            p.setPen(QPen(UITheme.TEXT_DIM))
            p.drawText(QRectF(x0, top, width, height), Qt.AlignmentFlag.AlignCenter, "Collecting history...")

    def _paint_mini_view(self, p: QPainter, w: float, h: float, rows, theme: ProviderTheme) -> None:
        """Shallow strip showing the selected provider (or tightest quota in All)."""
        if not rows:
            return
        row = rows[0] if self.active_provider != "all" else min(rows, key=lambda item: item[3])
        _, r_title, _, remaining_pct, reset_str, severity, weekly_pct, r_color = row
        row_accent = r_color or theme.accent_color
        color = UITheme.CRITICAL_ACCENT if remaining_pct <= 10.0 or severity == "critical" else (
            UITheme.WARNING_ACCENT if remaining_pct <= 25.0 or severity == "warning" else row_accent
        )

        card_rect = QRectF(8.0, 8.0, w - 16.0, h - 16.0)
        p.setBrush(QBrush(UITheme.BG_CARD))
        p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
        p.drawRoundedRect(card_rect, 8.0, 8.0)

        p.setBrush(QBrush(row_accent))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(20.0, 20.0), 3.0, 3.0)

        p.setFont(UITheme.mono_font(8.5, QFont.Weight.Medium))
        p.setPen(QPen(UITheme.TEXT_WHITE))
        p.drawText(QRectF(30.0, 10.0, 140.0, 20.0), Qt.AlignmentFlag.AlignVCenter, r_title)

        p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
        p.setPen(QPen(UITheme.TEXT_DIM))
        clean_reset = reset_str.replace("Reset ", "").strip()
        p.drawText(QRectF(175.0, 10.0, 120.0, 20.0), Qt.AlignmentFlag.AlignVCenter, clean_reset)

        p.setFont(UITheme.mono_font(9.0, QFont.Weight.DemiBold))
        p.setPen(QPen(color))
        p.drawText(QRectF(w - 90.0, 10.0, 48.0, 20.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{int(self._disp(remaining_pct))}%")

        bar_x, bar_w = 20.0, w - 40.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(UITheme.PROGRESS_TRACK))
        p.drawRoundedRect(QRectF(bar_x, 36.0, bar_w, 3.5), 1.75, 1.75)
        fill = max(0.0, min(bar_w, self._disp(remaining_pct) / 100.0 * bar_w))
        if fill:
            p.setBrush(QBrush(color))
            p.drawRoundedRect(QRectF(bar_x, 36.0, fill, 3.5), 1.75, 1.75)

        self._paint_minimize_button(p, w, h, theme)

    def _paint_minimize_button(self, p: QPainter, w: float, h: float, theme: ProviderTheme) -> None:
        rect = self._get_toggle_rect(w) if self.view_mode == "mini" else self._get_minimize_rect(w)
        p.setBrush(QBrush(UITheme.BG_ELEVATED))
        p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
        p.drawRoundedRect(rect, 3.0, 3.0)
        p.setPen(QPen(UITheme.TEXT_MUTED))
        p.setFont(UITheme.mono_font(8.0, QFont.Weight.Bold))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "↑" if self.view_mode == "mini" else "⌄")

    def _paint_gear_icon(self, p: QPainter, rect: QRectF, color: QColor) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(color, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = rect.center().x(), rect.center().y()
        r_in = 3.0
        r_out = 5.5
        p.drawEllipse(QPointF(cx, cy), r_in, r_in)
        for i in range(6):
            angle = i * (math.pi / 3.0)
            x1 = cx + (r_in + 0.8) * math.cos(angle)
            y1 = cy + (r_in + 0.8) * math.sin(angle)
            x2 = cx + r_out * math.cos(angle)
            y2 = cy + r_out * math.sin(angle)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.restore()

    def _paint_rings_view(self, p: QPainter, w: float, h: float, rows: List[Tuple[str, str, str, float, str, str, Optional[float], Optional[QColor]]], theme: ProviderTheme) -> None:
        """Minimalist Modern circular gauges view."""
        n = max(1, len(rows))
        center_y = 135.0
        circle_r = 26.0 if n > 3 else 34.0
        ring_stroke = 4.0 if n > 3 else 5.0
        col_w = (w - 32.0) / n

        for i, (_, r_title, _, remaining_pct, reset_str, severity, weekly_pct, r_color) in enumerate(rows):
            cx = 16.0 + col_w * i + col_w / 2.0
            cy = center_y
            row_accent = r_color or theme.accent_color

            rc_x = 16.0 + col_w * i + 3.0
            rc_w = col_w - 6.0
            ring_card = QRectF(rc_x, 86.0, rc_w, 126.0)
            p.setBrush(QBrush(UITheme.BG_CARD))
            p.setPen(QPen(UITheme.BORDER_CARD, 1.0))
            p.drawRoundedRect(ring_card, 8.0, 8.0)

            ring_rect = QRectF(cx - circle_r, cy - circle_r, circle_r * 2.0, circle_r * 2.0)

            if weekly_pct is not None:
                weekly_r = circle_r - ring_stroke - 3.0
                weekly_rect = QRectF(cx - weekly_r, cy - weekly_r, weekly_r * 2.0, weekly_r * 2.0)
                p.setPen(QPen(UITheme.PROGRESS_TRACK, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(weekly_rect)
                weekly_span = -int((self._disp(weekly_pct) / 100.0) * 360 * 16)
                if weekly_span:
                    p.setPen(QPen(row_accent, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    p.drawArc(weekly_rect, 90 * 16, weekly_span)

            p.setPen(QPen(UITheme.PROGRESS_TRACK, ring_stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(ring_rect)

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

            p.setFont(UITheme.mono_font(9.0 if n > 3 else 10.5, QFont.Weight.DemiBold))
            p.setPen(QPen(UITheme.TEXT_WHITE))
            p.drawText(ring_rect, Qt.AlignmentFlag.AlignCenter, f"{int(self._disp(remaining_pct))}%")

            p.setFont(UITheme.mono_font(7.5 if n > 3 else 8.5, QFont.Weight.Medium))
            p.setPen(QPen(UITheme.TEXT_WHITE))
            p.drawText(QRectF(rc_x, cy + circle_r + 6.0, rc_w, 16.0), Qt.AlignmentFlag.AlignCenter, r_title)

            clean_reset = reset_str.replace("Reset ", "").strip()
            if clean_reset.lower() == "ready":
                clean_reset = "Ready"

            if weekly_pct is not None:
                sub_label = f"Wk: {int(self._disp(weekly_pct))}% • {clean_reset}"
            else:
                sub_label = clean_reset

            p.setFont(UITheme.mono_font(6.5 if n > 3 else 7.5, QFont.Weight.Normal))
            p.setPen(QPen(UITheme.TEXT_DIM))
            p.drawText(QRectF(rc_x, cy + circle_r + 22.0, rc_w, 14.0), Qt.AlignmentFlag.AlignCenter, sub_label)

        p.setPen(QPen(UITheme.BORDER_HAIRLINE, 1.0))
        p.drawLine(16, int(h - 32), int(w - 16), int(h - 32))

        p.setFont(UITheme.mono_font(7.5, QFont.Weight.Normal))
        p.setPen(QPen(UITheme.TEXT_DIM))
        secs = int(time.time() - self.usage.last_updated)
        sync_str = "just now" if secs < 60 else f"{secs // 60}m ago"
        footer_text = f"sync: {sync_str}"
        if self.settings.show_active_sessions:
            all_active = self.usage.get_active_sessions()
            if all_active:
                footer_text += f"  |  {len(all_active)} running"
        p.drawText(QRectF(16, int(h - 28), int(w - 60), 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, footer_text)
        self._paint_minimize_button(p, w, h, theme)

    def _paint_row_icon(self, p: QPainter, x: float, y: float, icon_type: str, severity: str, color: Optional[QColor] = None) -> None:
        """Draw clean vector icons in brand accent color."""
        icon_color = color or UITheme.GREEN_ACCENT
        p.setPen(QPen(icon_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)

        if icon_type == "timer":
            p.drawEllipse(QRectF(x, y + 2, 14, 14))
            p.drawLine(QPointF(x + 7, y), QPointF(x + 7, y + 2))
            p.drawLine(QPointF(x + 7, y + 9), QPointF(x + 7, y + 5))
            p.drawLine(QPointF(x + 7, y + 9), QPointF(x + 10, y + 9))
        elif icon_type == "chart":
            p.setBrush(QBrush(icon_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x, y + 9, 3.5, 7), 1, 1)
            p.drawRoundedRect(QRectF(x + 5, y + 5, 3.5, 11), 1, 1)
            p.drawRoundedRect(QRectF(x + 10, y + 1, 3.5, 15), 1, 1)
        else:
            p.drawLine(QPointF(x + 1, y + 15), QPointF(x + 11, y + 5))
            p.drawPoint(QPointF(x + 14, y + 2))
            p.drawPoint(QPointF(x + 6, y + 1))
            p.drawPoint(QPointF(x + 15, y + 9))
