<div align="center">

<img src="assets/icon/app-icon.png" width="168" alt="CapsWriter for macOS">

# CapsWriter for macOS

**按住 Caps Lock 说话，松手即输入。完全离线，Apple Silicon 原生加速。**

</div>

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
git clone https://github.com/EdgarZhong/CapsWriter-for-macOS.git
cd CapsWriter-for-macOS
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

CapsWriter 需要两项系统权限（均位于”系统设置 › 隐私与安全性”）：

- **辅助功能**（Accessibility）：用于键盘事件拦截和自动粘贴
- **输入监控**（Input Monitoring）：用于 Caps Lock 按键捕获

首次启动时，CapsWriter 会自动弹出辅助功能授权框并打开系统设置面板，按通知提示依次打开两项开关，然后重启 CapsWriter 即可。

> ⚠️ **重要：开启权限后请用 CLI 重启，不要点系统设置弹窗里的「退出并重新打开」按钮。**
> 开启「输入监控 / 辅助功能」开关时，macOS 会提示「CapsWriter 需要退出并重新打开才能生效」。
> **请不要点那个「退出并重新打开」**，而是回到终端执行：
> ```bash
> capswriter restart
> ```
> 原因见下方「已知问题：双实例」。两项权限里 IM 条目何时出现在列表也由 macOS 决定、并不稳定；
> **若「输入监控」列表里没有 CapsWriter，请点列表下方「+」号，搜索并添加 CapsWriter 后再打开开关。**

**麦克风权限**

首次录音时 macOS 自动弹出授权对话框，点击「好」即可。

### 权限疑难排查

如果更新了软件（重新运行 `install.sh` 或 `build_launcher.sh`），权限可能因签名变化而失效。此时请先重置权限再重新启动：

```bash
capswriter reset-permissions   # 清除旧的辅助功能和输入监控权限条目
capswriter start               # 重新启动，按引导重新授权
```

如果权限引导反复不成功，也可手动处理：关闭 CapsWriter → 在系统设置的辅助功能和输入监控列表中删除 CapsWriter 条目 → 重新启动软件，按引导重新授权。

### 已知问题：双实例（良性，可规避）

**现象**：在「系统设置 › 隐私与安全性」里给 CapsWriter 开权限时，若点了系统弹窗的
**「退出并重新打开」**按钮让系统重新拉起客户端，之后再从**菜单栏**「重启 CapsWriter」，
可能出现**两个 CapsWriter 进程**（菜单栏出现两个图标）。

**影响**：基本无害。**生效的是最新启动的那个实例**，旧的那个已经失效、不再工作；
键盘接管、录音、识别一切正常，只是多了一个空转的残留进程。

**触发条件很窄**：仅当「本轮客户端是被系统设置面板拉起的」时才会发生。正常的菜单栏重启、
CLI 重启都**不会**触发。

**根因**：macOS 用 LaunchServices 重新拉起 GUI 应用时，新进程会被「领养」到一个动态标签，
脱离 launchd 的静态标签管辖，导致此刻的进程查杀短暂失准。这是 GUI 应用同时被 launchd 与
LaunchServices 管理的固有冲突，详见 `docs/macos-architecture-decisions.md`。

**规避**：开权限后**不要点系统弹窗的「退出并重新打开」**，改用最稳的 CLI：
```bash
capswriter restart
```
**清理**：万一已经出现双实例，用 CLI 一键清成单实例（CLI 能可靠杀掉所有残留）：
```bash
capswriter restart      # 或 capswriter stop 再 capswriter start
```

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
capswriter reset-permissions    # 重置辅助功能和输入监控权限（更新/rebuild 后使用）
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
| 语音模型 | Paraformer / SenseVoice | Qwen3-ASR（MLX 量化） |
| 推理后端 | ONNX（sherpa-onnx） | Apple MLX |
| 快捷键 | Windows 钩子 | CGEventTap + hidutil remap |
| 进程管理 | 手动启动 | launchd（client + server 独立托管） |
| 自启动 | 任务计划程序 | launchd plist |
| 发布形态 | .exe 安装包 | .app bundle（当前 clone + install.sh，暂不支持 dmg 分发） |
| GUI | 系统托盘 | 菜单栏图标（矢量 template，深浅色自适应） |

## 后续计划

- [x] 菜单栏图标（矢量 template，深浅色自适应）
- [x] 菜单栏状态显示与下拉菜单
- [x] 权限引导重写与进程生命周期稳定化
- [ ] 简洁精美的 GUI
- [ ] ASR 推理精度调优（进行中）
- [ ] .dmg 一键安装包

## 致谢

本项目基于 [HaujetZhao/CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline) 开发，遵循原项目 MIT 协议。感谢原作者 Haujet Zhao 的开源工作。

## License

MIT © Haujet Zhao（原作者） / Edgar Zhong（macOS 适配）
