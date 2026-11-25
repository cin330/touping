import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PySide6.QtCore import QDeadlineTimer, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def wait(qapp):
    """wait(ms) 转一会儿事件循环；wait(ms, 条件) 等到条件成立，超时就断言失败。"""
    def _wait(ms, condition=None):
        deadline = QDeadlineTimer(ms)
        loop = QEventLoop()
        while not deadline.hasExpired():
            if condition is not None and condition():
                return
            QTimer.singleShot(5, loop.quit)
            loop.exec()
        if condition is not None:
            assert condition(), "等待超时"
    return _wait


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOUPING_HOME", str(tmp_path / "home"))
