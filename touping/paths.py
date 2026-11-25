"""程序用到的所有路径。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_ID


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """打包后是 exe 所在目录；开发时是项目根目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """配置和日志目录。TOUPING_HOME 供测试时隔离用。"""
    override = os.environ.get("TOUPING_HOME")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_ID


def scrcpy_dir() -> Path:
    """只用程序自带的 scrcpy 和 adb，不去 PATH 里找，避免 adb 版本冲突。"""
    if is_frozen():
        return app_dir() / "scrcpy"
    return app_dir() / "vendor" / "scrcpy"


def scrcpy_exe() -> Path:
    return scrcpy_dir() / "scrcpy.exe"


def adb_exe() -> Path:
    return scrcpy_dir() / "adb.exe"


def missing_binaries() -> list[str]:
    need = [scrcpy_exe(), adb_exe(), scrcpy_dir() / "scrcpy-server"]
    return [p.name for p in need if not p.is_file()]
