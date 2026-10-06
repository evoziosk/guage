from __future__ import annotations

import sys
from pathlib import Path

APP_ID = "model-pulse"
PROJECT_DIR = Path(__file__).resolve().parent.parent


def _linux_file() -> Path:
    return Path.home() / ".config" / "autostart" / f"{APP_ID}.desktop"


def _command() -> str:
    return f'"{sys.executable}" -m src.main'


def is_enabled() -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                try:
                    winreg.QueryValueEx(k, "Guage")
                    return True
                except OSError:
                    winreg.QueryValueEx(k, "ModelPulse")
                    return True
        except OSError:
            return False
    return _linux_file().exists()


def set_enabled(enabled: bool) -> None:
    if sys.platform == "win32":
        import winreg

        key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, "Guage", 0, winreg.REG_SZ, f'cmd /c "set PYTHONPATH={PROJECT_DIR} && {_command()}"')
            else:
                for reg_name in ("Guage", "ModelPulse"):
                    try:
                        winreg.DeleteValue(k, reg_name)
                    except OSError:
                        pass
        return

    path = _linux_file()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Guage\n"
            "Comment=AI usage widget\n"
            f"Path={PROJECT_DIR}\n"
            f"Exec={_command()}\n"
            "X-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )
    elif path.exists():
        path.unlink()
