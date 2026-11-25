import ctypes
import os
import sys
import time
import uuid
import winreg
from pathlib import Path

import PySide6
import pytest
import win32gui
from PySide6.QtNetwork import QHostAddress, QTcpServer

from touping import autostart, hotkeys, winmem
from touping.adbproto import TRACK_SERVICE, encode_request
from touping.config import DEFAULT_HOTKEYS, Settings
from touping.presets import HIGH_QUALITY, LOW_LATENCY, WindowRect
from touping.session import ScrcpySession
from touping.tracker import DeviceTracker

FAKE = str(Path(__file__).with_name("fake_scrcpy.py"))
NO_ADB = Path("does-not-exist.exe")


# ---- 设备监听 ----
class FakeAdbServer:
    def __init__(self, port=0):
        self.server = QTcpServer()
        assert self.server.listen(QHostAddress.SpecialAddress.LocalHost, port)
        self.port = self.server.serverPort()
        self.clients, self.request = [], b""
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        client = self.server.nextPendingConnection()
        self.clients.append(client)
        client.readyRead.connect(lambda: self._read(client))

    def _read(self, client):
        self.request = bytes(client.readAll())
        client.write(b"OKAY")

    def push(self, text):
        data = text.encode()
        self.clients[-1].write(f"{len(data):04x}".encode() + data)

    def drop(self):
        self.clients[-1].abort()

    def close(self):
        self.server.close()
        for client in self.clients:
            client.abort()


@pytest.fixture
def tracked(qapp):
    server = FakeAdbServer()
    tracker = DeviceTracker(NO_ADB, server.port)
    seen, lost = [], []
    tracker.devicesChanged.connect(lambda ds: seen.append([d.serial for d in ds]))
    tracker.serverLost.connect(lambda: lost.append(1))
    tracker.start()
    yield server, tracker, seen, lost
    tracker.stop()
    server.close()


def test_tracker_receives_updates(tracked, wait):
    server, _, seen, _ = tracked
    wait(3000, lambda: server.request)
    assert server.request == encode_request(TRACK_SERVICE)
    server.push("")
    server.push("ABC device model:Pad\n")
    server.push("")
    wait(3000, lambda: len(seen) == 3)
    assert seen == [[], ["ABC"], []]


def test_tracker_reconnects_and_ignores_empty_list_after_restart(tracked, wait):
    server, _, seen, lost = tracked
    wait(3000, lambda: server.request)
    server.push("ABC device\n")
    wait(3000, lambda: seen == [["ABC"]])
    server.request = b""
    server.drop()
    wait(5000, lambda: server.request)
    assert lost == [1]
    server.push("")                 # server 刚重启，还没枚举出设备
    wait(300)
    server.push("ABC device\n")
    wait(3000, lambda: len(seen) == 2)
    wait(2300)
    assert seen == [["ABC"], ["ABC"]]


def test_tracker_reports_empty_if_device_really_gone(tracked, wait):
    server, _, seen, _ = tracked
    wait(3000, lambda: server.request)
    server.push("ABC device\n")
    wait(3000, lambda: seen)
    server.request = b""
    server.drop()
    wait(5000, lambda: server.request)
    server.push("")
    wait(4000, lambda: seen == [["ABC"], []])


def test_tracker_waits_for_late_server(qapp, wait):
    first = FakeAdbServer()
    port = first.port
    first.close()
    tracker = DeviceTracker(NO_ADB, port)
    tracker.start()
    wait(800)
    server = FakeAdbServer(port)
    wait(8000, lambda: server.request)
    tracker.stop()
    server.close()


# ---- scrcpy 进程 ----
@pytest.fixture
def run(qapp, monkeypatch):
    # venv 的 python.exe 只是个启动器，真窗口在子进程里，PID 对不上，所以用底层解释器
    monkeypatch.setenv("PYTHONPATH", str(Path(PySide6.__file__).parent.parent))
    created = []

    def _run(mode):
        session = ScrcpySession(Path(sys._base_executable), [FAKE, mode])
        ended = []
        session.ended.connect(lambda code, out: ended.append((code, out)))
        session.start()
        created.append(session)
        return session, ended

    yield _run
    for session in created:
        session.stop_blocking()


@pytest.mark.parametrize("mode,code", [("fail", 1), ("disconnect", 2)])
def test_session_exit_codes(run, wait, mode, code):
    _, ended = run(mode)
    wait(8000, lambda: ended)
    assert ended[0][0] == code
    assert ("ADB device" in ended[0][1]) == (mode == "fail")


def test_session_program_missing(qapp, wait):
    session = ScrcpySession(Path("C:/no/such/scrcpy.exe"), [])
    ended = []
    session.ended.connect(lambda code, out: ended.append((code, out)))
    session.start()
    wait(3000, lambda: ended)
    assert len(ended) == 1 and ended[0][0] == 1


def test_session_window_rect_and_graceful_stop(run, wait):
    session, ended = run("window")
    shown, rects = [], []
    session.windowShown.connect(lambda: shown.append(1))
    session.windowMoved.connect(rects.append)
    wait(15000, lambda: shown and rects)
    assert winmem.is_on_screen(rects[0])
    started = time.monotonic()
    session.stop()
    wait(5000, lambda: ended)
    assert ended[0][0] == 0                     # WM_CLOSE 正常退出，不是强杀
    assert time.monotonic() - started < 1.9


def test_session_minimize_and_show(run, wait):
    session, ended = run("window")
    session.minimize_window()                   # 窗口还没出现时什么也不做
    session.show_window()
    shown = []
    session.windowShown.connect(lambda: shown.append(1))
    wait(15000, lambda: shown)
    hwnd = session._hwnd
    session.minimize_window()
    wait(3000, lambda: win32gui.IsIconic(hwnd))
    session.show_window()
    wait(3000, lambda: not win32gui.IsIconic(hwnd))
    session.minimize_window()
    wait(3000, lambda: win32gui.IsIconic(hwnd))
    session.stop()                              # 最小化时也能正常关闭
    wait(5000, lambda: ended)
    assert ended[0][0] == 0
    session.show_window()                       # 窗口没了也不报错
    session.minimize_window()


def test_window_actions_on_dead_window_are_harmless(qapp):
    winmem.bring_to_front(0x7FFFFFF0)
    winmem.minimize(0x7FFFFFF0)


def test_session_kills_hung_process(run, wait):
    session, ended = run("hang")
    wait(500)
    session.stop()
    wait(6000, lambda: ended)
    assert len(ended) == 1


def test_off_screen_window_is_rejected(qapp):
    assert not winmem.is_on_screen(WindowRect(90000, 90000, 800, 600))


# ---- 快捷键 ----
@pytest.mark.parametrize("text,expected", [
    ("Ctrl+Alt+M", (3, 0x4D)), ("Ctrl+Alt+P", (3, 0x50)),
    ("Alt+1", (1, 0x31)), ("Alt+2", (1, 0x32)), ("Alt+3", (1, 0x33)),
    ("Ctrl+F5", (2, 0x74)), ("Ctrl+Shift+1", (6, 0x31)),
    ("M", None), ("Shift+M", None), ("", None), ("Ctrl+K, Ctrl+C", None),
])
def test_hotkey_parse(qapp, text, expected):
    assert hotkeys.parse(text) == expected


def test_hotkey_fires_and_detects_conflict(qapp, wait):
    manager, fired = hotkeys.HotkeyManager(), []
    combo = "Ctrl+Alt+Shift+F11"
    assert manager.set(71, combo, lambda: fired.append(1))
    assert not manager.set(72, combo, lambda: None)     # 已被占用
    thread = ctypes.windll.kernel32.GetCurrentThreadId()
    ctypes.windll.user32.PostThreadMessageW(thread, hotkeys.WM_HOTKEY, 71, 0)
    wait(1000, lambda: fired)
    wait(100)
    assert fired == [1]
    manager.clear_all()
    assert manager.set(72, combo, lambda: None)         # 释放后可再注册
    manager.clear_all()
    qapp.removeNativeEventFilter(manager)


# ---- 开机自启（用临时注册表键，不碰真正的 Run） ----
def test_autostart_registry():
    path = rf"Software\TouPingTest-{uuid.uuid4().hex}"
    try:
        assert autostart.current(path) is None
        assert autostart.apply(True, path)
        assert autostart.current(path) == autostart.command()
        assert autostart.apply(False, path)
        assert autostart.current(path) is None
        assert autostart.apply(False, path)
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


# ---- 设置窗口 ----
def test_settings_dialog(qapp):
    from PySide6.QtGui import QKeySequence
    from touping.settings_dialog import SettingsDialog
    dialog, saved = SettingsDialog(Settings()), []
    dialog.saved.connect(saved.append)
    dialog.bit_rate.setValue(12)
    dialog.edit_preset.setCurrentIndex(1)               # 切去改“高画质”
    assert dialog.bit_rate.value() == 16
    dialog.bit_rate.setValue(20)
    dialog.hotkeys["hotkey_toggle"].setKeySequence(QKeySequence("M"))
    dialog._save()
    assert not saved and "开始/停止投屏" in dialog.error.text()
    dialog.hotkeys["hotkey_toggle"].setKeySequence(QKeySequence("Ctrl+Alt+N"))
    dialog._save()
    result = saved[0]
    assert result.presets[LOW_LATENCY].bit_rate_mbps == 12
    assert result.presets[HIGH_QUALITY].bit_rate_mbps == 20
    assert result.hotkey_toggle == "Ctrl+Alt+N"
    assert result.hotkey_show == "Alt+1"


def test_settings_dialog_hotkeys(qapp):
    from PySide6.QtGui import QKeySequence
    from touping.settings_dialog import SettingsDialog
    dialog, saved = SettingsDialog(Settings()), []
    dialog.saved.connect(saved.append)
    edits = dialog.hotkeys
    assert list(edits) == list(DEFAULT_HOTKEYS)
    edits["hotkey_close"].setKeySequence(QKeySequence("Alt+1"))
    dialog._save()
    assert not saved
    assert "展示投屏" in dialog.error.text() and "关闭投屏" in dialog.error.text()
    dialog._show_hotkeys(DEFAULT_HOTKEYS)
    edits["hotkey_show"].setKeySequence(QKeySequence("Alt+2"))      # 互换两个
    edits["hotkey_minimize"].setKeySequence(QKeySequence("Alt+1"))
    edits["hotkey_close"].clear()
    edits["hotkey_toggle"].clear()                                  # 两个都留空不算重复
    dialog._save()
    result = saved[0]
    assert (result.hotkey_show, result.hotkey_minimize) == ("Alt+2", "Alt+1")
    assert (result.hotkey_close, result.hotkey_toggle) == ("", "")


def test_hotkey_edit_records_shift_digit(qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from touping.settings_dialog import HotkeyEdit
    mods = Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier
    edit = HotkeyEdit()
    qapp.sendEvent(edit, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Exclam, mods,
                                   2, 0x31, 0, "!"))
    text = edit.keySequence().toString()
    assert text == "Alt+Shift+1" and hotkeys.parse(text) == (5, 0x31)


def test_guide_lists_current_hotkeys(qapp):
    from touping.guide import GuideDialog
    dialog = GuideDialog()
    dialog.show_hotkeys(Settings())
    for combo in DEFAULT_HOTKEYS.values():
        assert combo in dialog._view.toPlainText()
    dialog.show_hotkeys(Settings(hotkey_show="Ctrl+Alt+9", hotkey_close=""))
    text = dialog._view.toPlainText()
    assert "Ctrl+Alt+9" in text and "关闭投屏：未设置" in text and "Alt+1" not in text
