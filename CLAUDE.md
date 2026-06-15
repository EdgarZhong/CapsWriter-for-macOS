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
| App 图标 | `.icns` 放 `assets/icon/app-icon.icns`（源）→ 拷入 bundle `Resources/` + `Info.plist` `CFBundleIconFile=app-icon` + 重签名；`build_launcher.sh` 每次构建自动同步。LSUIElement 不进 Dock，图标体现在 Finder / 简介 / 权限列表 |
| 通知后端 | `osascript`（归属脚本编辑器=卷轴）→ 改 **`UNUserNotificationCenter`**（CapsWriter 身份）；裸跑无 bundle 时回退 osascript；调用前用 `bundleIdentifier()` 防 abort。**横幅图标破图问题已 park**（见 `docs/bug-report-notification-icon.md`） |
| 键盘失败处理 | 见 `docs/macos-architecture-decisions.md` 第六节。**回调非阻塞铁律**（业务甩工作线程队列）；失败分类（timeout/丢keyUp=自救带预算，撤权/创建失败/RunLoop退出=fatal）；fatal 单路径=恢复 remap+通知+引导重授权后重启，**删 15s 静默循环**；撤权时主动 `CFRunLoopStop` |
| 权限恢复 UX | **渐进探测式**（2026-06-15 取代旧"统一−删除单弹窗"）：**只要「辅助功能」一项**（2026-06-15 实测收敛——主动型吞事件 tap 下「输入监控」被辅助功能蕴含、列表里根本不出现 CW，早先「双权限」是 dev 重签名旧记录的误判，**已删干净**：`run_guide`/就绪门控/`check_health` 三处输入监控分支全移除）；`macos_permission_guide.run_guide` 探测 granted/unknown/denied —— unknown 弹原生窗、denied 先提示拨开关并轮询、拨了超时(≈25s)仍不生效才升级到"−删除+重启"。"该删还是该拨"由 app 轮询判定，**用户不自己诊断 stale**。TCC 绑签名仍是底层原因（拨不生效=旧记录签名失配）。详见 `docs/macos-architecture-decisions.md` 第六节 |

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
| **M6：菜单栏图标** | ✅ | 自定义矢量 mark 作 template（`start_client_macos.py:_install_status_item`）：NSImage 原生读 SVG（`_NSSVGImageRep` 矢量）+ `isTemplate` 深浅自适应 + `autosaveName` 固定位置；旧系统 @2x PNG 兜底 + 文字兜底；仅图标暂不挂菜单；代码正式素材在 `assets/icon/capswriter-menubar-template.{svg,png}`（v2），设计/调试件留在 `assets/branding/` |
| **App 图标绑定** | ✅ | icns 绑入 bundle + `Info.plist` + 重签名 + `build_launcher.sh` 自动同步；Finder/简介/权限列表显示正确 |
| **通知原生化（UN）** | ✅ | `UNUserNotificationCenter` + osascript 回退 + bundleIdentifier 防 abort；**横幅图标破图已 park** |
| **M7：键盘捕获重构** | ✅ 代码+单测 | 回调非阻塞队列；丢 keyUp 用 `CGEventSourceKeyState` 对账自愈；fatal 单路径（删 15s 静默循环）；删 pynput/B 残骸 |
| **M7.1：永不冻结（实测纠正）** | ✅ 撤辅助功能已实测通过 | 根因 = macOS 撤辅助功能发的是 `DisabledByTimeout`（非 `DisabledByUserInput`），旧码盲目 re-enable 死 tap → 冻结且不弹提醒。修：①不变量「默认安全态=tap 禁用=键盘正常，绝不盲目 re-enable」②`_go_fatal` 先 `CGEventTapEnable(False)` 放行再善后 ③`_on_timeout` 查 `AXIsProcessTrusted` **回调自检**打断 re-enable 死循环（无需线程）。「永不冻结」由系统超时窗+不 re-enable 保证，**不依赖外部检查** |
| **M7.2：外部体检（自检盲区）** | ✅ 代码，待复验 | 回调死锁**不送事件 → 无法自检**，必须外部探测。`listener.check_health()`（**仅** `CGEventTapIsEnabled` 黑盒判据）**复用 5s 心跳**（`mic_runner._heartbeat_task`→`bridge.check_health`），**砍掉专用守护线程**。系统在背后禁 tap 即 fatal+引导。（2026-06-15：原 `IOHIDCheckAccess` 输入监控分支已删——见权限恢复 UX 决策） |
| **M7.3：就绪通知门控** | ✅ 代码，待复验 | `result_processor` 发「CapsWriter 就绪」前同步探测**辅助功能**权限，有权限问题改报「键盘接管未就绪」——不再出现「权限坏了却提示已就位」（跨平台惰性导入守卫） |
| **A：server 单例守卫** | ✅ 代码+单测 | 端口自检前置到模型加载之前（`app.start()` 开头）；被占则 `os._exit(0)`（KeepAlive 不重启），避免重复实例先加载模型；删掉 launchd 下会崩的 `input("按回车")`，改兜底 exit 0。**待运行时验收** |
| **B+C：渐进权限引导** | ✅ 代码+单测 | 新模块 `macos_permission_guide.py`：探测**辅助功能**(`AXIsProcessTrusted`)单一权限（unknown→原生弹窗 / denied→拨开关轮询 / 超时→升级删除）；接管 bridge `_handle_tap_failed`。四场景单测过。（2026-06-15：输入监控引导分支已删干净——主动 tap 下被辅助功能蕴含） |
| **通知横幅图标** | 🔲 park | 破图，下个会话受控实验（`docs/bug-report-notification-icon.md`） |
| **D：孤儿进程（client 脱离 launchd）** | ✅ 代码+实测 | 根因：client 作为 NSApplication GUI app 被 LaunchServices 从 `com.capswriter.client` 标签**领养**到 `application.com.capswriter.client.<ASN>` 动态标签，`_launchctl_pid(原标签)`/`launchctl stop 原标签` 够不到 → stop 误判"未在运行" → 孤儿存活、start 再起一个 → 双实例。修法：`capswriter.py` 新增 `_client_pids()`（`pgrep -f` 按 .app 可执行文件路径查，**label-independent**）+ `_stop_client()`（launchctl stop 协调 KeepAlive + 按身份 SIGTERM 兜底 + 10s 后 SIGKILL）；stop/start/uninstall/status 全改走它。`restart` 实测：停旧 client→起单实例，无双图标 |
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
