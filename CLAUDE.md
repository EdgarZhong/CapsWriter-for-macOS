# CapsWriter-Offline 当前阶段同步

## 当前目标

- 分支：`mac-dev`，基线：`master`
- 为 macOS / Apple Silicon 新增 `qwen_asr_mlx` 后端，实现 Caps Lock 长按录音、结果返回、剪贴板写入、自动上屏。
- **当前阶段：架构重新梳理完毕（2026-05-23）。capswriterd 废弃，改为 launchd 直接管两个独立 agent。P0 崩溃待诊断，P1 架构重构待实施。**
- 完整架构决策见 `docs/macos-architecture-decisions.md`

---

## 架构

```text
launchd
  ├─ CapsWriter.app/Contents/MacOS/CapsWriter  （client agent）
  │    ├─ NSApplication 主线程 → ErrorBus → status.json / 通知
  │    └─ asyncio 子线程 → CapsWriterClient
  │              ├─ MacOSCapsRemapSession / MacOSCapsF18Bridge / CGEventTap
  │              ├─ AudioRecorder / WebSocketManager
  │              └─ ResultProcessor → 剪贴板 / 上屏
  └─ start_server.py  （server agent）
       └─ qwen_asr_mlx（端口 6016，client 断连 60s 后自行退出）
```

**Ownership：** launchd 管两个 agent 生命周期；server 通过 WebSocket 连接状态自管生命周期；client 独占 Caps remap；CLI 封装 launchctl 统一控制两者。

**明确不采用：** capswriterd（已废弃）；.app 子进程管理 server；单独日志命令；remap repair 命令。

---

## 关键决策

| 决策 | 内容 |
|------|------|
| 发布形态 | 近期 clone + install.sh；远期 .dmg |
| server 生命周期 | client 断连等待 60s；`capswriter stop` 发 shutdown 信号立即退出 |
| 错误提示架构 | ErrorBus 统一内部出口；近期 status.json + CLI 先行；Unix socket 实时推送待 GUI 阶段 |
| CLI start 行为 | 阻塞等待，实时输出，成功或明确失败前不退出 |
| Accessibility 引导 | 列表有 → 点「-」删除；没有 → 等自动出现；不引导点「+」；osascript 弹窗只弹一次；15s 重试 |
| 连接状态通知 | 每次 WebSocket 状态变化发系统通知，冷启动第一次也通知 |
| 用户心智 | 运维层透明（只操作 CapsWriter 整体）；故障层用「识别引擎」指代 server |
| 菜单栏 GUI | 待机灰色 mic / 录音橙色 mic.fill；点击只有 Quit；无其他 UI |
| 显示名称 | `CapsWriter for macOS` |
| 信号处理 | SIGTERM 立即 cleanup（恢复 remap）；SIGINT 双击确认 |

---

## 任务看板

| 任务 | 状态 | 说明 |
|------|------|------|
| qwen_asr_mlx 接入 | ✅ | 真实音频闭环验证通过 |
| macOS 输入链路 | ✅ | 长按录音 → 结果 → 剪贴板 → 自动粘贴全链路验证 |
| .app bundle + launcher_embed | ✅ | Mach-O C 启动器，hardened runtime，麦克风胶囊显示 CapsWriter |
| AVFoundation 权限弹窗 | ✅ | 首次启动弹出麦克风授权对话框 |
| CGEventTap 失效恢复框架 | ✅ | 通知用户 + 打开设置 + 每 10s 重试；已消除 pynput 降级 |
| capswriter CLI | ✅ | install/start/stop/restart/status/doctor/help/remap 可用 |
| **P0：client 38s 后崩溃** | 🔴 待诊断 | 连接 server 约 38s 后意外退出，无 traceback；最可疑：AVFoundation 回调线程或恢复循环新代码 |
| **P1：架构重构（废弃 capswriterd）** | 🔴 待实施 | 两个独立 launchd plist；server 生命周期自管理；capswriter CLI 改走 launchctl |
| **P1：ErrorBus + status.json** | 🔴 待实施 | 统一内部报错出口；status.json 心跳写入；CLI start/status/doctor 对齐 |
| **P1：Accessibility 引导优化** | 🔴 待实施 | osascript 分支对话框；15s 重试；CLI 持续输出 |
| **P2.5：菜单栏图标** | 🔲 待实施 | SF Symbols mic/mic.fill；状态切换；Quit 菜单项；icon.ico → icns |
| **P2：Unix socket 实时推送** | 🔲 待实施（GUI 阶段） | CLI 实时订阅 .app 事件流 |
| launchd 端到端测试 | 🔲 待测试 | 重启验证开机自启 |
| FFmpeg 路径确认 | 🔲 待确认 | launcher_embed 进程 PATH 是否含 FFmpeg |

---

## 重签名注意事项（开发期）

每次运行 `build_launcher.sh` 后 Accessibility TCC 记录失效（csreq 变化）：
- 脚本结尾自动打开辅助功能设置
- 在列表中找到 CapsWriter → 点「-」删除 → 重启软件 → 重新授权
- 麦克风权限一般无需重置，除非录音全零才执行 `tccutil reset Microphone com.capswriter.client`

---

## 下一步工作

| 优先级 | 任务 |
|--------|------|
| **P0** | 诊断 38s 崩溃：改进 `_run_client()` exception logging 输出完整 traceback；查 `~/Library/Logs/DiagnosticReports/` |
| **P1** | 架构重构：两个 launchd plist；server 60s 自退出；CLI 改走 launchctl |
| **P1** | ErrorBus + status.json + CLI 改进 |
| **P1** | Accessibility 引导对话框 + 15s 重试 + CLI 持续输出 |
| **P2.5** | 菜单栏图标 |
| P2 | launchd 端到端测试 |
