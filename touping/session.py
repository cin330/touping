"""一次投屏：包住一个 scrcpy 进程。"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from . import winmem

log = logging.getLogger(__name__)

WINDOW_POLL_MS = 200      # 等窗口出现
RECT_POLL_MS = 2000       # 窗口出现后记位置
KILL_AFTER_MS = 2000

# scrcpy 的退出码（见 scrcpy.1 的 EXIT STATUS）
EXIT_OK = 0
EXIT_CANNOT_CONNECT = 1
EXIT_DISCONNECTED = 2


class ScrcpySession(QObject):
    windowShown = Signal()
    windowMoved = Signal(object)    # WindowRect
    ended = Signal(int, str)        # 退出码, 最近的输出

    def __init__(self, program: Path, args: list[str], adb: Path | None = None,
                 parent=None):
        super().__init__(parent)
        self._tail: deque[str] = deque(maxlen=30)
        self._hwnd: int | None = None
        self._rect = None
        self._done = False
        self._stopping = False

        self._proc = QProcess(self)
        self._proc.setProgram(str(program))
        self._proc.setArguments(args)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        if adb is not None:
            env.insert("ADB", str(adb))     # 强制用自带的 adb
        self._proc.setProcessEnvironment(env)
        self._proc.readyRead.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

        self._poll = QTimer(self)
        self._poll.setInterval(WINDOW_POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    def start(self) -> None:
        log.info("启动 scrcpy：%s", " ".join(self._proc.arguments()))
        self._proc.start()
        self._poll.start()

    def stop(self) -> None:
        """先礼后兵：WM_CLOSE 让 scrcpy 正常收尾，超时才强杀。"""
        if self._stopping or self._done:
            return
        self._stopping = True
        self._proc.terminate()
        QTimer.singleShot(KILL_AFTER_MS, self, self._kill)

    def stop_blocking(self) -> None:
        if self._done:
            return
        self._stopping = True
        self._proc.terminate()
        if not self._proc.waitForFinished(KILL_AFTER_MS):
            self._proc.kill()
            self._proc.waitForFinished(1000)

    def show_window(self) -> None:
        if self._hwnd is not None and not self._done:
            winmem.bring_to_front(self._hwnd)

    def minimize_window(self) -> None:
        if self._hwnd is not None and not self._done:
            winmem.minimize(self._hwnd)

    # ---- 内部 ----
    def _kill(self) -> None:
        if not self._done and self._proc.state() != QProcess.ProcessState.NotRunning:
            log.warning("scrcpy 没有及时退出，强制结束")
            self._proc.kill()

    def _on_output(self) -> None:
        text = bytes(self._proc.readAll()).decode("utf-8", "replace")
        for line in text.splitlines():
            if line.strip():
                self._tail.append(line.strip())
                log.info("scrcpy> %s", line.strip())

    def _on_poll(self) -> None:
        if self._hwnd is None:
            self._hwnd = winmem.find_window(int(self._proc.processId()))
            if self._hwnd is None:
                return
            self._poll.setInterval(RECT_POLL_MS)
            self.windowShown.emit()
        rect = winmem.window_rect(self._hwnd)
        if rect is not None and rect != self._rect:
            self._rect = rect
            self.windowMoved.emit(rect)

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._finish(EXIT_CANNOT_CONNECT, f"无法启动 {self._proc.program()}")

    def _on_finished(self, code: int, status) -> None:
        self._on_output()
        if status == QProcess.ExitStatus.CrashExit and not self._stopping:
            code = code or EXIT_CANNOT_CONNECT
        self._finish(code, "\n".join(self._tail))

    def _finish(self, code: int, output: str) -> None:
        if self._done:
            return
        self._done = True
        self._poll.stop()
        log.info("scrcpy 退出，代码 %s", code)
        self.ended.emit(code, output)
