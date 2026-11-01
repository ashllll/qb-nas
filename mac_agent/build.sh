#!/bin/bash
# 构建 QBNAS 剪贴板代理.app（不依赖 Xcode 工程，只有 swiftc + iconutil）
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="NAS 剪贴板代理"
BUILD_DIR="build"
APP="$BUILD_DIR/$APP_NAME.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "== 编译"
swiftc -O -target arm64-apple-macos13.0 -swift-version 5 \
  -framework AppKit -framework SwiftUI \
  Sources/Core/*.swift Sources/App/*.swift \
  -o "$APP/Contents/MacOS/QBNASAgent"

cp Resources/Info.plist "$APP/Contents/Info.plist"
cp Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns" 2>/dev/null || true

echo "== 签名（ad-hoc，本机自用）"
codesign --force --sign - --timestamp=none "$APP" >/dev/null 2>&1 || codesign --force --sign - "$APP"

echo "== 校验"
plutil -lint "$APP/Contents/Info.plist"
codesign --verify --verbose=2 "$APP" 2>&1 | tail -2
/usr/bin/plutil -extract LSUIElement raw "$APP/Contents/Info.plist"
echo "== 产物：$PWD/$APP"
