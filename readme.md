# CapsWriter-Offline

> 一个以“离线、低延迟、可高度自定义”为核心目标的语音输入项目。当前主代码基础最初面向 Windows 设计，`mac-dev` 分支正在推进 Apple Silicon 适配。

## 项目基本信息

| 项目项 | 说明 |
| --- | --- |
| 项目定位 | 本地离线语音输入、文件转录与 LLM 辅助修正工具 |
| 核心架构 | Client / Server 双进程架构；服务端负责推理，客户端负责录音、快捷键、上屏与 UI |
| 主要能力 | 实时听写、文件转录、热词替换、规则替换、LLM 角色、托盘交互、日记归档、UDP 广播与控制 |
| 当前稳定平台 | Windows 10/11 是现有稳定基线 |
| 当前演进方向 | 在保留现有整体架构的前提下，为 Apple Silicon MacBook 引入 macOS 输入链路，并新增基于 MLX 的 `Qwen3-ASR` 专用后端 |

## 架构概览

- 服务端：`start_server.py` 启动 WebSocket 服务、模型加载、识别流水线与结果分发。
- 客户端：`start_client.py` 启动快捷键监听、音频采集、结果后处理、文本注入、托盘与提示 UI。
- 模型层：当前主力模型为 ONNX 编码器 + GGUF 解码器组合，也保留 Paraformer / SenseVoice / 标点模型 / 对齐器等引擎。
- 配置层：根目录 `config_server.py`、`config_client.py`、`hot*.txt`、`LLM/*.py` 承担主要配置入口。

## 开发与测试环境

| 项目项 | 说明 |
| --- | --- |
| Python 版本约定 | 默认以 `mise` 管理的 Python 3.13 作为本地解释器；进入本仓库后使用项目级 `uv` 虚拟环境 |
| 依赖拆分 | 客户端依赖在 `requirements-client.txt`，服务端依赖在 `requirements-server.txt` |
| 外部工具 | 文件转录依赖 `ffmpeg` 在 `PATH` 中可用 |
| 模型目录 | 所有模型放在根目录 `models/` 下的既定子目录中 |
| 当前注意事项 | 现有依赖和实现仍以 Windows 为主，macOS 适配进展请查看 `CLAUDE.md` |

## 稳定启动入口

```bash
uv python pin 3.13
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-server.txt
uv pip install --python .venv/bin/python -r requirements-client.txt
.venv/bin/python start_server.py
.venv/bin/python start_client.py
```

- macOS 当前稳定客户端入口仍是 `start_client.py`；在麦克风实时模式下，它会先通过父进程临时把物理 `Caps Lock` 映射成 `F18`，客户端退出后自动恢复原始键位映射。

## 项目目录结构

```text
CapsWriter-Offline/
├── AGENTS.md
├── CLAUDE.md
├── LICENSE
├── assets/
│   ├── BUILD_GUIDE.md
│   ├── demo.png
│   └── ...
├── docs/
│   ├── CHANGELOG.md
│   ├── 环境依赖安装说明.md
│   ├── 文件转录功能如何使用.md
│   ├── 热词功能如何使用.md
│   ├── 角色功能如何使用.md
│   ├── 显卡加速的若干问题.md
│   ├── 模型下载的若干问题.md
│   ├── 识别语言如何配置.md
│   └── text_merge_algorithm.md
├── LLM/
│   ├── default.py
│   ├── 大助理.py
│   ├── 小助理.py
│   └── 翻译.py
├── models/
│   ├── Fun-ASR-Nano/
│   ├── Paraformer/
│   ├── Punct-CT-Transformer/
│   ├── Qwen3-ASR/
│   ├── Qwen3-ForcedAligner/
│   └── SenseVoice-Small/
├── core/
│   ├── client/
│   │   ├── audio/
│   │   ├── clipboard/
│   │   ├── connection/
│   │   ├── hotword/
│   │   ├── llm/
│   │   ├── manager/
│   │   ├── output/
│   │   ├── shortcut/
│   │   ├── transcribe/
│   │   └── ui/
│   ├── server/
│   │   ├── connection/
│   │   ├── engines/
│   │   ├── formatter/
│   │   ├── merger/
│   │   └── worker/
│   ├── tools/
│   └── ui/
├── config_client.py
├── config_server.py
├── hot-rule.txt
├── hot-server.txt
├── hot.txt
├── requirements-client.txt
├── requirements-server.txt
├── start_client.py
├── start_server.py
└── readme.md
```

## 重要文档索引表

| 内容 | 文件路径 |
| --- | --- |
| 稳定项目入口、目录骨架、运行方式 | `readme.md` |
| 协作规则、开发流程、文件安全策略 | `AGENTS.md` |
| 当前阶段目标、任务看板、阶段决策 | `CLAUDE.md` |
| 打包说明 | `assets/BUILD_GUIDE.md` |
| 更新日志 | `docs/CHANGELOG.md` |
| Qwen3-ASR macOS 专项规划、模型选型与客户端输入/上屏规格 | `docs/Qwen3-ASR_macOS_最小适配规划.md` |
| 模型下载与目录说明 | `docs/模型下载的若干问题.md` |
| 环境依赖安装 | `docs/环境依赖安装说明.md` |
| 识别语言配置 | `docs/识别语言如何配置.md` |
| 文件转录说明 | `docs/文件转录功能如何使用.md` |
| 热词系统说明 | `docs/热词功能如何使用.md` |
| LLM 角色说明 | `docs/角色功能如何使用.md` |
| 文本拼接算法说明 | `docs/text_merge_algorithm.md` |
| 显卡加速问题说明 | `docs/显卡加速的若干问题.md` |

## 关键代码入口

| 区域 | 入口 |
| --- | --- |
| 服务端启动 | `start_server.py` |
| 客户端启动 | `start_client.py` |
| 服务端门面 | `core/server/app.py` |
| 客户端门面 | `core/client/app.py` |
| 模型加载 | `core/server/worker/model_loader.py` |
| 识别引擎工厂 | `core/server/engines/factory.py` |
| 快捷键系统 | `core/client/shortcut/` |
| 结果输出 | `core/client/output/` |

## 代码规范与开发测试闭环入口

- 通用协作规则、代码规范、文件安全要求：见 `AGENTS.md`
- 当前阶段任务、风险、技术路线：见 `CLAUDE.md`
- 稳定的专项说明、算法说明、使用说明：见 `docs/`
