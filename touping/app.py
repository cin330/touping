"""托盘主程序：把各个模块接起来。"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
import time

from PySide6.QtCore import QObject, QProcess, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import APP_ID, APP_NAME, __version__, autostart, paths, winmem
from .config import HOTKEY_LABELS, Settings, SettingsStore
from .controller import Controller, Status
from .guide import GuideDialog
from .hotkeys import HotkeyManager
from .icons import app_icon, status_icon
from .presets import PRESET_LABELS, PRESET_ORDER
from .session import ScrcpySession
from .settings_dialog import SettingsDialog
from .tracker import DeviceTracker

log = logging.getLogger(__name__)

SAME_HINT_INTERVAL_S = 8
FIRST_RUN_HINT_MS = 4000
IDENTITY_TIMEOUT_MS = 5000
GUIDE_KEYS = {"no_device", "unauthorized", "offline", "first_run",
              "adb_conflict", "missing", "miracast"}


def setup_logging() -> None:
    directory = paths.data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        directory / "touping.log", maxBytes=512 * 1024, backupCount=2,
        encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler())

    def hook(kind, value, tb):
        log.critical("未处理的异常", exc_info=(kind, value, tb))

    sys.excepthook = hook


class SingleInstance(QObject):
    """同一用户只跑一份；再次双击时让已有的那份弹个气泡。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        user = re.sub(r"\W", "_", os.environ.get("USERNAME", "user"))
        self._name = f"{APP_ID}-{user}"
        self.server: QLocalServer | None = None

    def acquire(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self._name)
        if probe.waitForConnected(300):
            probe.disconnectFromServer()
            return False
        QLocalServer.removeServer(self._name)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        return self.server.listen(self._name)


class TrayApp(QObject):
    def __init__(self, qapp: QApplication, instance: SingleInstance):
        super().__init__()
        self._qapp = qapp
        self.store = SettingsStore(paths.data_dir() / "config.json")
        self._hint_times: dict[str, float] = {}
        self._last_hint = ""
        self._settings_dialog: SettingsDialog | None = None
        self._guide: GuideDialog | None = None

        self.tracker = DeviceTracker(paths.adb_exe(), parent=self)
        self.controller = Controller(
            settings=lambda: self.store.settings,
            session_factory=self._new_session,
            resolve_identity=self._resolve_identity,
            window_provider=self._remembered_window,
            parent=self,
        )
        self.tracker.devicesChanged.connect(self.controller.on_devices)
        self.tracker.serverLost.connect(self.controller.on_server_lost)
        self.controller.changed.connect(self.refresh)
        self.controller.notify.connect(self.notify)
        self.controller.windowMoved.connect(self._remember_window)
        self.controller.sessionEnded.connect(self.store.save)

        self._icons = {status: status_icon(status) for status in Status}
        self.tray = QSystemTrayIcon(self._icons[Status.NO_DEVICE], self)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self._on_message_clicked)
        self._build_menu()
        self.tray.show()

        self.hotkeys = HotkeyManager()
        self._hotkey_actions = {
            "hotkey_show": self.controller.show,
            "hotkey_minimize": self.controller.minimize,
            "hotkey_close": self.controller.close,
            "hotkey_toggle": self.controller.toggle,
            "hotkey_preset": self.controller.cycle_preset,
        }
        instance.server.newConnection.connect(self._on_second_instance)
        qapp.aboutToQuit.connect(self._on_quit)
        qapp.commitDataRequest.connect(lambda _manager: self.store.save())

        self._apply_hotkeys()
        if self.store.settings.autostart:
            autostart.apply(True)       # 程序挪了位置时顺便修正路径
        self.refresh()

        missing = paths.missing_binaries()
        if missing:
            log.error("缺少文件：%s，目录 %s", missing, paths.scrcpy_dir())
            fix = ("请重新解压完整的程序。" if paths.is_frozen()
                   else "请先运行 scripts\\fetch_scrcpy.ps1。")
            self.notify("missing", "缺少投屏组件",
                        f"找不到 {'、'.join(missing)}，{fix}")
        else:
            self.tracker.start()
            if not self.store.settings.guide_shown:
                QTimer.singleShot(FIRST_RUN_HINT_MS, self, self._first_run_hint)

    # ---- 菜单 ----
    def _build_menu(self) -> None:
        self.menu = QMenu()
        self.act_status = self.menu.addAction("")
        self.act_status.setEnabled(False)
        self.menu.addSeparator()
        self.act_toggle = self.menu.addAction("开始投屏", self.controller.toggle)

        presets = self.menu.addMenu("切换预设")
        group = QActionGroup(presets)
        self.preset_actions: dict[str, QAction] = {}
        for name in PRESET_ORDER:
            action = presets.addAction(PRESET_LABELS[name])
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, n=name: self.controller.set_preset(n))
            group.addAction(action)
            self.preset_actions[name] = action

        self.act_wireless = self.menu.addAction("切换到无线", self._on_wireless)
        self.menu.addSeparator()
        self.menu.addAction("免 USB 调试投屏（Windows 无线显示器）…", self.open_miracast)
        self.menu.addAction("使用说明", self.show_guide)
        self.menu.addAction("设置…", self.show_settings)
        self.menu.addSeparator()
        self.menu.addAction("退出", self._qapp.quit)
        self.tray.setContextMenu(self.menu)

    def refresh(self) -> None:
        ctl = self.controller
        status = ctl.status
        self.tray.setIcon(self._icons[status])
        self.tray.setToolTip(f"{APP_NAME}\n{ctl.summary}")
        self.act_status.setText(ctl.summary)
        self.act_toggle.setText("停止投屏" if ctl.active else "开始投屏")
        for name, action in self.preset_actions.items():
            action.setChecked(name == ctl.preset_name)
        self.act_wireless.setText("切回有线" if ctl.wireless else "切换到无线")
        self.act_wireless.setEnabled(ctl.can_go_wireless or ctl.can_go_wired)

    def _on_wireless(self) -> None:
        if self.controller.wireless:
            self.controller.switch_wired()
        else:
            self.controller.switch_wireless()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.controller.toggle()

    # ---- 提示 ----
    def notify(self, key: str, title: str, text: str) -> None:
        now = time.monotonic()
        if now - self._hint_times.get(key, -1e9) < SAME_HINT_INTERVAL_S \
                and key != "preset":
            return
        self._hint_times[key] = now
        self._last_hint = key
        log.info("提示：%s｜%s", title, text.replace("\n", " "))
        self.tray.showMessage(title, text, app_icon(), 6000)

    def _on_message_clicked(self) -> None:
        if self._last_hint in GUIDE_KEYS:
            self.show_guide()

    def _first_run_hint(self) -> None:
        self.store.settings.guide_shown = True
        self.store.save()
        if self.controller.status is Status.NO_DEVICE:
            self.notify("first_run", f"{APP_NAME}已在托盘运行",
                        "用数据线连接平板就会自动投屏。第一次使用请点这里看说明。")

    def _on_second_instance(self) -> None:
        socket = self.sender().nextPendingConnection() if self.sender() else None
        if socket is not None:
            socket.deleteLater()
        self.notify("running", f"{APP_NAME}已经在运行", self.controller.summary)

    # ---- 窗口 ----
    def show_guide(self) -> None:
        if self._guide is None:
            self._guide = GuideDialog()
            self._guide.setWindowIcon(app_icon())
        self._guide.show_hotkeys(self.store.settings)
        self._guide.show()
        self._guide.raise_()
        self._guide.activateWindow()

    def show_settings(self) -> None:
        if self._settings_dialog is None:
            dialog = SettingsDialog(self.store.settings)
            dialog.setWindowIcon(app_icon())
            dialog.saved.connect(self._on_settings_saved)
            dialog.finished.connect(self._on_settings_closed)
            self._settings_dialog = dialog
            # 录入新快捷键时，旧的全局快捷键不能抢按键
            self.hotkeys.clear_all()
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_settings_saved(self, settings: Settings) -> None:
        # 设置窗口开着的时候，投屏窗口可能又移动过，以最新的为准
        settings.window = self.store.settings.window
        settings.guide_shown = self.store.settings.guide_shown
        changed_default = (
            settings.default_preset != self.store.settings.default_preset)
        self.store.settings = settings
        self.store.save()
        if self._guide is not None:
            self._guide.show_hotkeys(settings)
        if not autostart.apply(settings.autostart):
            self.notify("autostart", "开机自启设置失败",
                        "可能被安全软件拦截，请允许后再试。")
        if changed_default:
            self.controller.set_preset(settings.default_preset)
        self.controller.settings_applied()

    def _on_settings_closed(self, _result: int) -> None:
        dialog, self._settings_dialog = self._settings_dialog, None
        if dialog is not None:
            dialog.deleteLater()
        self._apply_hotkeys()

    def _apply_hotkeys(self) -> None:
        self.hotkeys.clear_all()        # 先全部释放，互换两个快捷键时才不会撞上
        failed = []
        for ident, (name, callback) in enumerate(self._hotkey_actions.items(), 1):
            text = getattr(self.store.settings, name)
            if not self.hotkeys.set(ident, text, callback):
                failed.append(f"{HOTKEY_LABELS[name]}（{text}）")
        if failed:
            self.notify("hotkey", "快捷键不可用",
                        f"{'、'.join(failed)} 可能被其他程序占用，请到设置里换一个。")

    # ---- 免 USB 调试 ----
    def open_miracast(self) -> None:
        """打开系统的“投影到此电脑”页。画面由 Windows 接收，和 adb、scrcpy 无关。"""
        try:
            os.startfile("ms-settings:project")
        except OSError as exc:
            log.error("打不开系统设置：%s", exc)
            self.notify("miracast", "打不开“投影到此电脑”",
                        "请手动打开 Windows 设置 → 系统 → 投影到此电脑。")
            return
        self.notify("miracast", "免 USB 调试投屏",
                    "首次使用先安装“无线显示器”可选功能，再在平板上打开投屏并选择这台"
                    "电脑。点这里看说明。")

    # ---- 注入给控制器的依赖 ----
    def _new_session(self, args: list[str]) -> ScrcpySession:
        return ScrcpySession(paths.scrcpy_exe(), args, paths.adb_exe(), self)

    def _resolve_identity(self, serial: str, done) -> None:
        proc = QProcess(self)

        def finished(*_args) -> None:
            lines = bytes(proc.readAllStandardOutput()).decode(
                "utf-8", "replace").split()
            done(lines[0] if len(lines) == 1 and proc.exitCode() == 0 else "")
            proc.deleteLater()

        proc.finished.connect(finished)
        QTimer.singleShot(IDENTITY_TIMEOUT_MS, proc, proc.kill)
        proc.start(str(paths.adb_exe()),
                   ["-s", serial, "shell", "getprop", "ro.serialno"])

    def _remembered_window(self):
        rect = self.store.settings.window
        return rect if rect is not None and winmem.is_on_screen(rect) else None

    def _remember_window(self, rect) -> None:
        self.store.settings.window = rect

    # ---- 退出 ----
    def _on_quit(self) -> None:
        self.hotkeys.clear_all()
        self.controller.shutdown()
        self.tracker.stop()
        self.tracker.kill_server_if_ours()
        self.store.save()
        self.tray.hide()


def main() -> int:
    qapp = QApplication(sys.argv)
    qapp.setApplicationName(APP_ID)
    qapp.setApplicationDisplayName(APP_NAME)
    qapp.setApplicationVersion(__version__)
    qapp.setWindowIcon(app_icon())
    qapp.setQuitOnLastWindowClosed(False)

    instance = SingleInstance(qapp)
    if not instance.acquire():
        return 0
    setup_logging()
    log.info("启动 %s %s", APP_ID, __version__)
    tray = TrayApp(qapp, instance)
    code = qapp.exec()
    del tray
    return code
