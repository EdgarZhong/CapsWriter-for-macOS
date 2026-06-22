#!/usr/bin/env bash
# CapsWriter-Offline 本地安装脚本
# 目标：clone 后尽量自动完成 venv、依赖、macOS launcher 重建与全局命令安装，无需用户手动填写路径。

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
CAPSWRITER_PY="$PROJECT_DIR/capswriter.py"
BIN_DIR="$HOME/.local/bin"
WRAPPER="$BIN_DIR/capswriter"
MODEL_DIR="$PROJECT_DIR/models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "错误：当前 macOS 分支仅支持 macOS。" >&2
    exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "错误：当前 macOS 分支仅支持 Apple Silicon（arm64）。" >&2
    exit 1
fi

echo "=== 准备 Python 虚拟环境 ==="
if [[ ! -x "$VENV_PYTHON" ]]; then
    if ! command -v uv >/dev/null 2>&1; then
        echo "错误：未找到 uv，无法自动创建 .venv。" >&2
        echo "请先执行：brew install uv" >&2
        exit 1
    fi
    echo "未找到 .venv，使用 uv 创建 Python 3.13 虚拟环境..."
    uv venv --python 3.13 "$PROJECT_DIR/.venv"
fi

PY_MINOR="$("$VENV_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$PY_MINOR" != "3.13" ]]; then
    echo "错误：当前 .venv 是 Python $PY_MINOR，但本分支要求 Python 3.13。" >&2
    echo "为避免破坏用户文件，脚本不会自动删除 .venv；请自行移动旧 .venv 后重新运行。" >&2
    exit 1
fi

echo "=== 安装 / 更新 Python 依赖 ==="
if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV_PYTHON" -r "$PROJECT_DIR/requirements-server.txt"
    uv pip install --python "$VENV_PYTHON" -r "$PROJECT_DIR/requirements-client.txt"
else
    # 已存在 .venv 但没有 uv 时，退回 venv 内 pip，避免用户只因缺 uv 而无法更新依赖。
    "$VENV_PYTHON" -m pip install -r "$PROJECT_DIR/requirements-server.txt"
    "$VENV_PYTHON" -m pip install -r "$PROJECT_DIR/requirements-client.txt"
fi

echo "=== 重建 macOS App 启动器 ==="
# launcher 必须在目标机器重建：它需要链接当前 .venv 对应的 libpython，并写入相对 rpath。
bash "$PROJECT_DIR/build_launcher.sh"

echo "=== 安装 capswriter 命令 ==="
mkdir -p "$BIN_DIR"

# 包装脚本只保存当前项目路径和 .venv Python 路径；如果移动仓库，重新执行 install.sh 即可刷新。
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$VENV_PYTHON" "$CAPSWRITER_PY" "\$@"
EOF
chmod +x "$WRAPPER"

echo "✓ 已安装: $WRAPPER"

# 检查 PATH
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    echo ""
    echo "注意: $BIN_DIR 不在 PATH 中，请在 ~/.zshrc 或 ~/.bashrc 中添加："
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "然后重新打开终端或执行 source ~/.zshrc"
fi

echo ""
if [[ ! -d "$MODEL_DIR" ]]; then
    echo "提示：尚未检测到默认模型目录："
    echo "  $MODEL_DIR"
    echo "启动前请按 readme.md 下载 Qwen3-ASR MLX 模型。"
fi

echo ""
echo "下一步："
echo "  capswriter install"
echo "  capswriter start"
