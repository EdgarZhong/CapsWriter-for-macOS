# CapsWriter-Offline 当前阶段同步

## 当前目标

- 分支：`mac-dev`，基线：`master`
- 为 macOS / Apple Silicon 新增 `qwen_asr_mlx` 后端，实现 Caps Lock 长按录音、结果返回、剪贴板写入、自动上屏。
- **当前阶段：launchd 双 agent 架构已落地，下一步转入后端推理优化（2026-06-05）。当前优先排查 `qwen_asr_mlx` 在 macOS 上使用 1.7B-8bit 时的精度表现，重点对比 Windows 侧 4bit 路线。**
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
| 菜单栏 GUI | 采用**自定义矢量 mark**（"会说话的⇪"：气泡 + 波形 + Caps Lock）作菜单栏 template，**放弃** SF Symbols `waveform`；NSImage 原生读 SVG（`_NSSVGImageRep` 矢量，任意倍率清晰）+ `isTemplate` 深 / 浅色自适应 + `autosaveName` 固定位置；旧系统（<13）@2x PNG 兜底。当前仅图标，下拉菜单（📋 复制最近结果 / ✨ 编辑热词 / Quit）后续再加 |
| 显示名称 | `CapsWriter for macOS` |
| 信号处理 | SIGTERM：set_wakeup_fd + SigtermWatcher 守护线程（NSApp.run() C RunLoop 期间 Python signal handler 无法执行）→ _critical_cleanup() → os._exit(0)；SIGINT 双击确认 |
| 流式识别策略 | 当前阶段**不**把“产品级流式识别 / 流式显示”作为优先目标。Qwen3-ASR 的 decoder 虽具备自回归逐 token 输出能力，但要做成稳定的端到端流式体验仍需额外的 chunking、稳定前缀/不稳定尾巴管理与中间结果提交策略；现阶段先聚焦最终结果精度 |
| MLX 后端演进路线 | 当前 `qwen_asr_mlx` 只是一层最小适配，后续精度优化主路线改为：**fork `mlx-qwen3-asr`，接管中层推理编排**（prompt 组装、language/context 策略、generation config、chunking、aligner 接法），而非继续把 `Session.transcribe()` 作为黑盒 |

---

## 任务看板

| 任务 | 状态 | 说明 |
|------|------|------|
| qwen_asr_mlx 接入 | ✅ | 真实音频闭环验证通过 |
| macOS 输入链路 | ✅ | 长按录音 → 结果 → 剪贴板 → 自动粘贴全链路验证 |
| .app bundle + launcher_embed | ✅ | Mach-O C 启动器，hardened runtime，麦克风胶囊显示 CapsWriter |
| AVFoundation 权限弹窗 | ✅ | 首次启动弹出麦克风授权对话框 |
| CGEventTap 失效恢复框架 | ✅ | 通知用户 + 打开设置 + 每 10s 重试；已消除 pynput 降级 |
| **M1：launchd 双 plist 架构** | ✅ | capswriterd 归档；两个独立 plist；capswriter CLI 改走 launchctl；start_server.py 加 SIGTERM→exit 0 |
| **M2：server 60s 自退出** | ✅ | `_watch_connections()` 后台协程；首次有连接后开始监控；断连 60s 则 app.stop()+os._exit(0) |
| **M3：ErrorBus + status.json** | ✅ | `ErrorBus` 类；状态变化时写入；5s 心跳；退出删除；连接/录音状态已接入；traceback 补全 |
| **M4：CLI 改进** | ✅ | `start` 阻塞轮询 status.json 等 ready；`status` 读 status.json 展示完整快照 |
| **M5：Accessibility 引导优化** | ✅ | osascript 分支弹窗（只弹一次）；15s 重试；ErrorBus wire accessibility_ok；CLI start 超时有具体提示 |
| **SIGTERM 修复** | ✅ | set_wakeup_fd + SigtermWatcher 守护线程；_critical_cleanup() + os._exit(0)；capswriter stop 现可在几秒内干净退出 |
| **M6：菜单栏图标** | ✅ | 自定义矢量 mark 作 template（`start_client_macos.py:_install_status_item`）：NSImage 原生读 SVG（`_NSSVGImageRep` 矢量）+ `isTemplate` 深浅自适应 + `autosaveName` 固定位置；旧系统 @2x PNG 兜底 + 文字兜底；仅图标暂不挂菜单；资源在 `assets/branding/capswriter-menubar-template.{svg,png}` |
| **P3：后端推理精度调优** | 🟡 进行中 | 聚焦 `qwen_asr_mlx`：核对 8bit/4bit 模型选择、上下文/热词能力缺口、音频前处理与解码参数差异，评估是否需要补齐能力或回退默认规格 |
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
| P0 | 用同一批音频样本对比 `qwen_asr_mlx` 与 Windows `qwen_asr` 路线，区分“量化差异”与“接入差异” |
| P0 | fork `mlx-qwen3-asr`，摆脱 `Session.transcribe()` 黑盒接法，优先夺回 prompt 组装、language/context 策略、generation config 的控制权 |
| P0 | 复核并补齐 `qwen_asr_mlx` 当前缺失能力：服务端热词、解码参数、chunking 策略、aligner 接法 |
| P1 | 评估默认模型策略是否仍应保持“macOS 优先 8bit”，或改为可配置优先级 / 按机器回退 4bit |
| P1 | 在 fork 路线稳定后，再决定是否需要更下沉的 MLX 层改造；当前不投入产品级流式识别实现 |
| P2 | 菜单栏下拉菜单与状态显示（图标已落地，仅差菜单内容：📋 复制最近结果 / ✨ 编辑热词 / Quit） |
