# Qwen3-ASR macOS 专项规格

## 1. 文档目的

本文档只服务于 `mac-dev` 分支当前阶段目标：

- 仅支持 `Qwen3-ASR` 路线。
- macOS 服务端主路线采用 `qwen_asr_mlx`。
- Windows 继续保留现有 `qwen_asr_gguf`。
- 保持现有 Client / Server 架构，不做全项目大重写。
- 优先满足后台常驻、离电使用、低发热、低延迟、稳定输入的 macOS 使用场景。

本文档是当前阶段的 macOS 规格定义，不是长期全平台统一架构蓝图，也不是其他 ASR 引擎的适配计划。

---

## 2. 当前结论

### 2.1 已确认事项

- `Qwen3-ASR` 是当前唯一保留的 ASR 路线。
- macOS 不再以现有 `qwen_asr_gguf` 为主后端。
- macOS 新增并采用 `qwen_asr_mlx` 后端。
- Windows 继续保留现有 `qwen_asr_gguf`。
- macOS 初版不做 GUI：
  - 不启用 tray。
  - 不启用 toast。
  - 不启用 Tkinter 弹窗。
  - 热词、上下文、配置等入口不依赖 GUI。
- macOS 首版结果模式为：松开按键后尽快返回最终结果，不要求按住说话时展示中间流式文本。
- macOS 输出策略为：
  - 必须先写入系统剪贴板。
  - 然后只尝试自动粘贴一次。
  - 不重试。
  - 不因粘贴失败影响录音、识别、剪贴板写入主流程。
- macOS 的 Caps Lock remap 生命周期由 client 自己管理。
- 整体软件生命周期由单例 `CapsWriterController` / `capswriterd` 管理。
- 用户交互与控制入口统一为 `capswriter` 命令。
- 日志系统沿用主仓库既有实现，本阶段只要求确认后台运行时 server/client 仍然接入既有日志，并让新增的 `capswriterd` / controller 接入日志。

### 2.2 采用 MLX 的原因

- 程序是后台常驻，不是偶发转录工具。
- 用户经常离电使用 Mac，更看重续航、发热与常驻成本。
- MLX 更贴近 Apple Silicon 的原生推理路径。
- 已有现成的 `mlx-qwen3-asr` 工作可以借鉴，不需要从零实现 Qwen3-ASR。

---

## 3. 范围收敛

### 3.1 本阶段明确要做

- 为 macOS 增加 `qwen_asr_mlx` 服务端后端。
- 让 CapsWriter 的客户端主链路在 macOS 上可用：
  - Caps Lock 长按触发录音。
  - 麦克风录音采集。
  - 服务端识别。
  - 最终结果返回。
  - 结果写入剪贴板。
  - 在具备权限时尝试自动粘贴到当前输入焦点。
- 建立 macOS 后台运行形态：
  - `capswriterd` 作为单例后台控制器。
  - `capswriter` 作为唯一用户命令入口。
  - 只注册 `capswriterd` 为开机 / 登录自启动项。
- 接通日志：
  - server/client 后台运行时继续接入主仓库既有日志系统。
  - 新增的 `capswriterd` / CapsWriterController 也接入日志。
- 明确 Caps Lock remap 的保护策略：
  - 运行期间把物理 Caps Lock 映射为 F18。
  - 启动前保存一份系统 `UserKeyMapping` 快照。
  - 退出时恢复该快照。
  - 保留用户原本的其它自定义键盘映射。
- 明确 remap 快照持久化策略。
- 保留现有热词 TXT 机制：
  - macOS 首版只保留 TXT 热词热重载能力。
  - 用户可以直接改 TXT 文件。
  - 不强依赖 GUI。

### 3.2 本阶段明确不做

- 不适配 `SenseVoice`、`Fun-ASR-Nano`、`Paraformer`。
- 不把整个项目重构成 MLX-only。
- 不重做完整 GUI。
- 不先做打包器。
- 不先做发布流程。
- 不追求所有高级能力一步到位，例如复杂角色联动或全量平台统一抽象。
- 不把“按住说话时的中间结果流式显示”纳入首版必做范围。
- 不为 Python 配置文件实现热重载。Python 配置修改后通过 `capswriter restart` 生效。
- 不提供单独的日志查看命令。
- 不提供 remap repair 命令。
- 不支持用户在 client 运行期间手动修改 Caps Lock 源映射；client 运行期间由 client 独占接管 Caps Lock -> F18。

---

## 4. ASR 后端规格

### 4.1 现有 `ONNX + GGUF` 路线

优势：

- 改动更小。
- 复用现有代码更多。
- 更快产出第一个“能跑”的版本。

劣势：

- `encoder` 与 `decoder` 仍是两套运行时。
- 更像兼容方案，不像 Mac 原生方案。
- 对后台常驻、离电、低发热场景不占优。

### 4.2 `qwen_asr_mlx` 路线

优势：

- 更贴近 Apple Silicon 原生推理路径。
- 更适合常驻、短句高频触发和离电使用。
- 已有现成实现可借鉴，避免从零造轮子。
- 长期维护上更适合做 macOS 专用后端。

劣势：

- 不是最小补丁。
- 需要新增后端适配层。
- 模型来源当前主要依赖社区 MLX 权重，而非 Qwen 官方直接维护的 ASR-MLX 发布物。

### 4.3 最终决策

- macOS：采用 `qwen_asr_mlx`。
- Windows：保留 `qwen_asr_gguf`。
- 不做全局替换，只做按平台分流。

---

## 5. 可借鉴的现有工作

### 5.1 直接可借鉴实现

`mlx-qwen3-asr` 已具备以下能力：

- 支持 `Qwen3-ASR`。
- 提供 Python API。
- 提供 `Session`。
- 支持 `streaming`。
- 支持 `timestamps`。
- 提供 `mic`。
- 提供 HTTP / OpenAI-compatible server。

适合借鉴的方式：

- 不直接把它当成独立成品塞进 CapsWriter。
- 将其 Python 侧能力适配到 CapsWriter 当前的 `BaseASREngine`。
- 通过薄适配层屏蔽第三方 API 细节，避免业务逻辑直接依赖第三方实现。

### 5.2 可获取的 MLX 模型权重

当前已确认可以较轻松获取的 MLX 格式 `Qwen3-ASR` 权重包括：

- `mlx-community/Qwen3-ASR-0.6B-bf16`
- `mlx-community/Qwen3-ASR-1.7B-bf16`
- `mlx-community/Qwen3-ASR-1.7B-4bit`

当前阶段不要求先做自定义模型转换工具链，可以先用现成模型验证后端接入。

---

## 6. 模型规格

### 6.1 选型原则

使用场景：

- 后台常驻。
- 高频短句语音输入。
- 经常离电使用。
- 目标设备是 Apple Silicon MacBook。

优先级：

1. 常驻成本。
2. 发热与续航。
3. 响应速度。
4. 识别质量。
5. 显存 / 内存占用。

### 6.2 候选规格

#### 方案 A：`Qwen3-ASR-1.7B-8bit`

特点：

- 质量高于 4bit 量化。
- 在本地已经准备好权重的前提下，可以直接作为默认规格落地。
- 常驻成本高于 4bit，但仍明显低于 `bf16`。

判断：

- 作为本阶段默认规格。

#### 方案 B：`Qwen3-ASR-1.7B-4bit`

特点：

- 质量接近 1.7B 主规格。
- 内存与磁盘占用明显低于 `bf16`。
- 更适合后台常驻与离电使用。

判断：

- 作为本地回退规格。

#### 方案 C：`Qwen3-ASR-1.7B-bf16`

特点：

- 质量上限最高。
- 占用更大。
- 常驻和离电成本更高。

判断：

- 适合作为“插电 / 追求最高质量”的可选规格。

#### 方案 D：`Qwen3-ASR-0.6B-bf16`

特点：

- 更轻。
- 更适合低内存占用场景。
- 质量通常弱于 1.7B。

判断：

- 适合作为轻量备用规格，不建议作为主规格。

### 6.3 当前推荐

- 默认规格：`Qwen3-ASR-1.7B-8bit`
- 本地回退规格：`Qwen3-ASR-1.7B-4bit`
- 其他备选：`Qwen3-ASR-1.7B-bf16`、`Qwen3-ASR-0.6B-bf16`

---

## 7. 运行时架构规格

### 7.1 三层架构

macOS 首版采用三层运行架构：

```text
Layer 1: capswriter CLI
  - 用户唯一交互入口
  - 负责 install / uninstall / start / stop / restart / status / doctor
  - 不直接管理 server/client 业务细节
  - 不直接管理 Caps remap 生命周期

Layer 2: CapsWriterController / capswriterd
  - 单例后台控制器
  - 管理 server/client 生命周期
  - 管理整体状态
  - 接入主仓库既有日志系统
  - 管理 launchd 注册
  - 不直接接管 Caps remap ownership

Layer 3: server / client
  - server 负责 ASR 后端
  - client 负责输入链路、录音、WebSocket、上屏
  - client 内部唯一拥有 Caps remap 生命周期
```

### 7.2 Ownership 规则

```text
launchd 只负责拉起 capswriterd。
capswriterd 只负责拉起 server/client。
client 只负责自己的输入链路和 Caps remap。
server 只负责 ASR。
```

### 7.3 `capswriterd`

`capswriterd` 是整个 CapsWriter 软件在当前用户会话中的单例控制器。

职责：

- 启动 server。
- 等待 server ready。
- 启动 client。
- 监控 server/client 进程。
- 提供整体状态。
- 接收 `capswriter` 命令控制。
- 接入主仓库既有日志系统。
- 在登录自启动场景下作为唯一被 launchd 拉起的进程。

不负责：

- 不直接调用 `hidutil` 管理 Caps remap。
- 不监听 F18。
- 不处理短按 / 长按。
- 不处理录音。
- 不处理上屏。
- 不修改热词 TXT。
- 不直接修改 Python 配置。

### 7.4 `capswriter`

`capswriter` 是用户唯一命令入口。

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

配置相关命令后置，不属于本阶段必做范围。

### 7.5 开机 / 登录自启动

macOS 只注册一个 LaunchAgent：

```text
~/Library/LaunchAgents/com.capswriter.agent.plist
```

该 LaunchAgent 只启动：

```bash
/path/to/.venv/bin/python /path/to/project/capswriterd.py run
```

不注册 server/client 两个独立 plist。

原因：

- 避免 server/client 被 launchd 和 controller 双重管理。
- 保证整体状态由 `capswriterd` 统一表达。
- 保证用户只看到一个软件生命周期，而不是两个脚本。

---

## 8. 客户端输入链路规格

### 8.1 Caps Lock 使用目标

macOS 首版目标体验：

- 长按 Caps Lock：开始录音。
- 松开 Caps Lock：结束录音并请求最终识别结果。
- 短按 Caps Lock：仍应能正常切换大小写。
- client 运行期间，不应剥夺 Caps Lock 的基本大小写切换能力。

### 8.2 remap 路线

macOS 使用运行期 remap：

```text
物理 Caps Lock -> F18
```

client 监听 F18，再进行短按 / 长按分发。

### 8.3 remap ownership

Caps remap 生命周期只归 client 所有。

client 启动时：

- 读取当前系统 `UserKeyMapping`。
- 在注入 Caps Lock -> F18 前，保存一份当前系统映射快照。
- 在保留其它用户自定义映射的前提下，将 Caps Lock 源映射替换为 F18。
- 启动 F18 监听。

client 正常运行时：

- client 独占接管 Caps Lock -> F18。
- 不支持用户在 client 运行期间手动修改 Caps Lock 源映射。
- 如果用户需要修改键盘映射，应先 `capswriter stop`，修改后再 `capswriter start`。

client 正常退出时：

- 停止 F18 监听。
- 按启动前保存的快照恢复系统 `UserKeyMapping`。

client 异常退出后：

- 用户可以在 client 未运行时执行 `capswriter remap restore`，恢复上一次 client 启动前保存的系统映射快照。

### 8.4 保护用户自定义键盘映射

这是正式产品规格，不是实现细节。

要求：

- 启用 CapsWriter remap 时，不得清空用户已有的其它 `UserKeyMapping`。
- 只允许覆盖 `HIDKeyboardModifierMappingSrc == Caps Lock` 的源映射。
- 用户原本的其它键位 remap 必须保留。
- 退出时应恢复 CapsWriter 启动前的用户原始映射快照。
- `clear` 只能作为救援命令存在，必须要求显式 `--force`。

### 8.5 remap 快照持久化规格

remap 持久化只服务于一个目标：

```text
在 client 启动前保存一份系统原始 UserKeyMapping 快照，
以便 client 退出或用户手动 restore 时，可以恢复到启动前状态。
```

建议状态文件路径：

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
  "enabled_user_key_mapping": [
    {
      "HIDKeyboardModifierMappingSrc": 30064771129,
      "HIDKeyboardModifierMappingDst": 30064771181
    }
  ]
}
```

字段含义：

- `original_user_key_mapping`：client 启动前，在注入 Caps Lock -> F18 之前看到的系统映射快照。
- `enabled_user_key_mapping`：client 注入 Caps Lock -> F18 后预期写入系统的映射。
- `active`：表示该快照是否对应一次正在运行或尚未正常恢复的 remap session。

写入要求：

- 必须先保存 original 快照，再写入 Caps Lock -> F18 remap。
- 状态文件建议使用 atomic write，避免崩溃时留下半截 JSON。
- client 正常退出恢复后，可以保留状态文件，但应将 `active` 标记为 `false`。

### 8.6 remap 命令语义

#### `capswriter remap status`

用途：查看当前正在使用的系统 `UserKeyMapping` 状态，以及 CapsWriter 保存的上一次快照。

应展示：

- 当前系统实际 `UserKeyMapping`。
- 是否包含 Caps Lock -> F18。
- 快照文件是否存在。
- 快照是否 active。
- 快照创建时间。
- client 是否正在运行。

#### `capswriter remap restore`

用途：恢复上一次 client 启动时，在注入 Caps Lock -> F18 之前保存的系统键盘映射快照。

限制：

- 只能在 client 未运行时使用。
- 如果 client 正在运行，应拒绝执行，并提示用户先执行 `capswriter stop`。

#### `capswriter remap clear --force`

用途：清空系统所有 `UserKeyMapping`。

限制：

- 这是危险救援命令。
- 必须带 `--force`。
- 应只在 client 未运行时允许执行。

---

## 9. 输出与上屏规格

### 9.1 统一规则

松手结束录音后，客户端拿到最终识别结果时：

1. 必须先把最终识别结果写入系统剪贴板。
2. 然后尝试把结果上屏到当前输入焦点。
3. 自动上屏只尝试一次。
4. 不重试。
5. 不因自动上屏失败而阻塞、崩溃或影响识别主流程。

### 9.2 Windows 口径

- 保留现有成熟体验。
- 默认优先模拟打字上屏。
- 命中 `Config.paste_apps` 的应用时，强制走“剪贴板 + 粘贴”兜底。
- LLM typing mode 继续保留原有的流式打字体验。

### 9.3 macOS 口径

- 必保：最终文本写入系统剪贴板。
- 尽力：自动粘贴到当前输入焦点。
- 自动粘贴只尝试一次。
- 不执着重试。
- 如果粘贴失败，用户仍可以通过剪贴板或剪贴板管理器查看 / 使用结果。
- Accessibility 权限用途限定为自动粘贴。
- 开启 Accessibility 后，预期在常规输入框内可以稳定上屏。

### 9.4 权限口径

macOS 自动上屏依赖系统权限。

规格要求：

- `doctor` 应检查自动上屏相关权限。
- 缺权限时，应给出明确诊断。
- 缺权限时，剪贴板写入仍应可用。
- 缺权限时，不能让客户端进入异常循环或阻塞状态。

---

## 10. 配置规格

### 10.1 Python 配置文件

现有仓库中大量配置在 Python 文件中实现。

规格：

- Python 配置文件暂不做热重载。
- 用户修改 Python 配置后，通过以下命令让配置生效：

```bash
capswriter restart
```

- 若配置只影响 server，可后续优化为只重启 server。
- 若配置只影响 client，可后续优化为只重启 client。
- 首版不要求细粒度热更新。

### 10.2 热词 TXT

热词本身是 TXT 文本机制。

规格：

- macOS 保留现有 TXT 热词热重载能力。
- 用户可以直接修改 TXT 文件。
- 不要求通过 GUI 修改。
- 不要求为热词单独实现新的配置系统。
- Windows 现有热词能力不因 macOS 适配被破坏。

### 10.3 GUI 配置

macOS 初版不启用 GUI 配置入口。

要求：

- tray 禁用。
- toast 禁用。
- Tkinter 弹窗禁用。
- 与 GUI 相关的配置操作在 macOS 下应降级为 CLI 提示或 no-op。
- 禁用 GUI 不应影响 server/client 主链路。

---

## 11. 日志规格

本阶段不重新设计日志系统。

规格：

- 沿用主仓库既有日志实现。
- 确认 server/client 在后台运行时仍然接入现有日志系统。
- 新增的 `capswriterd` / CapsWriterController 也应接入日志。
- 不新增单独的日志查看命令作为本阶段用户交互范围。

---

## 12. 技术实施规格

### 12.1 服务端

目标：

- 新增 `qwen_asr_mlx` 后端，而不是替换现有 `qwen_asr_gguf`。

改动点：

1. `config_server.py`
   - 新增或调整 macOS 下的模型类型与模型目录配置。
   - 明确 `qwen_asr_mlx` 的默认模型规格。

2. `core/server/engines/factory.py`
   - 注册 `qwen_asr_mlx` 后端。
   - 保持原有 `qwen_asr` / `qwen_asr_gguf` 路线不被破坏。

3. `core/server/engines/qwen_asr_mlx/`
   - `asr_engine.py`
   - `session_adapter.py` 或等价封装
   - 必要的 schema / result 转换层

4. `requirements-server.txt`
   - 为 macOS 增加 MLX 相关依赖。
   - 保持 Windows 依赖策略不被误伤。

### 12.2 客户端

客户端按平台分流：

- 快捷键：macOS 使用 Caps -> F18 remap + F18 监听。
- 上屏：macOS 必写剪贴板，自动粘贴只尝试一次。
- 选中文字：按平台选择快捷键或可访问性方案。
- 前台窗口：增强 macOS 检测可用性。
- GUI：macOS 初版禁用。

---

## 13. 风险清单

### 风险一：MLX 后端接口与当前引擎抽象不完全一致

应对策略：

- 增加薄适配层。
- 不把第三方 API 直接散落到业务逻辑中。

### 风险二：社区 MLX 权重与官方权重在版本节奏上可能不同步

应对策略：

- 先固定当前可用模型版本。
- 后续再补升级策略。

### 风险三：macOS 权限限制影响输入链路

应对策略：

- 把权限申请、验证步骤和已知限制写入 `doctor`。
- 自动粘贴失败不影响剪贴板写入和识别主流程。

### 风险四：Caps remap 残留或误恢复用户映射

应对策略：

- client 启动前持久化 original snapshot。
- client 正常退出时恢复 snapshot。
- client 未运行时允许用户执行 `capswriter remap restore`。
- `clear` 必须通过 `capswriter remap clear --force` 显式触发。

### 风险五：后台运行后可观察性下降

应对策略：

- 沿用主仓库既有日志系统。
- 确认 server/client 后台运行时仍然接入日志。
- 新增 controller 日志。

---

## 14. 验收标准

满足以下条件即可视为本阶段达标：

- 文档与代码口径明确：
  - macOS = `qwen_asr_mlx`
  - Windows = `qwen_asr_gguf`
- 能在 macOS 上加载默认 MLX 模型并完成识别。
- 以“松开按键后尽快输出最终结果”为主要体验目标。
- client 运行时：
  - 长按 Caps Lock 可以录音。
  - 松开 Caps Lock 可以结束录音。
  - 短按 Caps Lock 仍应能切换大小写。
- macOS 输出链路满足：
  - 必先写剪贴板。
  - 自动粘贴只尝试一次。
  - 粘贴失败不影响主流程。
- remap 链路满足：
  - 保留用户已有其它 `UserKeyMapping`。
  - client 启动前保存 original snapshot。
  - client 退出时恢复 original snapshot。
  - client 未运行时可手动 restore。
- 后台运行链路满足：
  - 只注册 `capswriterd` 为自启动项。
  - 用户通过 `capswriter` 控制整体生命周期。
  - `capswriter status` 能表达整体状态。
- macOS GUI 初版禁用：
  - tray 不启动。
  - toast 不启动。
  - Tkinter 弹窗不启动。
- 日志接线满足：
  - server/client 继续接入既有日志系统。
  - 新增 controller 日志。
