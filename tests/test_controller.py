import pytest
from PySide6.QtCore import QObject, Signal

from touping.adbproto import Device
from touping.config import Settings
from touping.controller import Controller, Status, Timing
from touping.presets import HIGH_QUALITY, LOW_LATENCY

USB = Device("USB1", "device", "Pad")
TCP = Device("192.168.1.5:5555", "device", "Pad")


class FakeSession(QObject):
    windowShown = Signal()
    windowMoved = Signal(object)
    ended = Signal(int, str)

    def __init__(self, args):
        super().__init__()
        self.args, self.started, self.stopping = args, False, False
        self.window_calls = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopping = True

    def stop_blocking(self):
        self.ended.emit(0, "")

    def show_window(self):
        self.window_calls.append("show")

    def minimize_window(self):
        self.window_calls.append("minimize")


class Rig:
    def __init__(self):
        self.sessions, self.hints = [], []
        self.settings = Settings()
        self.resolve = {}
        self.ctl = Controller(
            lambda: self.settings, self._make,
            lambda serial, done: done(self.resolve.get(serial, "")),
            timing=Timing(retry=(20, 20, 20), unauthorized_hint=20),
        )
        self.ctl.notify.connect(lambda key, *_: self.hints.append(key))

    def _make(self, args):
        self.sessions.append(FakeSession(args))
        return self.sessions[-1]

    @property
    def last(self):
        return self.sessions[-1]

    def devices(self, *devices):
        self.ctl.on_devices(list(devices))

    def cast(self):
        self.devices(USB)
        self.last.windowShown.emit()
        return self.last


@pytest.fixture
def rig(qapp):
    return Rig()


def test_plug_starts_and_unplug_stops(rig):
    assert rig.ctl.status is Status.NO_DEVICE
    rig.devices(USB)
    assert rig.last.started and "--serial=USB1" in rig.last.args
    assert rig.ctl.status is Status.CONNECTING
    rig.last.windowShown.emit()
    assert rig.ctl.status is Status.CASTING
    rig.devices()
    assert rig.last.stopping
    rig.last.ended.emit(2, "")
    assert rig.ctl.status is Status.NO_DEVICE


def test_ten_replugs_always_recover(rig):
    for count in range(1, 11):
        rig.cast()
        assert len(rig.sessions) == count
        rig.devices()
        rig.last.ended.emit(2, "")
    assert rig.hints == []


def test_replug_before_old_session_closed(rig):
    """拔了又立刻插上，旧的 scrcpy 还没退完：等它退出后要自动再投。"""
    old = rig.cast()
    rig.devices()
    rig.devices(USB)
    assert len(rig.sessions) == 1 and old.stopping
    old.ended.emit(2, "")
    assert len(rig.sessions) == 2


def test_scrcpy_exits_before_tracker_notices_unplug(rig, wait):
    """拔线时 scrcpy 先退出、设备列表后更新：不能误报冲突，也不能乱重试。"""
    rig.cast().ended.emit(2, "disconnected")
    assert rig.ctl.status is Status.CONNECTING
    rig.devices()
    wait(80)
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.NO_DEVICE
    rig.devices(USB)
    assert len(rig.sessions) == 2


def test_manual_stop_stays_stopped_until_replug(rig):
    session = rig.cast()
    rig.ctl.toggle()
    assert session.stopping
    session.ended.emit(0, "")
    rig.devices(USB)
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.READY
    rig.devices()
    rig.devices(USB)
    assert len(rig.sessions) == 2


def test_closing_window_counts_as_manual_stop(rig):
    rig.cast().ended.emit(0, "")
    rig.devices(USB)
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.READY
    rig.ctl.toggle()
    assert len(rig.sessions) == 2


def test_unauthorized_flow(rig, wait):
    rig.devices(Device("USB1", "unauthorized"))
    assert rig.ctl.status is Status.PENDING and not rig.sessions
    wait(60)
    assert rig.hints == ["unauthorized"]
    rig.devices(USB)
    assert len(rig.sessions) == 1


def test_brief_unauthorized_is_silent(rig, wait):
    rig.devices(Device("USB1", "unauthorized"))
    rig.devices(USB)
    wait(60)
    assert rig.hints == [] and len(rig.sessions) == 1


def test_retries_then_gives_up(rig, wait):
    rig.devices(USB)
    for expected in (2, 3, 4):
        rig.last.ended.emit(1, "ERROR: boom")
        wait(500, lambda n=expected: len(rig.sessions) == n)
    rig.last.ended.emit(1, "ERROR: boom")
    wait(80)
    assert len(rig.sessions) == 4 and "failed" in rig.hints
    assert rig.ctl.status is Status.READY


def test_repeated_disconnects_warn_about_adb_conflict(rig, wait):
    rig.devices(USB)
    for expected in (2, 3, 4):
        rig.last.windowShown.emit()
        rig.last.ended.emit(2, "")
        wait(500, lambda n=expected: len(rig.sessions) == n)
    assert rig.hints.count("adb_conflict") == 1


def test_server_lost_repeatedly_warns(rig):
    for _ in range(3):
        rig.ctl.on_server_lost()
    assert rig.hints == ["adb_conflict"]


def test_stop_during_retry_cancels_it(rig, wait):
    rig.devices(USB)
    rig.last.ended.emit(1, "")
    rig.ctl.toggle()
    wait(80)
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.READY


def test_codec_fallback(rig, wait):
    rig.settings.default_preset = rig.ctl.preset_name = HIGH_QUALITY
    rig.devices(USB)
    assert "--video-codec=h265" in rig.last.args
    rig.last.ended.emit(1, "ERROR: Could not create default video encoder for h265")
    wait(500, lambda: len(rig.sessions) == 2)
    assert "--video-codec=h264" in rig.last.args and "codec" in rig.hints


def test_switch_preset_restarts(rig):
    session = rig.cast()
    rig.ctl.cycle_preset()
    assert session.stopping and rig.ctl.preset_name == HIGH_QUALITY
    session.ended.emit(0, "")
    assert "--video-bit-rate=16M" in rig.last.args and len(rig.sessions) == 2


def test_switch_preset_twice_uses_the_last_one(rig):
    session = rig.cast()
    rig.ctl.cycle_preset()
    rig.ctl.cycle_preset()
    session.ended.emit(0, "")
    assert len(rig.sessions) == 2 and "--video-bit-rate=8M" in rig.last.args


def test_stop_wins_over_pending_restart(rig):
    session = rig.cast()
    rig.ctl.cycle_preset()
    rig.ctl.stop()
    session.ended.emit(0, "")
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.READY


def test_unplug_during_restart(rig):
    session = rig.cast()
    rig.ctl.cycle_preset()
    rig.devices()
    session.ended.emit(2, "")
    assert len(rig.sessions) == 1
    rig.devices(USB)
    assert len(rig.sessions) == 2


def test_switch_to_wireless_survives_usb_blip_and_unplug(rig):
    rig.resolve[TCP.serial] = "USB1"
    wired = rig.cast()
    assert rig.ctl.can_go_wireless
    rig.ctl.switch_wireless()
    wired.ended.emit(0, "")
    assert "--tcpip" in rig.last.args and "--video-bit-rate=4M" in rig.last.args
    rig.devices()                   # adbd 重启，USB 闪断
    rig.devices(USB)
    rig.devices(USB, TCP)
    rig.last.windowShown.emit()
    rig.devices(TCP)                # 拔线
    rig.devices(USB, TCP)           # 再插上也不重复投屏
    assert len(rig.sessions) == 2 and not rig.last.stopping
    assert rig.ctl.wireless and "无线" in rig.ctl.summary
    assert rig.ctl.identity(TCP.serial) == "USB1"


def test_wireless_preset_switch_after_unplug_uses_tcp_serial(rig):
    rig.resolve[TCP.serial] = "USB1"
    rig.cast()
    rig.ctl.switch_wireless()
    rig.last.ended.emit(0, "")
    rig.devices(USB, TCP)
    rig.devices(TCP)
    rig.ctl.cycle_preset()
    rig.last.ended.emit(0, "")
    assert f"--serial={TCP.serial}" in rig.last.args
    assert "--tcpip" not in rig.last.args
    assert "--video-bit-rate=4M" in rig.last.args


def test_wireless_failure_falls_back_to_wired(rig):
    rig.cast()
    rig.ctl.switch_wireless()
    rig.last.ended.emit(0, "")
    rig.last.ended.emit(1, "ERROR: no ip")
    assert "wireless" in rig.hints
    assert len(rig.sessions) == 3 and "--tcpip" not in rig.last.args


def test_wireless_lost_without_cable_asks_to_replug(rig):
    rig.resolve[TCP.serial] = "USB1"
    rig.cast()
    rig.ctl.switch_wireless()
    rig.last.ended.emit(0, "")
    rig.devices(USB, TCP)
    rig.devices(TCP)
    rig.devices()
    rig.last.ended.emit(2, "")
    assert rig.hints == ["wireless"] and rig.ctl.status is Status.NO_DEVICE
    rig.devices(USB)
    assert len(rig.sessions) == 3 and "--tcpip" not in rig.last.args


def test_switch_back_to_wired(rig):
    rig.resolve[TCP.serial] = "USB1"
    rig.cast()
    rig.ctl.switch_wireless()
    rig.last.ended.emit(0, "")
    rig.devices(USB, TCP)
    assert rig.ctl.can_go_wired
    rig.ctl.switch_wired()
    rig.last.ended.emit(0, "")
    assert "--serial=USB1" in rig.last.args and not rig.ctl.wireless


def test_wireless_device_does_not_autostart(rig):
    rig.devices(TCP)
    assert not rig.sessions and rig.ctl.status is Status.READY
    rig.ctl.toggle()
    assert "--video-bit-rate=4M" in rig.last.args


# ---- 展示、最小化、关闭 ----
def test_show_minimize_close_cycle(rig):
    session = rig.cast()
    rig.ctl.minimize()
    rig.ctl.show()
    assert session.window_calls == ["minimize", "show"] and not session.stopping
    rig.ctl.close()
    assert session.stopping
    session.ended.emit(0, "")
    assert rig.ctl.status is Status.READY
    rig.devices(USB)                # 关掉之后不会自己又弹出来
    assert len(rig.sessions) == 1
    rig.ctl.show()
    assert len(rig.sessions) == 2 and rig.last.started
    assert rig.hints == []


def test_show_without_device_explains(rig):
    rig.ctl.show()
    assert rig.hints == ["no_device"] and not rig.sessions


def test_minimize_and_close_without_casting_only_hint(rig):
    rig.ctl.minimize()
    rig.ctl.close()
    assert rig.hints == ["not_casting", "not_casting"] and not rig.sessions
    rig.devices(USB)
    assert len(rig.sessions) == 1   # 没在投屏时按关闭，不影响之后插线自动投屏


def test_window_keys_wait_for_window(rig):
    """窗口还没出现：展示和最小化都不做，也不能重复启动。"""
    rig.devices(USB)
    rig.ctl.show()
    rig.ctl.minimize()
    assert len(rig.sessions) == 1 and rig.last.window_calls == []
    assert not rig.last.stopping and rig.hints == []


def test_close_during_startup(rig):
    rig.devices(USB)
    rig.ctl.close()
    assert rig.last.stopping
    rig.last.ended.emit(0, "")
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.READY


def test_show_right_after_close_reopens(rig):
    """按完关闭马上按展示：旧窗口还没退完，等它退出后重新投屏。"""
    session = rig.cast()
    rig.ctl.close()
    rig.ctl.show()
    rig.ctl.show()
    assert len(rig.sessions) == 1 and session.window_calls == []
    session.ended.emit(0, "")
    assert len(rig.sessions) == 2 and rig.last.started


def test_close_show_close_stays_closed(rig):
    session = rig.cast()
    rig.ctl.close()
    rig.ctl.show()
    rig.ctl.close()
    session.ended.emit(0, "")
    rig.devices(USB)
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.READY


def test_unplug_after_close_and_show(rig):
    session = rig.cast()
    rig.ctl.close()
    rig.ctl.show()
    rig.devices()
    session.ended.emit(0, "")
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.NO_DEVICE
    rig.devices(USB)
    assert len(rig.sessions) == 2


def test_close_during_retry_cancels_it(rig, wait):
    rig.devices(USB)
    rig.last.ended.emit(1, "")
    rig.ctl.minimize()
    rig.ctl.show()                  # 等待重试时按展示，不会多起一个
    rig.ctl.close()
    wait(80)
    assert len(rig.sessions) == 1 and rig.ctl.status is Status.READY
    assert rig.hints == []


def test_second_device_does_not_interrupt(rig):
    rig.cast()
    rig.devices(USB, Device("USB2", "device"))
    assert len(rig.sessions) == 1


def test_toggle_without_device_explains(rig):
    rig.ctl.toggle()
    assert rig.hints == ["no_device"] and not rig.sessions


def test_settings_change_restarts_only_when_needed(rig):
    session = rig.cast()
    rig.ctl.settings_applied()
    assert not session.stopping
    from dataclasses import replace
    preset = rig.settings.presets[LOW_LATENCY]
    rig.settings.presets[LOW_LATENCY] = replace(preset, max_size=1280)
    rig.ctl.settings_applied()
    assert session.stopping
    session.ended.emit(0, "")
    assert "--max-size=1280" in rig.last.args


def test_shutdown_never_restarts(rig):
    rig.cast()
    rig.ctl.shutdown()
    assert len(rig.sessions) == 1 and not rig.ctl.active


def test_replug_during_restart_leaves_no_stale_autostart(rig, wait):
    """切预设重启期间拔插：之后失败到放弃时，不能又被当成新设备无限重来。"""
    session = rig.cast()
    rig.ctl.cycle_preset()
    rig.devices()
    rig.devices(USB)
    session.ended.emit(0, "")
    for expected in (3, 4, 5):
        rig.last.ended.emit(1, "ERROR: boom")
        wait(500, lambda n=expected: len(rig.sessions) == n)
    rig.last.ended.emit(1, "ERROR: boom")
    wait(80)
    assert len(rig.sessions) == 5 and rig.ctl.status is Status.READY


def test_manual_stop_survives_state_flicker(rig):
    rig.cast()
    rig.ctl.toggle()
    rig.last.ended.emit(0, "")
    rig.devices(Device("USB1", "offline"))
    rig.devices(USB)
    assert len(rig.sessions) == 1


def test_manual_stop_does_not_jump_to_second_device(rig):
    session = rig.cast()
    rig.devices(USB, Device("USB2", "device"))
    rig.ctl.toggle()
    session.ended.emit(0, "")
    assert len(rig.sessions) == 1
