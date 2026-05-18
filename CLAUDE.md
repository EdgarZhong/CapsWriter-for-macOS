# CapsWriter-Offline 当前阶段同步

## 当前目标

- 分支：`mac-dev`，基线：`master`
- 目标：为 macOS / Apple Silicon 新增 `qwen_asr_mlx` 后端，在不大改现有 Client / Server 架构的前提下，实现稳定的 Caps Lock 长按录音、最终结果返回、剪贴板写入和可选自动上屏体验。
- **当前阶段：P0/P1 全部完成，正在做集成测试收尾。**

---

## 当前总体架构决策

### 最终运行架构

```text
launchd
  └─ capswriterd
       ├─ server process
       │    └─ start_server.py / qwen_asr_mlx
       └─ client process
            └─ start_client.py / core.client.main
                 └─ MacOSCapsRemapSession（client 独占 Caps remap 生命周期）
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
       └─ start_client.py
            └─ core.client.main → CapsWriterClient
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
| macOS GUI | 初版不要任何 GUI；tray / toast / Tkinter 弹窗全禁 |
| 模型优先级 | 默认 `Qwen3-ASR-1.7B-8bit`，本地回退 `1.7B-4bit` |
| remap ownership | client 是 Caps remap 的唯一生命周期 owner |
| remap 持久化 | client 启动前保存 original UserKeyMapping 快照，退出或手动 restore 时恢复 |
| client 运行期 remap 规则 | client 运行时独占接管 Caps Lock -> F18；需修改键盘映射先 stop |
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

---

## 已解决的历史问题

| 问题 | 解决方式 |
|------|---------|
| macOS 自动粘贴失败 (1002) | Accessibility 权限授权后解决；未授权时结果保留剪贴板 |
| 短按 Caps 误触发录音 | CGEventTap 主动拦截 + IOKit 切换，事件链路清晰，不再循环 |
| stop 后 remap 未恢复 | 补注 SIGTERM handler，client 收到 SIGTERM 后执行完整 cleanup |
| 终端出现 `^[[32~` | pynput 改为 Quartz CGEventTap 主动吞事件 |
| F18 Bridge 无事件（remap ownership 错位） | remap 移入 client 自身管理，supervisor 从启动链路移除 |

---

## 技术风险（残留）

- `capswriter install` launchd 端到端还未测试（重启验证）。
- launchd 环境变量与交互 shell 不同，路径已使用绝对路径，但需重启确认。
- 后台进程无 macOS 橙点麦克风指示器（子进程无 NSApplication 身份），决定暂不处理。

---

## 配置策略

### Python 配置

- 不做热重载。修改 `config_client.py` / `config_server.py` 后执行 `capswriter restart` 生效。

### 热词 TXT

- macOS 保留 TXT 热词热重载，用户直接修改 `hot.txt` / `hot-rule.txt`，无需 GUI。

---

## 下一步工作

| 优先级 | 任务 |
|--------|------|
| P1 | `capswriter install` 端到端测试（重启验证开机自启） |
| P2 | macOS 录音状态视觉反馈（音效提示或菜单栏图标） |
| P3 | 更完整 GUI 或菜单栏能力 |
| P3 | 更接近 Windows 的流式上屏体验 |
