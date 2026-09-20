# CapsWriter macOS 架构决策记录

> 最后更新：2026-09-20
> 适用分支：mac-dev
> 阅读边界：第零节为下一阶段已确认的目标架构，尚未实施；第一节及后续既有章节记录迁移前实现与历史决策，其中早期“最终结构/不采用”不约束第零节已确认的新方向。运行进展以 `../CLAUDE.md` 为准。

---

## 零、产品化目标架构：客户端主导编排与独立计算服务

### 已确认的产品与架构方向（2026-09-20 范围收敛）

现有 ASR 经最近几轮修复已初步达到产品级可用程度。本轮以可独立安装的 DMG 与新增 Dashboard 窗口的 UI/UX 重构为并列最高优先级。UI 重构仅限 Dashboard，灵动岛暂不实施；既有菜单栏、录音浮层和结果编辑框不纳入 UI/UX 重做范围。ASR 推理调优与新增助手能力不在本轮范围内，后续另行推进。客户端采用 Swift/SwiftUI + AppKit 原生产品壳，现有计算能力按服务边界保留合适语言的实现，首版继续复用 Python ASR 与热词算法。

**客户端是全部数据的起点、终点，以及人机交互核心状态机的唯一权威维护者。调用编排也在客户端，服务只负责各自的计算或功能。** 本轮讨论中的“可编排/插件化”指客户端能够自由组合不同服务，不要求建立统一插件底座、统一服务接口、后端编排器或通用流程语言。

| 职责 | 所属位置 | 边界 |
|---|---|---|
| 触发与数据采集 | 客户端 | 当前快捷键、录音、前台目标应用；未来选中区及其有无、Force Click、截图、剪贴板等入口均由客户端采集与管理 |
| 人机交互状态机 | 客户端 | 触发、采集中、处理中、取消、编辑确认、结果呈现及异常恢复的产品语义由客户端决定 |
| 服务调用与编排 | 客户端 | 决定调用哪个服务、传入哪些数据、调用顺序/并行关系、如何使用结果，以及超时/失败后的产品行为 |
| ASR 计算 | 独立 ASR 服务 | 首版复用现有 Python/MLX 基线；内部可以维护模型、缓存和请求状态，但不接管全局交互状态机 |
| 热词与别名处理 | 独立热词服务 | 将现有客户端 Python 逻辑摘出，保留音素匹配、别名、候选与现行替换行为，由客户端调用 |
| 未来 LLM 与其他服务 | 各自服务 | 采用适合任务的语言和接口，不因接入客户端而强制迁入同一种语言或插件框架 |
| 结果编辑、呈现与输出 | 客户端 | Dashboard、既有菜单栏/编辑框、剪贴板写入和向目标应用上屏；未来灵动岛暂不实施；计算服务不得自行向用户应用注入结果 |

```text
CapsWriter 原生客户端
  ├─ 触发/采集：快捷键、录音、选中区、Force Click、截图、剪贴板……
  ├─ 人机交互状态机 + 调用编排（客户端内）
  │    ├─ 调用 ASR 服务（现有 Python/MLX）
  │    ├─ 调用热词服务（现有 Python 算法）
  │    └─ 按需要调用 LLM 或其他服务（未来）
  └─ 结果编辑/呈现/上屏（客户端内）
```

图中未来采集入口与服务用于界定归属，不表示下一版必须全部实现。客户端可为各服务编写具体适配代码；协议、请求响应和连接方式按服务需要确定，不预设所有服务共用同一消息模型。客户端保留必要的任务关联、取消与过期结果识别能力；各服务内部如何执行不改变客户端的状态权威。

### 首版热词服务的范围

- 保留现有 Python、pypinyin、RapidFuzz 及当前处理逻辑；本次是服务化抽取，不做 Swift 算法移植，也不顺带改分词、阈值、模糊音权重或替换策略。
- 客户端负责词库的用户操作界面、发起更新和调用；服务负责现有词库加载/索引/匹配与计算结果返回。词库文件具体归属、更新接口与通信方式在服务抽取实施设计中确定，避免形成两份互相漂移的权威词库。
- 当前最关注的算法缺陷是**跨词边界的非语义替换**：相邻两个字原本不属于同一个词，却被音似匹配为一个替换目标。保留既有审计样本作为已知问题记录，优化办法以后另行讨论，不列为本轮服务抽取验收前置条件。
- 模糊匹配与别名是长期产品能力；与 ASR context-aware 的结合属于后续路线，仍由客户端组织相关服务调用，不在热词服务中建立全局编排器。

### 应用与分发边界

- 默认菜单栏常驻，Dashboard 按需打开，采用原生 macOS 侧栏与「概览、设置、词库、推理方案」四页；客户端负责权限引导与应用交互生命周期。
- 保持客户端与计算服务的进程隔离。目标是由应用提供自带本地服务的启停、状态检查与恢复入口，具体进程托管、登录自启和重连机制在实施前细化；不把“服务独立”解释为生命周期无人负责，也不把未来外部服务视为可由 App 任意启停的本地进程。
- DMG 中的 App 应包含所需运行时与依赖，用户无需克隆源码或安装 Python/uv/Homebrew。模型采用首次下载并支持本地导入，不随 DMG 打包；用户配置、词库及历史需在升级替换 App 后保留。
- 当前 launchd 双 agent 仍是实际运行架构。目标方案确认不等于已部署迁移；实施时必须验证旧安装的切换、单实例、权限、键盘映射恢复及核心听写行为，不能仅靠新 UI 宣称完成产品化。

### 下一阶段实施设计需细化的事项

1. Swift 客户端的模块与交互状态、各入口的数据生命周期；按现有听写功能先完成闭环。
2. 热词服务的具体调用与词库更新接口、调用开销，以及与原有处理链路的兼容验证。
3. 本地服务托管、应用重启/退出、服务崩溃恢复与旧 launchd 安装迁移。
4. DMG 自包含构建、签名公证、模型首次下载/本地导入及升级保留用户数据。

这些细节不改变上述已确认职责边界。具体阶段任务与实施状态只记入 `CLAUDE.md`；热词现状证据及后续算法议题见 `语音输入roadmap与桌面助手设计.md` 第二节。

---

### Dashboard 原生接入实施方案：状态读取层

**目标**：为新增 Dashboard 提供可验证的真实客户端状态，先建立 Swift 与现有 Python 快照格式的兼容边界。

**架构与技术栈**：macOS 13+、Swift Package Manager、Foundation；后续 SwiftUI 窗口消费 `DashboardCore`，此层不监听快捷键、不启动服务、不读取用户词库。原生客户端完整迁移仍按第零节职责推进，本阶段只读适配不等于迁移完成。

**规格**：本节遵循第零节应用与分发边界。执行按 `executing-plans` 逐项验证；完成状态只记录在 `CLAUDE.md`。

1. 新建 `native/CapsWriter/Package.swift`，提供 `DashboardCore` 库与 `DashboardCoreChecks`，最低 macOS 13，无外部依赖。
2. 新建 `Sources/DashboardCore/ClientSnapshot.swift`：`ClientSnapshot` 解码现有 `state/server_connected/accessibility_ok/microphone_ok/last_heartbeat/last_error`；`SnapshotReader.read(at:now:)` 返回状态，不向文件写入。当前 Python 时间戳无时区，必须按本机时区解析；允许未来带时区格式。快照超过 10 秒、时间在未来或无法解析时，禁止显示为可用。
3. 状态区分未运行、无法读取、状态过期、启动中、连接中、可用、录音中和错误；未知状态按错误处理，字段异常不静默伪装为正常。
4. 在 `Sources/DashboardCoreChecks/main.swift` 用合成 JSON 验证：Python 格式、断连、过期、未来时间、未知状态、缺失和损坏文件。使用内存合成夹具和不存在的临时路径，不触碰真实状态与权限。
5. 验证命令：`swift run --package-path native/CapsWriter DashboardCoreChecks`；文档链接、README 与 main 一致性及 `git diff --check` 同步验证。窗口范围已确认四页侧栏，模型交付已确认首次下载＋本地导入；后续在此边界展开接入。

### Dashboard 视觉规格

原生 macOS 26 Liquid Glass；玻璃用于侧栏与外观控件，内容采用清晰稳定的分层表面。复用 `assets/icon/app-icon.png` 和 `.icns`，不生成替代标志。侧栏保留原生导航、壁纸透色与键盘行为。外观提供跟随系统、浅色、深色，保存于独立开发 App 的 UserDefaults，不修改系统外观。

设计基准：冷蓝 `#397AC2`、冰蓝 `#D9E9FA`、浅表面 `#F5F7FA`、深表面 `#151B25`；文字与状态实际使用系统语义色以适配深浅色和对比度。标题用 SF Pro 系统标题字重，键帽用 SF Rounded，辅助数值用系统等宽字体。8/16/24/32 间距；单一视觉记忆点为 Caps Lock 键帽与装饰声纹，声纹不是实时音量计。减少透明度时使用不透明系统背景，不依赖动画表达状态。macOS 13–25 回退系统材质。

布局：原生侧栏（品牌 / 四页导航 / 外观选择）＋正文（紧凑页标题 / 听写状态与键帽 / 服务及权限 / 操作说明）。不用满屏玻璃卡片，避免大面积模糊影响可读性。以浅色与深色截图、原生选择行为、状态兼容回归和签名校验验收当前视觉迭代。

### Dashboard 窗口与开发包

`Sources/CapsWriterDashboard/CapsWriterDashboard.swift` 使用 SwiftUI 原生侧栏，四页分别为概览、设置、词库、推理方案。当前仅概览接入真实快照；其余页面明确显示尚未开放操作。窗口任务每两秒刷新状态，取消后停止；不通过快照修改客户端状态。

`tools/build_dashboard.sh` 编译上述 executable，生成 `build/CapsWriterDashboard.app`，Bundle ID 为 `com.capswriter.dashboard.dev`，与日常客户端分离。旧产物移入 `.archive/`，新包使用临时签名并执行严格签名校验。该开发包不含 Python/模型运行时，不作为完整 DMG 交付。

---

## 一、进程架构

### 迁移前结构（既有实现及历史设计）

```
用户
  ├── capswriter CLI（短命令，非常驻）
  │     ├── launchctl start/stop  ──────────────────→ launchd
  │     ├── 读 status.json（轮询快照）  ────────────→ ← ErrorBus 写入
  │     └── 订阅 Unix socket（实时事件）← GUI 阶段实现
  └── 菜单栏图标 NSStatusItem ← GUI 阶段实现
        ├── 直接订阅 ErrorBus（进程内，无需 socket）
        └── 点击 Quit → SIGTERM → .app cleanup → exit 0

launchd（OS 级，非用户代码）
  ├── CapsWriter.app/Contents/MacOS/CapsWriter  （client agent）
  │     ├── 主线程：NSApplication RunLoop
  │     │     ├── ErrorBus（统一报错出口，线程安全）
  │     │     │     ├── → log
  │     │     │     ├── → status.json（快照，供 CLI 轮询）
  │     │     │     ├── → 系统通知（同类 30s 去重）
  │     │     │     ├── → Unix socket 推送（供 CLI 实时订阅）← GUI 阶段实现
  │     │     │     └── → NSStatusItem 状态更新            ← GUI 阶段实现
  │     │     └── NSStatusItem（菜单栏图标）                ← GUI 阶段实现
  │     └── 子线程：asyncio → CapsWriterClient
  │             ├── MacOSCapsRemapSession ──────────┐
  │             ├── MacOSCapsF18Bridge → CGEventTap ┤
  │             ├── AudioRecorder ─────────────────┼──→ ErrorBus.report()
  │             ├── WebSocketManager → server:6016 ┤
  │             └── ResultProcessor → 剪贴板 / 上屏 ┘
  └── start_server.py（server agent，ASR 推理）
        └── qwen_asr_mlx → 端口 6016
```

### 关键决策

- **capswriterd 废弃**：不再有比 .app 更高层级的用户创建守护进程
- **launchd 直接管理两个独立 agent**：server 和 client 各自有独立 plist 和重启策略，互不依赖
- **launchd plist 策略**：`SuccessfulExit = false`——意图退出（exit 0）时 launchd 不重启，崩溃时自动重启
- **client 停止/查重必须按进程身份，不能只认 launchd 标签**（2026-06-15 修复 D 问题）

### client 被 LaunchServices「领养」→ 停止改按身份（D 问题根因与修复）

**现象**：`capswriter stop` 偶尔打印"客户端未在运行"却仍有 client 在跑；`restart` 后菜单栏出现**两个图标**。

**根因**：client 是 NSApplication GUI app（要菜单栏 NSStatusItem）。一旦它注册菜单栏/与 WindowServer 通信，**LaunchServices 会把这个进程从 `com.capswriter.client` 标签「领养」到 `application.com.capswriter.client.<ASN>` 动态标签**（PID 不变）。此后：
- `_launchctl_pid('com.capswriter.client')` 返回 `None` → `cmd_stop` 误判"未在运行"、跳过停止 → 旧实例存活；
- `cmd_start` 又经原标签起一个 → **双实例**。
- （注：`launcher_embed.c` 进程内跑 `Py_RunMain`、全程不 exec/fork，孤儿**不是**它自己派生的，纯粹是 LaunchServices 重新归属。）

**不采用的方向**：不为了好管而放弃菜单栏/NSApplication；领养是 GUI app 的固有行为，硬刚（阻止领养）脆弱。

**修复（`capswriter.py`）**：停止/查重一律**按可执行文件路径**识别，label-independent——
- `_client_pids()`：`pgrep -f <APP_EXECUTABLE>`，匹配任何标签下的全部 client 实例（含多个孤儿）；
- `_stop_client()`：① `launchctl stop 标签`（仍被原标签追踪时协调 KeepAlive 不重启）② 按身份 `SIGTERM` 兜底（client 借此恢复 hidutil remap）③ 10s 未退则 `SIGKILL`；
- `cmd_stop` / `cmd_start`（启动前查重）/ `cmd_uninstall` / `cmd_status`（多实例告警）全改走 `_client_pids()`。

### 明确不采用

- 不用 capswriterd 作为中间守护层
- 不让 .app 以子进程方式管理 server（解耦，避免 .app 成为进程管理器）
- 不注册三个 plist（只有 server + client 两个）

---

## 二、Server 生命周期自管理

server 不依赖外部守护，通过监听 client 的 WebSocket 连接状态管理自身生命周期：

| 情况 | server 行为 |
|------|------------|
| client 正常连接 | 正常运行 |
| client 断开（崩溃或重启） | 等待 60s，60s 内重连则继续 |
| 60s 内无重连 | 自行退出（exit 0），launchd 不重启 |
| `capswriter stop` | .app 通过 WebSocket 发 shutdown 信号 → server 立即退出（exit 0） |
| server 自身崩溃 | launchd 自动重启；client 通过 WebSocket 无限重试重连 |

**60s grace period 的意义**：避免 .app 崩溃后 launchd 重启（通常 <10s）导致 server 不必要地退出和重新加载 ML 模型（加载耗时 10-30s）。

### 单例守卫：端口自检必须前置到模型加载之前（2026-06-15）

单例靠端口绑定探测（`SocketManager._check_port` → bind `addr:port`）。**修复点**：原先该自检在 `socket_manager.start()` 里，而 `app.start()` 是先 `process_manager.start()`（拉子进程 + 等模型加载，吃内存）**才**到 socket 层——重复实例会**先把模型整个加载一遍**才发现端口冲突，叠加 launchd `KeepAlive` 会被无限重载。

收敛口径（`core/server/app.py`）：

- 端口自检**前置到 `app.start()` 开头、`process_manager.start()` 之前**；
- 端口被占 = 已有健康 server 在跑 → 本实例 `os._exit(0)`（**不是 exit 1**）。配合 plist `KeepAlive: SuccessfulExit=false`，launchd **不重启**它，旧 server 继续服务，重复实例静默死掉且**没加载过模型**。
- 删掉 `socket_manager.start()` 里端口冲突时的 `input("按回车键退出")`——launchd 下无 stdin 会抛 `EOFError` 崩溃，反被 `KeepAlive` 拉起无限重载；改为兜底 `os._exit(0)`（应对前置检查与真正 bind 之间的 TOCTOU 竞态）。
- **`_check_port` 必须设 `SO_REUSEADDR`**（与 `websockets.serve` 在 Unix 下默认 `reuse_address=True` 一致）：否则刚停的 server 留下的 **TIME_WAIT** 连接会让裸 bind 误报 `EADDRINUSE` → 守卫假阳性 → 新 server 秒退、`capswriter restart` 卡死在"等识别引擎就绪"。设了之后：真有活监听仍 bind 失败（两个活监听需 `SO_REUSEPORT`）→ 正确判占；仅 TIME_WAIT → bind 成功 → 正确判空。（2026-06-15 restart 卡死实测复现并修复）

> 注：client 因「输入监控」权限变更被系统强退/重开，**不会**牵连 server——client 从不拉起 server，server 是独立 launchd agent，多连一个 client 也只是多一条 socket。真正的重复实例风险在 **client 侧**（孤儿/reparent，见任务看板），与 server 无关。

---

## 三、用户心智与产品定位

### 用户永远不手动管理 server

- `capswriter start/stop/restart` 统一控制 server + client 两者
- server 对用户完全透明，不暴露为独立操作对象

### 分层暴露原则

| 层次 | 暴露程度 | 术语 |
|------|---------|------|
| 运维层（启停、重启） | 完全透明 | 只说"CapsWriter" |
| 故障层（错误信息） | 部分可见 | 用"识别引擎"指代 server |
| 操作指引 | 始终针对整体 | "重启 CapsWriter"，不说"重启识别引擎" |

### 发布形态路线

- **近期**：clone 仓库 + `install.sh` 一键安装
- **远期**：`.dmg` 安装包（用户拖入 /Applications）

---

## 四、错误提示架构

### ErrorBus（统一内部报错出口）

.app 内部所有子系统的错误统一经过 ErrorBus，由 ErrorBus 决定输出渠道：

```
子系统（WebSocket / CGEventTap / AudioRecorder / server 启动失败 / ...）
          ↓ report(error)
        ErrorBus（线程安全，asyncio.Queue + call_soon_threadsafe）
          ├── 写 log
          ├── 更新 status.json 快照
          ├── push 到 Unix socket（CLI 实时订阅）   ← GUI 阶段实现
          ├── 发系统通知（同类错误 30s 内去重）
          └── 更新菜单栏状态                        ← GUI 阶段实现
```

### 分阶段实现

| 阶段 | 实现内容 |
|------|---------|
| **当前（CLI 阶段）** | ErrorBus 内部架构 + 写 status.json + 系统通知 |
| **GUI 阶段** | Unix domain socket 实时推送 + 菜单栏状态更新 |

### status.json（统一数据源）

路径：`~/.capswriter/state/status.json`

```json
{
  "pid": 12345,
  "state": "ready",
  "server_connected": true,
  "accessibility_ok": true,
  "microphone_ok": true,
  "last_heartbeat": "2026-05-23T10:05:00",
  "last_error": null,
  "last_error_at": null
}
```

`state` 枚举：`starting` / `connecting` / `ready` / `recording` / `error`

- .app 写入（状态变化时 + 每 5s 心跳）
- 退出时删除文件
- CLI `capswriter status` 读取此文件（500ms 轮询，近期够用）

---

## 五、CLI 接口设计

### `capswriter start`

阻塞等待，实时输出，直到成功或明确失败：

```
正在启动 CapsWriter...
  ✓ 识别引擎已就绪
  ✓ 客户端已启动
  ✓ 辅助功能：已授权
CapsWriter 运行中
```

失败示例：
```
正在启动 CapsWriter...
  ✓ 识别引擎已就绪
  ✗ 辅助功能权限未授权，系统设置已打开
  ...（持续输出，见下节）
```

server 和 client 的启动错误均：① 回传 CLI 实时输出；② 发系统通知弹窗。

### `capswriter status`

读 status.json，显示当前状态快照：

```
CapsWriter for macOS  [就绪 ✓]
  识别引擎：已连接 (localhost:6016)
  辅助功能：已授权 ✓
  麦克风：已授权 ✓
  运行时长：12 分钟
```

未运行时：`CapsWriter 未运行，执行 capswriter start 启动`

### `capswriter doctor`

主动检查，不依赖 status.json，实时探测：
- 创建 CGEventTap 测试 Accessibility（成功即销毁）
- AVFoundation 查询麦克风授权状态
- TCP 连接测试 server:6016
- 检查 status.json 心跳新鲜度（> 10s 标记 stale）

每项输出 ✓ / ✗ + 具体修复指引。

---

## 六、键盘事件捕获：失败分类、自救与权限引导

> 本节为 2026-06-15 排查后收敛的口径，**取代**旧版"15s 静默自动恢复循环 + 无需重启"方案。
> 其中「权限引导」一小节经两轮重订：**2026-06-22**（丝滑首装 + tap 心跳真理裁决 + 防死循环铁律），再于 **2026-06-24** 二次收敛——**砍掉程序内的「统一手动指导面板」与运行期 stale 细分**，引导只说「打开开关 / 请重启」，stale 与一切疑难统一交给 `capswriter reset-permissions` 命令 + 文档兜底（见小节「2026-06-24 二次收敛」）。本节涉及「健康判据 / 撤权判据」的表述以「权限引导」小节为准。

### 背景：这块为什么娇贵

Caps Lock 经 hidutil 重映射到 F18，再用 **CGEventTap 主动拦截 F18** 并吞掉（防止 F18 在终端等吐 `^[[32~`）。该 tap 是 `kCGHIDEventTap` + `kCGHeadInsertEventTap` + `kCGEventTapOptionDefault`，**机制上只能按事件类型过滤**，所以必须订阅所有 keyDown/keyUp、再在回调里挑 keycode。回调跑 Python（受 GIL 约束）：

- **回调一旦阻塞 → 系统挂起全局键盘 → 冻结**（连 GUI 卡住）。这是本设计要根除的头号风险。
- 历史教训（本轮排查实证）：
  - 松开路径**同步**调用 `stop_recording` → 阻塞回调 → tap 超时被禁用 → 禁用窗口内**丢失 keyUp** → `_is_down` 永久卡在 True → 录音停不下、之后短按/长按/大写全失灵，只能重启客户端。
  - 撤权处理依赖"RunLoop 会自动退出"的**错误假设**（实际不退）→ `_on_tap_failed` 永不触发 → 静默失败、remap 不恢复、15s 循环没启动。

### 回调非阻塞铁律

tap 回调内**只允许**：判 keycode、吞掉 F18、向工作线程发信号。**绝不**在回调里跑业务（start/stop recording 一律甩到工作线程，与 `_on_hold_threshold` 已有做法对齐）。这是不冻键盘的根本前提。

### 永不冻结不变量（最高优先级，2026-06-15 实测重订）

**冻结的唯一根源**：`kCGEventTapOptionDefault` 主动 tap 是键盘事件的**必经卡点**。冻结 ⇔ 「一个**启用着**、但我们的进程没能及时服务的 tap」让系统把事件全扣在它那儿等。反之 **「禁用」的 tap 永不冻结任何东西**（事件直接透传）。

由此确立不变量（代码必须保证）：

1. **默认安全态 = tap 禁用 = 键盘 100% 正常**。tap 只在「证明健康（trusted）」时启用；任何不确定一律回落到禁用。
2. **业务与 tap 彻底解耦**：回调只入队（O(1)），录音/转写再慢也只堆队列，回调绝不等业务 → 「程序怎么接输入」永远冻不了键盘。
3. **走 fatal 第一动作 = 同步 `CGEventTapEnable(tap, False)` 放行键盘**，再做善后（恢复 remap / 引导）。即便善后自己卡住，键盘也早已通。
4. **绝不盲目 re-enable**。re-enable 是唯一危险操作，必须先确认 trusted。撤权时**系统已帮我们禁用了 tap（键盘本已通），旧代码却把它 re-enable 回去 → 这才冻死**——等于亲手拆了系统安全网。

### 失败分类（实测纠正）

| 事件 / 信号 | 真实含义 | 处理 |
|------|------|------|
| `DisabledByTimeout` + 仍 trusted | 偶发慢回调（回调已 O(1)，极罕见） | re-enable + 状态对账（**带预算 5s/3 次**） |
| `DisabledByTimeout` + **已失 trusted** | **运行时撤权的真实表现**（不是 `DisabledByUserInput`！） | `_go_fatal`：先禁 tap 再恢复 |
| 状态失稳（丢 keyUp，`_is_down` 卡死） | 内存态 ≠ 物理态 | `CGEventSourceKeyState(F18)` 对账，补跑 up |
| `DisabledByUserInput` | TCC 撤权（少数场景才走这条） | `_go_fatal` |
| `CGEventTapCreate == None` | 启动即无辅助功能/输入监控权限 | fatal → 权限引导（见 2026-06-22 重订小节） |
| **启动校验 ping 无回声**（start() 后 `_probe_alive` 超时） | tap 非空/enabled 却 stale 死（事件流经它却收不到） | 收掉 tap → `_handle_tap_failed` → 引导 |
| `CGEventTapIsEnabled==False` 但「意图启用」 | 系统在背后禁了 tap（撤辅助功能/超时/回调死锁）——运行期 5s 体检黑盒判据 | `_go_fatal` |
| RunLoop 意外退出 | tap 被系统作废 | fatal |

> **实测关键纠正**：运行时撤销辅助功能，macOS 发的是 **`DisabledByTimeout`**，不是 `DisabledByUserInput`。旧代码在 timeout 分支盲目 re-enable 死 tap → 永久冻结、且只来一次 timeout 预算来不及升级、`_handle_tap_failed` 永不触发 → **既冻键盘又不弹任何提醒**。

### 自检 vs 外部体检：按「事件是否送达回调」分类

失效能否被**回调自己发现**，取决于有没有事件送到回调面前。**循环能对送达的事件自检，但无法观测自己的「没在执行」**（死锁的线程跑不了自检代码——电话坏了的人没法用这台电话报修）：

| 失效 | 有事件送达? | 谁来发现 |
|------|:---:|------|
| 撤辅助功能 | ✅ `DisabledByTimeout` | **回调自检**（`_on_timeout` 查 `AXIsProcessTrusted`/预算，打断 re-enable 死循环）——不需要任何外部线程 |
| 回调真死锁 | ❌ | 外部体检（但系统已自动禁 tap 兜底**不冻**，体检只做善后） |
| 启动时 stale 死 tap（静默不冻） | ❌（事件流经它却收不到） | **单次启动校验 ping**（option C；不属运行期体检，见权限引导小节「覆盖边界」） |

**外部体检不是独立线程，而是复用已有的 5s 心跳**（`mic_runner._heartbeat_task` → `bridge.check_health()` → `listener.check_health()`）。理由：
- 「永不冻结」**不依赖**体检——由系统超时窗（约 1–2s，macOS 判定 tap 无响应即自动禁用，我们消不掉）+ `_on_timeout` 不盲目 re-enable 共同保证。体检只做**善后/检测**，非关键路径，故 5s 延迟无碍，**无需专用守护线程**（省一条线程，更优雅）。
- 运行期体检判据 = 便宜的 `CGEventTapIsEnabled`（系统维护的黑盒事实），覆盖「系统在背后禁了 tap」（撤辅助功能/超时/回调死锁）；**不**用 `IOHIDCheckAccess`（会被 stale 死 tap 骗）。**静默死的 stale tap（enabled 却收不到事件）由单次启动校验 ping 覆盖，运行期不再发 ping**（option C 取舍，见权限引导小节「覆盖边界」）。完全不信任回调/业务的自我汇报。
- 我们自己主动禁用时（fatal/stop）置 `_tap_should_be_enabled=False`，体检不误判。

### 自救（静默，不打扰用户）

- **timeout 且仍 trusted**：`CGEventTapEnable(tap, True)`，随后 `CGEventSourceKeyState(F18)` 对账物理键态；内存"按下"但物理"松开" → 丢了 keyUp → 补跑松开逻辑（停录音、清状态）。
- **自救带预算**：5s 内 timeout ≥3 次 → 升级 fatal。
- 自救全程**不动 remap、不弹窗、不发通知**。

### fatal（真故障）单路径 UX

任一 fatal 发生时，统一（`macos_caps_f18.py:_handle_tap_failed`，已在 TapFailedCallback 线程执行）：

1. **立刻恢复 hidutil remap**（Caps 变回普通键，消除"映射着但 tap 死了"的 limbo）；
2. `ErrorBus.update(accessibility_ok=False)` + 一条"键盘接管已暂停，正在引导你检查权限"通知；
3. 调 **权限引导状态机**（`macos_permission_guide`，见下文权限引导小节）——按权限探测决定引导哪一项（只说「打开开关 / 请重启」，**不再有程序内统一手动指导面板**；stale/疑难走 `capswriter reset-permissions`）；
4. **不自行退出进程**（杜绝 fatal→退出→KeepAlive 的 13s 死循环）：保持进程存活、原地轮询/引导，待用户按指引补齐权限并重启客户端后由全新会话重探重建。

**触发 fatal 的统一出口 `_go_fatal(reason)`**：① `CGEventTapEnable(tap, False)` 先放行键盘 → ② `_tap_should_be_enabled=False` → ③ `CFRunLoopStop` → `_run_loop_thread` 退出后调 `_handle_tap_unavailable` → `_handle_tap_failed`。三处调它：`_on_timeout`（失 trusted）、`DisabledByUserInput` 分支、守护线程（`CGEventTapIsEnabled==False`）。

### 权限引导：丝滑首装 + 统一文字指导（2026-06-22 重订）

> 本小节**取代** 2026-06-15「渐进探测式（app 替用户判断 stale）」与 2026-06-16「收敛为仅辅助功能」两版。重订动因：2026-06-22 实测 + 外部二次核查表明，旧版把"让条目出现"这件 macOS 上最脏的活硬塞给程序自动完成，导致自动逻辑与用户指导逻辑互相缠绕、条目出现时机不稳、并埋下 fatal→退出→KeepAlive 死循环。

**问题的根：把两件事搅成了一件。** 权限恢复本是两件独立的事——**(a) 让条目出现在列表里；(b) 用户把开关打开**。(b) 永远是用户的活，简单。**所有纠结（两次重启、自动/指导逻辑混杂、误报删除）都来自一个执念：想让程序替用户自动干 (a)。** 收敛口径：**只为"干净首装"这一条主路做自动注册，其它一切情况交给统一文字指导 + 用户判断（不把用户当傻子）。**

**产品决策：两个权限都引导。** 辅助功能 + 输入监控都纳入引导与门控。实测表明二者与 active tap 实时可用性的关系飘忽（受签名 stale 干扰），与其纠结"到底谁卡住"，不如两个都显式引导用户打开——最省心也最稳。

#### 工具箱（四象限最终 API）

| | 探测 | 操作（让条目出现 / 请求授权） | 唤起面板 |
|---|---|---|---|
| **辅助功能** | `AXIsProcessTrusted()`（只读 bool） | `AXIsProcessTrustedWithOptions({prompt:True})`（注册 AX 条目 + 原生框） | `...?Privacy_Accessibility` |
| **输入监控** | `IOHIDCheckAccess(ListenEvent)`（3 态，**仅作提示**） | **`CGEventTapCreate` 尝试**（唯一可靠的 IM 条目注册手段，前提 AX 已就绪） | `...?Privacy_ListenEvent` |

**真理裁决（不属任一权限）**：tap 是否真活，靠**事件能不能流过回调**判定——`CGEventTapIsEnabled` / `IOHIDCheckAccess` 都会被 cdhash 失效的 stale 死 tap 骗（句柄非空、enabled，却收不到任何事件）。

**单次启动校验 ping（option C，2026-06-22 定）**：`start()` 建好 tap 后发一发打标的合成 F18（`MacOSF18Listener._probe_alive`），看回调收不收得到——收到=真活→就绪；无回声=stale 死 tap→收掉它（放行键盘）+ **通知用户运行 `capswriter reset-permissions` 后重启**（2026-06-24 收敛：不再进程内自动引导/弹手动面板）。健康时这发 ping 被自己的回调吞掉，对 app 与录音零可见、不触发录音；死时它会泄漏一个 F18，但死 tap 下真实 Caps→F18 本就在泄漏，无妨。它的等待在调用线程、不在键盘热路径上，不增加冻结风险。

**覆盖边界（明牌取舍，已接受）**：合成 ping **只在启动打一发**；运行期 5s 体检仍用便宜的 `CGEventTapIsEnabled`，**不发 ping**（否则就成了被否掉的 option A）。因此：
- **启动时**的 stale / 静默死 tap：**覆盖 ✓**——stale 几乎总在启动时就已成立（cdhash 在重建/重签那刻就变，下次启动 tap 一建出来就是死的），启动 ping 正中靶心；
- **运行中途**才变成「事件流经它却静默收不到」：**不覆盖**（那是 option A 连续 ping 的活，已否）。可接受的理由：cdhash 在单进程生命周期内不变、不会跑着跑着 stale；运行中途撤辅助功能走 `DisabledByTimeout`（永不冻结网接住，非静默死）；「撤输入监控会否表现成静默死」本身无定论，且对这个改事件流的 tap，主权限更可能是辅助功能。真机若真撞上中途 stale，再升级到「空闲超时才补一发 ping」的轻量中间档。

**两条认知纠正**（外部二次核查得出）：
- `IOHIDCheckAccess` **有官方文档**（非未公开符号），故无需迁到 `CGPreflightListenEventAccess`；保留它，降级为提示性探测。
- IM 条目注册**不能靠** `IOHIDRequestAccess` / `CGRequestListenEventAccess`（基线实测无效）；**真正的载力手段是"在 AX 就绪前提下尝试创建 tap"**。逻辑反证：若"只有成功创建 tap 才注册条目"则死锁（没条目→开不了 IM→tap 永不成功→永无条目），故注册必发生在**尝试**这一刻；且**无 AX 时失败的尝试不注册**——这正是基线"开完辅助功能 IM 条目还出不来"的真因：它没在 AX 就绪后补一次 tap 尝试，就把用户导去了空的 IM 面板。

#### 三条逻辑规范

1. **分工是死的**：软件负责"触发注册 + 拉面板 + 轮询 + 决定说什么"；用户负责"拨开关 / 被明确告知时才动条目"。丝滑主路上软件自动注册，用户**不必点＋、不必去 Finder 找 app**。
2. **判断下一步只看两个信号**：权限探测（AX bool / IM 三态）+ tap 心跳。没有第三个输入。
3. **绝不因权限退出进程**：引导是进程内常驻状态机，原地等、原地恢复；杜绝 fatal→退出→KeepAlive 的 13s 死循环。

#### 状态机（每次启动从头跑，跨重启复跑同一条路径，无需记忆）

| 探测 | tap 心跳 | 判定 | 动作 |
|---|---|---|---|
| AX 未就绪 / IM 非 Granted（没条目 / 关着 / 刚注册） | —— | 缺授权 | 注册 + 拉面板 +「打开开关」**（绝不提剪条目）** |
| AX True 且 IM Granted | 活 | 就绪 | READY |
| AX True 且 IM Granted | **死** | **真 stale** | **通知用户运行 `capswriter reset-permissions` 后重启**（2026-06-24：不再程序内弹手动面板） |
| 运行中 tap 心跳死 | —— | 掉权 | 先恢复 remap 放行键盘 → 回到顶端重探（**不退出**） |

顺序铁律：**辅助功能先行**；AX 一旦探测到就绪，**立刻补一次 `CGEventTapCreate` 尝试**把 IM 条目注册出来，**确认条目出现后才打开 IM 面板**（修掉基线空面板 bug）。

> **实现要点（2026-06-24 实测修复）**：`CGEventTapCreate` 本身会触发「输入监控」TCC 弹窗并注册 IM 条目。若 `bridge.start()` 一上来就无条件 `_listener.start()`→`_create_tap()`，从零启动时**输入监控窗会抢在辅助功能窗之前弹出**（实测：IM 窗立刻弹、几秒后才导航到 AX、且首弹时 IM 条目已在列表里），违反「辅助功能先行」，用户顺手开了 IM 再重启则 AX 仍缺、行为不可预测。**修法**：`start()` 用只读的 `check_accessibility()`（`AXIsProcessTrusted`，无弹窗无副作用）前置判断——AX 未就绪时**不预创建 tap**，直接起线程跑 `_handle_tap_failed`→`run_guide`（AX 先弹）；待 AX 就绪后才由 `try_register_im`（`attempt_im_registration`）补一次 tap 尝试去注册/弹 IM。保证弹窗顺序恒为「辅助功能 → 输入监控」。

#### 防死循环铁律（最关键）

> **引导流程永不主动说「删/剪条目」，只说「打开开关 / 请重启」。** 一切需要动条目的操作（删 stale 旧记录、重新加回）都收口到 `capswriter reset-permissions` 命令，由用户显式触发，不由进程内引导自动判定。

为什么这能根除"注册→提示剪→剪+重启→又注册→又提示剪"的无限循环：引导分支里根本不存在「叫用户剪条目」这条出口——刚注册的新条目开关是关的（探测读作 not-granted），落「打开开关」分支即可；真 stale（探测全 granted 但 tap 心跳死）也不再进程内弹面板，而是通知用户跑 `reset-permissions`，命令会先停 client 再清干净两个 TCC 条目，下次 start 按当前 cdhash 重建有效记录，从零重走一次干净首装。

> 2026-06-24 收敛动因：旧版「程序内统一手动指导面板 + 运行期 stale 细分」让自动逻辑与用户指导逻辑互相缠绕、且 stale 误报风险高。砍掉面板、把 stale 兜底交给一条独立命令后，引导路径只剩「干净首装」一条主路，逻辑大幅简化。

#### 重启：一次为常态，偶发两次

- **干净首装 = 一次**：AX 弹框授权 → 程序当场探测到 AX 生效 → 立刻补 tap 尝试注册出 IM 条目 → 用户在同一次里**把两个开关一起打开** → 重启一次 → 可用。
- **偶发两次**：AX 授权后在当前进程**没当场生效**（macOS 偶发），IM 注册只能等重启后 AX 生效再做 → 第二轮重启。无需预判：每次重启都跑同一台状态机，能一次成就一次、不能就自动多走一轮，**不是两套逻辑**。

#### 2026-06-24 二次收敛：砍掉程序内手动指导面板，stale 兜底交给 `capswriter reset-permissions`

> 取代上面「统一手动指导面板」方案。动因：旧版把「凡需动条目就在进程内弹一段 +/− 指导文字」与「自动引导」混在一起，逻辑缠绕、stale 误报风险高，且 2026-06-24 实测发现旧进程残留时面板照弹、口径混乱。最终收敛为：**进程内引导只剩「干净首装」一条主路，永不弹手动指导面板、永不说删/剪条目**；一切需要动条目的疑难（stale 旧记录、重签后失效、运行期反复）统一交给一条独立命令。

**`capswriter reset-permissions`（实现见 `capswriter.py:cmd_reset_permissions`）**：

1. 先 `_stop_client()` 停掉正在运行的 client（避免 remap 残留，也避开「tccutil 撤运行中进程导致键盘冻结」那个非真实但有害的场景）；
2. `tccutil reset Accessibility com.capswriter.client` + `tccutil reset ListenEvent com.capswriter.client` 清干净两条 TCC 记录；
3. 提示用户 `capswriter start` 重新启动，按引导从零重走一次干净首装（新记录绑当前 cdhash，有效）。

**程序内何时引导用户用它**：① 启动校验 ping 判 stale（`macos_caps_f18.start()` stale 分支）；② 引导超时仍未生效（`run_guide` 阶段 1c）。两处都只发**通知**「请运行 capswriter reset-permissions 后重启」，不弹面板、不自动执行。

**README 同步**：「权限疑难排查」小节写明 `reset-permissions` 用法 + 手动兜底（自行去系统设置删条目重授权），覆盖命令不可用的极端情况。

> 设计取舍：把「动条目」这件最脏的活从「进程内自动判定」彻底剥离到「用户显式触发的命令」，是这轮简化的核心——根除了「注册→提示剪→剪→又注册」死循环的土壤，也让引导状态机只需处理「干净首装」一条主路。

#### 背景：stale 从哪来

macOS TCC 授权记录**绑代码签名**（ad-hoc 绑 cdhash，每次重签名都变）。拨开关只翻转同一条记录的允许/拒绝，**仅当当前签名 == 记录里的签名才生效**；dev 重签后 cdhash 变了就是"假生效"。「−」删除后下次注册会重建一条绑当前签名的新记录。**稳定签名（稳定 Designated Requirement）可根治 stale，但属另一条独立的线，近远期暂不投入。**

### 明确废弃

- **pynput 降级**：`_start_caps_lock_fallback` / `_start_f18_fallback`（均无人调用的死代码）+ controller 的 `direct_caps_mode`（永远 False）——是被否的"被动监听 Caps + IOKit 撤销"B 方案残骸，**删除**。
- **15s 静默自动恢复循环** + "无需重启"文案：被上面的 fatal 单路径取代。

---

## 七、连接状态变化通知

每次 WebSocket 连接状态发生变化，发系统通知：

| 事件 | 通知内容 |
|------|---------|
| 冷启动连接成功 | "识别引擎已连接，CapsWriter 就绪" |
| 冷启动连接失败 | "识别引擎未就绪，等待连接中" |
| 运行中断连 | "识别引擎连接断开" |
| 重连成功 | "识别引擎已重新连接" |

通知去重：同一状态 30s 内不重复发送。

### 通知后端：UNUserNotificationCenter（2026-06-15）

通知投递从 `osascript display notification`（被系统归属给"脚本编辑器"，图标是卷轴）改为
现代 **`UNUserNotificationCenter`**，以 CapsWriter 自身身份发送。实现见 `core/client/error_bus.py`：

- 优先 UN；进程无 bundle 身份（脱离 .app 裸跑调试）时回退 osascript，保证通知不丢。
- **安全闸**：调用 `currentNotificationCenter()` 前先用 `NSBundle.mainBundle().bundleIdentifier()`
  判断——裸跑时该调用会在 `dispatch_once` 块内抛 ObjC 异常**直接 abort**（`try/except` 拦不住）。
- 实际投递路径写入 `~/.capswriter/logs/notify.log` 便于确证。

**已知问题（已 park）**：launchd 启动的 agent 进程，UN 通知**横幅左侧图标显示为破图**
（设置面板、Finder、权限弹窗的图标均正常）。详细排查与下一步实验见
[`docs/bug-report-notification-icon.md`](bug-report-notification-icon.md)。大概率正式 release
（稳定签名 + /Applications）自动解决，留待后续会话处理。

---

## 八、实施顺序

| 优先级 | 任务 |
|--------|------|
| **P0** | 诊断 client 38s 崩溃（改进 exception logging，查 DiagnosticReports） |
| **P1** | 废弃 capswriterd，改写两个独立 launchd plist |
| **P1** | server 生命周期自管理（WebSocket 断连计时，60s 后 exit 0） |
| **P1** | ErrorBus 基础框架 + status.json 写入 |
| **P1** | CLI 改进：start 阻塞等待、status 读状态文件、doctor 对齐 |
| **P1** | Accessibility 引导对话框（osascript 分支文案 + 15s 重试） |
| **P2.5** | 菜单栏图标（静态 SF Symbols `waveform`，不随状态变化，避免与麦克风胶囊重合）+ 菜单项：📋 复制最近结果 / ✨ 编辑热词（open hot.txt）/ Quit；LLM 相关推后 |
| **P2.5** | Unix socket 实时推送（配合菜单栏 GUI 实现） |
| P2 | `capswriter install` launchd 端到端测试（重启验证） |
| P2 | FFmpeg 路径确认 |

---

## 九、模型常驻内存与启动预热

### 两种行为（2026-09-19 用户确认）

`config_server.py::Qwen3ASRMLXArgs.enable_wired_memory` 是唯一常驻开关，修改后重启server生效：

| 开关 | 运行行为 | 代价与边界 |
|------|----------|------------|
| `False` | 不调用`set_wired_limit`、`mlock`或其它常驻设置；允许macOS正常压缩、换出 | 用户接受长时间不用后首次转录可能要重新调页 |
| `True`（默认） | package Runner对全部真实模型权重页执行`mlock`，持有至cleanup/进程退出 | 约2.46GB权重不可换出；极端内存压力下整体推理竞争变慢仍可能发生 |

启动预热独立于常驻开关，默认开启一次静音转录以支付首次kernel编译成本。没有定时保活推理，也不按瞬时内存压力猜测模型是否“热”。常驻承诺只覆盖模型权重；系统调度、GPU功耗恢复、临时张量和磁盘读取等其它开销不等于零。

### 旧结论复核与修正

- 7月6日只验证`set_wired_limit`调用成功及预算约2.96GB，不能据此认定实际权重永久驻留。
- 9月前轮对照发现先设限再加载能看到约2.4GB wired增量，但仅调`_startup_initialize_runtime`内部顺序也太晚：Session构造已经加载并eval权重。
- 本机MLX 0.31.2对应源码中，`ResidencySet::resize`会补入已有buffer，也会在降限时移出buffer；因此“不追溯”和“降低额度不会撤pin”不是通用机制。
- 9月19日补验发现：提前设限、预热、收口并提交一次微型GPU运算后，系统wired从约3.04GB升至5.46GB，但空闲10秒又降至3.03GB。两阶段预算或一次GPU提交不足以满足长期不可换出的要求。该测量是本机现象，不宣称已定位所有Metal/驱动内部原因。
- 旧文档“macOS不支持mlock”错误。Apple文档明确保证成功`mlock`的页保持物理驻留；本机无需sudo即可锁住MLX原始共享buffer。MLX buffer协议直接导出`a.data<void>()`，不需要复制模型。

### 实现与生命周期

1. Runner创建Session并按开关预热后，计算锁页预算：`auto = min(active * 1.2, 总内存 * 0.6, recommended_working_set * 0.9)`。`wired_memory_limit`保留原字段名，支持auto/整数字节/`3g`等；现在表示实际权重锁页的上限，不再设置Metal额度。
2. `mlx_qwen3_asr/wired_memory.py::LockedModelWeights`读取全部`model.parameters()`原始buffer；用字节视图兼容bfloat16，按系统页对齐、合并重叠/相邻范围，只锁权重、不锁临时KV/cache。
3. 锁页对象保留buffer引用，保证原地址有效。完整权重超预算、非连续buffer或原生调用失败时回滚，拒绝仅锁部分权重。开启常驻但失败时引擎启动报错；接受换出时应显式关闭开关，不能静默降级为可换出模式。
4. cleanup逐段munlock，重复调用安全；解锁失败保留范围和引用以便重试。finalizer兜底释放遗忘的锁，进程退出时系统回收锁页。
5. server只透传意图并记录`method=mlock`、`locked_bytes`及预算；原生调用和页范围策略全部归package。关闭开关从新进程生效，不是运行期热切换。

### 验证入口与验收边界

- 单测：`PYTHONPATH=mlx-qwen3-asr .venv/bin/python -m pytest mlx-qwen3-asr/tests/test_capswriter_runner.py mlx-qwen3-asr/tests/test_wired_memory.py -q`。
- 真机：分别运行`.venv/bin/python tools/probe_qwen_residency.py --mode on --idle-seconds 1800`与`--mode off --idle-seconds 1800`。脚本独立加载当前模型，用固定语音比较空闲前后正文、权重地址和耗时；空闲期间只观察vm_stat，不唤醒GPU，也不人为制造极端内存压力。两模式应顺序运行以免互相争用。
- 不能只读API成功或刚转录后的瞬时wired峰值；至少观察空闲跨越10秒后的持续增量，并在保留模型引用时解锁确认增量撤销。系统wired是全局指标，会受其它进程影响，应结合成功锁页的页数和地址不变证据判断。
- 当前实际完成的观察时长与验收结果记录在`CLAUDE.md`，不能以几分钟实验声称数小时日常使用已验收。

参考：[Apple mlock手册](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/mlock.2.html)、[MLX 0.31.2 buffer协议](https://github.com/ml-explore/mlx/blob/v0.31.2/python/src/buffer.h)、[MLX 0.31.2驻留集合](https://github.com/ml-explore/mlx/blob/v0.31.2/mlx/backend/metal/resident.cpp)。


## 十、客户端麦克风生命周期

### 交互与边界（2026-09-18 用户确认）

- 保留短按切换大小写、长按 200ms 才开始占用麦克风、松手结束；本轮不通过提前开麦或缩短判定阈值换取速度。
- 录音设备选择由 `config_client.py` 的 `macos_mic_device` 决定（2026-09-19 起）：`'default'`（发布默认，普适）跟随系统当前默认输入，每次开流前刷新设备表（本机实测约 0.6ms）——默认输入切换后旧设备仍可能正常打开却录错麦，这种静默错误不能仅依赖开流失败来刷新；`'builtin'` 优先内建麦克风，找不到时刷新设备表重找、仍无则回退默认输入。
- 启动/停止必须配对；任务业务结束与底层句柄已释放是两个状态，不得仅凭 `recording=False` 或 `state.stream=None` 宣称关闭成功。

### 实现约束

1. 长按控制器在同一业务队列按顺序执行开始/结束，迟到的旧定时器以按压代号拒绝；退出或键盘接管失效时撤销未执行启动并结束在途录音。
2. 任务在 begin、状态与消费者发布完毕前持续处于启动中；松手/取消登记后再收尾。每次录音使用独立队列，开始、音频、结束均由事件循环按投递顺序入队；迟到音频保留原队列，不污染下一条。
3. macOS 采用 48kHz float32、20ms 块和 `latency='low'`；其它平台保持 50ms 与原默认延迟。实时回调只复制和投递，能量统计、日志和 trace 更新在普通事件循环执行，避免原生关闭等待被日志阻塞的回调。
4. 设备表刷新策略随 `macos_mic_device`：`default` 模式每次开流前刷新（代价约 0.6ms，为正确性必需），`builtin` 模式快速路径复用已初始化设备列表、不再反复 terminate/dlopen；开流失败且旧资源已释放时最多刷新设备表后重试一次。刷新使用 terminate/initialize，不 dlclose 动态库。
5. 关闭先请求回调主动退出，最多给 120ms 等待窗口，再在普通线程执行 `abort(ignore_errors=False)` 与 `close(ignore_errors=False)`。错误码不得静默忽略。正常关闭成功才释放资源归属；最长同步等待 1 秒，超时或错误保留故障流引用，禁止重开/重载。超时后原关闭若成功，下一次录音可继续。
6. 原生 finished callback 只发信号；意外结束后的重开在回调之外执行，并用流代号拒绝过期恢复请求。不得从原生回调栈关闭或重建正在回调的流。
7. 初始缓存跨过录音阈值时，缓存与当前音频块一并发送/保存，不能固定遗漏跨阈值的那一块。

PortAudio 对实时回调的阻塞/重入限制见 [官方回调说明](https://portaudio.com/docs/v19-doxydocs/writing_a_callback.html)。具体采集启停语义见 [官方生命周期说明](https://portaudio.com/docs/v19-doxydocs/start_stop_abort.html)。

### 验证与恢复边界

- 隔离验证：`tools/test_stream_stop_leak.py` 覆盖严格关闭错误码、超时资源归属、单次重试、迟到音频、分块兼容与缓存完整性；`tools/test_mic_shortcut_lifecycle.py` 覆盖正常松手的启动空窗、幂等结束、取消、退出以及初始化异常。
- 模拟测试不能证明 CoreAudio 偶发卡死已根治。Python 无法安全强杀卡在原生库里的线程；持续关闭失败仍需重启客户端释放进程资源。未来若要求此类故障完全自动恢复，需单独评估音频进程隔离及麦克风归属/权限成本。
- 延迟日志拆分 `device_ms / construct_ms / start_ms / total_ms`，关闭记录 `callback_exit / abort / close` 阶段；不能只看旧版统一 `stream.close()` 超时文字推断真实卡点。
