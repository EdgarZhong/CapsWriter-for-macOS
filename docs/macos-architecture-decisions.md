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

### 失败分类（判据：re-enable 同一个 tap 能否恢复）

| 事件 | 含义 | 能否自救 | 处理 |
|------|------|:---:|------|
| `DisabledByTimeout` | 回调太慢被系统临时禁用 | ✅ | re-enable + 状态对账（**带预算**） |
| 状态失稳（丢 keyUp，`_is_down` 卡死） | 内存态 ≠ 物理态 | ✅ | `CGEventSourceKeyState(F18)` 对账，补跑 up |
| `DisabledByUserInput` | 运行中 TCC 撤权 | ❌ | fatal → 走用户单路径 |
| `CGEventTapCreate == None` | 启动即无辅助功能权限 | ❌ | fatal → 走用户单路径 |
| RunLoop 意外退出 | tap 被系统作废 | ❌ | fatal → 走用户单路径 |

### 自救（静默，不打扰用户）

- **timeout**：`CGEventTapEnable(tap, True)`，随后用 `CGEventSourceKeyState(kCGEventSourceStateHIDSystemState, F18)` 查物理键态，与 `_is_down` 对账；若内存"按下"但物理"松开" → 丢了 keyUp → 补跑松开逻辑（停录音、清状态）。
- **自救带预算**：短时间内 timeout 反复（约定 **5s 内 ≥3 次**）→ 判定非偶发，**升级为 fatal**，避免无限静默打转。
- 自救全程**不动 remap、不弹窗、不发通知**。

### fatal（真故障）单路径 UX

任一 fatal 发生时，统一：

1. **立刻恢复 hidutil remap**（Caps 变回普通键，消除"映射着但 tap 死了"的 limbo）；
2. `ErrorBus.update(accessibility_ok=False)`；
3. 打开系统设置 → 辅助功能，并弹**唯一一个**通用引导弹窗（见下）；
4. **停**，不再静默轮询重建——等用户按指引重授权后**重启客户端**。

**`DisabledByUserInput` 修复点**：回调收到该事件时主动 `CFRunLoopStop(self._run_loop)`，让 RunLoop 真正退出 → 触发既有 `_on_tap_failed` 链路（不再靠"自动退出"的错误假设）。

### 单一弹窗文案（不分叉、重启式）

```
CapsWriter 失去了辅助功能权限，已暂停键盘接管。

请在刚打开的「辅助功能」设置中：
若列表里有 CapsWriter，点「−」删除它；
然后重启 CapsWriter，按提示重新授权即可。
```

### 为什么统一用"−删除"而不是"拨开关"

macOS TCC 授权记录**绑代码签名**（ad-hoc 签名绑 cdhash，每次重签名都变）：

- **拨开关（关→开）**：保留同一条 TCC 记录、只翻转允许/拒绝，**仅当当前签名 == 记录里存的签名才有效**——是个**会失败的子集**。
- **「−」删除**：删掉记录，下次申请**重建一条绑当前签名的新记录**，**永远有效**——是个**无害的超集**（即便拨开关本可成功，删了重授也一定成）。

**决策**：dev 阶段（频繁 ad-hoc 重签名）统一指导"**−删除（若有）+ 重启 + 重授权**"，一条指令覆盖"首次未授权 / 重签名失配 / 运行中撤权"全部场景，**零分叉、用户不用判断**。代价仅"纯撤权场景多删一次"，无害。

> 远期正式 release（稳定签名 + 装 /Applications）签名不再变，可简化为"重新拨开关即可"，届时再改文案。花钱注册 Apple 开发者账号获取稳定真签名是根治途径，**近远期均暂不考虑**。

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
