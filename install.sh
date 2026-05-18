#!/usr/bin/env bash
# CapsWriter-Offline 全局命令安装脚本
# 在 ~/.local/bin/capswriter 创建包装脚本，无需 sudo

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
CAPSWRITER_PY="$PROJECT_DIR/capswriter.py"
BIN_DIR="$HOME/.local/bin"
WRAPPER="$BIN_DIR/capswriter"

# 检查 venv
if [ ! -f "$VENV_PYTHON" ]; then
    echo "错误: 未找到 .venv/bin/python，请先创建虚拟环境并安装依赖" >&2
    exit 1
fi

# 创建目标目录
mkdir -p "$BIN_DIR"

# 写入包装脚本
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
