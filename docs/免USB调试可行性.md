# 不开 USB 调试，能不能投屏

**结论：能看到画面，但做不到 v1 的核心体验。** 不开 USB 调试时，“插线自动出画面、低延迟、电脑键鼠可操作”
三件事无法同时满足。v1 仍以 USB 调试为主，另加一个 Windows 无线显示器（Miracast）入口作备用。

## 为什么 scrcpy 必须要 USB 调试

scrcpy 要通过 adb 在平板上启动一个有 shell 权限的进程来抓屏和注入操作。没有 USB 调试就没有这个权限。
唯一的例外是 `scrcpy --otg`：它不用 adb，但只模拟键盘鼠标，**画面和声音都是关的**；
官方 FAQ 还列出它在 Windows 上可能有驱动问题。

## 各种路线对比

| 路线 | 有画面 | 电脑可操作 | 插线即自动 | 延迟 | 代价 |
| --- | --- | --- | --- | --- | --- |
| USB 调试 + scrcpy（v1） | 有 | 可以 | 可以 | 低 | 首次开一次 USB 调试 |
| Windows 无线显示器（Miracast） | 有 | 一般不行 | 不行，每次在平板上发起 | 明显更高 | 装一个系统可选功能，平板要支持 |
| `scrcpy --otg` | **没有** | 可以 | 可以 | 低 | Windows 驱动可能要折腾 |
| 自写安卓 App（MediaProjection） | 有 | 要另开无障碍权限 | 不行，每次要在平板上点同意 | 中 | 新写一个 App，大纲已列为暂不考虑 |
| USB‑C 视频输出 + 采集卡 | 有 | 不行（可配合 OTG） | 可以 | 低 | 买采集卡，平板 USB‑C 要支持视频输出 |

表中延迟一列是按原理判断的相对高低，没有实测。

自写 App 绕不开“每次点同意”：安卓官方文档规定，每个 MediaProjection 会话之前都必须征得用户同意；
安卓 14 起，重复使用同一次授权会直接抛异常。所以它替代不了“插线自动投屏”。

## 已经做了什么

- 托盘菜单的“免 USB 调试投屏（Windows 无线显示器）…”会打开系统的“投影到此电脑”页面。
- 说明页写了步骤和限制。

这台电脑已用 `netsh wlan show drivers` 查过：`Wireless Display Supported: Yes`（显卡、网卡都支持），
但没有查到无线显示器应用，首次使用要先安装“无线显示器”可选功能。

## 使用步骤

1. 右键托盘图标 → 免 USB 调试投屏。在打开的页面里点“可选功能”，安装“无线显示器”。
2. 启动“无线显示器”应用，让它处于等待连接状态。
3. 平板上打开 投屏 / 无线显示 / Smart View，选择这台电脑。

## 建议

- 平板是自己的：开 USB 调试。担心安全的话，只对自己的电脑点“一律允许”，需要时可在开发者选项里
  “撤销 USB 调试授权”。
- 平板不允许开（学校或公司管控）：用 Miracast 看画面。
- 必须有线、低延迟、零设置：先确认平板 USB‑C 支持视频输出，再考虑采集卡。

## 依据

- scrcpy 5.0.1：[doc/otg.md](https://github.com/Genymobile/scrcpy/blob/v5.0.1/doc/otg.md)、[FAQ](https://github.com/Genymobile/scrcpy/blob/v5.0.1/FAQ.md#otg-issues-on-windows)
- 微软：[Screen mirroring and projecting to your PC](https://support.microsoft.com/en-us/windows/project-to-a-screen-c35a75e2-f8e2-ad5a-9d78-9cf3fb739b59)
- 安卓：[Media projection](https://developer.android.com/media/grow/media-projection)
