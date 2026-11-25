import json

import pytest

from touping import adbproto, config
from touping.adbproto import Device, ProtocolError, TrackDecoder, parse_devices
from touping.config import Settings, SettingsStore
from touping.presets import (
    DEFAULT_PRESETS, HIGH_QUALITY, LOW_LATENCY, WindowRect, build_args,
)


# ---- 参数：逐条对应大纲“默认参数”表 ----
def test_low_latency_args():
    assert build_args("S1", DEFAULT_PRESETS[LOW_LATENCY]) == [
        "--serial=S1", "--max-size=1920", "--max-fps=60", "--video-bit-rate=8M",
        "--video-codec=h264", "--no-audio", "--keyboard=uhid", "--always-on-top",
        "--stay-awake",
    ]


def test_high_quality_args():
    assert build_args("S1", DEFAULT_PRESETS[HIGH_QUALITY]) == [
        "--serial=S1", "--max-fps=60", "--video-bit-rate=16M",
        "--video-codec=h265", "--keyboard=uhid", "--stay-awake",
    ]


@pytest.mark.parametrize("name", [LOW_LATENCY, HIGH_QUALITY])
def test_wireless_lowers_bit_rate(name):
    args = build_args("S1", DEFAULT_PRESETS[name], wireless=True, enable_tcpip=True)
    assert "--video-bit-rate=4M" in args and "--tcpip" in args


def test_never_touches_video_buffer():
    for preset in DEFAULT_PRESETS.values():
        assert not any("buffer" in a for a in build_args("S", preset, wireless=True))


def test_window_restore_args():
    args = build_args("S", DEFAULT_PRESETS[LOW_LATENCY],
                      window=WindowRect(-5, 20, 800, 600))
    assert args[-4:] == ["--window-x=-5", "--window-y=20",
                         "--window-width=800", "--window-height=600"]


# ---- adb 协议 ----
def test_parse_devices():
    text = (
        "ABC123                 device product:p model:Pad_6 device:d transport_id:1\n"
        "DEF456                 unauthorized transport_id:2\n"
        "192.168.1.5:5555       device product:p model:Pad_6 device:d transport_id:3\n"
        "adb-ABC-x (2)._adb-tls-connect._tcp device model:Pad transport_id:4\n"
        "XYZ                    offline transport_id:5\n"
    )
    devices = parse_devices(text)
    assert [(d.serial, d.state, d.is_tcp) for d in devices] == [
        ("ABC123", "device", False),
        ("DEF456", "unauthorized", False),
        ("192.168.1.5:5555", "device", True),
        ("adb-ABC-x (2)._adb-tls-connect._tcp", "device", True),
        ("XYZ", "offline", False),
    ]
    assert devices[0].label == "Pad 6"
    assert devices[1].label == "DEF456"


def test_parse_ignores_garbage():
    assert parse_devices("\n* daemon started\nfoo\n") == []


def test_decoder_byte_by_byte():
    line = b"ABC device model:Pad\n"
    stream = b"OKAY" + b"0000" + f"{len(line):04x}".encode() + line
    decoder = TrackDecoder()
    updates = [u for i in range(len(stream)) for u in decoder.feed(stream[i:i + 1])]
    assert updates == [[], [Device("ABC", "device", "Pad")]]


def test_decoder_rejects_fail():
    with pytest.raises(ProtocolError):
        TrackDecoder().feed(b"FAIL0005oops!")


def test_encode_request():
    assert adbproto.encode_request("host:version") == b"000chost:version"


# ---- 配置 ----
def test_config_roundtrip(tmp_path):
    store = SettingsStore(tmp_path / "a" / "config.json")
    store.settings.window = WindowRect(1, 2, 300, 400)
    store.settings.hotkey_toggle = ""
    store.settings.default_preset = HIGH_QUALITY
    store.save()
    assert SettingsStore(store.path).settings == store.settings


def test_config_from_before_window_hotkeys():
    """旧配置里没有新快捷键：补默认值，用户改过的保持不变。"""
    settings = config.from_dict({"hotkey_toggle": "Ctrl+Alt+N", "hotkey_preset": ""})
    assert (settings.hotkey_show, settings.hotkey_minimize,
            settings.hotkey_close) == ("Alt+1", "Alt+2", "Alt+3")
    assert (settings.hotkey_toggle, settings.hotkey_preset) == ("Ctrl+Alt+N", "")


def test_every_hotkey_has_a_label():
    assert list(config.HOTKEY_LABELS) == list(config.DEFAULT_HOTKEYS)


def test_config_corrupt_file_falls_back(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{oops", encoding="utf-8")
    assert SettingsStore(path).settings == Settings()
    assert (tmp_path / "config.bad.json").exists()


@pytest.mark.parametrize("raw", [
    [], None, "x", {"presets": 3, "window": [], "default_preset": "nope"},
    {"window": {"x": 0, "y": 0, "width": 1, "height": 1}},
    {"window": {"x": True, "y": 0, "width": 500, "height": 500}},
])
def test_config_bad_values(raw):
    assert config.from_dict(raw) == Settings()


def test_config_clamps_numbers():
    raw = {"presets": {LOW_LATENCY: {
        "bit_rate_mbps": 9999, "max_fps": -1, "max_size": True,
        "video_codec": "divx"}}}
    preset = config.from_dict(json.loads(json.dumps(raw))).presets[LOW_LATENCY]
    assert (preset.bit_rate_mbps, preset.max_fps) == (200, 1)
    assert (preset.max_size, preset.video_codec) == (1920, "h264")
