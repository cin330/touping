"""托盘图标：一块平板，颜色表示状态。用代码画，不依赖图片文件。"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

from .controller import Status

GRAY = "#8a8f98"
YELLOW = "#f2b01e"
GREEN = "#22b455"

# (颜色, 是否实心)。空心绿 = 设备已就绪但没在投屏，是对计划里三种颜色的补充
_STYLE = {
    Status.NO_DEVICE: (GRAY, True),
    Status.PENDING: (YELLOW, True),
    Status.CONNECTING: (YELLOW, True),
    Status.READY: (GREEN, False),
    Status.CASTING: (GREEN, True),
}


def _draw(size: int, color: str, filled: bool) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    unit = size / 16
    body = QRectF(1.5 * unit, 2.5 * unit, 13 * unit, 11 * unit)
    painter.setPen(QPen(QColor(color), 1.6 * unit))
    painter.setBrush(QColor(color) if filled else Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(body, 2 * unit, 2 * unit)
    # 中间的播放三角
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("white" if filled else color))
    path_points = [(6.4, 5.2), (6.4, 10.8), (11, 8)]
    painter.drawPolygon(QPolygonF([QPointF(x * unit, y * unit)
                                   for x, y in path_points]))
    painter.end()
    return pixmap


def status_icon(status: Status) -> QIcon:
    color, filled = _STYLE[status]
    icon = QIcon()
    for size in (16, 20, 24, 32, 48):
        icon.addPixmap(_draw(size, color, filled))
    return icon


def app_icon() -> QIcon:
    return status_icon(Status.CASTING)


if __name__ == "__main__":      # 打包脚本用：生成 exe 图标
    from PySide6.QtGui import QGuiApplication
    _app = QGuiApplication(sys.argv)
    if not _draw(256, GREEN, True).save(sys.argv[1], "ICO"):
        sys.exit("写 ico 失败")
