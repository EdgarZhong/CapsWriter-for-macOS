# CapsWriter-Offline 当前阶段同步

## 当前目标

- 分支：`mac-dev`，基线：`master`
- 目标：为 macOS / Apple Silicon 新增 `qwen_asr_mlx` 后端，并在不大改现有 Client / Server 架构的前提下，实现稳定的 Caps Lock 长按录音、最终结果返回、剪贴板写入和可选自动上屏体验。
- 当前阶段重点已经从“能跑通”转向：
  - 后台生命周期管理
  - macOS client 稳定性
  - remap 快照持久化与恢复
  - 上屏权限处理
  - 日志接线
  - 规格文档收敛

---

## 当前总体架构决策

### 最终运行架构

```text
launchd
  └─ capswriterd
       ├─ server process
       │    └─ start_server.py / qwen_asr_mlx
       └─ client process
            └─ start_client.py / core.client.main
                 └─ MacOSCapsRemapManager
```

### 用户交互入口

用户只通过一个命令控制整体软件：

```bash
capswriter
```

首版命令集合：

```bash
capswriter install
capswriter uninstall
capswriter start
capswriter stop
capswriter restart
capswriter status
capswriter doctor
```

remap 诊断 / 救援命令：

```bash
capswriter remap status
capswriter remap restore
capswriter remap clear --force
```

不提供：

日志查看命令和 remap repair 命令不属于本阶段用户交互范围。

### Ownership 规则

```text
launchd 只负责拉起 capswriterd。
capswriterd 只负责管理 server/client 生命周期。
client 自己负责 Caps remap 生命周期。
server 只负责 ASR。
```

### 明确不采用

- 不把 `MacOSCapsSupervisor` 作为正式架构路径。
- 不让 controller 直接接管 Caps remap。
- 不注册两个 launchd plist 分别启动 server/client。
- 不要求用户分别打开两个命令行窗口启动 server/client。
- 不提供单独的日志查看命令。
- 不提供 remap repair 命令。

---

## 当前实际路径

当前已有链路大致为：

```text
start_client.py
  └─ core.client.main
       └─ MacOSCapsF18Bridge（监听 F18 事件）
            └─ MacOSCapsController（短按 / 长按分发）
                 ├─ 短按 → synthesize_caps_lock_toggle()【待修复】
                 └─ 长按 → ShortcutManager → ShortcutTask
                             └─ AudioStreamManager（按需开流）
                                  └─ AudioRecorder / WebSocketManager / ResultProcessor
                                       └─ paste_text()【待修复：剪贴板必写，粘贴只尝试一次】
```

服务端：

```text
start_server.py
  └─ qwen_asr_mlx
```

后续正式后台路径应收敛为：

```text
capswriter CLI
  └─ IPC / state
       └─ capswriterd
            ├─ server process
            └─ client process
```

---

## 当前阶段决策

| 决策 | 内容 |
|------|------|
| macOS 后端 | `qwen_asr_mlx`，不替换 Windows 的 `qwen_asr_gguf` |
| 首版结果模式 | 松开后快速返回最终结果，不做中间流式显示 |
| 上屏策略 | macOS：必先写剪贴板，只尝试自动粘贴一次，不重试，不因失败阻塞主流程 |
| 权限口径 | Accessibility 权限仅用于自动粘贴 |
| macOS GUI | 初版不要任何 GUI；tray / toast / Tkinter 弹窗全禁 |
| 模型优先级 | 默认 `Qwen3-ASR-1.7B-8bit`，本地回退 `1.7B-4bit` |
| remap ownership | client 是 Caps remap 的唯一生命周期 owner |
| remap 持久化 | client 启动前保存 original UserKeyMapping 快照，退出或手动 restore 时恢复 |
| client 运行期 remap 规则 | client 运行时独占接管 Caps Lock -> F18；不支持用户此时手动修改 Caps 源映射 |
| 总生命周期 | `capswriterd` 是整体软件单例控制器 |
| 自启动 | 只注册 `capswriterd` |
| 用户入口 | 只暴露 `capswriter` 命令 |
| 配置策略 | Python 配置修改后通过 `capswriter restart` 生效 |
| 热词策略 | 保留 TXT 热词热重载；macOS 不强依赖 GUI |
| 日志策略 | 沿用主仓库既有日志；确认 server/client 接线，并让新增 controller 接入日志 |

---

## 任务看板

| 任务 | 状态 | 说明 |
|------|------|------|
| 服务端 `qwen_asr_mlx` 接入 | 已完成 | 真实启动 + 真实音频闭环验证通过，非 16kHz 重采样兜底已补 |
| 客户端 macOS 输入链路 | 已完成 | 全链路端到端桌面验证通过：长按录音、松手返回结果、剪贴板写入、自动粘贴上屏均正常 |
| `macos_caps_remap.py` 重写 | 已完成 | 完整 schema、atomic write、restore/clear --force 保护、日志接入 client logger |
| macOS 上屏 | 已完成 | osascript 失败只记 warning；剪贴板保留结果；授权辅助功能后自动粘贴上屏验证通过 |
| 短按 Caps Lock 补发 | 已完成 | 改用 IOKit `IOHIDSetModifierLockState` 直接切换状态，不受 hidutil remap 影响，桌面验证通过 |
| 修复 macOS GUI 崩溃 | 已完成 | toast/context/hotword handler 加 Darwin no-op；tray 本身已有 platform 检查 |
| 长按期间 Caps Lock 状态保护 | 已完成 | hidutil 在 HID 状态机之前拦截，物理长按不会改变系统 Caps Lock 状态，无需额外处理 |
| 日志接线 | 已完成 | `macos_caps_remap.py` 接入 client logger；其余 macOS 新模块均通过 `from . import logger` 接入 |
| `capswriterd` 控制器 | 已完成 | `capswriterd.py` 实现；PID 锁文件、监控循环、先停 client 再停 server、接入日志系统 |
| `capswriter` CLI | 已完成 | `capswriter.py` 实现；install/start/stop/restart/status/doctor/remap 子命令全部可用 |
| launchd 自启动 | 已完成 | `capswriter install` 生成 plist 并 launchctl load；`capswriter uninstall` 注销 |
| 配置入口整理 | 已完成 | `llm_enabled=False` 禁用 macOS LLM；Python 配置通过 `capswriter restart` 生效 |

---

## remap 审查结论

当前 `macos_caps_remap.py` 的核心方向通过，可以作为正式方案基础。

已经合理的点：

- 运行期将物理 Caps Lock 映射为 F18。
- 退出时恢复用户原有 `UserKeyMapping`。
- 启用时不是清空所有映射，而是只替换 Caps Lock 源映射。
- 能保留用户已有的其它自定义键盘映射。
- 能检测系统里已有 Caps->F18 的 stale 状态。
- 有独立 CLI 能力：`status / enable / restore / clear`。

需要按最新规格调整的点：

1. **快照持久化字段**

   remap 持久化的目的不是复杂配置系统，而是在 client 启动前保存一份系统 `UserKeyMapping` 快照，供退出或手动 restore 使用。

   建议状态文件：

   ```text
   ~/.capswriter/state/original_user_key_mapping.json
   ```

   建议内容：

   ```json
   {
     "schema_version": 1,
     "owner": "CapsWriter",
     "purpose": "macos_caps_remap_restore_snapshot",
     "created_at": "2026-05-18T21:00:00Z",
     "client_pid": 12345,
     "active": true,
     "original_user_key_mapping": [],
     "enabled_user_key_mapping": []
   }
   ```

2. **写入顺序**

   必须先保存 original snapshot，再写入 Caps Lock -> F18 remap。

3. **atomic state write**

   状态文件建议使用 atomic write，避免崩溃时留下半截 JSON。

4. **client 运行期 ownership**

   client 正常运行时必须保证 Caps Lock -> F18 处于接管状态。

   不考虑用户在 client 运行期间再手动修改 Caps Lock 源映射的场景。

   如果用户要修改键盘映射，应先：

   ```bash
   capswriter stop
   ```

5. **restore 命令限制**

   `capswriter remap restore` 只能在 client 未运行时使用。

   语义：恢复上一次 client 启动时，在注入 Caps Lock -> F18 之前保存的系统键盘映射快照。

6. **clear 命令保护**

   `clear` 会清空所有 `UserKeyMapping`，只能作为救援命令。

   正式 CLI 必须要求：

   ```bash
   capswriter remap clear --force
   ```

7. **不再提供 repair**

   不需要 remap repair 命令。

   语义收敛为：

   - client 运行中：client 保证接管 Caps Lock -> F18。
   - client 未运行：用户可以 restore 上次快照。
   - 极端情况：用户可以 clear --force。

---

## remap 命令语义

### `capswriter remap status`

用途：查看当前正在使用的系统 `UserKeyMapping` 状态，以及 CapsWriter 保存的上一次快照。

应展示：

- 当前系统实际 `UserKeyMapping`。
- 是否包含 Caps Lock -> F18。
- 快照文件是否存在。
- 快照是否 active。
- 快照创建时间。
- client 是否正在运行。

### `capswriter remap restore`

用途：恢复上一次 client 启动时，在注入 Caps Lock -> F18 之前保存的系统键盘映射快照。

限制：

- 只能在 client 未运行时使用。
- 如果 client 正在运行，应拒绝执行，并提示用户先执行：

```bash
capswriter stop
```

### `capswriter remap clear --force`

用途：清空系统所有 `UserKeyMapping`。

限制：

- 这是危险救援命令。
- 必须带 `--force`。
- 应只在 client 未运行时允许执行。

---

## 当前已观察到的问题

### 1. macOS 自动粘贴失败

日志：

```text
[05/18/26 21:09:39] WARNING osascript 粘贴失败:
36:68: execution error: “System Events”遇到一个错误：
“osascript”不允许发送按键。 (1002)
```

结论：

- `osascript` 发送 Cmd+V 需要 Accessibility 权限。
- 开启 Accessibility 后，预期在常规输入框中可以稳定上屏。
- 自动粘贴不能作为无权限下的必成功路径。
- 输出链路必须调整为：
  - 先写剪贴板。
  - 尝试自动粘贴一次。
  - 不重试。
  - 失败只记录 warning。
  - 不影响主流程。
- 用户若需要回看结果，可以依赖系统剪贴板或 Maccy 等剪贴板管理器。

### 2. 短按 Caps 后触发 F18 循环并误进入录音状态

准确描述：

```text
不是“长录音进入死循环”。
而是短按 Caps Lock 后触发 F18 循环，随后误触发录音状态。
现象是误打误撞进入录音，且无法手动退出，约 10 秒后才自己退出。
```

要求：

- client 运行时，短按 Caps Lock 仍应能正常切换大小写。
- 需要修复短按补发导致的事件重入 / 循环问题。
- 不在交接文档里指定具体修法，交给 coding agent 根据上下文修复。
- 修复后必须验证：
  - 短按 Caps 只切换大小写。
  - 不触发录音。
  - 长按 Caps 仍正常录音。
  - 松开 Caps 仍正常结束录音。

---

## 日志要求

当前仓库已经有日志实现，本阶段不重新设计日志系统。

只要求：

- 确认 server/client 在后台运行时仍然接入现有日志系统。
- 新增的 `capswriterd` / CapsWriterController 接入日志。
- 关键生命周期事件应能在日志中看到，例如启动、停止、server/client 异常退出、上屏失败、remap enable/restore 失败等。

不要求：

- 不实现单独的日志查看命令。
- 不在本交接文档里规定日志目录、轮转、tail 等细节。

---

## 配置策略

### Python 配置

仓库里现有很多配置在 Python 文件中。

当前策略：

- 不为 Python 配置做热重载。
- 用户修改 Python 配置后，使用：

```bash
capswriter restart
```

让配置生效。

理由：

- Python 配置影响面不固定。
- 有些配置属于 server。
- 有些配置属于 client。
- 有些配置需要进程初始化时读取。
- 首版以整体 restart 保证行为简单明确。

### 热词 TXT

热词本身是 TXT 文本机制，并且 Windows 版本下已有相关能力。

当前策略：

- macOS 保留 TXT 热词热重载能力。
- 用户可以直接修改 TXT 文件。
- 不强依赖 GUI。
- 不需要先做复杂 CLI 编辑器。

---

## 新会话交接

### 下一步工作（按优先级）

#### 1. 禁用 macOS GUI

根因：

以下组件均可能在后台线程调用 GUI 或创建窗口，macOS 下容易崩溃或不符合当前阶段无 GUI 策略。

| 文件 | 问题 | 修复方式 |
|------|------|----------|
| `core/ui/toast_manager.py` | Toast 线程 / Tk 相关行为 | macOS 下 no-op |
| `core/ui/tray.py` | pystray / tray 行为 | macOS 下 `TrayManager.start()` 直接 return |
| `core/ui/context_menu_handler.py` | Tkinter 上下文编辑弹窗 | macOS 下打印 CLI/TXT 提示后 return |
| `core/ui/hotword_menu_handler.py` | Tkinter 热词弹窗 | macOS 下打印 TXT 提示后 return |

目标：

- macOS 初版不启动任何 GUI。
- 禁用 GUI 不影响主链路。

#### 2. 修复上屏策略

当前问题：

- `osascript` 在缺少权限时失败。
- 当前日志中已出现 “不允许发送按键 (1002)”。
- 不能把 osascript 粘贴当成无条件稳定路径。

新目标：

```text
结果返回后：
  1. 必先写剪贴板。
  2. 尝试自动粘贴一次。
  3. 成功则结束。
  4. 失败只记录 warning。
  5. 不重试。
  6. 不影响主流程。
```

补充：

- Accessibility 权限用于自动粘贴。
- 开启权限后，预期在常规输入框中稳定上屏。
- 用户要回看结果可依赖剪贴板管理器，例如 Maccy。

#### 3. 修复短按 Caps 循环误触发录音

当前问题描述：

```text
短按 Caps Lock 后触发 F18 循环，随后误进入录音状态。
不是长录音死循环。
```

要求：

- 短按 Caps 在 client 运行时仍能切换大小写。
- 不应触发录音。
- 不应进入无法手动退出的录音状态。
- 修法交给 coding agent 结合上下文处理，不在文档中预设具体实现。

#### 4. 实现 `capswriterd`

目标：

- 单例后台 controller。
- 管理 server/client 生命周期。
- 不直接管理 remap。
- 提供状态。
- 接入既有日志系统。
- 支持被 launchd 拉起。

基本顺序：

```text
capswriterd run
  -> acquire singleton lock
  -> setup logging
  -> start IPC
  -> start server
  -> wait server ready
  -> start client
  -> wait client ready
  -> monitor loop
```

停止顺序：

```text
shutdown
  -> stop client
  -> wait client exit
  -> stop server
  -> wait server exit
  -> cleanup
```

注意：

- 先停 client，再停 server。
- 因为 client 持有输入链路和 remap 生命周期。

#### 5. 实现 `capswriter` CLI

首版命令：

```bash
capswriter install
capswriter uninstall
capswriter start
capswriter stop
capswriter restart
capswriter status
capswriter doctor
```

remap 诊断 / 救援：

```bash
capswriter remap status
capswriter remap restore
capswriter remap clear --force
```

明确不做：日志查看命令和 remap repair 命令。

#### 6. 实现 launchd 自启动

只注册：

```text
~/Library/LaunchAgents/com.capswriter.agent.plist
```

只启动：

```bash
/path/to/.venv/bin/python /path/to/project/capswriterd.py run
```

不要注册 server/client 两个 plist。

#### 7. 接通日志

沿用主仓库既有日志系统。

要求：

- 确认 server/client 后台运行时仍接入既有日志系统。
- 新增的 `capswriterd` / CapsWriterController 接入日志。
- 不实现单独的日志查看命令。
- 不在这里规定日志目录、轮转、tail 等细节。

#### 8. 加固 remap

基于当前 `macos_caps_remap.py` 补：

- original snapshot 持久化字段。
- 先写 snapshot，再写系统 remap。
- atomic state write。
- client 运行中独占接管 Caps Lock -> F18。
- `restore` 只能在 client 未运行时执行。
- `clear --force` 只能作为救援命令。
- 删除 / 不提供 `repair` 语义。

#### 9. 配置策略收口

- Python 配置改完后用 `capswriter restart`。
- 热词 TXT 保留热重载。
- macOS 不依赖 GUI 修改热词。

---

## 技术风险

- `osascript` / 自动粘贴需要 Accessibility 权限；缺权限时必须降级为剪贴板可用。
- 短按 Caps 当前会触发 F18 循环并误进入录音状态，需尽快修复。
- client 启动时 pynput 输出 “This process is not trusted!” 相关警告，需要消除、降级或纳入 doctor。
- 后台运行后无命令行窗口，需要确认既有日志系统仍可用，并为 controller 增加日志。
- 需用 `.venv/bin/python` 在项目根目录启动，系统 `python` 可能缺少 `colorama` 等依赖。
- remap restore 如果没有正确使用启动前 snapshot，可能误伤用户自定义键盘映射。
- launchd 环境变量与交互 shell 不同，路径必须使用绝对路径。

---

## 当前优先级摘要

```text
P0:
  - 禁用 macOS GUI
  - 修复短按 Caps 循环误触发录音
  - 修复上屏策略：剪贴板必写 + 自动粘贴一次
  - 确认日志接线，新增 controller 日志

P1:
  - 实现 capswriterd
  - 实现 capswriter CLI
  - 实现 launchd 自启动
  - 加固 remap snapshot 持久化 / restore / clear --force

P2:
  - doctor 权限诊断
  - remap 救援命令完善
  - Python 配置 restart 生效口径验证
  - 热词 TXT 热重载验证

P3:
  - 更细粒度配置重载
  - 更完整 GUI 或菜单栏能力
  - 更接近 Windows 的流式上屏体验
```
