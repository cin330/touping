"""首次使用说明：一页。"""
from __future__ import annotations

from html import escape

from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

from .config import HOTKEY_LABELS, Settings

GUIDE_HTML = """
<h2>三步开始投屏</h2>
<ol>
<li><b>打开开发者选项</b>：平板的 设置 → 关于平板电脑，连续点 7 次“版本号”
（小米点“MIUI 版本”或“OS 版本”）。</li>
<li><b>打开 USB 调试</b>：设置 → 更多设置或系统 → 开发者选项 → 打开“USB 调试”。<br>
<b>小米、红米</b>还要打开“USB 调试（安全设置）”，否则只能看画面，电脑键鼠不起作用。</li>
<li><b>插上数据线</b>：平板弹出“允许 USB 调试吗？”时勾选“一律允许”，再点允许。
之后每次插线都会自动投屏。</li>
</ol>

<h2>托盘图标的颜色</h2>
<ul>
<li>灰色：没有设备</li>
<li>黄色：设备未授权，或正在连接</li>
<li>空心绿：设备已连接，没在投屏</li>
<li>实心绿：正在投屏</li>
</ul>
<p>左键单击图标开始或停止投屏，右键打开菜单。</p>

<h2>快捷键</h2>
<p>在任何程序里都有效。要修改：右键托盘图标 → 设置。设置窗口开着时快捷键暂停。</p>
<ul>
{hotkeys}
</ul>
<p>没在投屏时，“展示投屏”会直接开始投屏；最小化后再按一次就能还原。</p>

<h2>用电脑键盘打中文</h2>
<p>投屏时电脑键盘相当于插在平板上的实体键盘。第一次用时，在投屏窗口里按
<b>左 Alt + K</b> 打开平板的实体键盘设置，选好键盘布局。之后用平板自己的输入法打字，
切换中英文按平板输入法的快捷键（多为 Ctrl + 空格 或 Shift）。</p>

<h2>遇到问题</h2>
<ul>
<li><b>插上没反应</b>：换一根能传数据的线；平板 USB 用途选“传输文件”；
部分品牌要在电脑上装一次官方 USB 驱动。</li>
<li><b>反复断开</b>：关掉手机助手、刷机工具、安卓模拟器。它们自带的 adb 会抢连接。</li>
<li><b>画面卡</b>：设置里把“分辨率上限”降到 1280。</li>
<li><b>无线连不上</b>：平板重启或换 Wi‑Fi 后无线会失效，重新插一次线即可。</li>
</ul>

<h2>不想开 USB 调试？</h2>
<p>可以用 Windows 自带的“无线显示器”（Miracast）只看画面：右键托盘图标 →
“免 USB 调试投屏”。首次使用要在打开的页面里安装“无线显示器”可选功能。
然后在平板上打开“投屏 / 无线显示 / Smart View”，选择这台电脑。</p>
<p>限制：每次要在平板上手动发起；延迟明显高于 USB；一般不能用电脑键鼠操作平板；
部分平板（如 Pixel）不支持 Miracast。</p>
"""


def guide_html(settings: Settings) -> str:
    rows = "\n".join(
        f"<li>{label}：<b>{escape(getattr(settings, name)) or '未设置'}</b></li>"
        for name, label in HOTKEY_LABELS.items())
    return GUIDE_HTML.replace("{hotkeys}", rows)


class GuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._html = ""
        self.setWindowTitle("使用说明")
        self.resize(560, 640)
        self._view = QTextBrowser()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

    def show_hotkeys(self, settings: Settings) -> None:
        """按当前设置刷新快捷键；内容没变就不动，免得滚动位置跳回开头。"""
        html = guide_html(settings)
        if html != self._html:
            self._html = html
            self._view.setHtml(html)
