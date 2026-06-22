# CapsWriter for macOS

> 按住 Caps Lock 说话，松手即输入。完全离线，Apple Silicon 原生加速。

[CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline) 的 Apple Silicon 适配版。原项目面向 Windows 设计，本 fork 为 macOS 重写了推理后端、键盘捕获和进程管理。

## 亮点

- **极低延迟**：Apple Silicon 上松手后约 0.3～0.6 秒返回结果
- **完全离线**：模型本地运行，录音数据不出设备
- **中文首选**：Qwen3-ASR 中文识别准确率出色，支持中英混输
- **轻量常驻**：后台能耗低，适合整天开着使用
- **原生集成**：以 .app 形式运行，支持录音时菜单栏麦克风胶囊显示，隐私无忧
- **菜单栏常驻**：原生矢量图标常驻菜单栏，自动适配深 / 浅色外观，位置可 ⌘ 拖动固定

## 系统要求

- macOS 13 及以上，Apple Silicon（M1 / M2 / M3 / M4 / M5）
- Python 3.13（推荐用 [mise](https://mise.jdx.dev/) 管理）
- [uv](https://docs.astral.sh/uv/)（依赖安装工具）

## 安装

### 第一步：克隆仓库

```bash
git clone https://github.com/EdgarZhong/CapsWriter-Offline.git
cd CapsWriter-Offline
git checkout mac-dev
```

### 第二步：本地安装

需要先安装 `uv`（若未安装：`brew install uv`）。随后执行：

```bash
bash install.sh
```

`install.sh` 会自动完成以下事项：

- 创建 / 检查 `.venv`（Python 3.13）
- 安装 / 更新 client 与 server 依赖
- 在当前机器重建 `CapsWriter.app` 启动器
- 安装全局 `capswriter` 命令到 `~/.local/bin`

> macOS 版启动器会嵌入 CPython，但不会把开发者机器的 `/Users/...` 路径写死到二进制里。安装脚本会在你的机器上重新链接 `.venv` 对应的 `libpython`，避免换用户名后 dyld 找不到 Python 动态库。

### 第三步：下载模型

从 Hugging Face 下载 Qwen3-ASR MLX 量化版本：

| 规格 | HuggingFace 仓库 | 大小 | 推荐场景 |
|------|-----------------|------|---------|
| 1.7B-8bit（默认） | `mlx-community/Qwen3-ASR-1.7B-8bit` | ~1.8 GB | 日常使用 |
| 1.7B-4bit（轻量） | `mlx-community/Qwen3-ASR-1.7B-4bit` | ~1.0 GB | 低内存 / 重度离电 |

```bash
uv pip install --python .venv/bin/python huggingface_hub

# 下载默认规格（8bit，约 1.8 GB）
.venv/bin/huggingface-cli download mlx-community/Qwen3-ASR-1.7B-8bit \
  --local-dir models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit
```

> 国内访问 Hugging Face 可在命令前加 `HF_ENDPOINT=https://hf-mirror.com`。

### 第四步：注册并启动后台服务

```bash
capswriter install
capswriter start
```

`capswriter install` 会注册 client / server 两个 launchd 服务，后续登录后自动启动。
若 `install.sh` 提示 `~/.local/bin` 不在 PATH，按提示将以下内容加入 `~/.zshrc` 后重开终端：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 第五步：授权（首次启动时）

CapsWriter 需要两项系统权限：（均位于“隐私与安全性”设置）

**辅助功能权限**（用于自动粘贴）
**输入监控权限**（用于caps lock动作拦截）

启动时若 CapsWriter 检测到权限缺失，会弹出引导对话框并自动打开系统设置，按提示操作即可。
若对应权限已打开但软件未按预期工作，请在相应设置列表中点击'-'将CapsWriter删除，然后重启软件重新授权。

**麦克风权限**

首次录音时 macOS 自动弹出授权对话框，点击「好」即可。

---

## 使用

### 启动与停止

```bash
capswriter start     # 启动后台服务
capswriter stop      # 停止
capswriter restart   # 重启（修改配置后使用）
capswriter status    # 查看当前运行状态
```

### 开机自启

```bash
capswriter install   # 注册 launchd，登录后自动启动，无需手动 start
capswriter uninstall # 取消自启
```

### 语音输入

启动后，在任意应用的输入框：

1. **长按 Caps Lock** → 开始录音（保持按住）
2. **松开** → 识别完成，写入剪贴板
3. **粘贴** →软件客户端尝试将结果自动粘贴到光标位置一次，**推荐配合maccy等剪贴板历史管理工具使用本软件**，便捷找回转录历史。
4. **短按 Caps Lock**（< 0.3 秒）→ 正常切换大小写，不触发录音

### 菜单栏图标

客户端运行时，菜单栏会常驻一个 CapsWriter 图标（矢量绘制，自动适配深 / 浅色菜单栏）。按住 **⌘ 拖动**可把它移到喜欢的位置，系统会记住，重启后保持不变；退出客户端时图标自动消失。

点击菜单栏图标可查看当前状态，并执行复制最近结果、编辑热词、重启 CapsWriter、退出 CapsWriter 等操作。

---

## 诊断与修复

```bash
capswriter doctor               # 检查环境、权限、模型文件
capswriter remap status         # 查看当前键盘映射状态
capswriter remap restore        # 恢复键盘映射（仅限未运行时使用）
capswriter remap clear --force  # 清空所有键盘映射（救援命令，若您设置有其它键盘映射，谨慎使用）
```
>键盘映射：软件使用`hidutil`将特殊键capslock映射到不常用键F18并进行监听，常规情况下不会影响用户通过hidutil已有的自定义键盘映射。
>
>若软件破坏已有映射或未能正确管理caps键，可退出软件并使用restore恢复
---

## 热词（自定义替换）

继承原项目的客户端热词能力
编辑根目录 `hot.txt`，运行时实时生效，无需重启：

```
# 格式：最终输出 | 别名1 | 别名2 | ...
CapsWriter  | Caps Rider | caps writer
Claude Code | cloud code | cloud cold
Qwen3-ASR   | 千问ASR
```

基于音素模糊匹配，说出别名时自动替换为目标词。

## 修改配置

核心配置在 `config_client.py` 和 `config_server.py`，修改后执行：

```bash
capswriter restart
```

---

## 与原版的区别

| 项目 | 原版（Windows） | 本 fork（macOS） |
|------|----------------|-----------------|
| 推理后端 | GGUF + ONNX | Apple MLX |
| 快捷键 | Windows 钩子 | CGEventTap + hidutil remap |
| 进程管理 | 手动启动 | launchd（client + server 独立托管） |
| 自启动 | 任务计划程序 | launchd plist |
| 发布形态 | .exe 安装包 | .app bundle（当前 clone + install.sh，暂不支持 dmg 分发） |
| GUI | 系统托盘 | 菜单栏图标（矢量 template，深浅色自适应） |

## 后续计划

- [x] 菜单栏图标（矢量 template，深浅色自适应）
- [x] 菜单栏状态显示与下拉菜单
- [ ] 简洁精美的GUI
- [ ] ASR推理精度调优
- [ ] .dmg 一键安装包

## 致谢

本项目基于 [HaujetZhao/CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline) 开发，遵循原项目 MIT 协议。感谢原作者 Haujet Zhao 的开源工作。

## License

MIT © Haujet Zhao（原作者） / Edgar Zhong（macOS 适配）
