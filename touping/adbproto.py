"""adb server 的 track-devices 协议：纯解析，不碰网络，方便测试。

连上 adb server 后发 "host:track-devices-l"，server 先回 OKAY，之后设备列表
每变一次就推送一段：4 位十六进制长度 + 文本。全程不用轮询，空闲时不占 CPU。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TRACK_SERVICE = "host:track-devices-l"

_LINE = re.compile(
    r"^(?P<serial>\S.*?)\s+"
    r"(?P<state>device|offline|unauthorized|authorizing|connecting|bootloader|"
    r"recovery|sideload|rescue|host|detached|unknown|no permissions[^:]*?)"
    r"(?=\s|$)(?P<rest>.*)$"
)
_TCP_SERIAL = re.compile(r"^(\[[0-9a-fA-F:]+\]|[^:\s]+):\d+$")


class ProtocolError(Exception):
    pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""

    @property
    def online(self) -> bool:
        return self.state == "device"

    @property
    def is_tcp(self) -> bool:
        return bool(_TCP_SERIAL.match(self.serial)) or "._adb" in self.serial

    @property
    def label(self) -> str:
        return self.model.replace("_", " ") if self.model else self.serial


def encode_request(service: str) -> bytes:
    data = service.encode("utf-8")
    return f"{len(data):04x}".encode("ascii") + data


def parse_devices(text: str) -> list[Device]:
    devices = []
    for line in text.splitlines():
        match = _LINE.match(line.strip())
        if not match:
            continue
        fields = dict(
            item.split(":", 1) for item in match["rest"].split() if ":" in item
        )
        devices.append(
            Device(match["serial"].strip(), match["state"], fields.get("model", ""))
        )
    return devices


class TrackDecoder:
    """增量解码：数据可能一个字节一个字节地来。"""

    def __init__(self) -> None:
        self._buf = b""
        self._handshaken = False

    def feed(self, data: bytes) -> list[list[Device]]:
        self._buf += data
        updates: list[list[Device]] = []
        if not self._handshaken:
            if len(self._buf) < 4:
                return updates
            status, self._buf = self._buf[:4], self._buf[4:]
            if status != b"OKAY":
                raise ProtocolError(f"adb server 拒绝请求：{status + self._buf!r}")
            self._handshaken = True
        while len(self._buf) >= 4:
            try:
                size = int(self._buf[:4], 16)
            except ValueError as exc:
                raise ProtocolError(f"长度字段无效：{self._buf[:4]!r}") from exc
            if len(self._buf) < 4 + size:
                break
            payload = self._buf[4:4 + size]
            self._buf = self._buf[4 + size:]
            updates.append(parse_devices(payload.decode("utf-8", "replace")))
        return updates
