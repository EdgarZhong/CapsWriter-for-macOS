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
| 权限引导（两权限） | **2026-06-22 重订，推翻旧"仅辅助功能"**：辅助功能 + 输入监控**都引导**。丝滑首装：AX 弹框→拨开关→（AX 就绪后程序**补一次 tap 尝试**注册出 IM 条目）→拨 IM 开关→重启一次即用，**用户不必点「+」、不必去 Finder 找 app**。**2026-06-24 二次收敛（已实测生效）**：**砍掉程序内「统一手动指导面板」**——引导只说「打开开关 / 请重启」，**永不弹面板、永不说删/剪条目**；stale 与一切疑难统一交给 `capswriter reset-permissions` 命令兜底。详见架构决策第六节『2026-06-24 二次收敛』。 |
| 连接状态通知 | 每次 WebSocket 状态变化发系统通知，冷启动第一次也通知 |
| 用户心智 | 运维层透明（只操作 CapsWriter 整体）；故障层用「识别引擎」指代 server |
| 菜单栏 GUI | 采用**自定义矢量 mark**（"会说话的⇪"：气泡 + 波形 + Caps Lock）作菜单栏 template，**放弃** SF Symbols `waveform`；NSImage 原生读 SVG（`_NSSVGImageRep` 矢量，任意倍率清晰）+ `isTemplate` 深 / 浅色自适应 + `autosaveName` 固定位置；旧系统（<13）@2x PNG 兜底。**下拉菜单已落地**（见任务 M8）：纯原生 `NSMenu`+`NSMenuItem`（无自定义视图，自动继承系统 Liquid Glass 材质 + 深浅色自适应），SF Symbol 模板图标。五项：状态表头(禁用，按 ErrorBus 快照刷新) / 复制最近结果(无结果置灰) / 编辑热词(open -t hot.txt) / 重启 CapsWriter(=`capswriter restart`) / 退出 CapsWriter(=`capswriter stop`) |
| 显示名称 | `CapsWriter for macOS` |
| 信号处理 | SIGTERM：set_wakeup_fd + SigtermWatcher 守护线程（NSApp.run() C RunLoop 期间 Python signal handler 无法执行）→ _critical_cleanup() → os._exit(0)；SIGINT 双击确认 |
| 流式识别策略 | 当前阶段**不**把“产品级流式识别 / 流式显示”作为优先目标。Qwen3-ASR 的 decoder 虽具备自回归逐 token 输出能力，但要做成稳定的端到端流式体验仍需额外的 chunking、稳定前缀/不稳定尾巴管理与中间结果提交策略；现阶段先聚焦最终结果精度 |
| MLX 后端演进路线 | 当前 `qwen_asr_mlx` 只是一层最小适配，后续精度优化主路线改为：**fork `mlx-qwen3-asr`，接管中层推理编排**（prompt 组装、language/context 策略、generation config、chunking、aligner 接法），而非继续把 `Session.transcribe()` 作为黑盒 |
| 模型常驻内存（权重 wiring）+ 启动预热 | **已敲定，待 fork 后实施**（2026-06-22）。解决 `qwen_asr_mlx` 偶发首次识别延迟极高（基本可确定是权重被 macOS 压缩/换出，再次推理需搬回物理内存）；**不**承诺解决"内存被别的程序激烈争用时推理变慢"：**不检测"模型是否还热"（RSS/瞬时压力/粘性压力全否决，系统内存管理是黑盒不可靠观测），改用 `mx.set_wired_limit` 把权重钉成 wired 常驻内存、不可换出**。配套**一次性启动预热**（付清 MLX kernel 编译，与开关无关，始终做）。wiring 做成 **server 可选项（`Qwen3ASRMLXArgs`，默认开，文档写明可关**——用户跑高占用软件时不该死赖内存）。自适应按 `get_active_memory()` 定大小、卡 `cap*0.6` 双保险、不需 sudo、仅 macOS 15+。**实施归属在 fork 的中层编排层，不在适配层**，避免重复搬迁。完整推理留痕见 `docs/macos-architecture-decisions.md` 第九节 |
| App 图标 | `.icns` 放 `assets/icon/app-icon.icns`（源）→ 拷入 bundle `Resources/` + `Info.plist` `CFBundleIconFile=app-icon` + 重签名；`build_launcher.sh` 每次构建自动同步。LSUIElement 不进 Dock，图标体现在 Finder / 简介 / 权限列表 |
| 通知后端 | `osascript`（归属脚本编辑器=卷轴）→ 改 **`UNUserNotificationCenter`**（CapsWriter 身份）；裸跑无 bundle 时回退 osascript；调用前用 `bundleIdentifier()` 防 abort。**横幅图标破图问题已 park**（见 `docs/bug-report-notification-icon.md`） |
| 键盘失败处理 | 见 `docs/macos-architecture-decisions.md` 第六节。**回调非阻塞铁律**（业务甩工作线程队列）；失败分类（timeout/丢keyUp=自救带预算，撤权/创建失败/RunLoop退出=fatal）；fatal 单路径=恢复 remap+通知+引导重授权后重启，**删 15s 静默循环**；撤权时主动 `CFRunLoopStop` |
| 权限恢复 UX | **2026-06-22 重订（取代"渐进探测式/仅辅助功能"）**：**两件事分离**——(a) 让条目出现 由程序自动（AX 弹框 / IM 靠 AX 就绪后补 tap 尝试注册，request API 实测无效已弃）；(b) 拨开关 永远是用户的活。判下一步只看 **探测 + tap 心跳** 两个信号；**绝不因权限退出进程**（杜绝 fatal→退出→KeepAlive 死循环）。**防死循环铁律**：只有「探测全 granted 且 tap 心跳死」才提示删条目，其余一律「打开开关」。stale 真假靠**单次启动校验 ping（option C）**确诊；运行期体检仍用 `CGEventTapIsEnabled`。**2026-06-24 收敛**：判 stale 后**不再进程内弹手动面板**，改为通知用户运行 `capswriter reset-permissions`（先停 client → tccutil reset 两权限 → 重启从零重走）。详见架构决策第六节『2026-06-24 二次收敛』。<br>**2026-06-25 实测收敛（放弃精确编排 TCC）**：多轮实测确认 macOS 两权限弹窗顺序（AX vs IM 先弹）与 **IM 条目何时出现在列表里**都**不可由程序可靠控制**（同一从零路径出现多种表现）——这是 TCC 的固有不确定性，非本项目 bug，**决定不再投入精确编排**。对策只一条：让引导**对任意顺序都健壮**——IM 通知文案改为不再断言「条目已就位」，而是显式兜底「若列表里没有 CapsWriter，请点「+」搜索并添加」（`macos_permission_guide.py` 阶段 3）。配合 `reset-permissions` 兜底，口径足够。 |

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
| **M6：菜单栏图标** | ✅ | 自定义矢量 mark 作 template（`start_client_macos.py:_install_status_item`）：NSImage 原生读 SVG（`_NSSVGImageRep` 矢量）+ `isTemplate` 深浅自适应 + `autosaveName` 固定位置；旧系统 @2x PNG 兜底 + 文字兜底；代码正式素材在 `assets/icon/capswriter-menubar-template.{svg,png}`（v2），设计/调试件留在 `assets/branding/` |
| **M8：菜单栏下拉菜单** | ✅ 代码+冒烟测试，待运行时复验 | 2026-06-22。纯原生 `NSMenu`+`NSMenuItem`（**铁律：不塞自定义视图**，否则破坏系统材质）→ 自动继承 Liquid Glass + 深浅色自适应；SF Symbol 模板图标同样随外观反色。`_StatusMenuController(NSObject)` 兼 target+`NSMenuDelegate`，`menuNeedsUpdate:` 刷新表头文案 + 复制项置灰。**状态表头用彩色 emoji 圆点（color glyph，禁用项也显色）按 `ErrorBus.state` 着色：🟢运行正常(ready) / 🔵录音中(recording) / 🟡识别引擎未连接(connecting) / 🔴客户端故障(error，或 ready 但 microphone_ok=False) / ⚪️启动中(starting)**——直接用 state 字段避开启动期误报红灯。五项：状态表头(禁用) / 复制最近结果(`NSPasteboard`，回退 `last_recognition_text`) / 编辑热词(`open -t hot.txt`) / 重启(`capswriter restart`) / 退出(`capswriter stop`)。**退出/重启都会 SIGTERM 杀掉 client 自身，故 detached 派生（`start_new_session=True`），解释器用 `.venv/bin/python` 与 install.sh 一致**。ErrorBus 新增 `snapshot()`。冒烟测试：菜单结构/selector 解析/复制置灰+写剪贴板/状态文案映射全过 |
| **App 图标绑定** | ✅ | icns 绑入 bundle + `Info.plist` + 重签名 + `build_launcher.sh` 自动同步；Finder/简介/权限列表显示正确 |
| **通知原生化（UN）** | ✅ | `UNUserNotificationCenter` + osascript 回退 + bundleIdentifier 防 abort；**横幅图标破图已 park** |
| **M7：键盘捕获重构** | ✅ 代码+单测 | 回调非阻塞队列；丢 keyUp 用 `CGEventSourceKeyState` 对账自愈；fatal 单路径（删 15s 静默循环）；删 pynput/B 残骸 |
| **M7.1：永不冻结（实测纠正）** | ✅ 撤辅助功能已实测通过 | 根因 = macOS 撤辅助功能发的是 `DisabledByTimeout`（非 `DisabledByUserInput`），旧码盲目 re-enable 死 tap → 冻结且不弹提醒。修：①不变量「默认安全态=tap 禁用=键盘正常，绝不盲目 re-enable」②`_go_fatal` 先 `CGEventTapEnable(False)` 放行再善后 ③`_on_timeout` 查 `AXIsProcessTrusted` **回调自检**打断 re-enable 死循环（无需线程）。「永不冻结」由系统超时窗+不 re-enable 保证，**不依赖外部检查** |
| **M7.2：外部体检（自检盲区）** | ✅ 代码，待复验 | `listener.check_health()` 运行期只保留 `CGEventTapIsEnabled` 这一条黑盒判据，**复用 5s 心跳**（`mic_runner._heartbeat_task`→`bridge.check_health`），**不**把 `IOHIDCheckAccess` 当 fatal 条件。**补充（2026-06-22）**：「enabled 却静默收不到事件」的 stale 死 tap 是 `CGEventTapIsEnabled` 的盲区，现由**单次启动校验 ping（option C）**覆盖——只在 `start()` 后打一发，运行期不发 ping。 |
| **M7.3：就绪通知门控** | ✅ 代码，待复验 | `result_processor` 不再用权限位去“猜”键盘是否已就绪，而是直接读取 bridge 的真实运行态：只有 active `CGEventTap` 已成功建立才会写 `state=ready` 并发「CapsWriter 就绪」；否则落到 `state=error` + 「键盘接管未就绪」。输入监控不再参与 ready 门控。 |
| **A：server 单例守卫** | ✅ 代码+单测 | 端口自检前置到模型加载之前（`app.start()` 开头）；被占则 `os._exit(0)`（KeepAlive 不重启），避免重复实例先加载模型；删掉 launchd 下会崩的 `input("按回车")`，改兜底 exit 0。**待运行时验收** |
| **B+C：权限引导重写（两权限 + 状态机）** | ✅ **2026-06-24 实测通过**（干净首装 / 撤辅助功能引导 / 通知文案 / option C ping 均正常） | 2026-06-22 整体重写（推翻"仅辅助功能/渐进探测"）。`macos_permission_guide.py`：四象限工具箱（辅助功能 `AXIsProcessTrusted`/`AXIsProcessTrustedWithOptions(prompt)`/面板；输入监控 `IOHIDCheckAccess` 仅作提示/面板`Privacy_ListenEvent`新增）+ `PermPhase` 全局时效状态 + `run_guide` 状态机（辅助功能先行→AX 就绪后注入 `try_register_im` 补一次 tap 尝试注册 IM→引导 IM；**防死循环铁律**只在「探测全 granted+心跳死」弹统一手动指导）+ 统一手动指导文案。`macos_f18_listener.py`：被动心跳 `_last_event_ts` + 合成 ping `_probe_alive`/`tap_healthy`（option C 单次启动校验）+ `attempt_im_registration`（IM 注册手段；request API 实测无效已弃）。`macos_caps_f18.py`：`start()` 建 tap 后 ping 确诊真活/stale，`_handle_tap_failed` 注入回调跑状态机、上报 `perm_phase`、**绝不退出进程**。`error_bus.py`：加 `perm_phase` 字段。四文件 py_compile 通过。**合成 ping 真能流过 HID tap、从零首装、撤辅助功能引导均已 2026-06-24 实测通过。** <br>**2026-06-24 二次收敛（已实测）**：砍掉 `STALE_MANUAL` / `dialog` / `run_guide` 的 `tap_healthy` 参数 / 手动指导面板文案；`run_guide` 签名收为 `run_guide(notify, *, try_register_im, on_phase)`；阶段 4 改为通知「权限已就绪请重启」；stale 交给 `reset-permissions`。`macos_f18_listener.check_health()` 撤回 AX 检查（tccutil 非真实场景）。 |
| **G：菜单栏绿点不灭（撤权后状态不更新）** | ✅ 代码，待复验 | 2026-06-24。根因：菜单栏圆点读 `ErrorBus.state`，而 `result_processor` 只在**连接状态变化**时才用 `bridge.is_tap_available()` 重算 state；运行中撤辅助功能走 `_handle_tap_failed`，只改了 `accessibility_ok=False`、**没动 `state`** → 圆点停在 `ready`（绿）误导用户「以为还在工作」。修法：`macos_caps_f18.py` 的 `_handle_tap_failed` 与 `start()` stale 分支都改为 `eb.update(state='error', accessibility_ok=False)`，圆点立刻转红「键盘接管/权限未就绪」；撤权后无人会刷回 ready（除非重连），稳定显示红直到重启。 |
| **H：双实例看门狗 + remap PID 守卫** | ✅ 代码+实测 | 2026-06-24。(1) `_critical_cleanup()` 在坏态 tap 上调 `CGEventTapEnable(False)` 可能死锁 → SIGTERM 后进程变僵尸 → 菜单栏重启时与新进程并存双实例。修：`start_client_macos.py` 在 `_critical_cleanup()` 开头起 2s 看门狗线程，挂住则 `os._exit(0)` 强退（**2026-06-25 纠正**：原写 `os._exit(1)` 是回归——非零退出会被 client plist 的 `KeepAlive(SuccessfulExit=false)` 当崩溃复活，自己造双实例；看门狗职责只是「保证退出」而非「报告失败」，故用 0）。(2) 杀旧僵尸进程时其 cleanup 会 `restore()` 清掉系统全局 hidutil remap，连带把**新进程**的 Caps 接管也清了（用户现象「caps 接管突然失效」）。修：`macos_caps_remap.py` 的 `restore()` 加 PID 归属校验——state 文件 `client_pid != 自己` 则跳过 restore。 |
| **I：弹窗顺序（输入监控抢先于辅助功能）** | ✅ 代码，待复验 | 2026-06-24。根因：`bridge.start()` 无条件 `_listener.start()`→`_create_tap()`，而 `CGEventTapCreate` 本身会触发「输入监控」TCC 弹窗+注册 IM 条目 → 从零启动时 IM 窗抢在 AX 窗之前弹、且首弹时 IM 条目已在列表（违反辅助功能先行，用户顺手开 IM 再重启则 AX 仍缺、不可预测）。修法：`start()` 用只读 `check_accessibility()` 前置判断，AX 未就绪时**不预创建 tap**，直接起线程跑 `_handle_tap_failed`→`run_guide`（AX 先弹）；AX 就绪后才由 `try_register_im` 补 tap 尝试注册/弹 IM。弹窗顺序恒为「辅助功能 → 输入监控」。 |
| **reset-permissions 命令** | ✅ 代码，待复验 | 2026-06-24。`capswriter.py:cmd_reset_permissions`：先 `_stop_client()` 停 client（避 remap 残留 + 避开 tccutil 撤运行中进程致冻结）→ `tccutil reset Accessibility/ListenEvent com.capswriter.client` → 提示 `capswriter start` 从零重走。stale/疑难统一兜底入口，替代被砍掉的进程内手动面板。README「权限疑难排查」已同步。 |
| **J：去 client KeepAlive（根治双实例）** | ✅ 代码，待复验（需 uninstall→install 重写 plist） | 2026-06-25。根因（与任务 H 同一病灶的架构层）：client plist 设了 `KeepAlive(SuccessfulExit=false)`，而 client 是 GUI app、生命周期本应由用户/CLI/菜单栏自管。macOS 授予输入监控/辅助功能时会**强杀 client**（权限在进程启动时读取），这一强杀被 launchd 当崩溃复活 → 复活的孤儿（常被 LaunchServices 领养到动态标签）与显式 start/手动重启相撞 = 双实例；同一 KeepAlive 也是历史「13s fatal 死循环」的根。修：`capswriter.py:_build_client_plist` **删除 client 的 KeepAlive、保留 `RunAtLoad`**（登录自启）；授权后重启由用户/CLI 显式 `start`。**server plist 仍保留 KeepAlive**（server 非 GUI、不被权限强杀，正常停止 exit 0 不与 KeepAlive 相争，需要崩溃恢复）。⚠️ `cmd_install` 在 plist 已存在时跳过写入，故**复验前必须 `capswriter uninstall` → `capswriter install`** 才能让新 plist 生效。 |
| **K：rpath 检测 4级→3级（误判重签致 TCC 失效）** | ✅ 代码，待复验 | 2026-06-25。`capswriter.py:_launcher_uses_relative_python_rpath()` 检测串写的是 `@executable_path/../../../../.venv/lib`（4 级），而 `build_launcher.sh` 实际产出 3 级 `@executable_path/../../../.venv/lib` → 永远判「未用相对 rpath」→ 每次 `install`/`doctor` 都误判需重建 launcher + ad-hoc 重签 → cdhash 变 → 辅助功能/输入监控 TCC 授权失效（每次 install 后权限都要重授）。修：检测串改 3 级，与 build_launcher.sh 严格一致。 |
| **L：双实例（系统面板拉起后 GUI 重启）** | 📋 **已知问题，文档化不修（2026-06-25 用户拍板）** | **取证已闭环**（日志/launchctl list 实锤），决定**不修**。现象：仅当「本轮 client 是被系统设置面板的『退出并重新打开』按钮拉起」时，再从菜单栏重启会出现两个 client 进程。**良性**：生效的是最新实例，旧的已失效，功能全正常，只是多个空转残留。正常菜单栏重启 / CLI 重启都不触发。**根因（实锤）**：LaunchServices 重新拉起 GUI app → 新进程被领养到动态标签 `application.com.capswriter.client.<ASN>`（脱离静态标签），此刻 `_client_pids()` 的 `pgrep` 短暂查不到该实例 → `_stop_client` 打印「客户端未在运行」一个没杀 → `cmd_start` 又拉一个 = 双实例（日志 00:37:01 实录）。这是 GUI app 同时被 launchd 与 LaunchServices 管理的固有所有权冲突。**用户否决「最小止血（单实例守卫）」**（理由：止不住且现象良性）。**对策**：①README 列已知问题 + 规避建议（授权后用 `capswriter restart`，勿点系统弹窗「退出并重新打开」；IM 条目缺失则点「+」手动添加）；②IM 通知文案已去掉「条目已就位」断言改为「+」手动添加兜底。**清理**：`capswriter restart` 可靠清成单实例。**架构层正解（未排期）**：路线 A——client 脱离 launchd 做成正常 GUI app + 启动自我单实例守卫（`NSRunningApplication`/`LSMultipleInstancesProhibited`）+ CLI/GUI 改为「对唯一实例下指令」，launchd 仅留给 server。 |
| **通知横幅图标** | 🔲 park | 破图，下个会话受控实验（`docs/bug-report-notification-icon.md`） |
| **D：孤儿进程（client 脱离 launchd）** | ✅ 代码+实测 | 根因：client 作为 NSApplication GUI app 被 LaunchServices 从 `com.capswriter.client` 标签**领养**到 `application.com.capswriter.client.<ASN>` 动态标签，`_launchctl_pid(原标签)`/`launchctl stop 原标签` 够不到 → stop 误判"未在运行" → 孤儿存活、start 再起一个 → 双实例。修法：`capswriter.py` 新增 `_client_pids()`（`pgrep -f` 按 .app 可执行文件路径查，**label-independent**）+ `_stop_client()`（launchctl stop 协调 KeepAlive + 按身份 SIGTERM 兜底 + 10s 后 SIGKILL）；stop/start/uninstall/status 全改走它。`restart` 实测：停旧 client→起单实例，无双图标 |
| **E：Caps 长按松手后麦克风卡住** | ✅ 代码+单测，待运行时复验 | 2026-06-22 修复。根因=`task.launch()` 开流（`start_recording_session()` 数百毫秒）期间 `is_recording` 仍为 False，松手 stop 被 `stop_press_to_talk` 丢弃→麦克风永不关闭。修法：`ShortcutTask` 引入 `_lifecycle_lock`+`_launching`+`_stop_pending`，补齐“录音启动中/待停止”语义：launch **锁外**开流（让 stop 能无阻塞登记 pending）、开流后进锁置 `is_recording=True` 并取出 pending，若启动期间已松手则末尾立即 `finish()`；新增线程安全入口 `request_finish()`（启动中登记 pending，否则按 is_recording 决定）；`finish()/cancel()` 改为锁内 check-and-set **幂等**；`stop_press_to_talk` 委托 `request_finish`。开流/关流被串行到同一线程，且“只要开流已开始，松手后必有可达关闭路径”。3 场景隔离单测通过（启动中松手/正常长按/重复 finish 幂等）。<br>—— 原记录：2026-06-19 13:09 复现。最终状态已收敛：**系统级麦克风指示持续亮起，说明麦克风被打开但没有被正确关闭；同时 `Caps` 接管、client 心跳、短按切换均正常。** 关键日志链路：`13:09:55.872` 长按成立并开始 `start_recording_session()`/`stream open requested`/`找到音频设备`；`13:09:56.301` 松手后进入 `stop_press_to_talk()`，但因 `task.is_recording` 尚未置真，被判定为“当前未在录音，忽略 stop_press_to_talk”。由此可知：① 音频流打开动作已经启动，所以系统看到麦克风在录；② 关闭流路径未执行，所以麦克风不会自动收掉；③ 本次并未进入稳定的 `recording=True -> begin/data/finish -> server 识别` 正常链路，server 侧也没有对应新任务，因此这次更接近“识别未真正触发”，而不是“识别后收尾失败”。根因归类：**start/stop 状态机竞态**，不是权限、event tap 或 server 断连问题。 |
| **F：launcher 硬编码 Python 路径** | ✅ 代码+构建验证 | 2026-06-22 修复用户反馈：旧 `CapsWriter.app/Contents/MacOS/CapsWriter` 由开发机编译，Mach-O 含 `LC_RPATH=/Users/edgar/.local/share/mise/.../lib` 且 C 字符串含 `PY_BASE_PREFIX=/Users/edgar/...`，换用户名后 dyld 在 main() 前找不到 `libpython3.13.dylib` 直接闪退。修法：`launcher_embed.c` 运行时读取 `.venv/capswriter-python-prefix`（缺失时退回 `pyvenv.cfg`）定位目标机器 Python base prefix；`build_launcher.sh` 在 `.venv/lib` 创建 `libpython3.13.dylib` 符号链接，并用 `@executable_path/../../../../.venv/lib` 相对 rpath 链接，移除编译期 `PY_BASE_PREFIX`；`install.sh` 负责自动创建/更新 `.venv`、安装依赖、重建 launcher、安装命令；`capswriter install/doctor` 增加旧 launcher 检测与自动重建提示。验证：`bash -n install.sh build_launcher.sh`、`python -m py_compile capswriter.py`、`bash build_launcher.sh`、`otool -L/-l` 显示 `@rpath/libpython3.13.dylib` + 相对 rpath，`strings CapsWriter... | rg '/Users/'` 无命中，`_launcher_rebuild_reason()` 返回 None。 |
| **P3：后端推理精度调优** | 🟡 进行中 | 聚焦 `qwen_asr_mlx`：核对 8bit/4bit 模型选择、上下文/热词能力缺口、音频前处理与解码参数差异，评估是否需要补齐能力或回退默认规格 |
| **权重 wiring + 启动预热** | 📝 已设计待实施 | 2026-06-22 敲定方案，**仅文档落地，未写代码**。`mx.set_wired_limit` 钉住权重防换出 + 一次性启动预热；server 可选项（默认开，可关）。**待 `mlx-qwen3-asr` fork 落成可编辑源码后，在其中层编排层实施**（不在适配层，避免重复搬迁）。详见决策表对应行 + `docs/macos-architecture-decisions.md` 第九节 |
| **P2：Unix socket 实时推送** | 🔲 待实施（GUI 阶段） | CLI 实时订阅 .app 事件流 |
| launchd 端到端测试 | 🔲 待测试 | 重启验证开机自启 |
| FFmpeg 路径确认 | 🔲 待确认 | launcher_embed 进程 PATH 是否含 FFmpeg |

---

## 重签名注意事项（开发期）

每次运行 `build_launcher.sh` 后可能因签名变化导致 Accessibility TCC 记录需要重新确认：
- 脚本不再自动 `tccutil reset`，也不主动打开系统设置，避免构建阶段修改用户权限状态。
- 启动时由现有权限引导流程处理辅助功能授权；必要时在列表中找到 CapsWriter → 关闭再打开，或删除后重启软件重新授权。
- 麦克风权限一般无需重置，除非录音全零才由用户手动执行 `tccutil reset Microphone com.capswriter.client`。

---

## 下一步工作

> **权限引导已于 2026-06-22 重写完成（四文件编译通过，待明日从零实测）**，最终设计见架构决策第六节『权限引导（2026-06-22 重订）』+ 决策表「权限引导（两权限）/ 权限恢复 UX」。调查过程留痕见 [`docs/macos-permission-investigation.md`](docs/macos-permission-investigation.md)，但**其中早期结论已被本轮收敛取代**，勿再据其行动：①健康判据采 **option C「单次启动校验 ping」**，非全程回调心跳，运行期仍用 `CGEventTapIsEnabled`；②`IOHIDCheckAccess` 经核查有官方文档、**不迁** CG API，仅降级为提示；③IM 条目注册靠「AX 就绪后补一次 tap 尝试」（request API 实测无效）；④稳定 DR 签名是**另一条独立根因线**，本轮未做。

| 优先级 | 任务 |
|--------|------|
| **P0：权限引导重写** | ✅ **2026-06-24 实测主路通过 + 二次收敛落地**。两权限都引导 + `run_guide` 状态机 + option C 启动校验 ping + 绝不退出进程 + **砍掉手动面板/stale 交给 reset-permissions**。剩余复验项见「实测进度」表 #5–9（重点：菜单栏绿点修复复验、运行中撤 AX 键盘恢复、stale/reset-permissions/perm_phase）。 |
| P0 | 用同一批音频样本对比 `qwen_asr_mlx` 与 Windows `qwen_asr` 路线，区分“量化差异”与“接入差异” |
| P0 | fork `mlx-qwen3-asr`，摆脱 `Session.transcribe()` 黑盒接法，优先夺回 prompt 组装、language/context 策略、generation config 的控制权 |
| P0（依赖上一行） | fork 落成可编辑源码后，在其中层编排层实施**权重 wiring（`mx.set_wired_limit`）+ 一次性启动预热**；wiring 做成 server 可选项（`Qwen3ASRMLXArgs`，默认开，README 写明可关）。方案见 `docs/macos-architecture-decisions.md` 第九节 |
| P0 | 复核并补齐 `qwen_asr_mlx` 当前缺失能力：服务端热词、解码参数、chunking 策略、aligner 接法 |
| P0 | ✅ 已修复 `Caps` 长按竞态（见看板任务 E）：覆盖“开流已开始但 `task.is_recording` 尚未置真时松手”的 stop 丢失场景，麦克风流必定被关闭。**待真机运行时复验** |
| P1 | 评估默认模型策略是否仍应保持“macOS 优先 8bit”，或改为可配置优先级 / 按机器回退 4bit |
| P1 | 在 fork 路线稳定后，再决定是否需要更下沉的 MLX 层改造；当前不投入产品级流式识别实现 |
| P2 | ✅ 菜单栏下拉菜单已落地（见任务 M8）。**待真机复验**：液态玻璃/深浅色观感、复制/编辑/重启/退出四动作的实际行为 |

---

## 实测进度（权限引导，2026-06-24）

> 前置（模拟从零）：`capswriter reset-permissions`（= 停 client + tccutil reset 两权限），或手动 `tccutil reset Accessibility/ListenEvent com.capswriter.client`。
> **重要教训**：测前务必 `capswriter restart` 让新代码生效——2026-06-24 首测因跑的是旧进程，误见已删掉的「加加减减面板」、通知文案不对，restart 后即正常。

| # | 测项 | 结果 |
|---|------|------|
| 1 | **丝滑首装一次重启**：从零 → 弹 AX 框 → 开 AX 开关 → 程序自动让 IM 条目出现 → 开 IM 开关 → 重启 → 可用 | ✅ 通过 |
| 2 | **option C 启动校验 ping**：合成 F18 ping 流过 HID tap 被回调收到，健康判活、不误判 stale | ✅ 通过 |
| 3 | **引导不弹手动面板 / 不说删条目**：刚注册新条目（开关未开）只提示「打开开关」 | ✅ 通过（restart 新代码后） |
| 4 | **通知文案正确**：撤 AX 后引导能正常提示「✅辅助功能已就绪」→「权限已就绪请重启」 | ✅ 通过 |
| 5 | **运行中撤辅助功能（系统设置拨开关）**：键盘 ~1s 恢复、进程不退出、无 13s 死循环 | 🟡 引导/通知已验，键盘恢复+不退出待再确认 |
| 6 | **菜单栏绿点不灭（任务 G）**：撤权后圆点应转红 | 🔲 **已修代码，待复验** |
| 7 | **stale（重签后）**：`build_launcher.sh` 重签 → start → 应通知「请运行 reset-permissions」 | 🔲 待测 |
| 8 | **reset-permissions 命令**：停 client + 清两权限 + 提示，之后 start 从零重走 | 🔲 待测 |
| 9 | **状态可见**：`perm_phase` 反映 probing/guide_ax/guide_im/ready（**已无 stale_manual**） | 🔲 待测 |
| 10 | **弹窗顺序（任务 I）**：从零启动应**先弹辅助功能窗**，AX 配好后才弹输入监控窗；首弹时 IM 条目不应提前出现 | 🔲 **已修代码，待复验** |

---

## 最近故障记录

### 2026-06-24：权限引导实测三个问题（旧进程残留 + 绿点不灭）

- 背景：权限引导 2026-06-22 重写后首次从零实测，并在测中做了 2026-06-24 二次收敛（砍手动面板）。
- 现象与定位：
  1. **拨开关后仍弹「加加减减权限面板」**：该面板文案在所有 `.py` 里已搜不到（确属已删）→ 判定**当时跑的是旧进程**（client 启动时间早于文件 mtime）。用户 `capswriter restart` 加载新代码后，面板消失、通知文案恢复正常。**教训：测前必 restart。**
  2. **只弹「已就绪」不提示重启**：同因旧进程；新代码 `run_guide` 阶段 4 会发「权限已就绪，请重启 CapsWriter」，restart 后验证正常。
  3. **撤辅助功能后键盘接管已失效，菜单栏圆点仍绿**（真 bug，见看板任务 G）：圆点读 `ErrorBus.state`，`result_processor` 只在连接状态变化时重算 state，运行中撤权走 `_handle_tap_failed` 只改了 `accessibility_ok` 没改 `state` → 停在 ready。已修：`_handle_tap_failed` 与 start() stale 分支改为 `eb.update(state='error', ...)`。**待复验。**

### 2026-06-24（续）：从零引导弹窗顺序错乱（输入监控抢先）

- 现象：从零启动 → **输入监控窗立刻弹**，几秒后才导航到辅助功能；AX 配好后又回到输入监控；且第一次弹窗时输入监控条目已在列表里。
- 根因（见看板任务 I）：`bridge.start()` 一上来就 `_listener.start()`→`_create_tap()`，而 `CGEventTapCreate` 本身会触发「输入监控」TCC 弹窗并注册 IM 条目——这一发抢在了 `run_guide` 的 AX 弹窗之前。
- 风险：用户顺手先开了输入监控就重启，辅助功能仍缺 → 行为不可预测。
- 修：`start()` 用只读 `check_accessibility()` 前置判断，AX 未就绪时不预创建 tap，直接进引导（AX 先弹），AX 就绪后才补 tap 尝试注册/弹 IM。**待复验。**

### 2026-06-19：Caps 长按竞态导致麦克风未关闭

- 复现时间：2026-06-19 13:09 左右。
- 用户侧最终现象：松手后系统仍持续显示麦克风开启；重启 client 后恢复。
- 当时仍然正常的部分：`Caps` 接管未丢，client 仍持续写心跳，短按 `Caps Lock` 仍能正常切换大小写。
- 明确异常的部分：麦克风占用未释放，没有走到正常的 stop/close 流程。
- client 关键日志序列：
  - `13:09:55.872` `[caps-controller] hold threshold reached, start recording`
  - `13:09:55.872` `[audio] stream open requested by recording session`
  - `13:09:55.874` `找到音频设备: MacBook Air麦克风, 声道数: 1`
  - `13:09:56.301` `[caps-controller] long press, stop recording`
  - `13:09:56.301` `[caps_lock] 当前未在录音，忽略 stop_press_to_talk`
- 由日志缺失反推的结论：
  - 没有看到本次对应的 `task.is_recording=True` / `录音状态已更新: recording=True`
  - 没有看到 `stream close requested by recording session end`
  - 没有看到 server 侧新增的本次麦克风任务日志
  - 因此可以判断：麦克风流开启动作已经开始，但录音状态尚未正式立起；松手 stop 被丢弃后，既没有正确关闭麦克风，也没有把完整录音送入识别链路
- 当前根因判断：
  - `core/client/shortcut/task.py` 中 `task.launch()` 先执行 `start_recording_session()` 打开音频流，再执行 `task.is_recording = True`
  - `core/client/shortcut/shortcut_manager.py` 中 `stop_press_to_talk()` 只有在 `task.is_recording` 已为真时才会真正 stop
  - 如果用户在“音频流已开始打开，但 `task.is_recording` 还没置真”的竞态窗口里松手，就会出现 stop 被忽略、麦克风未关闭、识别未真正启动的异常
- 后续修复方向：
  - 需要补齐“录音启动中 / 待停止”语义，或者把状态置位时机前移
  - 目标不是只修日志口径，而是保证：**只要麦克风打开动作已经开始，松手后就一定存在可达的关闭路径**

### 2026-06-22：输入监控未更新导致键盘接管 fatal 死循环

- 复现时间：2026-06-22 12:35 前后。
- 触发背景：先修复了 launcher rpath off-by-one（见任务 F 更新），client 已能正常启动；随后用户**仅在系统设置里更新了“辅助功能”，未更新“输入监控”**，重启 client。
- 用户侧最终现象：先提示“键盘接管未生效”，再提示“权限已配置，待重启”，重启后**依然不断循环**，键盘接管始终无法真正建立。
- 关键日志序列（每 ~13s 换一个新 PID：46652 → 46739 → 46817，循环往复）：
  - `[caps-remap] enabling CapsLock -> F18`（已写入 remap，Caps→F18 映射生效）
  - `[f18-listener] CGEventTap 创建失败，请确认已授权辅助功能（Accessibility）权限。`
  - `[caps-f18-bridge] CGEventTap 真故障，恢复键盘并引导用户重授权`
  - `[caps-remap] restoring original UserKeyMapping=[]`（恢复键盘）
  - 进程退出 → launchd `KeepAlive(SuccessfulExit=false)` 重新拉起 → 回到第一步
- 根因判断（用户确认）：
  - **CGEventTap 实际受“输入监控（Input Monitoring）”门控，而非仅“辅助功能”。** 本次只更新了辅助功能、输入监控未更新，所以 tap 创建持续失败。
  - 代码层把 tap 创建失败一律归为 fatal，fatal 路径会让**进程退出**，再被 launchd `KeepAlive` 拉起 → 形成 ~13s 一轮的死循环；用户无法跳出。
  - 日志文案只提“请确认已授权辅助功能”，**误导**用户只去开辅助功能，掩盖了真正缺失的输入监控。
- 与既有决策的冲突：
  - 此前（CLAUDE.md「权限恢复 UX / 决策表」与任务 M7.2/M7.3）已把输入监控**移出**自动引导链路，只在 CLI/通知里作人工提示。本次实测推翻该决策——输入监控是 CGEventTap 的硬门控，必须重新纳入引导。
- 已采取的临时动作：`capswriter stop` 止住循环（已确认无 client 进程）。
- 下一步（见「下一步工作」P0 最优先项）：重做权限引导，重新纳入输入监控门控；并消除 fatal→退出→KeepAlive 重启的死循环（退出前确认权限就绪，或改原地等待不退出）。
- 旁证（另一相关风险，非本次根因）：`build_launcher.sh` 重建会以 **ad-hoc 签名**重签（`Signature=adhoc`，cdhash 随构建变化），辅助功能 / 输入监控的 TCC 授权按 cdhash 绑定，**每次重建都会失效**。重做权限引导时需一并考虑“重签后授权失效”的口径（评估稳定自签名证书以让授权跨重建存活）。
