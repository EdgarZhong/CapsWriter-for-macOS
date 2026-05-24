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

## 六、Accessibility 权限失效引导

### 问题背景

程序无法区分两种失败场景：
- **首次未授权**：TCC 无记录，CGEventTapCreate 返回 nil
- **重签名后 csreq 失效**：TCC 有旧记录但 csreq 不匹配，CGEventTapCreate 同样返回 nil

### 统一引导方案

**不引导用户点「+」**：install.sh 场景下 .app 不在 /Applications，用户无法通过 Finder 找到。

触发失败后：
1. 自动打开系统设置 → 辅助功能
2. osascript 弹窗（**只弹一次**）：

```
CapsWriter 需要辅助功能权限

请在刚刚打开的「辅助功能」设置中：

• 若列表中已有 CapsWriter
  → 点「−」删除，稍等约 15 秒

• 若列表中没有 CapsWriter
  → 稍等片刻，它将自动出现

看到 CapsWriter 后开启右侧开关即可。
CapsWriter 将自动恢复，无需重启。
```

3. 后台每 **15s** 重试 `CGEventTapCreate`
4. CLI 持续输出状态，**成功前不退出**：

```
仍未授权，请在系统设置中完成操作... (15s 后重试)
仍未授权，请在系统设置中完成操作... (15s 后重试)
✓ 辅助功能权限已获取，CapsWriter 启动完成
```

5. 成功后发系统通知"辅助功能权限已恢复"

### 删除旧记录的必要性

macOS TCC 存储 csreq（代码需求 blob）。重签名后 csreq 改变，旧记录虽显示"已开启"但实际失效。删除旧记录后，下次 CGEventTapCreate 调用会触发 macOS 重新注册，用户开启开关后生效。

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
