"""场景预设，以及把预设翻译成 scrcpy 命令行参数。

参数名已对照 scrcpy v5.0.1 的手册（app/scrcpy.1）核对。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

LOW_LATENCY = "low_latency"
HIGH_QUALITY = "high_quality"
PRESET_ORDER = (LOW_LATENCY, HIGH_QUALITY)
PRESET_LABELS = {LOW_LATENCY: "低延迟", HIGH_QUALITY: "高画质"}

CODECS = ("h264", "h265", "av1")
WIRELESS_MAX_MBPS = 4


@dataclass(frozen=True)
class Preset:
    max_size: int          # 0 = 原生分辨率
    max_fps: int
    bit_rate_mbps: int
    video_codec: str
    no_audio: bool
    always_on_top: bool


DEFAULT_PRESETS = {
    LOW_LATENCY: Preset(1920, 60, 8, "h264", no_audio=True, always_on_top=True),
    HIGH_QUALITY: Preset(0, 60, 16, "h265", no_audio=False, always_on_top=False),
}


@dataclass(frozen=True)
class WindowRect:
    """投屏窗口客户区的位置和大小，单位是物理像素。"""
    x: int
    y: int
    width: int
    height: int


def build_args(
    serial: str,
    preset: Preset,
    *,
    wireless: bool = False,
    enable_tcpip: bool = False,
    window: WindowRect | None = None,
) -> list[str]:
    """拼出 scrcpy 的参数列表。

    wireless:     画面走 Wi-Fi，码率降到 4M
    enable_tcpip: 设备现在插着线，让 scrcpy 自己把它切到无线
    不传 --video-buffer：scrcpy 默认就是 0，外壳不改这个参数。
    """
    if wireless or enable_tcpip:
        preset = replace(
            preset, bit_rate_mbps=min(preset.bit_rate_mbps, WIRELESS_MAX_MBPS)
        )

    args = [f"--serial={serial}"]
    if enable_tcpip:
        args.append("--tcpip")
    if preset.max_size > 0:
        args.append(f"--max-size={preset.max_size}")
    args += [
        f"--max-fps={preset.max_fps}",
        f"--video-bit-rate={preset.bit_rate_mbps}M",
        f"--video-codec={preset.video_codec}",
    ]
    if preset.no_audio:
        args.append("--no-audio")
    args.append("--keyboard=uhid")
    if preset.always_on_top:
        args.append("--always-on-top")
    args.append("--stay-awake")
    if window is not None:
        args += [
            f"--window-x={window.x}",
            f"--window-y={window.y}",
            f"--window-width={window.width}",
            f"--window-height={window.height}",
        ]
    return args
