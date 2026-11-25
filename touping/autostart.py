"""开机自启：当前用户注册表的 Run 项，不需要管理员权限。"""
from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path

from . import APP_ID
from .paths import app_dir, is_frozen

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command() -> str:
    if is_frozen():
        return f'"{Path(sys.executable).resolve()}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{app_dir() / "run.pyw"}"'


def current(key_path: str = RUN_KEY) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, APP_ID)[0]
    except OSError:
        return None


def apply(enabled: bool, key_path: str = RUN_KEY) -> bool:
    """按设置写入或删除。程序换了位置后再次调用会自动改成新路径。"""
    try:
        if enabled:
            if current(key_path) != command():
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, command())
        elif current(key_path) is not None:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_ID)
        return True
    except OSError as exc:
        log.error("修改开机自启失败：%s", exc)
        return False
