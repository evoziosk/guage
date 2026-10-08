from src.ui.wallpaper import WindowsWallpaperMode


class ProgmanChildWorker:
    """Model the Windows 11 layout with WorkerW parented directly to Progman."""

    def FindWindowW(self, class_name, _window_name):
        return 100 if class_name == "Progman" else 0

    def SendMessageTimeoutW(self, *_args):
        return 1

    def FindWindowExW(self, parent, _after, class_name, _window_name):
        if parent == 100 and class_name == "SHELLDLL_DefView":
            return 101
        if parent == 100 and class_name == "WorkerW":
            return 200
        return 0

    def EnumWindows(self, callback, _lparam):
        callback(100, 0)
        return 1


def test_finds_worker_window_parented_to_progman():
    wallpaper = WindowsWallpaperMode.__new__(WindowsWallpaperMode)
    wallpaper._user32 = ProgmanChildWorker()

    assert wallpaper._find_worker_window() == 200
