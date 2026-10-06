import signal
import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PySide6 import QtCore, QtWidgets

from src.aggregator import UsageAggregator
from src.config import WidgetSettings
from src.insights import write_snapshot
from src.ui.tray import UsageTrayIcon
from src.ui.widget import AIUsageWidget


def main() -> None:
    # Handle Ctrl+C cleanly
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("AI Usage Widget")
    app.setOrganizationName("AIWidgets")

    settings = WidgetSettings.load()
    aggregator = UsageAggregator(settings)

    # Show UI first; the aggregator's background loop does the initial live fetch
    # (a blocking fetch here delayed startup by the slowest provider, twice).
    aggregator.subscribe(write_snapshot)

    widget = AIUsageWidget(aggregator, settings)
    widget.show()
    aggregator.start()

    # System Tray
    tray = None
    if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        tray = UsageTrayIcon(widget, aggregator, parent=widget)
        tray.show()

    try:
        sys.exit(app.exec())
    finally:
        aggregator.stop()


if __name__ == "__main__":
    main()
