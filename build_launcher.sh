#!/bin/bash
# 编译 launcher_embed.c，链接项目 .venv 暴露的 libpython，输出到 CapsWriter.app/Contents/MacOS/CapsWriter
# 用法：bash build_launcher.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
    echo "错误：未找到 .venv/bin/python，请先创建 venv" >&2
    exit 1
fi

PY_VER="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_BASE="$("$PY" -c 'import sys; print(sys.base_prefix)')"
PY_INC="$("$PY"  -c 'import sysconfig; print(sysconfig.get_path("include"))')"
PY_LIBDIR="$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
PY_LDLIB="$("$PY"  -c 'import sysconfig; print(sysconfig.get_config_var("LDLIBRARY"))')"
PY_LDNAME="${PY_LDLIB#lib}"
PY_LDNAME="${PY_LDNAME%.dylib}"
PY_LIBSRC="$PY_LIBDIR/$PY_LDLIB"

if [[ ! -f "$PY_LIBSRC" ]]; then
    echo "错误：未找到 Python 动态库: $PY_LIBSRC" >&2
    echo "请确认 .venv 使用的是启用共享库的 Python 3.13（推荐 uv/mise/Homebrew）。" >&2
    exit 1
fi

# 动态库加载必须在 main() 之前完成，因此不能等 C 代码运行后再寻找 libpython。
# 这里把目标机器的 libpython 通过 .venv/lib 下的符号链接暴露出来，再使用
# @executable_path 相对 rpath。最终 Mach-O 不再保存 /Users/某个用户名/... 这类构建机路径。
VENV_LIBDIR="$SCRIPT_DIR/.venv/lib"
VENV_LIBPY="$VENV_LIBDIR/$PY_LDLIB"
mkdir -p "$VENV_LIBDIR"
if [[ -e "$VENV_LIBPY" && ! -L "$VENV_LIBPY" ]]; then
    echo "错误：$VENV_LIBPY 已存在且不是符号链接，为避免覆盖用户文件，已停止。" >&2
    exit 1
fi
ln -sfn "$PY_LIBSRC" "$VENV_LIBPY"

# C 启动器运行时需要 Python base prefix 来定位标准库。该路径属于目标机器环境，
# 只写入 .venv 内的生成文件，不写入 Mach-O，避免把构建机用户名打进发布二进制。
printf '%s\n' "$PY_BASE" > "$SCRIPT_DIR/.venv/capswriter-python-prefix"

SRC="$SCRIPT_DIR/CapsWriter.app/Contents/MacOS/launcher_embed.c"
OUT="$SCRIPT_DIR/CapsWriter.app/Contents/MacOS/CapsWriter"

echo "=== 编译 launcher_embed.c ==="
echo "  Python:  $PY_VER  ($PY_BASE)"
echo "  Include: $PY_INC"
echo "  Lib:     $PY_LIBDIR / $PY_LDLIB"
echo "  RPath:   @executable_path/../../../.venv/lib"
echo "  Output:  $OUT"

SDK_PATH="$(xcrun --show-sdk-path)"
clang -std=c11 -Wall -Wextra -O2 -arch arm64 \
    -isysroot "$SDK_PATH" \
    -I"$PY_INC" \
    -DCW_PY_VERSION="\"$PY_VER\"" \
    "$SRC" \
    -L"$VENV_LIBDIR" \
    -l"$PY_LDNAME" \
    -Wl,-rpath,"@executable_path/../../../.venv/lib" \
    -o "$OUT"

echo "=== 同步应用图标 ==="
# 源文件 assets/icon/app-icon.icns 为准，拷入 bundle Resources（Info.plist 已声明 CFBundleIconFile=app-icon）
ICON_SRC="$SCRIPT_DIR/assets/icon/app-icon.icns"
ICON_DST="$SCRIPT_DIR/CapsWriter.app/Contents/Resources/app-icon.icns"
if [[ -f "$ICON_SRC" ]]; then
    cp "$ICON_SRC" "$ICON_DST"
    echo "  已同步: $ICON_DST"
else
    echo "  警告：未找到 $ICON_SRC，跳过图标同步" >&2
fi

echo "=== 重新签名 ==="
# hardened runtime（--options runtime）让 .app 以正式 bundle 身份运行；
# disable-library-validation 允许加载用户本机 Python 管理器提供的 libpython（非 Apple 签名）。
ENTITLEMENTS_PLIST="$SCRIPT_DIR/CapsWriter.app/Contents/entitlements.plist"
cat > "$ENTITLEMENTS_PLIST" << 'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <key>com.apple.security.device.audio-input</key>
    <true/>
</dict>
</plist>
PLIST_EOF
codesign --force --deep --options runtime --sign - --entitlements "$ENTITLEMENTS_PLIST" "$SCRIPT_DIR/CapsWriter.app"
codesign --verify --deep --strict "$SCRIPT_DIR/CapsWriter.app"

echo ""
echo "提示：若这是已授权后的重新签名，macOS 可能要求重新确认辅助功能权限；"
echo "      启动时 CapsWriter 会自动引导，不在构建阶段主动重置 TCC。"

echo ""
echo "✓ 完成。验证链接："
otool -L "$OUT" | grep -E "python|Python"
otool -l "$OUT" | grep -A3 LC_RPATH | grep path
if strings "$OUT" | grep -q '/Users/'; then
    echo "警告：启动器二进制仍包含 /Users/ 绝对路径，请检查构建参数。" >&2
    exit 1
fi
