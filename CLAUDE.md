# CapsWriter-Offline 当前阶段同步

## 当前目标

- 分支：`mac-dev`，基线：`master`
- 目标：为 macOS / Apple Silicon 新增 `qwen_asr_mlx` 后端，在不大改现有 Client / Server 架构的前提下，实现稳定的 Caps Lock 长按录音、最终结果返回、剪贴板写入和可选自动上屏体验。
- **当前阶段：P0/P1 全部完成。正在推进 P2 — macOS `.app` bundle 封装与麦克风指示器。**

---

## 当前总体架构决策

### 最终运行架构

```text
launchd
  └─ capswriterd
       ├─ server process
       │    └─ start_server.py / qwen_asr_mlx
       └─ CapsWriter.app（client 进程，具有 macOS GUI 应用身份）
            └─ NSApplication RunLoop（主线程）
                 └─ asyncio 事件循环（子线程）
                      └─ core.client.main → CapsWriterClient
                           ├─ MacOSCapsRemapSession（client 独占 Caps remap 生命周期）
                           └─ 录音 / WebSocket / 结果处理
```

### 用户交互入口

用户只通过一个命令控制整体软件：

```bash
capswriter
```

完整命令集：

```bash
capswriter install      # 注册 launchd 服务（开机自启）
capswriter uninstall    # 注销 launchd 服务
capswriter start        # 启动后台服务
capswriter stop         # 停止后台服务
capswriter restart      # 重启后台服务（改配置后使用）
capswriter status       # 查看运行状态
capswriter doctor       # 环境与权限检查
capswriter help         # 详细帮助

capswriter remap status           # 查看 Caps Lock remap 状态
capswriter remap restore          # 恢复原始键盘映射（仅限 client 未运行时）
capswriter remap clear --force    # 清空所有 UserKeyMapping（救援命令）
```

全局命令通过项目根目录 `install.sh` 注册到 `~/.local/bin/capswriter`。

### Ownership 规则

```text
launchd 只负责拉起 capswriterd。
capswriterd 只负责管理 server/client 生命周期。
client 自己负责 Caps remap 生命周期。
server 只负责 ASR。
```

### 明确不采用

- 不把 `MacOSCapsSupervisor` 作为正式架构路径（已从 start_client.py 移除）。
- 不让 capswriterd 直接接管 Caps remap。
- 不注册两个 launchd plist 分别启动 server/client。
- 不提供单独的日志查看命令。
- 不提供 remap repair 命令。

---

## 当前实际路径

```text
capswriter CLI
  └─ capswriterd（PID 锁文件单例）
       ├─ start_server.py → qwen_asr_mlx（等待端口 6016 就绪）
       └─ CapsWriter.app（通过 open 命令启动）
            └─ NSApplication（主线程 RunLoop，提供 macOS GUI 应用身份）
                 └─ asyncio 子线程 → CapsWriterClient
                      ├─ MacOSCapsRemapSession（client 启动前保存快照，启用 Caps→F18）
                      └─ MacOSCapsF18Bridge
                           └─ MacOSF18Listener（Quartz CGEventTap 主动拦截，F18 不透传终端）
                                └─ MacOSCapsController（短按/长按分发）
                                     ├─ 短按 → IOHIDSetModifierLockState 切换大小写
                                     └─ 长按 → ShortcutManager → AudioStreamManager
                                                   └─ AudioRecorder / WebSocketManager
                                                        └─ ResultProcessor
                                                             └─ 写剪贴板 → 尝试 osascript 粘贴一次
```

---

## 当前阶段决策

| 决策 | 内容 |
|------|------|
| macOS 后端 | `qwen_asr_mlx`，不替换 Windows 的 `qwen_asr_gguf` |
| 首版结果模式 | 松开后快速返回最终结果，不做中间流式显示 |
| 上屏策略 | 必先写剪贴板，只尝试自动粘贴一次，失败只记 warning，不重试 |
| 权限口径 | Accessibility 权限仅用于自动粘贴；CGEventTap 也需要 Accessibility |
| macOS GUI | client 包装为 `.app` bundle（Agent App，LSUIElement=true，无 Dock 图标）；现有 Windows 专用 tray/toast/Tkinter 弹窗仍禁用 |
| 模型优先级 | 默认 `Qwen3-ASR-1.7B-8bit`，本地回退 `1.7B-4bit` |
| remap ownership | client 是 Caps remap 的唯一生命周期 owner |
| remap 持久化 | client 启动前保存 original UserKeyMapping 快照，退出或手动 restore 时恢复 |
| client 运行期 remap 规则 | client 运行时独占接管 Caps Lock -> F18；需修改键盘映射先 stop |
| client 进程形态 | 包装为 `CapsWriter.app`（Agent App，LSUIElement=true），主线程 NSApplication RunLoop，asyncio 在子线程 |
| client 启动方式 | capswriterd 通过 `open CapsWriter.app` 启动 client，替代直接 `python start_client.py` |
| 总生命周期 | `capswriterd` 是整体软件单例控制器 |
| 自启动 | 只注册 `capswriterd`（`~/Library/LaunchAgents/com.capswriter.agent.plist`） |
| 用户入口 | `capswriter` 全局命令（`~/.local/bin/capswriter`，由 `install.sh` 注册） |
| 配置策略 | Python 配置修改后通过 `capswriter restart` 生效 |
| 热词策略 | 保留 TXT 热词热重载；macOS 不强依赖 GUI |
| 信号处理 | SIGTERM 立即执行 cleanup（恢复 remap），SIGINT 双击确认退出 |
| 显示名称 | `CapsWriter for macOS`（CLI 交互统一使用此名称） |

---

## 任务看板

| 任务 | 状态 | 说明 |
|------|------|------|
| 服务端 `qwen_asr_mlx` 接入 | ✅ 已完成 | 真实启动 + 真实音频闭环验证通过，非 16kHz 重采样兜底已补 |
| 客户端 macOS 输入链路 | ✅ 已完成 | 全链路端到端桌面验证通过：长按录音、松手返回结果、剪贴板写入、自动粘贴上屏均正常 |
| `macos_caps_remap.py` 重写 | ✅ 已完成 | 完整 schema、atomic write、restore/clear --force 保护、日志接入 |
| macOS 上屏 | ✅ 已完成 | 剪贴板必写；osascript 失败只记 warning；授权 Accessibility 后自动粘贴验证通过 |
| 短按 Caps Lock 补发 | ✅ 已完成 | IOKit `IOHIDSetModifierLockState` 直接切换，不受 hidutil remap 影响 |
| 修复 macOS GUI 崩溃 | ✅ 已完成 | toast/context/hotword handler Darwin no-op；tray 已有 platform 检查 |
| 长按期间 Caps Lock 状态保护 | ✅ 已完成 | hidutil 在 HID 状态机前拦截，长按不改变系统 Caps Lock 状态 |
| 日志接线 | ✅ 已完成 | capswriterd / 所有 macOS 新模块均接入既有日志系统 |
| `capswriterd` 控制器 | ✅ 已完成 | PID 锁文件、监控循环、先停 client 再停 server、接入日志 |
| `capswriter` CLI | ✅ 已完成 | install/start/stop/restart/status/doctor/help/remap 全部可用，错误命令提示 help |
| launchd 自启动 | ✅ 已实现 | `capswriter install` 生成 plist；`capswriter uninstall` 注销；端到端测试待做 |
| 配置入口整理 | ✅ 已完成 | `llm_enabled=False` 禁用 macOS LLM；Python 配置通过 `capswriter restart` 生效 |
| F18 事件不透传终端 | ✅ 已完成 | Quartz CGEventTap 主动拦截，回调返回 None 吞掉 F18，终端不再出现 `^[[32~` |
| SIGTERM 信号修复 | ✅ 已完成 | `register_signal` 补注 SIGTERM，stop 后 client 正确恢复 remap |
| 全局命令注册 | ✅ 已完成 | `install.sh` 写入 `~/.local/bin/capswriter` 包装脚本 |
| `launchd install/uninstall` 端到端测试 | 🔲 待测试 | 实现已完成，需重启验证开机自启效果 |
| **P2: `.app` bundle 封装** | ✅ 已完成 | `CapsWriter.app` 创建完毕；CFBundleExecutable 原为 shell 脚本，macOS 26 拒绝执行（-10669），已改为编译的 Mach-O C 启动器 |
| **P2: NSApplication 集成** | ✅ 已完成 | `start_client_macos.py`：主线程 NSApplication RunLoop，asyncio 在子线程；SIGTERM/SIGINT 处理；client PID 文件写入 |
| **P2: capswriterd 启动方式适配** | ✅ 已完成 | capswriterd 通过 `open -W -n CapsWriter.app` 启动 client，读取 PID 文件追踪 |
| **P2: 麦克风橙色胶囊** | 🔴 进行中 | 详见下方"麦克风指示器问题分析" |
| P3: 菜单栏状态图标（待定） | 💤 暂不实施 | 菜单栏常驻 CapsWriter 图标，录音时切换状态，点击有菜单。有了 `.app` + NSApplication 后顺手可做，但当前不是优先级 |

---

## 已解决的历史问题

| 问题 | 解决方式 |
|------|---------|
| macOS 自动粘贴失败 (1002) | Accessibility 权限授权后解决；未授权时结果保留剪贴板 |
| 短按 Caps 误触发录音 | CGEventTap 主动拦截 + IOKit 切换，事件链路清晰，不再循环 |
| stop 后 remap 未恢复 | 补注 SIGTERM handler，client 收到 SIGTERM 后执行完整 cleanup |
| 终端出现 `^[[32~` | pynput 改为 Quartz CGEventTap 主动吞事件 |
| F18 Bridge 无事件（remap ownership 错位） | remap 移入 client 自身管理，supervisor 从启动链路移除 |
| `.app` 启动失败 -10669 | macOS 26 的 Launch Services 不再允许 shell 脚本作为 CFBundleExecutable；改为编译 Mach-O C 启动器（`clang launcher.c`）解决 |
| 极短录音后无响应（死锁） | `stream.close()` 在录音时间 <300ms 时可在 macOS 卡死，同时持有 `_session_lock`，导致后续所有按键等锁；改为带 5s 超时的后台线程调用，超时后放弃，进程自动恢复 |

---

## 麦克风指示器问题分析

### 现状（截至 2026-05-20）

- 录音功能正常（长按 Caps Lock → 转录 → 粘贴全链路验证通过）
- **Control Center 的麦克风指示器显示 "Python3"，而非 "CapsWriter"**
- **录音期间菜单栏左侧未出现橙色麦克风胶囊**（macOS 26 可能已变更行为，待确认）

### 根因

`open -W -n CapsWriter.app` 启动 C launcher（Mach-O），随即 `execv` 把进程替换为 Python 二进制。`execv` 后进程镜像变为 Python，TCC 对不同权限类别的追踪机制不同：

| TCC 类别 | 追踪机制 | 实际结果 |
|----------|----------|----------|
| Input Monitoring | Launch Services bundle 注册 | ✅ 显示 "CapsWriter" |
| Microphone（CoreAudio） | 当前进程可执行文件的代码签名身份 | ❌ 显示 "Python3" |

### 修复方案（待实施）

**方案：内嵌 Python（替代 execv）**

C launcher 不使用 `execv`，改为通过 `dlopen` 加载 `libpython3.13.dylib`，在进程内调用 `Py_RunMain()`，C binary 始终作为主进程存活，TCC 全程看到 CapsWriter 身份。

可行性确认：
- `Py_ENABLE_SHARED = 1` ✓
- `libpython3.13.dylib` 存在于 `~/.local/share/mise/installs/python/3.13.13/lib/` ✓
- 编译命令：`clang launcher.c -L<libdir> -lpython3.13 -rpath <libdir> -o CapsWriter.app/Contents/MacOS/CapsWriter`

---

## 技术风险（残留）

- `capswriter install` launchd 端到端还未测试（重启验证）。
- launchd 环境变量与交互 shell 不同，路径已使用绝对路径，但需重启确认。
- 麦克风橙色胶囊在 macOS 26 的显示位置/行为是否有变更，需对比确认（现象：Control Center 有橙色点但菜单栏无胶囊）。

---

## 配置策略

### Python 配置

- 不做热重载。修改 `config_client.py` / `config_server.py` 后执行 `capswriter restart` 生效。

### 热词 TXT

- macOS 保留 TXT 热词热重载，用户直接修改 `hot.txt` / `hot-rule.txt`，无需 GUI。

---

## 下一步工作

| 优先级 | 任务 | 说明 |
|--------|------|------|
| **P2** | **麦克风指示器显示 CapsWriter** | 改 C launcher 为内嵌 Python（libpython dlopen + Py_RunMain），不再 execv，见"麦克风指示器问题分析" |
| **P2** | **确认 macOS 26 橙色胶囊行为** | 修复归属后，验证录音时菜单栏左侧是否出现橙色胶囊，或仅在 Control Center 显示（macOS 26 行为可能变更） |
| P2 | `capswriter install` 端到端测试 | 需重启验证开机自启效果 |
| P3 | 菜单栏状态图标（待定） | 常驻图标 + 录音状态切换 + 点击菜单 |
| P3 | 更接近 Windows 的流式上屏体验 | — |
