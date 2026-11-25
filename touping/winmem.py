"""用 pywin32 找到投屏窗口，读取它的位置，以及把它调出来、最小化。"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

import pywintypes
import win32api
import win32con
import win32gui
import win32process

from .presets import WindowRect

log = logging.getLogger(__name__)

# pywin32 没有包装这个函数。用异步版：投屏窗口卡住时不会把托盘程序也拖住
_show_async = ctypes.WinDLL("user32").ShowWindowAsync
_show_async.argtypes = (wintypes.HWND, ctypes.c_int)


def find_window(pid: int) -> int | None:
    """返回该进程的可见主窗口句柄。"""
    found: list[int] = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and not win32gui.GetWindow(
            hwnd, win32con.GW_OWNER
        ):
            if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                # 控制台窗口也可能挂在这个进程名下，排除掉
                if win32gui.GetClassName(hwnd) != "ConsoleWindowClass":
                    found.append(hwnd)
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except pywintypes.error:
        return None
    return found[0] if found else None


def window_rect(hwnd: int) -> WindowRect | None:
    """客户区位置和大小。最小化、最大化、全屏时返回 None，不记这些状态。"""
    try:
        if win32gui.IsIconic(hwnd):
            return None
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            return None
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        x, y = win32gui.ClientToScreen(hwnd, (left, top))
        width, height = right - left, bottom - top
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        mon = win32api.GetMonitorInfo(monitor)["Monitor"]
    except pywintypes.error:
        return None
    if (x, y, x + width, y + height) == tuple(mon):
        return None
    if width < 100 or height < 100:
        return None
    return WindowRect(x, y, width, height)


def bring_to_front(hwnd: int) -> None:
    """最小化了就还原，然后放到最前面。最大化、全屏的状态保持不变。"""
    try:
        if win32gui.IsIconic(hwnd):
            _show_async(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except pywintypes.error as exc:
        log.warning("投屏窗口没能放到最前面：%s", exc)


def minimize(hwnd: int) -> None:
    _show_async(hwnd, win32con.SW_MINIMIZE)


def is_on_screen(rect: WindowRect) -> bool:
    """上次的位置是否还在某个显示器里：标题栏所在的那条要能看见、能拖动。"""
    strip = (rect.x, rect.y, rect.x + rect.width, rect.y + 40)
    for _, _, (left, top, right, bottom) in win32api.EnumDisplayMonitors(None, None):
        overlap_w = min(strip[2], right) - max(strip[0], left)
        overlap_h = min(strip[3], bottom) - max(strip[1], top)
        if overlap_w >= 100 and overlap_h >= 20:
            return True
    return False
