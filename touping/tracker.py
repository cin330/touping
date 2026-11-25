"""监听设备插拔：连到 adb server，等它推送变化。"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

from .adbproto import TRACK_SERVICE, ProtocolError, TrackDecoder, encode_request

log = logging.getLogger(__name__)

RECONNECT_MIN_MS = 500
RECONNECT_MAX_MS = 5000
START_SERVER_TIMEOUT_MS = 15000
# adb server 重启后要过一会儿才枚举出 USB 设备，这段时间内的空列表不算“拔线”
SETTLE_MS = 2000


def adb_port() -> int:
    try:
        return int(os.environ.get("ANDROID_ADB_SERVER_PORT", "5037"))
    except ValueError:
        return 5037


class DeviceTracker(QObject):
    devicesChanged = Signal(list)   # list[Device]
    serverLost = Signal()           # 已建立的连接断了，常见原因是别的 adb 抢占

    def __init__(self, adb: Path, port: int | None = None, parent=None):
        super().__init__(parent)
        self._adb = adb
        self._port = port if port is not None else adb_port()
        self._running = False
        self._awaiting_settle = False
        self._delay = RECONNECT_MIN_MS
        self._decoder = TrackDecoder()
        self._starter: QProcess | None = None
        self.started_server = False   # 这个 server 是不是我们拉起来的

        self._socket = QTcpSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.readyRead.connect(self._on_ready_read)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.errorOccurred.connect(self._on_error)

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._ensure_server)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(lambda: self.devicesChanged.emit([]))

    def start(self) -> None:
        self._running = True
        self._ensure_server()

    def stop(self) -> None:
        self._running = False
        self._retry.stop()
        self._settle.stop()
        self._socket.abort()
        if self._starter is not None:
            self._starter.kill()
            self._starter.waitForFinished(1000)

    def kill_server_if_ours(self) -> None:
        """退出时关掉自己拉起的 adb server，否则 adb.exe 被占用，文件夹删不掉。"""
        if self.started_server and self._adb.is_file():
            proc = QProcess()
            proc.start(str(self._adb), ["-P", str(self._port), "kill-server"])
            proc.waitForFinished(2000)

    # ---- 内部 ----
    def _ensure_server(self) -> None:
        if not self._running or self._starter is not None:
            return
        if not self._adb.is_file():
            self._connect()
            return
        # 先跑自带 adb 的 start-server：server 版本不一致时由它负责替换
        proc = QProcess(self)
        self._starter = proc
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(self._on_start_server_done)
        proc.errorOccurred.connect(self._on_start_server_error)
        QTimer.singleShot(START_SERVER_TIMEOUT_MS, proc, proc.kill)
        proc.start(str(self._adb), ["-P", str(self._port), "start-server"])

    def _on_start_server_done(self, *_args) -> None:
        proc, self._starter = self._starter, None
        if proc is None:
            return
        output = bytes(proc.readAll()).decode("utf-8", "replace")
        if "started successfully" in output:
            self.started_server = True
        if output.strip():
            log.info("adb start-server: %s", output.strip().replace("\r\n", " | "))
        proc.deleteLater()
        self._connect()

    def _on_start_server_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            log.error("adb 启动失败：%s", self._adb)
            self._on_start_server_done()

    def _connect(self) -> None:
        if self._running:
            self._socket.abort()
            self._decoder = TrackDecoder()
            self._socket.connectToHost("127.0.0.1", self._port)

    def _on_connected(self) -> None:
        self._socket.setSocketOption(
            QAbstractSocket.SocketOption.KeepAliveOption, 1
        )
        self._socket.write(encode_request(TRACK_SERVICE))

    def _on_ready_read(self) -> None:
        try:
            updates = self._decoder.feed(bytes(self._socket.readAll()))
        except ProtocolError as exc:
            log.error("%s", exc)
            self._socket.abort()
            self._schedule_retry()
            return
        for devices in updates:
            self._delay = RECONNECT_MIN_MS
            log.info("设备列表：%s", [(d.serial, d.state) for d in devices])
            if not devices and self._awaiting_settle:
                self._settle.start(SETTLE_MS)
            else:
                self._settle.stop()
                self.devicesChanged.emit(devices)
            self._awaiting_settle = False

    def _on_disconnected(self) -> None:
        if not self._running:
            return
        log.warning("与 adb server 的连接断开")
        self._awaiting_settle = True
        self.serverLost.emit()
        self._schedule_retry()

    def _on_error(self, error) -> None:
        # 已连接后断开会走 disconnected，这里只处理压根没连上的情况
        if (
            self._running
            and error != QAbstractSocket.SocketError.RemoteHostClosedError
            and self._socket.state() != QAbstractSocket.SocketState.ConnectedState
        ):
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        if not self._retry.isActive():
            self._retry.start(self._delay)
            self._delay = min(self._delay * 2, RECONNECT_MAX_MS)
