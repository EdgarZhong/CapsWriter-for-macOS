#!/bin/bash
# 编译 launcher_embed.c，链接 libpython，输出到 CapsWriter.app/Contents/MacOS/CapsWriter
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

SRC="$SCRIPT_DIR/CapsWriter.app/Contents/MacOS/launcher_embed.c"
OUT="$SCRIPT_DIR/CapsWriter.app/Contents/MacOS/CapsWriter"

echo "=== 编译 launcher_embed.c ==="
echo "  Python:  $PY_VER  ($PY_BASE)"
echo "  Include: $PY_INC"
echo "  Lib:     $PY_LIBDIR / $PY_LDLIB"
echo "  Output:  $OUT"

clang -std=c11 -Wall -Wextra -O2 -arch arm64 \
    -I"$PY_INC" \
    -DPY_BASE_PREFIX="\"$PY_BASE\"" \
    -DCW_PY_VERSION="\"$PY_VER\"" \
    "$SRC" \
    -L"$PY_LIBDIR" \
    -l"$PY_LDNAME" \
    -Wl,-rpath,"$PY_LIBDIR" \
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
# hardened runtime（--options runtime）保持 TCC Accessibility 授权有效；
# disable-library-validation 允许加载外部 libpython（mise 安装，非 Apple 签名）
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

echo "=== 重置麦克风 TCC 记录 ==="
tccutil reset Microphone com.capswriter.client

echo ""
echo "⚠️  签名已更新，Accessibility（辅助功能）权限需要手动刷新："
echo "   → 系统设置 → 隐私与安全性 → 辅助功能"
echo "   → 找到 CapsWriter，先关闭再打开（或删除后重新添加）"
echo "正在打开设置..."
open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'

echo ""
echo "✓ 完成。验证链接："
otool -L "$OUT" | grep -E "python|Python"
otool -l "$OUT" | grep -A3 LC_RPATH | grep path
