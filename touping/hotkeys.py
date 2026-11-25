"""全局快捷键：RegisterHotKey + Qt 原生事件过滤器。"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, Qt
from PySide6.QtGui import QKeySequence

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = (
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)

_SPECIAL = {
    Qt.Key.Key_Space: 0x20, Qt.Key.Key_PageUp: 0x21, Qt.Key.Key_PageDown: 0x22,
    Qt.Key.Key_End: 0x23, Qt.Key.Key_Home: 0x24, Qt.Key.Key_Left: 0x25,
    Qt.Key.Key_Up: 0x26, Qt.Key.Key_Right: 0x27, Qt.Key.Key_Down: 0x28,
    Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Delete: 0x2E,
}


def parse(text: str) -> tuple[int, int] | None:
    """"Ctrl+Alt+M" -> (修饰键, 虚拟键码)。必须带 Ctrl、Alt 或 Win，否则会挡住打字。"""
    sequence = QKeySequence(text)
    if sequence.count() != 1:
        return None
    combo = sequence[0]
    flags, key = combo.keyboardModifiers(), combo.key()
    mods = 0
    for qt_flag, win_flag in (
        (Qt.KeyboardModifier.ControlModifier, MOD_CONTROL),
        (Qt.KeyboardModifier.AltModifier, MOD_ALT),
        (Qt.KeyboardModifier.ShiftModifier, MOD_SHIFT),
        (Qt.KeyboardModifier.MetaModifier, MOD_WIN),
    ):
        if flags & qt_flag:
            mods |= win_flag
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return None
    code = int(key)
    if int(Qt.Key.Key_A) <= code <= int(Qt.Key.Key_Z):
        return mods, code
    if int(Qt.Key.Key_0) <= code <= int(Qt.Key.Key_9):
        return mods, code
    if int(Qt.Key.Key_F1) <= code <= int(Qt.Key.Key_F24):
        return mods, 0x70 + code - int(Qt.Key.Key_F1)
    if key in _SPECIAL:
        return mods, _SPECIAL[key]
    return None


class HotkeyManager(QAbstractNativeEventFilter):
    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[int, Callable[[], None]] = {}
        QCoreApplication.instance().installNativeEventFilter(self)

    def set(self, ident: int, text: str, callback: Callable[[], None]) -> bool:
        """注册快捷键；空字符串表示不用。返回 False 表示无效或被别的程序占用。"""
        self.clear(ident)
        if not text:
            return True
        parsed = parse(text)
        if parsed is None:
            return False
        mods, key = parsed
        if not _user32.RegisterHotKey(None, ident, mods | MOD_NOREPEAT, key):
            log.warning("快捷键 %s 注册失败，错误码 %s", text, ctypes.get_last_error())
            return False
        self._callbacks[ident] = callback
        return True

    def clear(self, ident: int) -> None:
        if self._callbacks.pop(ident, None) is not None:
            _user32.UnregisterHotKey(None, ident)

    def clear_all(self) -> None:
        for ident in list(self._callbacks):
            self.clear(ident)

    def nativeEventFilter(self, event_type, message):
        # 热键是线程消息，没有窗口过程，每次按键只会从这里经过一次
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam in self._callbacks:
            self._callbacks[msg.wParam]()
            return True, 0
        return False, 0
