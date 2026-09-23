# CapsWriter-Offline 当前迭代看板

> 更新：2026-09-23 · 工作分支：`mac-dev`
> 本轮聚焦客户端产品化：Dashboard UI/UX 重构与 DMG 分发并列最高优先级。被压缩的历史流水备份于 `.archive/CLAUDE-20260923-pre-cleanup.md` 及更早归档。

## 目标全景（详版见 docs/语音输入roadmap与桌面助手设计.md）

- **远期形态**：从语音输入工具升级为本地常驻桌面语音 LLM 助手（问答/翻译/改写/转录二次处理）；主文本脑 MiniCPM5-2B 4bit MLX + 视觉脑 LFM2.5-VL-3B；灵动岛交互保留设想、暂不实施。
- **后端推理三条腿**：① Qwen Context 路线（当前基线，个性化上下文，v0 先做输入框文本提取）；② FireRed AED 路线（sherpa-onnx INT8 跑 CPU，目标常驻省约 1GB；精度≥Qwen 且内存显著下降才替换主后端）；③ Native Streaming 路线（stable-only、append-only，最后做）。伪流式已明确排除。
- **硬约束**：16GB 无风扇 MacBook Air；ASR 与常驻 ~2B LLM 共存是内存预算根源。推理侧前置工程排序：冻结 Qwen baseline → 极简评测 harness → FireRed 实验 → Context v0 → Streaming。
- **近期产品化**：DMG 单 App 用户旅程（下载→拖入→权限/模型引导→听写）；客户端主导架构（Swift App 即客户端 runtime 终局，采集/交互状态机/编排/呈现全部原生归 App；ASR 是唯一独立推理服务，未来 LLM 另起独立 WS 服务，不建统一 Engine）；热词不服务化，保留 Python 算法、以 App 托管 private helper 形态存在；模型首次下载+本地导入。最高口径见 `docs/macos-产品化架构定稿.md`。

## 三项任务与优先级

| 优先级 | 事项 | 本轮范围与完成定义 | 当前状态 |
|---|---|---|---|
| P0 | Dashboard UI/UX 重构 | 仅限新增 Dashboard 窗口；承载设置、词库与推理方案管理入口，原生侧栏五页 | 五页与桥接已接入；外观仅剩顶栏渐进模糊活跃（见下） |
| P0 | .dmg 一键安装包 | 自包含 App 与运行时；安装、权限/模型引导、服务生命周期、旧安装迁移验证；用户无需克隆源码或装 Python/uv/Homebrew | 待实施设计 |
| 后续 | ASR 推理精度调优 | 不在本轮范围，保留现有基线 | 暂缓 |

## 客户端重构范围收敛（2026-09-23，用户原始口径）

用户确认的客户端重构剩余任务全景：
1. 界面规格：转录历史页、热词管理页、推理方案页、概览页多处需要调整——**由用户另行给口径或交其他 coding agent 整体实施，本线不主动排期**。
2. 服务端生命周期收归客户端：原生 .app 全面掌控服务端生命周期；废弃 CLI 操控 launchd 的方案，或把 CLI 桥接到客户端二进制。
3. 客户端配置重构：客户端不再读配置文件，客户端范畴全部划归原生 .app 本体；服务端各计算服务的配置可继续用 py 文件。
4. 设置页重设计为面向用户的控制面：分板块（纯客户端/App、后端推理引擎、客户端热词算法、服务端 context），高频易调前置，不照搬 py config，部分设置不暴露、废弃项删除；只以当前基线后端（Qwen3-ASR）为考虑对象，板块职责清晰后再按路线图扩展。
5. 标注系统保留：编辑框为 PyObjC/AppKit 实现（`core/client/output/edit_panel.py`，非 SwiftUI）；v2 数据层（`core/client/output/annotation_store.py` → `evals/manual_cases/v2/`）已与 UI 解耦，Dashboard 历史页经 `tools/dashboard_history.py` 共用同一数据契约。随 Swift 客户端迁移时 UI 直译重写、数据契约不变；前置是把 v2 标注语义固化为 docs 规格页。
6. 单一 App 形态：Dashboard 并入客户端本体 .app（现有 Dashboard.app 与客户端 .app 两个并存是错误形态）；平时菜单栏常驻，菜单栏提供"打开 Dashboard"入口；仅当 Dashboard 窗口打开时 App 才出现在 Dock，窗口关闭即退出 Dock（activation policy 动态切换）。
7. 架构分层定稿（2026-09-23 二次澄清，最高口径见 `docs/macos-产品化架构定稿.md`）：**Swift App 即客户端 runtime 终局**，不再保留 Python headless client；快捷键状态机、音频采集、ASR WS client、session/result pipeline、焦点管理、剪贴板、Paste、编辑框、输入链 annotation 采集全部迁 Swift。ASR 是唯一独立推理服务（WS 6016）；未来本地 LLM 另起独立 process + WS，不建统一 Engine。**热词不服务化**：作为 App 托管的 private Python helper（stdin/stdout JSON，不监听端口、不做 health check、无独立生命周期），算法不动。文本上屏唯一路线 Paste（NSPasteboard + CGEvent Cmd+V，禁多 fallback）；焦点策略（真 AX FocusAnchor，失效禁上屏+通知）与剪贴板保留策略（changeCount 安全恢复）为正交双设置。结果数据保留 raw_text / corrected_text / final_text / hotword_matches 四段。
8. 施工口径（2026-09-23）：不做过渡方案；施工期间现有运行中的客户端/服务端保持运行，只改代码不动进程；用户提供干净提交基线后开工。范围经二次澄清后显著扩大（整个客户端 runtime 迁移进入本轮），不再承诺当天完成。设置页详细条目清单另行专项修订。

执行状态：标注系统探查与设计已完成（2026-09-23）；生命周期收归与配置重构的探查已完成；架构分层定稿经 2026-09-23 二次澄清后落档于 `docs/macos-产品化架构定稿.md`（Swift App 即客户端终局、热词不服务化、Paste 唯一上屏路线），判断准则已沉淀进 `AGENTS.md`，施工待干净基线。文档卫生收尾（2026-09-23）：`macos-permission-investigation.md`、`bug-report-macos-mic-indicator.md`、`github_outreach_draft.md`、`Python 正则表达式.md` 及 `autonomous-runs/`、`superpowers/` 已移入 `docs/archived/`；上游 5 份中文用户文档按用户指示保持不动；命名保持 `readme.md` + `README.dev.md` 现状（大小写不敏感 APFS 上 `README.md` 与 `readme.md` 无法共存）。

## 本轮产品化集成工作路线（2026-09-23 定稿，架构细则见 `docs/macos-产品化架构定稿.md`）

**目标形态**：单一 CapsWriter.app（Swift 原生）为最高控制层级，且 **App 自身就是客户端 runtime 终局**——不再保留 Python headless client。菜单栏常驻，菜单栏提供"打开 Dashboard"入口；Dashboard 窗口打开时 App 才进 Dock，关闭即退出（activation policy `.accessory` ↔ `.regular` 动态切换）。首次打开引导（Dashboard 先起、概览页承担部分引导）属 UX 细节，后续打磨，不在本轮。

**进程拓扑**（终局）：

```text
CapsWriter.app [Swift]
├── Native Client Runtime：ShortcutEngine / AudioEngine(AVAudioEngine) /
│   ASR WS Client / Session+Result Pipeline / FocusManager(真 AX FocusAnchor) /
│   PasteInjector(唯一 Paste 路线) / ClipboardPolicy(changeCount 安全恢复) /
│   Editor(编辑框 Swift/AppKit 重写) / Annotation Capture / Dashboard / MenuBar
├── Private Hotword Helper [Python 暂留，stdin/stdout JSON，无端口不服务化]
└──── WS 6016 ──► ASR Python Process（MANAGED=1 托管）
```

**通信**：ASR 走 WS 6016 协议不变；热词 helper 走 stdin/stdout JSON（**不用 WS/端口**，无独立 health check）；旁路状态文件（status.json 等）在 Swift runtime 接管相应职能后即退役。

**快捷键产品语义**：设置页只给预设（长按 Caps Lock / 长按 Fn → press-to-talk；Ctrl+Fn → toggle recording），不开放自由录制；底层抽象为 `pressToTalk`/`toggleRecording` 行为；Caps→F18→hidutil 成熟机制本轮可复用，ownership 归 ShortcutEngine。

**配置**：客户端域归 App 本体，不再读配置文件；服务端 `config_server.py` 不动；废弃字段（4 个 `macos_caps_remap_*` 孤儿、`paste_apps`、双端 `enable_tray`、`llm_*`、Windows 引擎参数字段）实施时从 config 本体清除。

**旧物处置（不做过渡）**：Python client 主体退役归档（快捷键/录音/上屏/编辑框职能迁完即摘）；launchd 双 agent 退役（切换时用户手动停旧进程）；`capswriter` CLI 的 launchctl 控制面删除；`capswriterd.py`、`core/client/launcher/` 归档；C 启动器版 CapsWriter.app 构建链废止。

**施工约束**：现有运行中的客户端/服务端保持运行，只改代码不动进程；开发用独立 bundle ID；不碰词库文件（避免触发运行中客户端 watchdog 热加载）；6016 被旧 server 占用期间，新 App 可直接连接现有 server 联调；托管拉起与完整切换待用户停旧进程后验收。范围较上一轮澄清显著扩大（整个客户端 runtime 迁移进入本轮），排期以实际推进为准，不再承诺当天完成。

**施工顺序（用户给干净提交基线后开工）**：
1. 单一主 App 骨架：`native/CapsWriter` 菜单栏 + Dashboard WindowGroup 合并 + Dock 策略切换；构建脚本输出统一 CapsWriter.app（开发 bundle ID）。
2. ServiceManager（Swift）托管 ASR server：服务描述符、`posix_spawn` 拉起、端口就绪探测（6016 可连≡模型就绪）、崩溃重启退避、退出收尾；`start_server.py` 支持 `MANAGED=1`（关 60s 断连自退）。
3. Swift 客户端 runtime 迁移（本轮主体工程，建议按此子序推进）：ASR WS client → AudioEngine 录音 → ShortcutEngine 状态机 → Session/Result Pipeline（四段结果保留）→ FocusManager + PasteInjector + ClipboardPolicy → 编辑框迁移（v2 数据契约不变）→ 输入链 annotation 采集接入。
4. 热词 private helper：现有 Python 算法原样包进 helper，stdin/stdout JSON 协议，随 App 启停；词库文件归属与更新路径在实施时定死，避免双权威词库。
5. 配置归 App 收尾：菜单栏五项职能落地（状态/编辑框开关/打开 Dashboard/重启服务/退出）；`dashboard_settings.py` 客户端半侧改为写 App 配置（服务端半侧保留）。
6. 旧物清理：Python client 主体、CLI launchctl 面、死代码与旧 .app 构建链按上文归档废止。
7. 验证：`bash tools/build_dashboard.sh` + `swift run --package-path native/CapsWriter DashboardCoreChecks`；dev bundle 连现有 server 联调；迁移职能逐项真机验收。

**紧随其后的下一轮**：模型首次下载/本地导入引导与首次打开引导 UX；自包含 DMG 与用户数据目录迁 Application Support；设置页详细条目专项修订（分板块：纯客户端/App、后端推理引擎、客户端热词算法、服务端 context）；界面四页规格调整（用户另行安排）；标注 v2 语义规格页固化；CLI 薄桥接进 App 二进制（可选）。Dashboard 顶栏渐进模糊暂停，待用户回来处理。

## 已确认的实施边界

- 平台基线：只支持 Apple Silicon（arm64）与 macOS 26.0+；Dashboard 构建固定 CLT 26.6、Swift/SwiftPM 6.3.x、macOS 26.5 SDK；不维护旧系统回退。
- Dashboard 只支持普通窗口；外观仅保留右上角自动/浅色/深色三档原生分段控件。
- 窗口材质唯一视觉口径：`docs/dashboard-窗口材质分层规格.md`。
- 转录历史：查找、便捷复制、编辑；编辑结果按现有编辑框标注语义进入 eval 库，不另建标注标准。
- 模型下载默认 Hugging Face 国内镜像 `https://hf-mirror.com`；`HF_ENDPOINT` 保留为覆盖入口。推理方案确认为 Qwen3-ASR 1.7B-8bit 与 1.7B-4bit 两个。
- 概览按真实服务、权限与资源状态显示，仅全部就绪才显示"准备好听你说话"；推理方案页顶部刷新重新检测资源。
- UI 重构只做新增 Dashboard 窗口；灵动岛暂不实施，不借本轮重做菜单栏、录音浮层或结果编辑框的 UI/UX。
- 热词抽取复用现有 Python 实现，保留模糊匹配、别名、算法、阈值和替换行为；不移植 Swift，不顺带优化跨词误替换。
- 当前运行实现仍为 Python 客户端与 launchd 双 agent；目标架构已确认，不代表 Swift 客户端或自包含 DMG 已完成。

## 本轮执行状态

### Dashboard 已完成项（2026-09-20 ~ 09-21，简记）

- 五页导航（概览/设置/词库/推理方案/转录历史）+ 真实状态概览 + 现有 App Icon + 持久化外观切换；设置经 `tools/dashboard_settings.py`（白名单+备份）读写 `config_*_local.py`，词库经 `tools/dashboard_vocabulary.py`，历史经 `tools/dashboard_history.py`（最近 200 条，修订追加 eval v2）；`tools/build_dashboard.sh` 出独立开发包。
- 外观跟随系统修复：仅主 Dashboard 三档 Picker 写 `NSApp.appearance`，跟随系统写 `nil`；权限窗口已解绑。全屏能力禁用（`.fullScreenNone` + macOS 26 官方 `windowFullScreenBehavior(.disabled)`，AX 验证为 zoom button，提交 `bc1d342`）。
- 工具链事故与恢复：CLT 27/Swift 6.4/SDK 27 曾产生非等价产物；已恢复 CLT 26.6 + Swift 6.3.3 + SDK 26.5（基线提交 `aae1247`，旧混合 `.build` 留存于 `native/CapsWriter/.build-before-clt26-restore`）。默认窗口 1000×700pt、侧栏 ideal 212pt。
- 材质分层：透明 NSWindow + 整窗 `.sidebar` 背板保留（`DashboardWindowBackdrop` / `DashboardSurfaceStyle.backdropMaterial`，用户确认观感不变）；挖孔、`holeWidth`、侧栏额外 `.popover` 已撤回；标题用官方 `toolbar(removing: .title)`，侧栏按钮单一 `sidebarVisibility` 绑定；macOS 26 用 `ToolbarSpacer(.flexible)` 把外观 Picker 推至 trailing。
- 资源修正：`models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit/` 混入的 4bit 元数据已按官方 8bit 仓库替换（备份 `.archive/qwen3-asr-8bit-metadata-20260920-110246/`）。
- 验证：5 组 Swift 状态兼容 + 9 项就绪检查通过；构建与开发包签名校验通过；深浅色窗口截图复测通过。未改个人配置/词库/日常服务；开发窗口独立 Bundle ID。侧栏切换时系统日志有非崩溃 `Invalid view geometry`，本轮不修，留后续 UI 专项。

### 活跃：顶栏渐进模糊（唯一进行中的外观项）

- 视觉口径：顶栏总高 52pt，底部 24pt 内从无模糊过渡到最大模糊，纯模糊无色带。
- **已提交稳定基线（2026-09-23，`cecc735`）**：52pt 顶栏、24pt 渐变、30pt 最大模糊；滤镜挂 NSSplitView detail 上方、mask 用窗口坐标。22:42 归档基准（半径 64）完整保留于 `.archive/dashboard-build-20260920-224413-88977/`；半径 256 未通过验收，不得作基线。探索历史、失败方案与待验证假设见 `docs/dashboard-顶栏渐进模糊排查记录.md`。
- 2026-09-23 运行时取证仅完成第 1 步，原始 140 行 dump 保存在 `.archive/dashboard-titlebar-runtime-20260923/runtime-trace.txt`：`makeNSView`、`updateNSView`、`viewDidMoveToWindow` 均执行；滤镜确实是 `NSSplitView` 子视图，但该实例 `arrangesAllSubviews=false`、`arrangedCount=2`、`backdropIsArranged=false`，因此当前滤镜不是分栏 pane。窗口坐标中滤镜为 `(0,648,1000,52)`，侧栏列为 `(0,0,198,700)`，系统标题栏容器为 `(0,648,1000,52)`。临时诊断源码已撤回并重建；未实施挂载层、mask 缓存、clear tail 或半径调整。
- 用户最新反馈（待处理）：24pt 渐变的渐进感不明显（疑似渐变区太窄）；Max Blur 强度偏低希望更高；"36pt 会触发下半区透明"的疑问待解释。
- 桌面限制：已授权 computer use 操作普通窗口验收；禁止全屏/大面积覆盖窗口。
- 侧栏更透暂缓；减少透明度仅完成代码接入、未实机验收。

### 待办

- **本轮主线**：按「本轮产品化集成工作路线」施工（单一 App 骨架 + ServiceManager 托管 ASR + Swift 客户端 runtime 迁移 + 热词 private helper + 配置归 App + 旧物清理）。**基线已形成（2026-09-23：`cecc735` 外观基线 + `fc3e75c` 文档提交），grill 收敛完毕（`.grill/capswriter-2026-09-23-productization-autonomous-scope.md`）：本轮范围=阶段 A 能力边界；验收分三阶段（A 纯自主 / B 晚饭无人值守真实系统能力，dev server 用 6016、用户离开前停旧 server / C 用户配合）；词库 Swift 直读写 + helper mtime 热重载。** 阶段 A 施工计划已写 `docs/plans/2026-09-23-产品化架构集成-阶段A.md`（未提交）；10 个 worktree 已建（`../CapsWriter-Offline-wt-T1`~`T10`，分支 `wt/T1`~`wt/T10`，均在 `fc3e75c`）。T2/T3 子 agent 曾分派后被用户暂停，已停止、零产出、各 worktree 干净。**下一步：用户确认计划文档 → 分派 T1-T10（T2/T3 可直接续跑）→ 主会话 review 合并与接线集成 → 整包验证并产出阶段 B/C 验收计划书。**
- 模型首次下载/本地导入引导；自包含 DMG 与用户数据目录迁 Application Support；首次打开引导（Dashboard 先起、概览页承担引导）UX 打磨。
- 设置页详细条目清单：另行专项，与用户逐条修订。
- 界面规格四页（转录历史/热词管理/推理方案/概览）调整：等用户另行给口径，本线不主动排期。首次打开引导（Dashboard 先起、概览页承担引导）同属后续 UX 打磨。
