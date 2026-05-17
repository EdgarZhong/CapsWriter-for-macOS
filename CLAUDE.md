# CapsWriter-Offline 当前阶段同步

## 当前目标

- 分支：`mac-dev`
- 基线来源：`master`
- 当前阶段目标：仅围绕 `Qwen3-ASR` 单一路线，为 macOS / Apple Silicon 新增 `qwen_asr_mlx` 后端，在保持现有 Client / Server 架构不大动的前提下，优先优化后台常驻、离电使用、低发热与低延迟体验。

## 当前状态

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
- 已确认失败：在用户真实桌面环境中，`Caps Lock` 无论长按还是短按，行为都与系统默认一致，既不会唤起录音，也不会改变顶部菜单栏的麦克风指示状态，松手后仍会触发大小写锁定切换。
- 已确认失败：用户已在非微信窗口（`TextEdit`）以及 `ABC` / 拼音输入法下重复测试，现象完全一致，因此当前问题已排除“微信前台接管”和“输入法切换映射”这两个方向。
- 已确认失败：当前基于 `pynput` / `darwin_intercept` 的 macOS `Caps Lock` 接管方案，在真实环境中不能稳定进入项目的热键状态机，不能作为继续迭代的可靠基础。

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
| 实现 macOS 客户端输入链路 | 进行中 | 权限已打通，但当前 `pynput` 路线在真实环境中仍无法可靠接管 `Caps Lock`，需要切换到更底层的 macOS 原生事件 tap 专用实现 |
| 更新稳定文档入口 | 已完成 | `readme.md` 已同步项目级 `uv` 环境与稳定启动方式 |

## 风险与前置条件

- macOS 端要稳定监听按键、注入文本、读取选中文本，通常需要“辅助功能”“输入监控”“麦克风”等系统权限。
- 社区提供的 MLX 权重可直接获取，但仍需验证其与 CapsWriter 结果格式、时间戳与流式模式的对齐成本。
- 若后续发现 `mlx-qwen3-asr` 的 Python API 与当前引擎抽象差异较大，仍需写一层适配代码，而不是直接替换一两个函数。
- 当前客户端冷启动虽然已不再卡在 Win32 导入点，但如果 Codex / 终端进程未被加入 macOS 辅助功能白名单，系统级键盘监听仍无法真实生效。
- 启动日志中仍存在既有 `watchdog` 的 `FSEventsEmitter` 重复 watch 警告，该问题不属于本轮 `Caps Lock` 接管修复，但后续可能影响 LLM 目录热更新体验。
- 当前最大的技术风险已经收敛为：`Caps Lock` 在 macOS 上不能继续依赖 `pynput` 通用监听抽象，需要直接上更底层的 Quartz / 原生事件 tap 专用实现，否则无法保证“长按录音、松手结束、且不切换大小写”的交互目标。

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
- 已完成：更底层的原始 Quartz HID tap 探针尝试尚未形成稳定结论，当前最可靠的阶段性判断是“不要继续围绕 `pynput` 路线补丁式修修补补”。

## 新会话交接

- 当前最重要的结论：不要再继续把 macOS `Caps Lock` 接管建立在 `pynput` 的通用监听抽象上。
- 下一步推荐路线：保留 Windows 逻辑不动，macOS 下把 `Caps Lock` 从通用 `ShortcutManager` 里拆出，改为独立原生事件 tap 模块，只把“按下开始录音 / 松开结束录音 / 维持原锁定状态”桥接回现有录音任务链路。
- 继续排查时，应优先对比“最小可工作的原生 tap 示例”和“项目内 manager 接法”之间的差异，而不是继续让用户重复做盲测。

## 单 Agent 编排

- 当前由主 Agent 负责全部工作。
- 暂未拆分子 Agent，避免在文档基线尚未稳定前产生并行口径漂移。

## 参考资料

- MLX 官方仓库: <https://github.com/ml-explore/mlx>
- MLX Qwen3-ASR 社区实现: <https://github.com/moona3k/mlx-qwen3-asr>
- MLX Community Qwen3-ASR-1.7B-4bit: <https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-4bit>
- MLX Community Qwen3-ASR-0.6B-bf16: <https://huggingface.co/mlx-community/Qwen3-ASR-0.6B-bf16>
