#!/usr/bin/env bash
# 构建独立 Dashboard 开发包；与日常 CapsWriter 的身份分离，避免影响现有授权。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE="$ROOT/native/CapsWriter"
OUT="$ROOT/build/CapsWriterDashboard.app"

# Dashboard 的唯一构建基线是 Apple Silicon + macOS 26.0+、CLT 26.6、
# Swift 6.3.x 与 SDK 26.5；不再为旧系统或其他工具链保留构建分支。
if [[ "$(uname -m)" != "arm64" ]]; then
    echo "CapsWriter Dashboard requires Apple Silicon (arm64)." >&2
    exit 1
fi
MACOS_MAJOR="$(sw_vers -productVersion | awk -F. '{print $1}')"
if (( MACOS_MAJOR < 26 )); then
    echo "CapsWriter Dashboard requires macOS 26.0 or newer." >&2
    exit 1
fi
SDK_PATH="/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk"
if [[ ! -d "$SDK_PATH" ]]; then
    echo "Missing Dashboard build SDK: $SDK_PATH" >&2
    exit 1
fi
export SDKROOT="$SDK_PATH"

swift build --package-path "$PACKAGE" --product CapsWriterDashboard
BIN="$(swift build --package-path "$PACKAGE" --show-bin-path)"
# 旧构建整体保留，方便回滚；禁止清空目录或覆盖重要现有产物。
if [[ -e "$OUT" ]]; then
    BACKUP="$ROOT/.archive/dashboard-build-$(date +%Y%m%d-%H%M%S)-$$"
    mkdir -p "$BACKUP"
    mv "$OUT" "$BACKUP/"
fi
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
cp "$BIN/CapsWriterDashboard" "$OUT/Contents/MacOS/CapsWriterDashboard"
cp "$ROOT/assets/icon/app-icon.png" "$OUT/Contents/Resources/app-icon.png"
cp "$ROOT/assets/icon/app-icon.icns" "$OUT/Contents/Resources/app-icon.icns"
# 路径仅进入独立开发包；正式 DMG 需由自包含运行时替代该开发桥接。
cp "$ROOT/tools/dashboard_resources.py" "$OUT/Contents/Resources/dashboard_resources.py"
cp "$ROOT/tools/dashboard_settings.py" "$OUT/Contents/Resources/dashboard_settings.py"
cp "$ROOT/tools/dashboard_vocabulary.py" "$OUT/Contents/Resources/dashboard_vocabulary.py"
cp "$ROOT/tools/dashboard_history.py" "$OUT/Contents/Resources/dashboard_history.py"
python3 - "$ROOT" "$OUT/Contents/Resources/development-paths.json" <<'PYCONFIG'
import json
import sys
from pathlib import Path
Path(sys.argv[2]).write_text(json.dumps({"root": sys.argv[1], "python": sys.executable}))
PYCONFIG
cat > "$OUT/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.capswriter.dashboard.dev</string>
<key>CFBundleName</key><string>CapsWriterDashboard</string>
<key>CFBundleDisplayName</key><string>CapsWriter Dashboard</string>
<key>CFBundleExecutable</key><string>CapsWriterDashboard</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.3.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleIconFile</key><string>app-icon</string>
<key>LSMinimumSystemVersion</key><string>26.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
# 临时签名仅用于本地验证；不等同于 Developer ID 签名或公证。
codesign --force --sign - "$OUT"
codesign --verify --strict "$OUT"
printf '开发包：%s\n' "$OUT"
