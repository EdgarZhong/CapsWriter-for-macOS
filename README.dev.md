# CapsWriter for macOS 开发说明

本文是三份核心文档中的稳定项目说明。安装、使用与常见问题见 [用户 README](readme.md)；协作规则、代码规范与开发测试 SOP 见 [AGENTS.md](AGENTS.md)；当前目标、任务与执行状态见 [CLAUDE.md](CLAUDE.md)。

## 项目定位与现有能力

本项目是 CapsWriter-Offline 的 Apple Silicon 适配版，提供离线语音输入、Caps Lock 长按录音、短按切换大小写、结果复制与自动粘贴、热词与别名替换、菜单栏状态及编辑入口。默认使用 Qwen3-ASR MLX 后端。

## 当前运行架构

- `capswriter.py` 提供安装、启停、状态与诊断 CLI；launchd 分别托管客户端与 ASR 服务。
- `CapsWriter.app` 的原生启动器嵌入 CPython，运行 Python 客户端；客户端负责快捷键、录音、热词处理和结果输出，菜单栏通过 macOS 原生能力集成。
- 客户端通过 WebSocket 向服务端发送音频；`core/server/` 负责连接、任务调度和后端调用。
- 默认 `qwen_asr_mlx` 通过本地 `mlx-qwen3-asr` 子模块 Runner 调用 MLX。录音期间缓存音频，收到结束标记后推理；不等同于录音期间持续输出识别结果。
- 当前安装方式为源码加 `install.sh`，尚未提供自包含 DMG。Swift 原生客户端与服务化是目标设计，详见架构规格第零节，不能视为现有实现。

## 项目目录结构

```text
CapsWriter-Offline/
├── readme.md                 # 用户安装、使用和故障排查
├── README.dev.md             # 稳定开发说明与文档索引
├── AGENTS.md                 # 规则、规范与开发测试 SOP
├── CLAUDE.md                 # 当前迭代看板
├── capswriter.py             # 管理 CLI
├── start_client.py / start_server.py
├── config_client.py / config_server.py
├── install.sh / build_launcher.sh
├── requirements-client.txt / requirements-server.txt
├── CapsWriter.app/           # macOS App 与原生启动器
├── core/
│   ├── client/               # 录音、快捷键、热词、LLM、编辑与输出
│   ├── server/               # 连接、调度、推理引擎与结果处理
│   ├── ui/                   # 界面和通知支持
│   ├── tools/                # 公共工具与平台探针
│   └── protocol.py           # 通信协议
├── native/CapsWriter/        # Swift Dashboard 与状态兼容层
├── mlx-qwen3-asr/             # MLX 推理 Git 子模块
├── LLM/                      # 既有角色配置
├── tools/                    # 回归检查与诊断脚本
├── evals/                    # 评测入口与数据集说明
├── docs/                     # 专项规格、SOP 与历史记录
├── assets/                   # 图标等资源
├── models/                   # 本地模型与下载说明
├── diagnogs/                 # 诊断材料
└── .archive/                 # 本地备份，Git 忽略
```

## 开发与测试环境

- macOS 13 及以上、Apple Silicon；项目使用 Python 3.13 与 `.venv`，依赖由 uv 管理。
- 客户端与服务端依赖分别见 `requirements-client.txt`、`requirements-server.txt`；macOS 原生启动器构建需要 Apple 编译工具链。
- 克隆时初始化 `mlx-qwen3-asr` 子模块；其跟踪分支为 `capswriter-macos`，版本以主仓库记录的子模块提交为准。
- 按 [用户安装步骤](readme.md#安装) 准备模型、权限与运行环境。`bash install.sh` 会安装依赖、重建启动器并写入全局 CLI，不是只读检查。
- 默认模型为 Qwen3-ASR-1.7B-8bit；真实录音、输入注入、权限和模型推理须在 macOS 真机验证。下面的无麦克风测试不能替代真机验收。

```bash
# 初始化推理子模块；依赖更新遵循项目环境约定。
git submodule update --init --recursive mlx-qwen3-asr
uv pip install --python .venv/bin/python -r requirements-server.txt
uv pip install --python .venv/bin/python -r requirements-client.txt

# 修改启动器后重建；签名变化可能需要重新授权。
bash build_launcher.sh
```

## 代码规范与开发测试闭环

统一遵循 [AGENTS.md 代码规范](AGENTS.md#代码规范) 和 [开发测试 SOP](AGENTS.md#开发测试-sop)：启动时读取核心文档与近期 Markdown 变更，收敛范围，重要文件先备份，再实施最小必要修改，运行定向验证，将结果回写当前看板。平台能力保持边界清晰，新增注释用中文解释意图与非直观逻辑。

规则只维护在 `AGENTS.md`，动态执行状态只维护在 `CLAUDE.md`；本文维护稳定事实与可复用入口。

## 关键文档

| 文档 | 路径 | 内容 |
|------|------|------|
| macOS 架构决策 | `docs/macos-architecture-decisions.md` | 现有 launchd、权限、录音与推理架构记录；第零节为客户端主导编排、独立计算服务与 DMG 的目标设计（尚未实施） |
| Qwen3-ASR macOS 适配规格 | `docs/Qwen3-ASR_macOS_最小适配规划.md` | macOS 版 Qwen3-ASR 后端接入范围、模型规格和阶段边界 |
| ASR 调优总文档 | `docs/ASR调优总文档.md` | ASR 默认值、回归排查证据、Worker延迟/顺序调度、语言前缀修复与回退方式、评测入口 |
| 语音输入 Roadmap 与桌面助手设计 | `docs/语音输入roadmap与桌面助手设计.md` | 产品化范围、热词服务化与已知算法问题、后续 ASR/桌面助手路线、暂缓实施的灵动岛设想与 Golden Set 评测方案 |
| Dashboard 窗口材质分层规格 | `docs/dashboard-窗口材质分层规格.md` | 整窗磨砂半透明三层结构（背板/标题栏渐隐/侧栏玻璃）的需求口径、禁止项、技术线索与可执行验收标准；实现待按规格进行 |

---

## 调度与音频回归检查

以下检查不加载识别模型、不占用麦克风：

```bash
.venv/bin/python tools/test_worker_scheduling.py -v
.venv/bin/python tools/test_mic_shortcut_lifecycle.py -v
.venv/bin/python tools/test_stream_stop_leak.py -v
```

Worker对同一连接的音频包与结束标记保持FIFO、跨连接轮转；已有待处理包时不等待未来包，每轮收包有数量上限。Runner最终日志将`final排队`和`final处理`分别记录，`耗时`仍是提交至完成的总时长。

当前Qwen MLX Runner在录音期间接收并缓存音频，收到结束标记后才调用模型；模型内部的≤30秒切分发生在此次调用中。录音过程中提前推理稳定片段尚未实现。

## 麦克风生命周期验证

开发回归入口（不使用真实麦克风）：

```bash
.venv/bin/python tools/test_stream_stop_leak.py
.venv/bin/python tools/test_mic_shortcut_lifecycle.py
```

启停机制与验证边界见 [macOS 架构决策](docs/macos-architecture-decisions.md#十客户端麦克风生命周期)。

## 模型权重常驻验证

可复现检查：`PYTHONPATH=mlx-qwen3-asr .venv/bin/python -m pytest mlx-qwen3-asr/tests/test_capswriter_runner.py mlx-qwen3-asr/tests/test_wired_memory.py -q`。真机独立进程检查：`.venv/bin/python tools/probe_qwen_residency.py --mode on --idle-seconds 1800`；关闭模式改为`--mode off`，两者顺序运行，不重启日常服务。

## 编辑框回归入口

```bash
.venv/bin/python tools/test_editor_annotation.py
.venv/bin/python tools/test_editor_result_flow.py
.venv/bin/python tools/test_editor_ui_contract.py
```

## 原生 Dashboard 开发入口

`native/CapsWriter` 是独立 Swift package，最低 macOS 13，使用系统 Swift 工具链；当前提供四页导航和读取现有客户端快照的概览。设置页已提供持久化的跟随系统／浅色／深色外观选择；听写设置、词库与模型管理尚未接入操作，不代表原生客户端迁移完成。macOS 26 使用系统玻璃导航与控件，旧系统回退系统材质，并尊重减少透明度设置。

```bash
# 无 XCTest 依赖的合成状态兼容检查，不读写真实用户状态。
swift run --package-path native/CapsWriter DashboardCoreChecks
# 构建并临时签名开发 App；旧开发包自动备份至 .archive/。
bash tools/build_dashboard.sh
```

产物位于 `build/CapsWriterDashboard.app`，使用独立开发 Bundle ID。当前只读 `~/.capswriter/state/status.json`，不监听快捷键、不占用麦克风。开发签名不等同于分发签名或公证；此包没有捆绑 Python/ASR，不是完整 DMG 产品。

## 版本与分支

`main` 为发布分支，`mac-dev` 为开发分支。本 fork 使用 0.x 版本编号，发布记录维护于 main 根目录的 `CHANGELOG.md`；上游版本号与本 fork 的版本号分开解释。用户 `readme.md` 与 main 当前发布版本保持一致，不写入未发布能力；开发说明独立维护，合并时保留各自职责。
