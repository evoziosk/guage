from __future__ import annotations

import sys
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

from src import autostart
from src.aggregator import UsageAggregator
from src.insights import lowest_remaining, tray_summary
from src.ui.styles import UITheme
from src.ui.widget import AIUsageWidget


def create_tray_icon(lowest_pct: Optional[float] = None) -> QIcon:
    """Generate a 64x64 tray icon; with lowest_pct it shows the most constrained quota."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Base rounded dark square
    path = QPainterPath()
    path.addRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    p.fillPath(path, QColor(24, 26, 35))
    p.setPen(QPen(QColor(255, 255, 255, 40), 2))
    p.drawPath(path)

    if lowest_pct is not None:
        if lowest_pct <= 10:
            col = UITheme.CRITICAL_ACCENT
        elif lowest_pct <= 25:
            col = UITheme.WARNING_ACCENT
        else:
            col = QColor(74, 222, 128)
        p.setPen(QPen(col, 3))
        p.drawPath(path)
        f = QFont("Sans", 26, QFont.Weight.Black)
        p.setFont(f)
        p.drawText(QRectF(4, 4, 56, 56), Qt.AlignmentFlag.AlignCenter, str(int(lowest_pct)))
        p.end()
        return QIcon(pix)

    # 3 colored dots inside representing Claude, Codex, Antigravity
    dots = [
        (18, 32, UITheme.CLAUDE_PRIMARY),
        (32, 32, UITheme.CODEX_PRIMARY),
        (46, 32, UITheme.ANTIGRAVITY_PRIMARY),
    ]
    p.setPen(Qt.PenStyle.NoPen)
    for x, y, col in dots:
        p.setBrush(QBrush(col))
        p.drawEllipse(QPoint(x, y), 5, 5)

    p.end()
    return QIcon(pix)


MENU_STYLE = """
    QMenu {
        background-color: #1a1c23;
        color: #e0e0e0;
        border: 1px solid #333644;
        border-radius: 8px;
        padding: 4px;
    }
    QMenu::item {
        padding: 6px 24px;
        border-radius: 4px;
    }
    QMenu::item:selected {
        background-color: #2a2e3d;
    }
"""


def build_app_menu(widget: AIUsageWidget, toggle_cb, parent: Optional[QtWidgets.QWidget] = None) -> QtWidgets.QMenu:
    """Menu for the system tray icon."""
    menu = QtWidgets.QMenu(parent)
    menu.setStyleSheet(MENU_STYLE)

    menu.addAction("Show / Hide Widget").triggered.connect(toggle_cb)

    v_menu = menu.addMenu("View")
    for mode, label in [("bars", "Bars (detailed)"), ("rings", "Rings"), ("mini", "Mini (one line each)")]:
        act = v_menu.addAction(label)
        act.triggered.connect(lambda checked=False, m=mode: widget.set_view_mode(m))

    mini_action = menu.addAction("Expand from compact strip" if widget.view_mode == "mini" else "Minimize to compact strip")
    mini_action.triggered.connect(widget.toggle_mini_mode)

    wallpaper_action = menu.addAction("Wallpaper mode (experimental)")
    wallpaper_action.setCheckable(True)
    wallpaper_action.setChecked(widget._wallpaper_mode.active)
    wallpaper_action.setEnabled(sys.platform == "win32")
    wallpaper_action.setToolTip("Attach behind desktop icons; Explorer updates may end the mode.")
    wallpaper_action.toggled.connect(
        lambda checked: widget.toggle_wallpaper_mode() if checked != widget._wallpaper_mode.active else None
    )

    menu.addAction("Toggle Used / Remaining %").triggered.connect(widget.toggle_style)

    # Active Sessions submenu
    sessions_menu = menu.addMenu("Active Sessions")
    act_toggle = sessions_menu.addAction("Enabled")
    act_toggle.setCheckable(True)
    act_toggle.setChecked(widget.settings.show_active_sessions)
    act_toggle.triggered.connect(widget._toggle_active_sessions)

    sessions_menu.addSeparator()
    act_bars = sessions_menu.addAction("Show Separate Bars for Each")
    act_bars.setCheckable(True)
    act_bars.setChecked(getattr(widget.settings, "session_display_mode", "bars") == "bars")
    act_bars.triggered.connect(lambda: widget._set_session_display_mode("bars"))

    act_cycle = sessions_menu.addAction("Cycle in Single Bar")
    act_cycle.setCheckable(True)
    act_cycle.setChecked(getattr(widget.settings, "session_display_mode", "bars") == "cycle")
    act_cycle.triggered.connect(lambda: widget._set_session_display_mode("cycle"))

    active_list = widget.usage.get_active_sessions()
    if active_list:
        sessions_menu.addSeparator()
        for s in active_list:
            item = sessions_menu.addAction(f"[{s.display_provider}] {s.notation}")
            item.setEnabled(False)

    menu.addAction("Refresh Now").triggered.connect(widget.manual_refresh)

    top_action = menu.addAction("Always on Top")
    top_action.setCheckable(True)
    top_action.setChecked(widget.settings.always_on_top)

    top_action.toggled.connect(widget.set_always_on_top)

    login_action = menu.addAction("Start on login")
    login_action.setCheckable(True)
    try:
        login_action.setChecked(autostart.is_enabled())
    except Exception:
        pass

    def _toggle_login(checked: bool):
        try:
            autostart.set_enabled(checked)
        except Exception:
            pass

    login_action.toggled.connect(_toggle_login)

    menu.addSeparator()
    menu.addAction("Quit").triggered.connect(QtWidgets.QApplication.instance().quit)
    return menu


class UsageTrayIcon(QtWidgets.QSystemTrayIcon):
    """System tray integration for Windows and Linux."""

    def __init__(self, widget: AIUsageWidget, aggregator: UsageAggregator, parent: Optional[QtCore.QObject] = None):
        super().__init__(create_tray_icon(), parent)
        self.widget = widget
        self.aggregator = aggregator
        self.setToolTip("Guage - AI Usage & Model Monitor (Claude, Codex, Antigravity, OpenCode)")

        self._build_menu()
        self.activated.connect(self._on_activated)

        # Alerts, tooltip and dynamic icon follow live data
        self.widget.alert_callback = self._notify
        self.widget.data_applied.connect(self._refresh_status)
        self._refresh_status()

    def _notify(self, title: str, message: str) -> None:
        if self.supportsMessages():
            self.showMessage(title, message, QtWidgets.QSystemTrayIcon.MessageIcon.Warning, 8000)

    def _refresh_status(self) -> None:
        usage = self.widget.usage
        tip = tray_summary(usage)
        if self.widget.settings.show_active_sessions and usage.active_sessions:
            tip += f"\n● {len(usage.active_sessions)} active session{'s' if len(usage.active_sessions) > 1 else ''} running"
        self.setToolTip(tip)
        low = lowest_remaining(usage)
        self.setIcon(create_tray_icon(low[1] if low else None))

    def _build_menu(self) -> None:
        self.setContextMenu(build_app_menu(self.widget, self._toggle_widget))

    def _on_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_widget()

    def _toggle_widget(self) -> None:
        if self.widget.isVisible():
            self.widget.hide()
        else:
            self.widget.show()
            self.widget.raise_()
            self.widget.activateWindow()
