"""Experimental Windows desktop-layer parenting for the usage widget."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional


class WindowsWallpaperMode:
    """Attach a top-level HWND to Explorer's behind-icons WorkerW window."""

    _WM_SPAWN_WORKER = 0x052C
    _WS_CHILD = 0x40000000
    _WS_POPUP = 0x80000000
    _WS_EX_TOPMOST = 0x00000008
    _GWL_STYLE = -16
    _GWL_EXSTYLE = -20
    _SWP_NOACTIVATE = 0x0010
    _SWP_NOZORDER = 0x0004
    _SWP_FRAMECHANGED = 0x0020
    _SWP_SHOWWINDOW = 0x0040
    _HWND_TOPMOST = wintypes.HWND(-1)
    _HWND_NOTOPMOST = wintypes.HWND(-2)

    def __init__(self) -> None:
        self._state: Optional[dict] = None
        self._user32 = None
        if sys.platform == "win32":
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._configure_api()

    @property
    def active(self) -> bool:
        return self._state is not None

    def _configure_api(self) -> None:
        u = self._user32
        hwnd = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowW.restype = hwnd
        u.FindWindowExW.argtypes = [hwnd, hwnd, wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowExW.restype = hwnd
        u.SendMessageTimeoutW.argtypes = [
            hwnd,
            wintypes.UINT,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        u.EnumWindows.argtypes = [
            callback_type(wintypes.BOOL, hwnd, ctypes.c_ssize_t),
            ctypes.c_ssize_t,
        ]
        u.EnumWindows.restype = wintypes.BOOL
        u.GetParent.argtypes = [hwnd]
        u.GetParent.restype = hwnd
        u.GetWindowRect.argtypes = [hwnd, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.ScreenToClient.argtypes = [hwnd, ctypes.POINTER(wintypes.POINT)]
        u.ScreenToClient.restype = wintypes.BOOL
        u.SetParent.argtypes = [hwnd, hwnd]
        u.SetParent.restype = hwnd
        u.SetWindowPos.argtypes = [hwnd, hwnd, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SetWindowPos.restype = wintypes.BOOL
        u.IsWindow.argtypes = [hwnd]
        u.IsWindow.restype = wintypes.BOOL
        self._get_window_long = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        self._set_window_long = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        self._get_window_long.argtypes = [hwnd, ctypes.c_int]
        self._get_window_long.restype = ctypes.c_ssize_t
        self._set_window_long.argtypes = [hwnd, ctypes.c_int, ctypes.c_ssize_t]
        self._set_window_long.restype = ctypes.c_ssize_t

    def _set_style(self, hwnd: int, index: int, value: int) -> bool:
        ctypes.set_last_error(0)
        self._set_window_long(hwnd, index, value)
        return ctypes.get_last_error() == 0

    def _find_worker_window(self) -> int:
        u = self._user32
        progman = u.FindWindowW("Progman", None)
        if progman:
            result = ctypes.c_size_t()
            u.SendMessageTimeoutW(
                progman,
                self._WM_SPAWN_WORKER,
                0,
                0,
                0,
                1000,
                ctypes.byref(result),
            )

        found = wintypes.HWND()
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        callback_type = callback_type(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(top_level, _lparam):
            shell_view = u.FindWindowExW(top_level, None, "SHELLDLL_DefView", None)
            if shell_view:
                worker = u.FindWindowExW(None, top_level, "WorkerW", None)
                if worker:
                    found.value = worker
                    return False
            return True

        u.EnumWindows(callback_type(visit), 0)
        if found.value:
            return int(found.value)

        # Recent Windows 11 builds expose the wallpaper WorkerW as a child
        # of Progman rather than as a top-level sibling of the icon view.
        if progman:
            worker = u.FindWindowExW(progman, None, "WorkerW", None)
            if worker:
                return int(worker)
        return 0

    def enable(self, hwnd: int) -> tuple[bool, str]:
        if sys.platform != "win32" or self._user32 is None:
            return False, "Wallpaper mode is available only on Windows."
        if self.active:
            return True, ""

        worker = self._find_worker_window()
        if not worker:
            return False, "Windows did not expose a compatible desktop layer. The widget remains a normal window."

        hwnd = int(hwnd)
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False, "Could not read the widget window geometry."
        point = wintypes.POINT(rect.left, rect.top)
        if not self._user32.ScreenToClient(worker, ctypes.byref(point)):
            return False, "Could not map the widget position to the desktop layer."

        style = int(self._get_window_long(hwnd, self._GWL_STYLE))
        exstyle = int(self._get_window_long(hwnd, self._GWL_EXSTYLE))
        parent = self._user32.GetParent(hwnd)
        self._state = {
            "hwnd": hwnd,
            "worker": int(worker),
            "parent": int(parent or 0),
            "style": style,
            "exstyle": exstyle,
            "rect": (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
        }

        child_style = (style & ~self._WS_POPUP) | self._WS_CHILD
        child_exstyle = exstyle & ~self._WS_EX_TOPMOST
        if not self._set_style(hwnd, self._GWL_STYLE, child_style):
            self._state = None
            return False, "Could not prepare the widget window for desktop parenting."
        if not self._set_style(hwnd, self._GWL_EXSTYLE, child_exstyle):
            self._set_style(hwnd, self._GWL_STYLE, style)
            self._state = None
            return False, "Could not prepare the widget window for desktop parenting."

        ctypes.set_last_error(0)
        previous_parent = self._user32.SetParent(hwnd, worker)
        if not previous_parent and ctypes.get_last_error():
            self._set_style(hwnd, self._GWL_STYLE, style)
            self._set_style(hwnd, self._GWL_EXSTYLE, exstyle)
            self._state = None
            return False, "Windows rejected the desktop-layer parent. The widget remains a normal window."
        if int(self._user32.GetParent(hwnd) or 0) != int(worker):
            self.disable(always_on_top=bool(exstyle & self._WS_EX_TOPMOST))
            return False, "Windows did not attach the widget to the desktop layer. The normal window was restored."

        flags = self._SWP_NOACTIVATE | self._SWP_NOZORDER | self._SWP_FRAMECHANGED | self._SWP_SHOWWINDOW
        if not self._user32.SetWindowPos(hwnd, None, point.x, point.y, rect.right - rect.left, rect.bottom - rect.top, flags):
            self.disable(always_on_top=bool(exstyle & self._WS_EX_TOPMOST))
            return False, "Could not place the widget on the desktop layer. The normal window was restored."
        return True, ""

    def disable(self, always_on_top: bool = True) -> tuple[bool, str]:
        state = self._state
        if state is None:
            return True, ""
        hwnd = state["hwnd"]
        if not self._user32.IsWindow(hwnd):
            self._state = None
            return False, "The widget window no longer exists."

        child_style = int(self._get_window_long(hwnd, self._GWL_STYLE))
        child_exstyle = int(self._get_window_long(hwnd, self._GWL_EXSTYLE))
        style = state["style"]
        exstyle = state["exstyle"]
        self._set_style(hwnd, self._GWL_STYLE, style)
        self._set_style(hwnd, self._GWL_EXSTYLE, exstyle)
        ctypes.set_last_error(0)
        previous_parent = self._user32.SetParent(hwnd, state["parent"])
        if not previous_parent and ctypes.get_last_error():
            self._set_style(hwnd, self._GWL_STYLE, child_style)
            self._set_style(hwnd, self._GWL_EXSTYLE, child_exstyle)
            return False, "Windows could not restore the widget as a normal window."

        x, y, width, height = state["rect"]
        insert_after = self._HWND_TOPMOST if always_on_top else self._HWND_NOTOPMOST
        flags = self._SWP_NOACTIVATE | self._SWP_FRAMECHANGED | self._SWP_SHOWWINDOW
        if not self._user32.SetWindowPos(hwnd, insert_after, x, y, width, height, flags):
            self._state = None
            return False, "The widget was detached, but Windows could not restore its original position."
        self._state = None
        return True, ""

    def host_is_alive(self) -> bool:
        if not self.active:
            return False
        hwnd = self._state["hwnd"]
        worker = self._state["worker"]
        return bool(
            self._user32.IsWindow(worker)
            and self._user32.IsWindow(hwnd)
            and int(self._user32.GetParent(hwnd) or 0) == worker
        )
