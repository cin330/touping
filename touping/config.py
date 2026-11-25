"""用户设置的读写。文件损坏或字段越界时退回默认值，程序照常启动。"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .presets import (
    CODECS, DEFAULT_PRESETS, LOW_LATENCY, PRESET_ORDER, Preset, WindowRect,
)

log = logging.getLogger(__name__)

# 键名就是 Settings 里的字段名，设置窗口和说明页都按这个顺序列出来
DEFAULT_HOTKEYS = {
    "hotkey_show": "Alt+1",
    "hotkey_minimize": "Alt+2",
    "hotkey_close": "Alt+3",
    "hotkey_toggle": "Ctrl+Alt+M",
    "hotkey_preset": "Ctrl+Alt+P",
}
HOTKEY_LABELS = {
    "hotkey_show": "展示投屏",
    "hotkey_minimize": "最小化投屏",
    "hotkey_close": "关闭投屏",
    "hotkey_toggle": "开始/停止投屏",
    "hotkey_preset": "切换预设",
}


@dataclass
class Settings:
    default_preset: str = LOW_LATENCY
    presets: dict[str, Preset] = field(default_factory=lambda: dict(DEFAULT_PRESETS))
    autostart: bool = False
    hotkey_show: str = DEFAULT_HOTKEYS["hotkey_show"]
    hotkey_minimize: str = DEFAULT_HOTKEYS["hotkey_minimize"]
    hotkey_close: str = DEFAULT_HOTKEYS["hotkey_close"]
    hotkey_toggle: str = DEFAULT_HOTKEYS["hotkey_toggle"]
    hotkey_preset: str = DEFAULT_HOTKEYS["hotkey_preset"]
    window: WindowRect | None = None
    guide_shown: bool = False


def _int(value, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, int(value)))


def _bool(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _str(value, default: str) -> str:
    return value if isinstance(value, str) else default


def _preset(raw, default: Preset) -> Preset:
    if not isinstance(raw, dict):
        return default
    codec = raw.get("video_codec")
    return Preset(
        max_size=_int(raw.get("max_size"), default.max_size, 0, 8192),
        max_fps=_int(raw.get("max_fps"), default.max_fps, 1, 240),
        bit_rate_mbps=_int(raw.get("bit_rate_mbps"), default.bit_rate_mbps, 1, 200),
        video_codec=codec if codec in CODECS else default.video_codec,
        no_audio=_bool(raw.get("no_audio"), default.no_audio),
        always_on_top=_bool(raw.get("always_on_top"), default.always_on_top),
    )


def _window(raw) -> WindowRect | None:
    if not isinstance(raw, dict):
        return None
    values = [raw.get(k) for k in ("x", "y", "width", "height")]
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        return None
    rect = WindowRect(*values)
    return rect if rect.width >= 100 and rect.height >= 100 else None


def from_dict(raw) -> Settings:
    if not isinstance(raw, dict):
        return Settings()
    presets = raw.get("presets") if isinstance(raw.get("presets"), dict) else {}
    chosen = raw.get("default_preset")
    return Settings(
        default_preset=chosen if chosen in PRESET_ORDER else LOW_LATENCY,
        presets={
            name: _preset(presets.get(name), DEFAULT_PRESETS[name])
            for name in PRESET_ORDER
        },
        autostart=_bool(raw.get("autostart"), False),
        **{name: _str(raw.get(name), default)
           for name, default in DEFAULT_HOTKEYS.items()},
        window=_window(raw.get("window")),
        guide_shown=_bool(raw.get("guide_shown"), False),
    )


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.settings = self._load()

    def _load(self) -> Settings:
        try:
            return from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return Settings()
        except (OSError, ValueError) as exc:
            log.warning("配置读取失败，改用默认值：%s", exc)
            try:
                os.replace(self.path, self.path.with_suffix(".bad.json"))
            except OSError:
                pass
            return Settings()

    def save(self) -> None:
        """先写临时文件再替换，避免写到一半断电留下坏文件。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(asdict(self.settings), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("配置保存失败：%s", exc)
