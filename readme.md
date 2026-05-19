# CapsWriter for macOS

> [CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline) 的 Apple Silicon 最小适配版。
> 按住 Caps Lock 说话，松手即输入。完全离线，延迟极低。

原项目面向 Windows 设计。本 fork 在保留原有架构的前提下，为 macOS / Apple Silicon 新增了 MLX 推理后端和命令行管理入口，去掉了 GUI（后续版本计划加入原生 macOS GUI）。

## 体验

- **延迟**：Apple Silicon 上松手后约 0.3～0.6 秒返回结果
- **离线**：模型本地运行，录音数据不出设备
- **准确**：Qwen3-ASR 中文识别效果优秀，支持中英混输
- **常驻**：后台进程轻量，发热低，适合离电使用

## 系统要求

- macOS 13 及以上，Apple Silicon（M1 / M2 / M3 / M4）
- Python 3.13（推荐用 [mise](https://mise.jdx.dev/) 或 [pyenv](https://github.com/pyenv/pyenv) 管理）
- [uv](https://docs.astral.sh/uv/)（依赖管理）

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/EdgarZhong/CapsWriter-Offline.git
cd CapsWriter-Offline
git checkout mac-dev
```

### 2. 安装依赖

```bash
uv python pin 3.13
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-server.txt
uv pip install --python .venv/bin/python -r requirements-client.txt
```

### 3. 下载模型

从 Hugging Face 下载 Qwen3-ASR 的 MLX 量化权重，放到 `models/Qwen3-ASR/mlx/` 目录：

| 规格 | HuggingFace 仓库 | 大小 | 推荐场景 |
|------|-----------------|------|---------|
| 1.7B-8bit（默认） | `mlx-community/Qwen3-ASR-1.7B-8bit` | ~1.8 GB | 日常使用 |
| 1.7B-4bit（轻量） | `mlx-community/Qwen3-ASR-1.7B-4bit` | ~1.0 GB | 低内存 / 重度离电 |
| 1.7B-bf16（高质量） | `mlx-community/Qwen3-ASR-1.7B-bf16` | ~3.5 GB | 插电 / 追求最高准确率 |

```bash
# 示例：下载默认规格
pip install huggingface_hub
huggingface-cli download mlx-community/Qwen3-ASR-1.7B-8bit \
  --local-dir models/Qwen3-ASR/mlx
```

也可以直接在 Hugging Face 网页下载后放入对应目录。

### 4. 注册全局命令

```bash
bash install.sh
```

执行后 `capswriter` 命令在当前用户全局可用（写入 `~/.local/bin/`）。

### 5. 授予辅助功能权限

首次启动时 macOS 会弹窗请求权限。请在系统设置中确认：

**系统设置 → 隐私与安全性 → 辅助功能**

将运行 client 的 Python（通常是 `.venv/bin/python`）或终端 App 添加到列表。

此权限用于：键盘事件拦截（CGEventTap）和自动粘贴到输入框。

## 使用

### 启动和停止

```bash
capswriter start     # 启动后台服务（server + client）
capswriter stop      # 停止
capswriter restart   # 重启（修改配置后使用）
capswriter status    # 查看运行状态
```

### 开机自启

```bash
capswriter install   # 注册 launchd，登录后自动启动
capswriter uninstall # 取消自启
```

### 语音输入

启动后在任意应用的输入框：

- **长按 Caps Lock** → 开始录音（松手前保持按住）
- **松开** → 识别完成，结果自动写入剪贴板并粘贴到光标位置
- **短按 Caps Lock** → 正常切换大小写，不触发录音

### 热词（自定义替换）

编辑根目录 `hot.txt`，client 运行时实时生效，无需重启：

```
# 格式：最终输出 | 别名1 | 别名2 | ...
CapsWriter | Caps Rider
Claude Code | cloud code | cloud cold
Qwen3-ASR  | 千问ASR
```

基于音素模糊匹配，说出别名时自动替换为第一列的目标词。

### 修改配置

核心配置在 `config_client.py` 和 `config_server.py`，修改后执行：

```bash
capswriter restart
```

### 诊断

```bash
capswriter doctor            # 检查环境、权限、模型文件
capswriter remap status      # 查看当前键盘映射状态
capswriter remap restore     # 恢复键盘映射（仅限 client 未运行时）
capswriter remap clear --force  # 清空所有键盘映射（救援命令）
```

## 与原版的区别

| 项目 | 原版（Windows） | 本 fork（macOS） |
|------|----------------|-----------------|
| 推理后端 | GGUF + ONNX | Apple MLX |
| 快捷键 | Windows 钩子 | CGEventTap + hidutil remap |
| 进程管理 | 手动启动 | `capswriterd` 守护进程 |
| 自启动 | 任务计划程序 | launchd |
| GUI | 系统托盘 | 无（v1 CLI 优先） |

## 后续计划

- [ ] 原生 macOS GUI（菜单栏图标 / 状态提示）
- [ ] 按住说话时的实时中间结果显示

## 致谢

本项目基于 [HaujetZhao/CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline) 开发，遵循原项目 MIT 协议。

## License

MIT © Haujet Zhao（原作者） / Edgar Zhong（macOS 适配）
