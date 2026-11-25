"""投屏控制器：决定什么时候开始、停止、重试投屏。

不直接碰 scrcpy、adb 和托盘，都由外部注入，便于用假对象测试。
“identity” 指一台物理设备：有线用 USB 序列号；无线设备查到它的 ro.serialno 后，
和有线记录合并成同一台。
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .adbproto import Device
from .config import Settings
from .presets import PRESET_LABELS, PRESET_ORDER, Preset, WindowRect, build_args
from .session import EXIT_DISCONNECTED, EXIT_OK

log = logging.getLogger(__name__)


class Status(Enum):
    NO_DEVICE = "没有设备"
    PENDING = "设备未授权或正在连接"
    READY = "已连接，未投屏"
    CONNECTING = "正在启动投屏"
    CASTING = "正在投屏"


@dataclass
class Timing:
    """所有等待时间，单位毫秒。测试里会调小。"""
    retry: tuple[int, ...] = (1000, 2000, 4000)
    unauthorized_hint: int = 1500    # 已授权设备刚插上也会短暂显示未授权
    stable_after: int = 30000        # 稳定投屏这么久后，失败次数清零
    flap_window: int = 60000
    flap_count: int = 3


@dataclass(frozen=True)
class Plan:
    serial: str
    identity: str
    preset_name: str
    preset: Preset
    wireless: bool = False
    enable_tcpip: bool = False


class StopReason(Enum):
    USER = 1
    RESTART = 2
    REMOVED = 3
    QUIT = 4


class Controller(QObject):
    changed = Signal()
    notify = Signal(str, str, str)      # key, 标题, 正文
    windowMoved = Signal(object)
    sessionEnded = Signal()

    def __init__(
        self,
        settings: Callable[[], Settings],
        session_factory: Callable[[list[str]], QObject],
        resolve_identity: Callable[[str, Callable[[str], None]], None] | None = None,
        window_provider: Callable[[], WindowRect | None] = lambda: None,
        timing: Timing | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._session_factory = session_factory
        self._resolve_identity = resolve_identity
        self._window_provider = window_provider
        self.timing = timing or Timing()

        self.devices: dict[str, Device] = {}
        self.preset_name = settings().default_preset
        self._alias: dict[str, str] = {}        # 无线序列号 -> 物理设备
        self._resolving: set[str] = set()
        self._armed: list[str] = []             # 新插上、等着自动投屏的设备
        self._suppressed: set[str] = set()      # 用户手动停过，拔插之前不再自动投屏
        self._hinted: set[str] = set()          # 已提示过授权的设备

        self._session = None
        self._plan: Plan | None = None
        self._next_plan: Plan | None = None
        self._reason: StopReason | None = None
        self._window_shown = False
        self._shown_at = 0.0
        self._plugged_at = 0.0
        self._failures = 0
        self._force_h264: set[str] = set()      # 已知编不了 h265/av1 的设备
        self._flaps: deque[float] = deque()

        self._disconnected_pending = False
        self._retry_plan: Plan | None = None
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._on_retry)

    # ---- 状态查询 ----
    def identity(self, serial: str) -> str:
        return self._alias.get(serial, serial)

    @property
    def active(self) -> bool:
        return self._session is not None or self._retry.isActive()

    @property
    def status(self) -> Status:
        if self._session is not None and self._window_shown:
            return Status.CASTING
        if self.active:
            return Status.CONNECTING
        if any(d.online for d in self.devices.values()):
            return Status.READY
        return Status.PENDING if self.devices else Status.NO_DEVICE

    @property
    def wireless(self) -> bool:
        return bool(self._plan and self._session is not None and self._plan.wireless)

    @property
    def summary(self) -> str:
        plan = self._plan if self._session is not None else self._retry_plan
        if plan is not None:
            device = self._device_for(plan)
            link = "无线" if plan.wireless else "有线"
            label = device.label if device else plan.identity
            return (f"{self.status.value}：{label}"
                    f"（{link}，{PRESET_LABELS[plan.preset_name]}）")
        target = self._pick_device() or next(iter(self.devices.values()), None)
        return f"{self.status.value}：{target.label}" if target else self.status.value

    @property
    def can_go_wireless(self) -> bool:
        return not self.wireless and self._usb_target() is not None

    @property
    def can_go_wired(self) -> bool:
        return self.wireless and self._find(self._plan.identity, tcp=False) is not None

    # ---- 用户操作 ----
    def toggle(self) -> None:
        if self.active:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self.active:
            return
        device = self._pick_device()
        if device is None:
            self._explain_no_device()
            return
        self._suppressed.discard(self.identity(device.serial))
        self._failures = 0
        self._launch(self._make_plan(device, wireless=device.is_tcp))

    def stop(self) -> None:
        plan = self._plan if self._session is not None else self._retry_plan
        if plan is not None:
            self._suppressed.add(plan.identity)
        self._armed.clear()
        self._cancel_retry()
        self._next_plan = None
        if self._session is not None:
            self._stop_session(StopReason.USER)
        self.changed.emit()

    def show(self) -> None:
        """展示投屏：没在投屏就开始，已经在投屏就把窗口调到最前面。"""
        if self._session is None:
            self.start()
        elif self._reason is StopReason.USER:
            self._restart_after_stop()
        elif self._window_shown and self._reason is None:
            self._session.show_window()

    def minimize(self) -> None:
        if not self.active:
            self._explain_not_casting()
        elif self._window_shown and self._reason is None:
            self._session.minimize_window()

    def close(self) -> None:
        if self.active:
            self.stop()
        else:
            self._explain_not_casting()

    def set_preset(self, name: str) -> None:
        if name not in PRESET_ORDER or name == self.preset_name:
            return
        self.preset_name = name
        self._restart_current()
        self.changed.emit()

    def cycle_preset(self) -> None:
        index = PRESET_ORDER.index(self.preset_name)
        self.set_preset(PRESET_ORDER[(index + 1) % len(PRESET_ORDER)])
        self.notify.emit("preset", "已切换预设", PRESET_LABELS[self.preset_name])

    def settings_applied(self) -> None:
        """设置改了：正在投屏且参数真的变了，就重启投屏让它生效。"""
        if self._plan is not None and self._session is not None:
            if self._effective_preset(self._plan.identity) != self._plan.preset:
                self._restart_current()
        self.changed.emit()

    def switch_wireless(self) -> None:
        usb = self._usb_target()
        if usb is None or self.wireless:
            return
        identity = self.identity(usb.serial)
        self._suppressed.discard(identity)
        tcp = self._find(identity, tcp=True)
        plan = self._make_plan(tcp or usb, wireless=True)
        self._replace_with(plan)

    def switch_wired(self) -> None:
        if not self.can_go_wired:
            return
        usb = self._find(self._plan.identity, tcp=False)
        self._replace_with(self._make_plan(usb, wireless=False))

    def shutdown(self) -> None:
        self._cancel_retry()
        self._armed.clear()
        self._next_plan = None
        if self._session is not None:
            self._reason = StopReason.QUIT
            self._session.stop_blocking()

    # ---- 设备变化 ----
    def on_devices(self, devices: list[Device]) -> None:
        before = self.devices
        self.devices = {d.serial: d for d in devices}
        present = {self.identity(s) for s in self.devices}

        for identity in {self.identity(s) for s in before} - present:
            self._suppressed.discard(identity)
            if identity in self._armed:
                self._armed.remove(identity)
        for serial in set(before) - set(self.devices):
            self._hinted.discard(serial)
            self._alias.pop(serial, None)

        for device in devices:
            was_online = device.serial in before and before[device.serial].online
            if device.online and not was_online:
                self._on_online(device)
            elif not device.online and device.serial not in self._hinted:
                QTimer.singleShot(
                    self.timing.unauthorized_hint, self,
                    lambda s=device.serial: self._hint_unauthorized(s),
                )

        if self._retry.isActive() and not self._plan_alive(self._retry_plan):
            self._cancel_retry()
        if self._session is not None and not self._plan_alive(self._plan):
            self._stop_session(StopReason.REMOVED)
        self._consider_autostart()
        self.changed.emit()

    def on_server_lost(self) -> None:
        self._record_flap()

    def _on_online(self, device: Device) -> None:
        self._hinted.discard(device.serial)
        if device.is_tcp:
            # 无线是补充手段：只做合并，不自动投屏
            self._start_resolving(device.serial)
            return
        identity = self.identity(device.serial)
        running_here = (
            self._session is not None
            and self._reason is None
            and self._plan.identity == identity
        )
        if running_here or identity in self._suppressed:
            return      # 例如切无线时 adbd 重启，USB 会闪断一下
        if identity not in self._armed:
            self._armed.append(identity)
            self._plugged_at = time.monotonic()

    def _start_resolving(self, serial: str) -> None:
        if self._resolve_identity is None or serial in self._resolving:
            return
        self._resolving.add(serial)

        def done(identity: str) -> None:
            self._resolving.discard(serial)
            if identity and serial in self.devices:
                self._alias[serial] = identity
                log.info("合并设备：%s 就是 %s", serial, identity)
                self.changed.emit()

        self._resolve_identity(serial, done)

    def _consider_autostart(self) -> None:
        if self.active:
            return
        for identity in list(self._armed):
            device = self._find(identity, tcp=False)
            if device is not None:
                self._failures = 0
                self._launch(self._make_plan(device, wireless=False))
                return

    # ---- 会话 ----
    def _launch(self, plan: Plan) -> None:
        self._cancel_retry()
        if plan.identity in self._armed:
            self._armed.remove(plan.identity)
        self._plan = plan
        self._reason = None
        self._window_shown = False
        args = build_args(
            plan.serial, plan.preset,
            wireless=plan.wireless, enable_tcpip=plan.enable_tcpip,
            window=self._window_provider(),
        )
        session = self._session_factory(args)
        self._session = session
        session.windowShown.connect(self._on_window_shown)
        session.windowMoved.connect(self.windowMoved)
        session.ended.connect(self._on_ended)
        session.start()
        self.changed.emit()

    def _stop_session(self, reason: StopReason) -> None:
        # 已经在为“重启”收尾时，用户停止、设备拔出的优先级更高
        if self._reason in (None, StopReason.RESTART):
            self._reason = reason
        self._session.stop()

    def _replace_with(self, plan: Plan) -> None:
        self._cancel_retry()
        self._failures = 0
        if self._session is None:
            self._launch(plan)
        else:
            self._next_plan = plan
            self._stop_session(StopReason.RESTART)

    def _restart_after_stop(self) -> None:
        """刚停止、scrcpy 还没退完就又要展示：等它退出后重新投屏。"""
        device = self._pick_device()
        if device is None:
            return
        self._suppressed.discard(self.identity(device.serial))
        self._failures = 0
        self._next_plan = self._make_plan(device, wireless=device.is_tcp)
        self._reason = StopReason.RESTART

    def _restart_current(self) -> None:
        plan = self._plan if self._session is not None else self._retry_plan
        if plan is None or self._reason not in (None, StopReason.RESTART):
            return
        if plan.wireless and self._next_plan is None:
            device = (self._find(plan.identity, tcp=True)
                      or self._find(plan.identity, tcp=False))
        else:
            base = self._next_plan or plan
            device = self.devices.get(base.serial)
            plan = base
        if device is not None:
            self._replace_with(self._make_plan(device, wireless=plan.wireless))

    def _on_window_shown(self) -> None:
        self._window_shown = True
        self._shown_at = time.monotonic()
        if self._plugged_at:
            log.info("从检测到设备到出现窗口：%.2f 秒",
                     self._shown_at - self._plugged_at)
            self._plugged_at = 0.0
        self.changed.emit()

    def _on_ended(self, code: int, output: str) -> None:
        session, self._session = self._session, None
        reason, self._reason = self._reason, None
        shown, self._window_shown = self._window_shown, False
        plan, next_plan = self._plan, self._next_plan
        self._next_plan = None
        if session is not None:
            session.deleteLater()
        self.sessionEnded.emit()

        if reason is StopReason.QUIT:
            return
        if reason is StopReason.RESTART and next_plan is not None:
            if self._plan_alive(next_plan):
                self._launch(next_plan)
                return
        elif reason is None:
            if code == EXIT_OK:
                # 用户直接关了投屏窗口，等同于手动停止
                self._suppressed.add(plan.identity)
                self._armed.clear()
            elif plan.wireless:
                self._wireless_failed(plan)
            else:
                self._wired_failed(plan, code, output, shown)
        self._consider_autostart()
        self.changed.emit()

    def _wireless_failed(self, plan: Plan) -> None:
        if self._find(plan.identity, tcp=False) is not None:
            if plan.identity not in self._armed:
                self._armed.append(plan.identity)
            text = "已改回有线投屏。请确认电脑和平板在同一个 Wi‑Fi 下。"
        else:
            text = "请重新插上数据线，会自动恢复有线投屏。"
        self.notify.emit("wireless", "无线连接失败或已断开", text)

    def _wired_failed(self, plan: Plan, code: int, output: str, shown: bool) -> None:
        stable_s = self.timing.stable_after / 1000
        if shown and time.monotonic() - self._shown_at >= stable_s:
            self._failures = 0

        if (not shown and plan.preset.video_codec != "h264"
                and re.search(r"encoder|codec", output, re.IGNORECASE)):
            self._force_h264.add(plan.identity)
            plan = replace(plan, preset=replace(plan.preset, video_codec="h264"))
            self.notify.emit("codec", "这台设备不支持当前编码", "已自动改用 H.264。")

        if self._failures >= len(self.timing.retry):
            self._suppressed.add(plan.identity)
            self.notify.emit("failed", "投屏失败", self._explain_failure(output))
            return
        self._retry_plan = plan
        self._retry.start(self.timing.retry[self._failures])
        self._failures += 1
        if code == EXIT_DISCONNECTED:
            self._disconnected_pending = True

    def _on_retry(self) -> None:
        plan, self._retry_plan = self._retry_plan, None
        if self._disconnected_pending:
            # 等了一会儿设备还在，说明不是拔线，而是连接被抢或不稳
            self._disconnected_pending = False
            self._record_flap()
        if plan is not None and self._plan_alive(plan):
            self._launch(plan)
        else:
            self._consider_autostart()
        self.changed.emit()

    def _cancel_retry(self) -> None:
        self._retry.stop()
        self._retry_plan = None
        self._disconnected_pending = False

    # ---- 选设备 ----
    def _find(self, identity: str, *, tcp: bool) -> Device | None:
        for device in self.devices.values():
            if (device.online and device.is_tcp == tcp
                    and self.identity(device.serial) == identity):
                return device
        return None

    def _pick_device(self) -> Device | None:
        online = [d for d in self.devices.values() if d.online]
        return next((d for d in online if not d.is_tcp), online[0] if online else None)

    def _usb_target(self) -> Device | None:
        if self._plan is not None and self._session is not None:
            return self._find(self._plan.identity, tcp=False)
        return next(
            (d for d in self.devices.values() if d.online and not d.is_tcp), None
        )

    def _device_for(self, plan: Plan) -> Device | None:
        return self.devices.get(plan.serial) or next(
            (d for s, d in self.devices.items() if self.identity(s) == plan.identity),
            None,
        )

    def _plan_alive(self, plan: Plan | None) -> bool:
        if plan is None:
            return False
        if plan.wireless and plan.enable_tcpip:
            # 序列号是有线的，但画面走 Wi-Fi：拔线、USB 闪断都不影响它
            return True
        device = self.devices.get(plan.serial)
        return device is not None and device.online

    def _effective_preset(self, identity: str) -> Preset:
        preset = self._settings().presets[self.preset_name]
        if identity in self._force_h264 and preset.video_codec != "h264":
            preset = replace(preset, video_codec="h264")
        return preset

    def _make_plan(self, device: Device, *, wireless: bool) -> Plan:
        identity = self.identity(device.serial)
        return Plan(
            serial=device.serial,
            identity=identity,
            preset_name=self.preset_name,
            preset=self._effective_preset(identity),
            wireless=wireless,
            enable_tcpip=wireless and not device.is_tcp,
        )

    # ---- 提示 ----
    def _hint_unauthorized(self, serial: str) -> None:
        device = self.devices.get(serial)
        if device is None or device.online or serial in self._hinted:
            return
        self._hinted.add(serial)
        self._emit_pending_hint(device)

    def _emit_pending_hint(self, device: Device) -> None:
        if device.state in ("unauthorized", "authorizing"):
            self.notify.emit(
                "unauthorized", "设备还没有授权",
                "请解锁平板，在弹窗里勾选“一律允许”，再点“允许 USB 调试”。",
            )
        else:
            self.notify.emit(
                "offline", f"设备暂时不可用（{device.state}）",
                "请重新插拔数据线，或换一个 USB 口。",
            )

    def _explain_no_device(self) -> None:
        if self.devices:
            self._emit_pending_hint(next(iter(self.devices.values())))
        else:
            self.notify.emit(
                "no_device", "没有检测到设备",
                "请用数据线连接平板，并打开 USB 调试。点这里看说明。",
            )

    def _explain_not_casting(self) -> None:
        self.notify.emit("not_casting", "当前没有投屏", self.summary)

    @staticmethod
    def _explain_failure(output: str) -> str:
        errors = [l for l in output.splitlines() if "ERROR" in l.upper()]
        detail = (errors or output.splitlines() or ["没有更多信息"])[-1]
        return f"{detail[:150]}\n请重新插拔数据线再试。"

    def _record_flap(self) -> None:
        now = time.monotonic()
        self._flaps.append(now)
        while self._flaps and now - self._flaps[0] > self.timing.flap_window / 1000:
            self._flaps.popleft()
        if len(self._flaps) >= self.timing.flap_count:
            self._flaps.clear()
            self.notify.emit(
                "adb_conflict", "连接反复断开",
                "可能有其他手机助手在抢占连接。请关闭手机助手、刷机工具、"
                "模拟器等程序后重新插线。",
            )
