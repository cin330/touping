# 安卓投屏工具（TouPing）

平板插上电脑就自动投屏的 Windows 托盘程序。投屏内核是官方 [scrcpy](https://github.com/Genymobile/scrcpy) 5.0.1，
外壳只负责检测设备、拼参数、管进程，不改 scrcpy 代码。

快捷键：`Alt+1` 展示投屏，`Alt+2` 最小化，`Alt+3` 关闭，都可以在设置里改。
环境配置和完整用法见 [docs/使用教程.md](docs/使用教程.md)。

## 从源码运行

```powershell
uv sync --python 3.13                                               # 装依赖
powershell -ExecutionPolicy Bypass -File scripts\fetch_scrcpy.ps1   # 下载 scrcpy（锁定版本，校验 SHA-256）
.venv\Scripts\python.exe -m touping                                 # 启动
```

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物在 `dist\TouPing`，整个文件夹拷到别的电脑，双击 `TouPing.exe` 即可，不需要 Python 和 adb。

## 测试

```powershell
.venv\Scripts\python.exe -m pytest
```

测试会短暂弹出一个小窗口、注册一个热键，并在注册表里建一个临时键（测完即删），不碰真正的开机自启项。

## 代码结构

| 文件 | 作用 |
| --- | --- |
| `touping/app.py` | 托盘、菜单、提示，把各模块接起来 |
| `touping/controller.py` | 状态机：何时开始、停止、重试、切无线 |
| `touping/tracker.py`、`adbproto.py` | 监听 adb server 推送的设备变化，不轮询 |
| `touping/session.py` | 一个 scrcpy 进程的启动、停止和退出码 |
| `touping/presets.py` | 两个预设，以及到 scrcpy 参数的翻译 |
| `touping/winmem.py` | 读取和校验投屏窗口位置，调出和最小化窗口 |
| `touping/config.py`、`settings_dialog.py` | 设置的存取和界面 |
| `touping/hotkeys.py`、`autostart.py`、`guide.py`、`icons.py` | 快捷键、自启、说明页、图标 |

配置和日志在 `%APPDATA%\TouPing`。日志会记录“从检测到设备到出现窗口”的秒数，方便验收。

## 与大纲的差异

- 托盘多一种“空心绿”：设备已连接、没在投屏。大纲的三种颜色没覆盖这个状态。
- 切到无线时，码率取“预设值和 4M 中较小的一个”。
- 设备编不了 H.265 时，自动改用 H.264 重试。
- 多三个全局快捷键：`Alt+1` 展示、`Alt+2` 最小化、`Alt+3` 关闭投屏窗口。
- 菜单多一项“免 USB 调试投屏”，见 [docs/免USB调试可行性.md](docs/免USB调试可行性.md)。
