# CapsWriter-Offline 当前阶段同步

## 当前目标

- 分支：`mac-dev`
- 基线来源：`master`
- 当前阶段目标：仅围绕 `Qwen3-ASR` 单一路线，为 macOS / Apple Silicon 新增 `qwen_asr_mlx` 后端，在保持现有 Client / Server 架构不大动的前提下，优先优化后台常驻、离电使用、低发热与低延迟体验。

## 当前状态

- 说明：本节保留了本阶段多轮探索的时间线记录。若旧条目提到 `Quartz`、`darwin_intercept`、`MacOSCapsLockTap` 或 `native tap`，均指已归档的历史尝试，不代表当前主程序路径；当前有效口径以“当前实际路径”“当前阶段决策”“任务看板”为准。

- 已完成：从 `master` 创建并切换到 `mac-dev`。
- 已完成：递归扫描项目目录，确认代码核心位于 `core/client`、`core/server`、`models`、`docs`。
- 已完成：查看近两次 Markdown 提交，确认最近文档口径主要是“v2.5 正式发布”以及“Qwen3-ASR 0.6B 不再作为主下载项”。
- 已完成：补齐项目级文档基线，开始按 `readme.md` / `AGENTS.md` / `CLAUDE.md` 分离职责。
- 已完成：与用户收敛本阶段范围，仅保留 `Qwen3-ASR` 路线，不改造其他引擎。
- 已完成：与用户确认 macOS 后端优先采用 MLX，而不是继续沿用当前 `ONNX + GGUF + llama.cpp` 的拼装式 Mac 适配。
- 已完成：与用户确认首版不要求按住说话时的中间结果流式显示，只要求松开后尽快输出最终结果，并把低延迟作为优先目标。
- 已完成：更新 `docs/` 下的 Qwen3-ASR macOS 专项规划，并同步模型选型建议。
- 已完成：确认 macOS 默认模型规格切换为 `Qwen3-ASR-1.7B-8bit`，并保留本地 `1.7B-4bit` 作为回退规格。
- 已完成：接入服务端 `qwen_asr_mlx` 基础代码路径，已覆盖 `config_server.py`、`EngineFactory`、`ModelLoader`、`requirements-server.txt` 与新引擎目录。
- 已完成：建立项目级 `uv` Python 3.13 环境，客户端与服务端依赖均已安装到 `.venv`，`uv pip check` 通过。
- 已完成：本地 `models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit` 已导入完成，并将其切换为默认模型目录选择；本地 `1.7B-4bit` 保留为回退目录。
- 已完成：`Downloads` 中失败残留的 `Qwen3-ASR-1.7B-MLX-4bit` 已移出下载目录并归档到项目 `.archive/`。
- 已完成：真实服务端启动验证已通过，`qwen_asr_mlx` 可在 macOS 本机完成启动前模型检查并成功监听 `6016` 端口。
- 已完成：真实音频输入闭环验证已通过，`/Users/edgar/Music/测试音频.m4a` 已成功完成“音频解码 -> 模型加载 -> 最终文本输出”。
- 已发现并修复：`qwen_asr_mlx` 遇到非 `16kHz` 音频时会隐式依赖 `ffmpeg` 做重采样，现已在引擎层补齐本地重采样兜底。
- 已发现：客户端在 macOS 首次真实启动时即阻塞于 `core/client/shortcut/key_mapper.py` 对 `pynput._util.win32.KeyTranslator` 的直接导入，尚未进入热键监听与录音链路。
- 已完成：本机 `ffmpeg` 已通过 Homebrew 安装，版本为 `8.1.1`，后续媒体转码和外部依赖路径不再受缺失二进制阻塞。
- 已完成：客户端快捷键层已新增 macOS 分支，`Caps Lock` 不再走 Win32 `win32_event_filter`，改为使用 Quartz / `pynput darwin_intercept` 处理底层 `flagsChanged` 事件。
- 已完成：`Caps Lock` 的 macOS 路径已按“按下开始录音、松开结束录音、短按补发原键、长按不切换大小写锁定”接入现有任务状态机。
- 已完成：为降低 macOS 启动期副作用，`core/client/__init__.py`、`core/client/shortcut/__init__.py`、`core/client/output/__init__.py`、`core/client/llm/__init__.py` 已改为惰性导出，避免导入局部模块时提前拉起整套运行栈。
- 已完成：客户端输出链路已补齐 macOS 分支，文本输出默认改为剪贴板粘贴，读取选区改为发送 `Command+C`。
- 已确认新事实：较早一轮真实桌面环境中“`Caps Lock` 完全无效、不会唤起录音”的现象已经被后续集成回归推翻；当前原生 `Caps Lock` 路径、录音链路与服务端识别链路实际上都能跑通，剩余问题已收敛为“系统大小写锁定状态仍会变化”以及“拿不到可靠的物理 `keydown/keyup` 对”。
- 已确认失败：用户已在非微信窗口（`TextEdit`）以及 `ABC` / 拼音输入法下重复测试，现象完全一致，因此当前问题已排除“微信前台接管”和“输入法切换映射”这两个方向。
- 已确认失败：当前基于 `pynput` / `darwin_intercept` 的 macOS `Caps Lock` 接管方案，在真实环境中不能稳定进入项目的热键状态机，不能作为继续迭代的可靠基础。
- 已完成：新增原生调试脚本 `core/tools/macos_caps_probe.py`，支持 `session` / `hid` 两层监听，以及 `observe` / `swallow-caps` 两种模式，便于后续持续验证 macOS `Caps Lock` 事件。
- 已完成：用户配合下的原生 `kCGHIDEventTap` 实测表明，`Caps Lock` 可以稳定以 `flagsChanged + keycode=57` 形式被直接捕获，且不依赖 `pynput`。
- 已完成：用户配合下的原生 `kCGHIDEventTap` 吞事件实测表明，在初始 `hid_alpha=False` 的前提下，多次短按 / 长按 `Caps Lock` 后，全局 HID 状态仍保持不变，说明 “底层捕获 + 直接吞掉 `Caps Lock`” 这一路线具备继续工程化的价值。
- 已完成：macOS 原生 `Caps Lock` 劫持已从 `ShortcutManager` 内联 Quartz 分支中拆出，收敛为独立模块 `core/client/shortcut/macos_caps_lock_tap.py`。
- 已完成：`ShortcutManager` 已改为只桥接原生 `Caps Lock` 语义事件回现有录音状态机，并把 restore 决策收口到管理器，避免继续在 `ShortcutTask` 中扩散 Darwin 特判。
- 已确认新事实：在完整客户端集成场景里，`kCGHIDEventTap` 对 `Caps Lock` 并没有稳定提供可用的 `按下 -> 长按 -> 松开` 物理语义；最新日志仅捕获到单条翻转相关事件，且系统锁定状态仍会变化。
- 已确认新事实：此前观察到的菜单栏麦克风图标并非微信前台自己的 UI；用户随后用 macOS 系统录音机复现实验后，已确认该图标属于系统原生麦克风指示，并且能够展示当前正在占用麦克风的软件。
- 已完成：与用户再次收敛目标，当前仍坚持沿用 `Caps Lock` 作为产品交互键，不改交互语义；后续优先尝试比当前 `Quartz / CGEventTap` 更底层的方案。
- 已完成：与用户收敛本轮清理范围，只保留不接入主程序的独立测试脚本；旧 `native tap` 主程序路径全部退出主干运行链路。
- 已完成：`core/client/shortcut/macos_caps_lock_tap.py` 已归档到 `.archive/2026-05-18-macos-caps-remap/`，`core/tools/macos_caps_probe.py` 与 `core/tools/macos_mic_probe.py` 保留为独立调试脚本。
- 已完成：macOS 客户端主程序已切到 `hidutil Caps Lock -> F18` 路线，新增 `macos_caps_remap.py`、`macos_f18_listener.py`、`macos_caps_controller.py`、`macos_caps_synth.py` 与 `macos_caps_f18.py`。
- 已完成：`start_client.py` 已在 macOS 麦克风模式下改为通过 `macos_caps_supervisor` 拉起子进程，父进程负责 `Caps Lock` remap 生命周期管理与退出恢复。
- 已完成：macOS 麦克风输入流已改为按需打开 / 按需关闭，目标是只在真正录音时触发系统麦克风占用指示。

## 当前实际路径

- macOS 客户端当前主路径已更新为：`start_client.py` -> `macos_caps_supervisor` 启用 `hidutil` 映射 -> 子进程 `core.client.main` 启动客户端 -> `MacOSCapsF18Bridge` 监听 F18 -> `MacOSCapsController` 判定短按 / 长按 -> 短按走 `synthesize_caps_lock_toggle()`，长按走 `ShortcutManager.start_press_to_talk('caps_lock')` -> `ShortcutTask` 按需打开 `AudioStreamManager` -> `AudioRecorder` / `WebSocketManager` / `ResultProcessor` 继续沿用现有识别链路。
- 旧的 `MacOSCapsLockTap + kCGHIDEventTap` 主程序路径已归档，不再参与正式运行，只保留独立探针脚本做调试对照。
- 当前这条新路径已经通过语法、映射启停、supervisor 生命周期与按需开流的自动 smoke；真实桌面层面的“物理 Caps Lock 长按录音 / 短按切换大小写 / 系统麦克风图标跟随录音出现”仍需人工回归确认。

## 当前技术判断

| 方向 | 当前判断 | 依据 |
| --- | --- | --- |
| macOS 服务端后端 | 新增 `qwen_asr_mlx`，不直接替换现有 Windows `qwen_asr_gguf` | 更符合 Apple Silicon 常驻、离电场景，也更容易保持跨平台兼容边界 |
| Apple Silicon 运行效率 | MLX 路线优先于现有 `ONNX + GGUF` 拼装路线 | MLX 面向 Apple Silicon 设计，适合统一内存与常驻低功耗场景 |
| 现有工作可借鉴度 | 较高 | `mlx-qwen3-asr` 已提供 Python API、Session、streaming、timestamps、mic、server |
| 客户端输入输出 | 仍需建立平台抽象层 | 当前客户端大量依赖 `keyboard`、`pynput._util.win32`、`win32_event_filter`、`Ctrl+C`、`keyboard.write` |
| 模型获取难度 | 低 | 当前已具备本地 `1.7B-8bit` 默认目录，并保留本地 `1.7B-4bit` 回退目录 |

## 已识别的 macOS 改造缺口

### 客户端

- `core/client/shortcut/key_mapper.py` 直接依赖 `pynput._util.win32.KeyTranslator`。
- `core/client/shortcut/shortcut_manager.py` 直接基于 `win32_event_filter` 处理键盘和鼠标消息。
- `core/client/output/text_output.py` 仍依赖 `keyboard.write`，macOS 上不可作为稳定方案。
- `core/client/llm/llm_get_selection.py` 直接发送 `Ctrl+C`，需要改为 `Command+C` 或更稳妥的可访问性方案。
- `core/tools/window_detector.py` 虽然已有 macOS 分支，但目前只对少数 App 用 AppleScript 做了弱支持，不足以覆盖真实输入场景。

### 服务端

- 现有 `qwen_asr_gguf` 路线不再是 macOS 主路线，需要新增 `qwen_asr_mlx` 适配层。
- `EngineFactory`、`config_server.py`、模型加载与依赖管理需要增加 MLX 路径。
- 需要确认 MLX 模型目录约定、下载来源和默认规格。

## 当前阶段决策

### 决策一：macOS 采用独立 `qwen_asr_mlx` 后端

- Windows 继续保留现有 `qwen_asr_gguf`。
- macOS 不再把 `ONNX + GGUF + llama.cpp` 当作主路线。
- `qwen_asr_mlx` 作为并存后端接入，减少对现有稳定路径的冲击。

### 决策二：尽量只替换服务端推理，不重写整体架构

- 保留当前 Client / Server、WebSocket、热词、LLM、上屏与托盘结构。
- 优先只在 `core/server/engines/` 范围内新增 MLX 后端适配。
- 客户端只做 macOS 输入链路最小必要修补。

### 决策三：模型优先级按当前本地权重准备状态更新

- 默认规格：`Qwen3-ASR-1.7B-8bit`
- 本地回退：`Qwen3-ASR-1.7B-4bit`
- 其他备选：`Qwen3-ASR-1.7B-bf16` / `Qwen3-ASR-0.6B-bf16`

### 决策四：首版以最终结果低延迟优先，不做中间结果流式显示

- 按住说话过程中，不要求展示中间识别文本。
- 交互目标是“松开按键后尽快得到最终结果”。
- 这意味着首版后端适配可优先围绕最终结果路径设计，不为中间态流式回显增加额外复杂度。

### 决策五：结果返回后的上屏与剪贴板体验口径

- 用户已确认：松手结束录音后，客户端拿到最终结果时，应当“尝试上屏”的同时，把同一份最终结果复制到剪贴板。
- 用户已确认：Windows 继续保留原有体验分层，不因 macOS 问题回退这条成熟路径。
- Windows 当前产品口径：
  - 默认优先模拟打字上屏。
  - 命中 `Config.paste_apps` 的应用时，强制走“剪贴板 + 粘贴”兜底。
  - LLM typing mode 继续保留原有的流式打字体验。
- macOS 当前产品口径：
  - 首版以上屏稳定性优先，不强求和 Windows 一样的流式打字观感。
  - 允许默认采用“复制到剪贴板 + 粘贴上屏”的稳定路线。
  - 待上屏与剪贴板链路稳定后，再评估是否恢复更接近 Windows 的默认打字体验。

## 任务看板

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| 建立 `mac-dev` 分支 | 已完成 | 已从 `master` 切出 |
| 核心文档基线整理 | 已完成 | 已补齐 `AGENTS.md`，并重写阶段性 `CLAUDE.md` |
| macOS 技术路线调研 | 已完成 | 已收敛为只支持 `Qwen3-ASR` 的 MLX 主路线 |
| 平台差异点清单 | 已完成 | 已定位 macOS 输入链路与服务端后端替换缺口 |
| 输出专项规划文档 | 已完成 | 已更新为 `qwen_asr_mlx` 主路线并补模型选型 |
| 确认 MLX 模型规格 | 已完成 | 默认 `1.7B-8bit`，本地回退 `1.7B-4bit` |
| 设计 `qwen_asr_mlx` 适配层 | 已完成 | 已对齐 `BaseASREngine`，采用 `mlx_qwen3_asr.Session` 薄适配 |
| 服务端最小接入实现 | 已完成 | 已通过真实启动与真实音频闭环验证，非 `16kHz` 输入的重采样兜底也已补齐 |
| 首版结果模式收敛 | 已完成 | 首版只要求松开后快速返回最终结果，不要求中间流式显示 |
| 清理旧 macOS `native tap` 主程序路径 | 已完成 | 旧实现已归档，独立测试脚本保留 |
| 实现 macOS 客户端输入链路 | 进行中 | `hidutil -> F18 -> 新状态机 -> 按需开流` 已完成代码接入和自动 smoke，待真实桌面按键与录音回归 |
| 收敛结果上屏体验口径 | 已完成 | 已确认“尝试上屏 + 同步复制到剪贴板”的统一规则，以及 Windows/macOS 分平台体验策略 |
| 排查 macOS 结果输出崩溃 | 已完成 | `llm_output_typing.py` 和 `result_processor.py` 中 `import keyboard` 均已在 macOS 下隔离；桌面回归确认崩溃已消失 |
| 验证 macOS 长按期间 Caps Lock 状态 | 进行中 | 用户观察到长按期间键盘灯仍可能切换，需在上屏和短按补发修复后做二轮桌面复测 |
| 修复 macOS 上屏（Cmd+V 粘贴） | 待修复 | 剪贴板注入正常，但 `pynput` 模拟 `Cmd+V` 在前台应用中未生效 |
| 修复短按 Caps Lock 补发 | 待修复 | `synthesize_caps_lock_toggle()` 在真实环境中未能切换大小写 |
| 补齐 `Caps Lock` 因果时间线追踪 | 已完成 | 已在按键触发、音频首帧入队、识别任务绑定、最终结果回到客户端这几处补统一 trace 日志 |
| 新增更底层 HID 探针 | 已完成 | `core/tools/macos_caps_probe.py` 已支持 `IOHIDManager` 独立模式和 `hid-manager` 组合模式，便于对照 `CGEventTap` 与更底层物理输入值 |
| 更新稳定文档入口 | 已完成 | `readme.md` 已同步项目级 `uv` 环境与稳定启动方式 |

## 风险与前置条件

- macOS 端要稳定监听按键、注入文本、读取选中文本，通常需要“辅助功能”“输入监控”“麦克风”等系统权限。
- 社区提供的 MLX 权重可直接获取，但仍需验证其与 CapsWriter 结果格式、时间戳与流式模式的对齐成本。
- 若后续发现 `mlx-qwen3-asr` 的 Python API 与当前引擎抽象差异较大，仍需写一层适配代码，而不是直接替换一两个函数。
- 当前客户端冷启动虽然已不再卡在 Win32 导入点，但如果 Codex / 终端进程未被加入 macOS 辅助功能白名单，系统级键盘监听仍无法真实生效。
- 启动日志中仍存在既有 `watchdog` 的 `FSEventsEmitter` 重复 watch 警告，该问题不属于本轮 `Caps Lock` 接管修复，但后续可能影响 LLM 目录热更新体验。
- 当前最大的技术风险已经切换为：虽然主程序已改走 `hidutil -> F18`，但仍需真实桌面回归确认 `pynput` 对 remap 后 `F18` 的 `keydown/keyup` 是否在本机权限环境下稳定可见，以及菜单栏麦克风图标是否只在录音期间出现。
- 当前新增风险已经收敛为三条：
  - `pynput` 在 macOS 新版本上模拟 `Cmd+V` 不被前台应用接受，可能需要改用 `CGEventPost` 或 `osascript` 注入粘贴命令。
  - `synthesize_caps_lock_toggle()` 合成的 Caps Lock 事件可能被 `hidutil` remap 再次截获为 F18，导致短按补发逻辑形成死循环/静默失败。
  - 长按 `Caps Lock` 期间，用户观察到键盘灯仍可能亮起并保持，说明系统锁定状态可能仍被切换。
- 已关闭的风险：`CFDataValidateRange` 崩溃已彻底修复；麦克风指示灯已确认正常工作。

## 本轮验证

- 已完成：`python -m py_compile` 验证新增和修改的服务端文件语法通过。
- 已完成：修复 `core/server/worker/check_model.py` 的模型白名单遗漏，`qwen_asr_mlx` 不再被启动前校验误判为不支持类型。
- 已完成：用假模块注入方式验证 `qwen_asr_mlx` 适配层的 `create_stream -> accept_waveform -> decode_stream -> cleanup` 逻辑闭环。
- 已完成：项目级 `uv` 环境验证，`.python-version` 已固定为 `3.13`，`requirements-server.txt` 与 `requirements-client.txt` 已全部安装进 `.venv`。
- 已完成：`uv pip check --python .venv/bin/python` 验证 223 个已安装包兼容，无缺包冲突。
- 已完成：本地模型目录验证，`models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit` 与 `models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-4bit` 均可被默认选择逻辑识别。
- 已完成：默认模型选择已切换为“优先本地 `1.7B-8bit`，缺失时回退本地 `1.7B-4bit`，最后回退远端 `mlx-community/Qwen3-ASR-1.7B-4bit`”。
- 已完成：真实 `mlx-qwen3-asr` 服务启动验证，服务端已在本机成功监听 `0.0.0.0:6016`。
- 已完成：真实音频 `/Users/edgar/Music/测试音频.m4a` 验证，最终识别结果为“现在开始录音。本音频用作千问三ASR Caps Writer后端MLX框架跑通的验证测试。”
- 已完成：修复 `core/server/engines/qwen_asr_mlx/asr_engine.py`，对非 `16kHz` 音频在引擎层先做本地线性重采样，避免上游库因缺少 `ffmpeg` 而报错。
- 已完成：真实客户端启动验证，当前阻断点已定位为 `pynput._util.win32.KeyTranslator` 的 macOS 导入失败。
- 已完成：`python -m py_compile` 通过，`key_mapper.py`、`shortcut_manager.py`、`text_output.py`、`llm_get_selection.py` 语法正常。
- 已完成：局部导入验证通过，`core.client.shortcut.key_mapper`、`core.client.shortcut.shortcut_manager`、`core.client.output.text_output`、`core.client.llm.llm_get_selection` 均可独立导入，不再触发整套客户端提前初始化。
- 已完成：`Caps Lock` 的 macOS 状态机单元级验证通过，重复按下/释放不会产生重复事件，逻辑分发表现符合预期。
- 已完成：真实客户端冷启动验证通过，程序已进入麦克风模式初始化、热词/LLM 启动与 WebSocket 连接阶段，不再卡死在早期导入。
- 已确认：真实冷启动环境中仍提示 “This process is not trusted! Input event monitoring will not be possible until it is added to accessibility clients.”，因此本轮尚未在已授权环境下完成真实按键录音闭环。
- 已完成：用户已为 Codex 开启“辅助功能 / 输入监控 / 麦克风”三项权限；重新启动后 `not trusted` 日志消失，权限问题不再是主阻塞。
- 已完成：真实桌面测试中，用户多次长按/短按 `Caps Lock`，客户端日志没有稳定出现对应录音启动链路，且系统默认大小写锁定行为未被改变。
- 已完成：独立 `pynput` 简化探针曾捕获到 `Caps Lock` 的 `Key.caps_lock` 与 `flagsChanged` 事件，但项目内 `ShortcutManager` 对照探针未能稳定收到同类回调，说明当前项目接法与简化探针之间存在关键差异。
- 已完成：新增 `core/tools/macos_caps_probe.py` 并通过 `python -m py_compile` 语法检查。
- 已完成：`python -m core.tools.macos_caps_probe --tap hid --mode observe --duration 20` 在用户真实按键操作下稳定捕获到多次 `flagsChanged keycode=57`，且事件对短按 / 长按均可见。
- 已完成：`python -m core.tools.macos_caps_probe --tap hid --mode swallow-caps --duration 20` 在用户真实按键操作下稳定吞掉多次 `Caps Lock` 事件，最终 `final_hid_alpha=False`、`final_hid_flags=0x00000000`，说明原生 HID tap 路线具备压住锁定状态的可行性。
- 已完成：`python -m py_compile` 已通过，覆盖 `core/client/shortcut/macos_caps_lock_tap.py`、`shortcut_manager.py`、`task.py` 与 `core/tools/macos_caps_probe.py`。
- 已完成：模块导入 smoke test 通过，`MacOSCapsLockTap`、`ShortcutManager`、`ShortcutTask` 均可独立导入。
- 已完成：`MacOSCapsLockTap.start() -> sleep(1s) -> stop()` 生命周期 smoke test 通过，线程与 RunLoop 可正常启动和释放。
- 已完成：完整客户端集成回归中，长按 `Caps Lock` 时录音链路确实会被触发，并已拿到服务端识别结果，但系统 `Caps Lock` 锁定状态仍会变化；最新原生日志仅记录到单条 `source_pid=0, is_key_down=False, event_flags=0x00000100, hid_flags=0x00010100, suppress=True` 事件，未能形成可靠的物理按下/松开对。
- 已完成：`python -m py_compile` 已通过，覆盖 `core/client/state.py`、`core/client/shortcut/task.py`、`core/client/audio/stream.py`、`core/client/audio/recorder.py`、`core/client/output/result_processor.py`、`core/client/shortcut/shortcut_manager.py` 与 `core/tools/macos_caps_probe.py`。
- 已完成：导入 smoke test 通过，`ClientState`、`ShortcutTask`、`AudioStreamManager`、`AudioRecorder`、`ResultProcessor` 与 `IOHIDManagerListener` 均可独立导入。
- 已完成：`python -m core.tools.macos_caps_probe --tap manager --mode observe --duration 1.0` smoke test 通过，`IOHIDManager` backend 可独立启动和关闭。
- 已完成：`python -m core.tools.macos_caps_probe --tap hid-manager --mode observe --duration 1.0` smoke test 通过，`CGEventTap + IOHIDManager` 组合模式可共同挂载到同一 RunLoop。
- 已完成：独立麦克风探针实测通过，默认输入设备在静默与说话之间存在明显能量差异，不是全零帧；这说明客户端拿到的确实是真实麦克风波形。
- 已完成：完整客户端 `Caps Lock` 实测通过，按下后录音启动、松开后结束录音，同一条 `trace` 上能追到首帧音频入队、前几帧音频能量与最终识别结果，证明按键链路和真实麦克风采集链路都已贯通。
- 已确认未解：客户端在结果处理阶段仍会触发 `Assertion failed: (range.location <= dataLength), function __CFDataValidateRange, file CFData.c, line 219.`，后续需要单独定位这个 CFData 断言，避免继续污染桌面测试。
- 已完成：`python -m py_compile` 已通过，覆盖 `start_client.py`、`core/client/main.py`、`core/client/app.py`、`core/client/manager/mic_runner.py`、`core/client/audio/stream.py`、`core/client/shortcut/task.py`、`core/client/shortcut/shortcut_manager.py`、`core/client/launcher/macos_caps_supervisor.py` 以及全部新增 macOS Caps 模块。
- 已完成：`python -m core.client.shortcut.macos_caps_remap status -> enable -> status -> restore -> status` 已通过，确认 `hidutil` 映射启用与恢复闭环正常。
- 已完成：`MacOSCapsController` 短按 / 长按单元 smoke 通过，短按会触发 `toggle`，长按会按顺序触发 `start -> stop`。
- 已完成：`python start_client.py` 新入口 smoke 通过，已成功进入 `macos_caps_supervisor -> core.client.main` 子进程链路，结束后 `UserKeyMapping` 已恢复为空映射。
- 已完成：`CapsWriterClient().stream.start_recording_session() -> stop_recording_session()` smoke 通过，macOS 当前配置下音频流确实按需打开并在会话结束后关闭。
- 未完成：尚未在本轮自动化里完成“真实物理 Caps Lock 按键 + 真实说话 + 系统麦克风图标”三者同时成立的桌面人工回归。
- 已完成：用户桌面测试确认“连续短按 Caps Lock 时，大小写锁定切换正常；短按不会误触发麦克风图标”。
- 已完成：用户桌面测试确认“长按录音链路已跑通，服务端结果已返回客户端”，当前终端输出结果为“接口啊，所以说呢，就是说我们”。
- 已完成：用户桌面测试确认“本轮没有观察到菜单栏左侧麦克风图标，TextEdit 未成功上屏”。
- 已确认新事实：客户端在识别结果返回并打印到终端后，立刻触发 `Assertion failed: (range.location <= dataLength), function __CFDataValidateRange, file CFData.c, line 219.`，随后 supervisor 恢复键位映射；这说明崩溃点位于结果输出阶段，而不是录音上传或服务端推理阶段。
- 已确认用户观察：长按期间键盘上的 Caps 指示灯会亮起且松手后仍可能保持，尚未通过实际打字验证是否真的处于大写锁定状态。
- 已完成（上一轮）：最小复现确认 `CFDataValidateRange` 的导入级触发点是 `core/client/llm/llm_output_typing.py` 顶层 `import keyboard`；已改为懒加载，导入级崩溃已消失。
- 已完成（上一轮）：`core/client/clipboard/clipboard.py` 的剪贴板读写在 macOS 下已改走 `pbcopy/pbpaste` 子进程，不再直接触碰 `pyclip` Pasteboard 后端。
- 已完成（上一轮）：`paste_text()` 的粘贴命令已改为 macOS 下发送 `Cmd+V`，而非 `Ctrl+V`。
- 已完成（上一轮）：`safe_copy/safe_paste` 单独 smoke 通过；`paste_text()` 单独执行时可成功粘贴上屏。
- 未完成：尚未完成新一轮完整桌面回归，”长按录音 -> 结果返回 -> 上屏”端到端是否稳定仍待用户验证。
- 已完成：定位并修复 `result_processor.py:_log_modifier_key_state()` 中遗漏的 `import keyboard`，该方法在每次结果处理末尾被调用，是 macOS 上 `CFDataValidateRange` 崩溃的最终根因。
- 已完成：新一轮完整桌面回归通过，`CFDataValidateRange` 崩溃已彻底消失，客户端不再在结果返回后退出。
- 已确认：长按录音链路端到端通畅——录音、发送、服务端识别、结果返回、注入剪贴板均正常。
- 已确认：系统麦克风橙色圆点指示灯正常出现，时机正确（录音开始出现、录音结束消失）。
- 已确认未解：`Cmd+V` 粘贴上屏失败——识别结果已成功写入剪贴板，但 `pynput` 模拟的 `Cmd+V` 未能在前台应用中触发粘贴。
- 已确认未解：短按 `Caps Lock` 无法切换大小写——`synthesize_caps_lock_toggle()` 补发逻辑在真实环境中失效。

## 本轮完成项

- 已清理旧的 `native tap` 主程序路径，并把废弃实现归档到 `.archive/2026-05-18-macos-caps-remap/`。
- 已把 macOS 主程序切到 `hidutil Caps Lock -> F18` 路线，并补齐 supervisor、remap 管理、F18 监听、短按/长按控制器与按需开流。
- 已完成自动化 smoke：语法、supervisor 生命周期、`hidutil` 映射启停、音频流按需打开/关闭、控制器短按/长按分发。
- 已完成首轮用户桌面验证：短按切换正常；长按录音、松手返回结果正常；当前失败点集中在”上屏”和”系统状态保持”。
- 已完成产品口径收敛：最终结果返回时，客户端要”尝试上屏，同时复制到剪贴板”；Windows 保留原有打字/特定应用粘贴/LLM 流式打字体验，macOS 首版优先稳定上屏。
- 彻底修复 `CFDataValidateRange` 崩溃：`llm_output_typing.py` 和 `result_processor.py` 中的 `import keyboard` 均已在 macOS 下隔离。
- 确认麦克风指示灯正常工作，`sounddevice` 按需开流方案在真实环境中可触发系统橙色圆点。

## 尚存问题

- 上屏失败：`pynput` 模拟 `Cmd+V` 在真实前台应用中未能触发粘贴，但剪贴板内容已正确写入；排查方向包括：`pynput` 对 `Cmd+V` 的合成在 macOS 新版本的兼容性、`safe_copy` 到发送粘贴命令之间是否需要额外延时、或者改用 `CGEventPost` 直接注入键盘事件。
- 短按补发失效：`synthesize_caps_lock_toggle()` 在真实环境中没有成功切换大小写，需要排查合成事件是否真正被系统接受。
- 长按期间 Caps 指示灯仍可能亮起并保持，说明”长按录音不切换系统 Caps Lock 状态”这一产品要求尚未满足。

## 交接建议

- 下个会话优先修复两个功能缺陷：
  1. **上屏**：`paste_text()` 中 `pynput` 发送 `Cmd+V` 未生效，排查方向是时序问题（`safe_copy` 后加 sleep）或改用 `CGEventPost` / `osascript` 注入粘贴命令
  2. **短按补发**：`synthesize_caps_lock_toggle()` 失效，需要确认合成的 Caps Lock 事件是否被 `hidutil` remap 再次截获导致循环失效（remap 把 Caps Lock -> F18，那合成的 Caps Lock 也会变成 F18）
- 长按期间 Caps Lock 状态保护是第三优先级，在前两个功能可用后再排查。

## 新会话交接

- 当前最重要的结论：核心录音链路和服务端识别已完全打通，`CFDataValidateRange` 崩溃已彻底修复，麦克风指示灯工作正常。
- 剩余三个功能缺陷优先级排序：1）上屏（Cmd+V 未生效）→ 2）短按补发（synthesize 失效）→ 3）长按状态保护。
- 上屏排查方向：`pynput` 模拟 `Cmd+V` 可能在 macOS 新版本不被前台应用接受，备选方案包括 `CGEventPost`、`osascript -e 'tell application “System Events” to keystroke “v” using command down'`、或直接用 `subprocess` 调 AppleScript。
- 短按补发排查方向：`hidutil` remap 把 Caps Lock -> F18 是全局的，`synthesize_caps_lock_toggle()` 合成 Caps Lock 事件时可能被 remap 再次截获变成 F18，形成静默失败。解决思路是合成时临时恢复 remap、或直接合成 `NX_KEYTYPE_CAPS_LOCK` HID 事件绕过 `hidutil`。
- 客户端启动时 `pynput` 会输出 4 条 “This process is not trusted!” 假警告，实际权限已授予且功能正常工作，后续需消除或降级该日志。
- 产品规格口径更新：最终结果返回时，客户端要”尝试上屏，同时复制到剪贴板”；Windows 保留原有打字/命中 `paste_apps` 时改走粘贴/LLM 流式打字体验，macOS 首版优先稳定上屏。

## 单 Agent 编排

- 当前由主 Agent 负责全部工作。
- 暂未拆分子 Agent，避免在文档基线尚未稳定前产生并行口径漂移。

## 参考资料

- MLX 官方仓库: <https://github.com/ml-explore/mlx>
- MLX Qwen3-ASR 社区实现: <https://github.com/moona3k/mlx-qwen3-asr>
- MLX Community Qwen3-ASR-1.7B-4bit: <https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-4bit>
- MLX Community Qwen3-ASR-0.6B-bf16: <https://huggingface.co/mlx-community/Qwen3-ASR-0.6B-bf16>
