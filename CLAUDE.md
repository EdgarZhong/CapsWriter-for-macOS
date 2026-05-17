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
- 进行中：更新 `docs/` 下的 Qwen3-ASR macOS 专项规划，并同步模型选型建议。

## 当前技术判断

| 方向 | 当前判断 | 依据 |
| --- | --- | --- |
| macOS 服务端后端 | 新增 `qwen_asr_mlx`，不直接替换现有 Windows `qwen_asr_gguf` | 更符合 Apple Silicon 常驻、离电场景，也更容易保持跨平台兼容边界 |
| Apple Silicon 运行效率 | MLX 路线优先于现有 `ONNX + GGUF` 拼装路线 | MLX 面向 Apple Silicon 设计，适合统一内存与常驻低功耗场景 |
| 现有工作可借鉴度 | 较高 | `mlx-qwen3-asr` 已提供 Python API、Session、streaming、timestamps、mic、server |
| 客户端输入输出 | 仍需建立平台抽象层 | 当前客户端大量依赖 `keyboard`、`pynput._util.win32`、`win32_event_filter`、`Ctrl+C`、`keyboard.write` |
| 模型获取难度 | 低 | 目前已有可直接使用的 `mlx-community` Qwen3-ASR 权重，无需先自行转换 |

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

### 决策三：模型优先级按常驻离电场景排序

- 首选：`Qwen3-ASR-1.7B-4bit`
- 备选轻量：`Qwen3-ASR-0.6B-bf16`
- 备选高质量：`Qwen3-ASR-1.7B-bf16`

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
| 输出专项规划文档 | 进行中 | 更新为 `qwen_asr_mlx` 主路线并补模型选型 |
| 确认 MLX 模型规格 | 进行中 | 当前重点比较 `1.7B-4bit` / `1.7B-bf16` / `0.6B-bf16` |
| 设计 `qwen_asr_mlx` 适配层 | 待开始 | 对齐 `BaseASREngine` 与当前结果结构 |
| 首版结果模式收敛 | 已完成 | 首版只要求松开后快速返回最终结果，不要求中间流式显示 |
| 实现 macOS 客户端输入链路 | 待开始 | 仅覆盖当前语音输入主路径所需能力 |
| 更新稳定文档入口 | 进行中 | 保持 `readme.md` 只提供稳定索引，不写动态看板 |

## 风险与前置条件

- macOS 端要稳定监听按键、注入文本、读取选中文本，通常需要“辅助功能”“输入监控”“麦克风”等系统权限。
- 社区提供的 MLX 权重可直接获取，但仍需验证其与 CapsWriter 结果格式、时间戳与流式模式的对齐成本。
- 若后续发现 `mlx-qwen3-asr` 的 Python API 与当前引擎抽象差异较大，仍需写一层适配代码，而不是直接替换一两个函数。

## 单 Agent 编排

- 当前由主 Agent 负责全部工作。
- 暂未拆分子 Agent，避免在文档基线尚未稳定前产生并行口径漂移。

## 参考资料

- MLX 官方仓库: <https://github.com/ml-explore/mlx>
- MLX Qwen3-ASR 社区实现: <https://github.com/moona3k/mlx-qwen3-asr>
- MLX Community Qwen3-ASR-1.7B-4bit: <https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-4bit>
- MLX Community Qwen3-ASR-0.6B-bf16: <https://huggingface.co/mlx-community/Qwen3-ASR-0.6B-bf16>
