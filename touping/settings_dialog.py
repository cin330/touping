"""设置窗口：只有一页。"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from .config import DEFAULT_HOTKEYS, HOTKEY_LABELS, Settings
from .hotkeys import parse
from .presets import CODECS, DEFAULT_PRESETS, PRESET_LABELS, PRESET_ORDER, Preset

SIZES = ((0, "原生（不限）"), (2560, "2560"), (1920, "1920"),
         (1600, "1600"), (1280, "1280（最流畅）"), (1024, "1024"))
CODEC_LABELS = {"h264": "H.264（兼容最好）", "h265": "H.265（同码率更清晰）",
                "av1": "AV1（仅新设备）"}


class HotkeyEdit(QKeySequenceEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumSequenceLength(1)
        self.setClearButtonEnabled(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # 按住 Shift 时 Qt 给的是“!”而不是“1”，记成 Alt+Shift+! 就注册不了
        key = event.nativeVirtualKey()
        if 0x30 <= key <= 0x39 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            event = QKeyEvent(event.type(), key, event.modifiers(), chr(key))
        super().keyPressEvent(event)


class SettingsDialog(QDialog):
    saved = Signal(object)      # Settings

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(400)
        self._settings = settings
        self._presets = dict(settings.presets)
        self._editing = settings.default_preset

        self.default_preset = QComboBox()
        self.edit_preset = QComboBox()
        for name in PRESET_ORDER:
            self.default_preset.addItem(PRESET_LABELS[name], name)
            self.edit_preset.addItem(PRESET_LABELS[name], name)

        self.max_size = QComboBox()
        for value, label in SIZES:
            self.max_size.addItem(label, value)
        self.bit_rate = QSpinBox(minimum=1, maximum=200, suffix=" Mbps")
        self.max_fps = QSpinBox(minimum=1, maximum=240, suffix=" 帧/秒")
        self.codec = QComboBox()
        for name in CODECS:
            self.codec.addItem(CODEC_LABELS[name], name)
        self.on_top = QCheckBox("窗口置顶")
        self.no_audio = QCheckBox("关闭音频（声音留在平板上播放）")
        reset = QPushButton("恢复这个预设的默认值")
        reset.setAutoDefault(False)
        reset.clicked.connect(self._reset_preset)

        self.autostart = QCheckBox("开机自动启动")
        self.hotkeys = {name: HotkeyEdit() for name in HOTKEY_LABELS}
        reset_hotkeys = QPushButton("恢复默认快捷键")
        reset_hotkeys.setAutoDefault(False)
        reset_hotkeys.clicked.connect(lambda: self._show_hotkeys(DEFAULT_HOTKEYS))
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #d13438")
        self.error.hide()

        general = QFormLayout()
        general.addRow("默认预设", self.default_preset)
        general.addRow("", self.autostart)

        hotkey_box = QGroupBox("全局快捷键（点一下输入框，再按新的组合键；点 × 表示不用）")
        hotkey_form = QFormLayout(hotkey_box)
        for name, edit in self.hotkeys.items():
            hotkey_form.addRow(HOTKEY_LABELS[name], edit)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(reset_hotkeys)
        hotkey_form.addRow(row)

        box = QGroupBox("预设参数")
        form = QFormLayout(box)
        form.addRow("要修改的预设", self.edit_preset)
        form.addRow("分辨率上限", self.max_size)
        form.addRow("码率", self.bit_rate)
        form.addRow("帧率上限", self.max_fps)
        form.addRow("视频编码", self.codec)
        form.addRow("", self.on_top)
        form.addRow("", self.no_audio)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(reset)
        form.addRow(row)

        note = QLabel("无线投屏时码率自动限制在 4 Mbps 以内。")
        note.setEnabled(False)
        buttons = QDialogButtonBox()
        buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(general)
        layout.addWidget(hotkey_box)
        layout.addWidget(box)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

        self._select(self.default_preset, settings.default_preset)
        self._select(self.edit_preset, self._editing)
        self.autostart.setChecked(settings.autostart)
        self._show_hotkeys({name: getattr(settings, name) for name in self.hotkeys})
        self._show_preset(self._presets[self._editing])
        self.edit_preset.currentIndexChanged.connect(self._switch_preset)

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        combo.setCurrentIndex(max(0, combo.findData(value)))

    def _show_preset(self, preset: Preset) -> None:
        if self.max_size.findData(preset.max_size) < 0:
            self.max_size.addItem(str(preset.max_size), preset.max_size)
        self._select(self.max_size, preset.max_size)
        self.bit_rate.setValue(preset.bit_rate_mbps)
        self.max_fps.setValue(preset.max_fps)
        self._select(self.codec, preset.video_codec)
        self.on_top.setChecked(preset.always_on_top)
        self.no_audio.setChecked(preset.no_audio)

    def _read_preset(self) -> Preset:
        return Preset(
            max_size=self.max_size.currentData(),
            max_fps=self.max_fps.value(),
            bit_rate_mbps=self.bit_rate.value(),
            video_codec=self.codec.currentData(),
            no_audio=self.no_audio.isChecked(),
            always_on_top=self.on_top.isChecked(),
        )

    def _switch_preset(self) -> None:
        self._presets[self._editing] = self._read_preset()
        self._editing = self.edit_preset.currentData()
        self._show_preset(self._presets[self._editing])

    def _reset_preset(self) -> None:
        self._show_preset(DEFAULT_PRESETS[self._editing])

    def _show_hotkeys(self, texts: dict[str, str]) -> None:
        for name, edit in self.hotkeys.items():
            edit.setKeySequence(QKeySequence(texts[name]))

    def _read_hotkeys(self) -> dict[str, str]:
        portable = QKeySequence.SequenceFormat.PortableText
        return {name: edit.keySequence().toString(portable)
                for name, edit in self.hotkeys.items()}

    @staticmethod
    def _hotkey_error(texts: dict[str, str]) -> str:
        used: dict[tuple[int, int], str] = {}
        for name, text in texts.items():
            if not text:
                continue
            label, combo = HOTKEY_LABELS[name], parse(text)
            if combo is None:
                return (f"“{label}”的快捷键 {text} 不能用：要带 Ctrl、Alt 或 Win，"
                        f"再加一个字母、数字或功能键，例如 {DEFAULT_HOTKEYS[name]}。")
            if combo in used:
                return f"“{used[combo]}”和“{label}”的快捷键相同，请改掉一个。"
            used[combo] = label
        return ""

    def _save(self) -> None:
        hotkeys = self._read_hotkeys()
        error = self._hotkey_error(hotkeys)
        self.error.setText(error)
        self.error.setVisible(bool(error))
        if error:
            return
        self._presets[self._editing] = self._read_preset()
        self.saved.emit(replace(
            self._settings,
            default_preset=self.default_preset.currentData(),
            presets=dict(self._presets),
            autostart=self.autostart.isChecked(),
            **hotkeys,
        ))
        self.accept()
