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

## 九、模型常驻内存（MLX 权重 wiring）与启动预热

> 状态：**方案已敲定，待实施**（2026-06-22）。实施位置取决于 `mlx-qwen3-asr` fork 落地，见本节「实施顺序与归属」。在 fork 成为可编辑源码之前**不动代码**。

### 要解决的问题与边界

- **要解决**：`qwen_asr_mlx` 在 macOS 上**偶发的首次识别延迟极高**（实测可达 ~2 分钟，见自动记忆 `project_perf_memory_pressure`）。基本可确定是**权重被换出**所致：MLX 权重在 Apple Silicon 上走 Metal 统一内存（可分页），空闲或内存压力期间会被 macOS 压缩 / 换出到 swapfile；再次推理时这些权重要先被搬回物理内存，这段搬运开销就是那次高延迟。
- **不在范围内**：系统内存正被别的高占用程序激烈争用时，推理本身变慢——那是真实资源不足，本方案不承诺解决。
- 说明：每次推理都必然跑一遍前向传播（这是基线，不是问题来源）；MLX 首次还有一次性的图 / kernel 编译开销，由「启动预热」单独覆盖，与本问题无关。

### 为什么不做「检测模型是否还热」

曾设想「录音开始发信号预热 + 判断是否热则跳过」。结论是**放弃检测**：系统内存管理是黑盒，RSS（混入临时张量，无法隔离权重）、瞬时内存压力（驱逐是粘性的、压力是瞬时的，会漏判）、粘性压力标志（仍漏 idle page-out）等任何"推断权重此刻在不在 RAM"的信号都是猜。改为下面的「直接钉住」，从源头消除换出，无需检测。

### 最终方案：不检测，直接「钉住」——`mx.set_wired_limit`

与其检测驱逐后补救，不如**从源头让权重不可被换出**。MLX 原生提供 `mlx.core.set_wired_limit(limit_bytes)`（**macOS 15.0+**；本机 Darwin 25 = macOS 26，满足）：

- 它告诉 Metal 驱动**允许把多少字节钉成 wired 内存** —— wired = 常驻物理 RAM，**永不被分页 / 压缩 / 换出**；返回旧值，默认 `0`（不钉）。
- 补充事实：**macOS 不支持 `mlock`/`mlockall`**，POSIX 路线走不通；`set_wired_limit` 是 Metal 原生唯一正道。

落地三件事（**wiring 与预热解耦**）：

| 做什么 | 时机 | 作用 | 是否受开关控制 |
|--------|------|------|----------------|
| ① 一次性启动预热（空跑一次静音推理） | 模型 load 之后 | 付清 MLX 惰性图 / Metal kernel 的**一次性编译**成本，让首次真实识别更快 | **否**，始终做（与"赖内存"无关） |
| ② wire 住权重（`set_wired_limit`） | 预热之后 | 权重常驻物理内存，**根治换出导致的冷启动延迟** | **是**，server 可选项，默认开 |

自适应定大小（不写死 GB）：

```python
# 伪代码：预热后读取实际占用，再据系统上限保守钉住
active = mx.get_active_memory()                              # ≈ 模型实际占用（1.7B-8bit ~2GB）
cap    = mx.metal.device_info()["max_recommended_working_set_size"]
wired  = min(int(active * 1.2), int(cap * 0.6))             # 贴合模型大小 + 余量，且远低于系统上限
mx.set_wired_limit(wired)                                    # 仅 macOS 15+；hasattr + try/except 兜底，绝不致命
```

### 配置开关（server 可选项）

- **位置**：收敛在 MLX 引擎参数 `Qwen3ASRMLXArgs`（`config_server.py`），因为这是 **Metal 特性，其他后端没有**；通过 `EngineFactory` 透传进 `ASREngineConfig`。
- **默认**：**开启**。
- **语义**：仅控制第②步 wiring；关闭后权重恢复为可被系统正常换出的普通缓冲（启动预热仍照常做）。
- **为什么必须可关（用户口径，2026-06-22 敲定）**：wired 内存不可换出，会**长期占住约 2GB+ 物理内存**。当用户要运行别的近乎占满内存的高占用软件时，我们不应"死赖在内存里"，须允许其释放这部分常驻占用。

### 诚实的边界与风险

1. `set_wired_limit` 是**上限 / 许可**，不是逐 buffer 的 pin；worker 内只此一个模型、额度又卡在模型大小附近，效果等价于"钉住这个模型"，这正是官方推荐用法。
2. **wired 不可换出**，设太大（社区警告勿用默认的 ~75% RAM）会饿死系统甚至 kernel panic；故双保险卡在 `active*1.2` 与 `cap*0.6`，只锁模型那点。
3. 只锁 ~2GB **远低于** `max_recommended_working_set_size`，**不需要 `sudo sysctl iogpu.wired_limit_mb`**，不动系统配置。
4. **8GB 小内存机**：锁 2GB 占比偏高，更凸显"可关"的必要性。
5. 仅 macOS 15.0+ 有该 API：低版本 / 非 macOS 走 `hasattr` + `try/except` 静默跳过，不致命。

### 实施顺序与归属（为什么现在不写代码）

wiring + 启动预热属于**中层推理编排**，与「MLX 后端演进路线」决策一致——主路线是 **fork `mlx-qwen3-asr`、接管 `Session` 这层**（prompt / language / generation / chunking / aligner）。若现在改在适配层 `core/server/engines/qwen_asr_mlx/asr_engine.py`，待 fork 落成可编辑源码后会**重复搬迁**，并可能与后续调优改动打架。故定：

1. **现在**：仅本文档记录决策（已完成）。
2. **用户**：fork `mlx-qwen3-asr`，落成可编辑源码（editable install / vendored）。
3. **之后**：在 fork 基础上实施 ①启动预热 ②wiring；server 配置开关（`Qwen3ASRMLXArgs`，默认开）+ README 配置入口同步落地。

参考来源：
[MLX `set_wired_limit` 文档](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_wired_limit.html)、
[Metal — MLX 文档](https://ml-explore.github.io/mlx/build/html/python/metal.html)、
[What 19 GB of Memory Compression Taught Me About MLX on M1 Max](https://dev.to/sleepyquant/what-19-gb-of-memory-compression-taught-me-about-mlx-on-m1-max-3eha)。
