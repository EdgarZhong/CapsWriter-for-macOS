# CapsWriter macOS 架构决策记录

> 最后更新：2026-05-23
> 适用分支：mac-dev

---

## 一、进程架构

### 最终结构

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

> 注：client 因「辅助功能」权限变更被系统强退/重开，**不会**牵连 server——client 从不拉起 server，server 是独立 launchd agent，多连一个 client 也只是多一条 socket。真正的重复实例风险在 **client 侧**（孤儿/reparent，见任务看板），与 server 无关。

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
| `CGEventTapCreate == None` | 启动即无辅助功能权限 | fatal → 渐进权限引导 |
| `CGEventTapIsEnabled==False` 但「意图启用」 | **心跳体检黑盒判据**：系统在背后禁了 tap（撤辅助功能/超时/**回调死锁**） | `_go_fatal` |
| RunLoop 意外退出 | tap 被系统作废 | fatal |

> **实测关键纠正**：运行时撤销辅助功能，macOS 发的是 **`DisabledByTimeout`**，不是 `DisabledByUserInput`。旧代码在 timeout 分支盲目 re-enable 死 tap → 永久冻结、且只来一次 timeout 预算来不及升级、`_handle_tap_failed` 永不触发 → **既冻键盘又不弹任何提醒**。

### 自检 vs 外部体检：按「事件是否送达回调」分类

失效能否被**回调自己发现**，取决于有没有事件送到回调面前。**循环能对送达的事件自检，但无法观测自己的「没在执行」**（死锁的线程跑不了自检代码——电话坏了的人没法用这台电话报修）：

| 失效 | 有事件送达? | 谁来发现 |
|------|:---:|------|
| 撤辅助功能 | ✅ `DisabledByTimeout` | **回调自检**（`_on_timeout` 查 `AXIsProcessTrusted`/预算，打断 re-enable 死循环）——不需要任何外部线程 |
| 回调真死锁 | ❌ | 外部体检（但系统已自动禁 tap 兜底**不冻**，体检只做善后） |

**外部体检不是独立线程，而是复用已有的 5s 心跳**（`mic_runner._heartbeat_task` → `bridge.check_health()` → `listener.check_health()`）。理由：
- 「永不冻结」**不依赖**体检——由系统超时窗（约 1–2s，macOS 判定 tap 无响应即自动禁用，我们消不掉）+ `_on_timeout` 不盲目 re-enable 共同保证。体检只做**善后/检测**，非关键路径，故 5s 延迟无碍，**无需专用守护线程**（省一条线程，更优雅）。
- 体检**只读系统维护的黑盒事实**（`CGEventTapIsEnabled`），完全不信任回调/业务的自我汇报；即便回调彻底死锁也能发现并善后。
- 我们自己主动禁用时（fatal/stop）置 `_tap_should_be_enabled=False`，体检不误判。

### 自救（静默，不打扰用户）

- **timeout 且仍 trusted**：`CGEventTapEnable(tap, True)`，随后 `CGEventSourceKeyState(F18)` 对账物理键态；内存"按下"但物理"松开" → 丢了 keyUp → 补跑松开逻辑（停录音、清状态）。
- **自救带预算**：5s 内 timeout ≥3 次 → 升级 fatal。
- 自救全程**不动 remap、不弹窗、不发通知**。

### fatal（真故障）单路径 UX

任一 fatal 发生时，统一（`macos_caps_f18.py:_handle_tap_failed`，已在 TapFailedCallback 线程执行）：

1. **立刻恢复 hidutil remap**（Caps 变回普通键，消除"映射着但 tap 死了"的 limbo）；
2. `ErrorBus.update(accessibility_ok=False)` + 一条"键盘接管已暂停，正在引导你检查权限"通知；
3. 调 **渐进权限引导**（`macos_permission_guide.run_guide`，见下）——探测「辅助功能」真实状态，按 stale 与否分级引导；
4. **停**，不再静默轮询重建——等用户按指引把权限补齐后**重启客户端**。

**触发 fatal 的统一出口 `_go_fatal(reason)`**：① `CGEventTapEnable(tap, False)` 先放行键盘 → ② `_tap_should_be_enabled=False` → ③ `CFRunLoopStop` → `_run_loop_thread` 退出后调 `_handle_tap_unavailable` → `_handle_tap_failed`。三处调它：`_on_timeout`（失 trusted）、`DisabledByUserInput` 分支、守护线程（`CGEventTapIsEnabled==False`）。

### 权限引导：渐进探测式（2026-06-15 收敛，取代旧"单弹窗统一删除"）

**只引导「辅助功能」一项（2026-06-15 实测收敛，曾误判为双权限）：**

对这种**主动型**吞事件 tap（`kCGEventTapOptionDefault`，吞 F18 + 拦截全局 keyDown/keyUp），「**辅助功能** Accessibility」是**充分且唯一**的权限，「**输入监控** Input Monitoring」被它**蕴含**。干净环境下只授辅助功能 + 重启即可完全工作，「输入监控」列表里**根本不会出现 CapsWriter**。早先「输入监控也要」的观感，来自 dev 反复重签名留下的**失效旧记录** + 系统联动；单独引导它只会对着一个空列表干等 25s 误报「未授予」，纯属死胡同，**已整体删除**（`run_guide` / 就绪门控 / `check_health` 三处的输入监控分支均移除）。

> 「输入监控」`kTCCServiceListenEvent`（`IOHIDCheckAccess(ListenEvent)`）只对**被动监听型** tap（`kCGEventTapOptionListenOnly`）有意义；本项目用的是主动吞事件 tap，不走这条权限。

**仍要这一层引导的理由**（核心矛盾）：用户**肉眼分不清**「关着的有效条目」和「关着的失效条目（dev 重签名后 cdhash 对不上的旧记录）」——列表长一样、无时间戳。所以"该删还是该拨"**不能甩给用户判断**。

**渐进探测式**（`macos_permission_guide.run_guide`，单一权限）：

| 探测到的状态 | 动作 | API |
|------|------|-----|
| granted | 直接完成 | `AXIsProcessTrusted` |
| unknown（从没问过） | 触发**原生授权弹窗**（按当前签名新建有效记录，最干净） | `AXIsProcessTrustedWithOptions{prompt:True}` |
| denied（有记录但关着） | 开面板 + 提示"**先拨开关**" → 后台**轮询** | 轮询 `check_accessibility` |
| denied 且拨了仍不生效（超时） | 升级提示"**这是旧记录 → − 删除 → 重启 → 重新允许**" | osascript 弹窗 |

**关键**：用户任意时刻屏幕上只有一条明确指令；"拨开关到底生没生效"由 app 轮询判定，**用户不必自己诊断 stale**。先试最轻的「拨开关」（覆盖运行时撤权这种有效记录场景），只有轮询超时（≈25s）才升级到「删除+重启」（覆盖 dev 重签名的失效记录场景）。

### 为什么"拨不生效"就一定是旧记录

macOS TCC 授权记录**绑代码签名**（ad-hoc 签名绑 cdhash，每次重签名都变）：

- **拨开关**：保留同一条 TCC 记录、只翻转允许/拒绝，**仅当当前签名 == 记录里存的签名才生效**。签名变了（dev 重签名后）就是"假生效"——开关拨上了，运行进程读到的仍是拒绝。
- **「−」删除**：删掉记录，下次申请**重建一条绑当前签名的新记录**，永远有效。

所以"拨了开关、轮询 25s 仍探测不到授权" ≈ "这条记录的签名和当前二进制对不上" → 升级删除。这一判断由 app 完成，用户只需照做。

> 远期正式 release（稳定签名 + 装 /Applications）签名不再变，denied 永远靠拨开关即可生效，几乎不会走到"升级删除"分支。花钱注册 Apple 开发者账号获取稳定真签名是根治途径，**近远期均暂不考虑**。

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
