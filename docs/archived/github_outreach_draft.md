# GitHub 联系原作者草稿

## 策略建议

1. 先在原仓库发 Issue 打招呼（见下方）
2. 原作者回应有兴趣 → 再发 PR（目标分支建议由原作者创建 `macos` 分支）
3. 原作者无回应或不感兴趣 → 直接将自己的 fork 作为独立项目开源

---

## Issue 草稿（发到原仓库）

**标题：**
```
[macOS] Apple Silicon 适配已完成，是否有意合入？
```

**正文：**

```
你好！

我在 `mac-dev` 分支上完成了 CapsWriter-Offline 的 macOS / Apple Silicon 适配，
目前在 MacBook Air M2 上稳定运行，想来问问你是否有意将这部分合入上游。

### 主要新增内容

- **新 ASR 后端**：`qwen_asr_mlx`，基于 Apple MLX 框架运行 Qwen3-ASR 模型，
  完全本地推理，无需 CUDA
- **macOS 输入链路**：
  - `hidutil` 将 Caps Lock 临时映射为 F18，客户端退出后自动恢复
  - Quartz `CGEventTap` 主动拦截 F18 事件（需 Accessibility 权限）
  - 短按 Caps Lock → 切换大小写（IOKit 直接操作）
  - 长按 Caps Lock → 录音，松手返回识别结果，写入剪贴板，`osascript` 自动粘贴
- **守护进程**：`capswriterd`，管理 server / client 子进程生命周期
- **CLI 控制命令**：`capswriter start / stop / restart / status / doctor / help`，
  支持 `launchd` 开机自启
- **不影响 Windows**：所有 macOS 新增代码均在独立模块，Windows 路径无改动

### 仓库位置

https://github.com/EdgarZhong/CapsWriter-Offline/tree/mac-dev

### 问题

如果你有兴趣合入，建议在上游建一个 `macos` 分支，我可以向那个分支发 PR；
如果目前没有精力维护多平台，完全理解，我会在自己的 fork 上继续开源维护。

感谢你做了这么好用的项目！
```

---

## PR 草稿（如果原作者同意）

**PR 标题：**
```
feat: add macOS / Apple Silicon support (qwen_asr_mlx backend + capswriter CLI)
```

**PR 正文：**

```
## 概述

为 CapsWriter-Offline 新增 macOS / Apple Silicon 平台支持。
所有新增代码在独立模块中，不影响现有 Windows 路径。

## 新增内容

### ASR 后端
- `core/server/engines/qwen_asr_mlx/`：基于 Apple MLX 的 Qwen3-ASR 推理后端
- 支持 Qwen3-ASR-1.7B（8bit / 4bit），无需 CUDA

### macOS 输入链路
- `core/client/shortcut/macos_caps_remap.py`：hidutil Caps Lock → F18 生命周期管理
- `core/client/shortcut/macos_f18_listener.py`：CGEventTap 主动事件拦截
- `core/client/shortcut/macos_caps_controller.py`：短按 / 长按分发
- `core/client/shortcut/macos_caps_state.py`：IOKit 大小写状态切换

### 守护进程与 CLI
- `capswriterd.py`：PID 锁文件单例守护进程，管理 server / client 子进程
- `capswriter`（由 `install.sh` 注册到 `~/.local/bin`）：
  `install / uninstall / start / stop / restart / status / doctor / help / remap`

## 测试环境

- MacBook Air M2，macOS 15.x
- Python 3.13（mise 管理）
- 模型：Qwen3-ASR-1.7B-8bit（mlx-community）

## 注意事项

- 需要授予辅助功能（Accessibility）权限用于 CGEventTap 和自动粘贴
- 无 Accessibility 权限时自动降级为直接监听 Caps Lock（功能等价，无波浪线副作用）
- launchd 开机自启需执行 `capswriter install`
```

---

## 如果选择独立开源

直接把 fork 改个名字（如 `CapsWriter-macOS`）、更新 README、发 Release 即可。
这条路最简单，完全自主，不依赖原作者响应。
```
