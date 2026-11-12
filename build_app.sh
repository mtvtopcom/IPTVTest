#!/bin/bash
# 生成可双击运行的 macOS .app（无需 py2app / PyInstaller）
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$PROJECT_DIR/dist"
APP_NAME="IPTV直播源测试.app"
APP_DIR="$DIST_DIR/$APP_NAME"
CONTENTS="$APP_DIR/Contents"
RES="$CONTENTS/Resources/app"

mkdir -p "$DIST_DIR"
if [ -d "$APP_DIR" ]; then
  rm -rf "$APP_DIR"
fi
mkdir -p "$CONTENTS/MacOS" "$RES"

cp -R "$PROJECT_DIR/core" "$RES/core"
cp "$PROJECT_DIR/gui.py" "$PROJECT_DIR/cli.py" "$PROJECT_DIR/selftest.py" "$RES/"
if [ -d "$PROJECT_DIR/config" ]; then
  cp -R "$PROJECT_DIR/config" "$RES/config"
fi
if [ -d "$PROJECT_DIR/bin" ]; then
  mkdir -p "$RES/bin"
  cp -R "$PROJECT_DIR/bin/." "$RES/bin/"
  chmod +x "$RES/bin/"* 2>/dev/null || true
fi
# 应用图标（Finder / Dock）与窗口图标（直接命令行启动时使用）
if [ -f "$PROJECT_DIR/assets/AppIcon.icns" ]; then
  cp "$PROJECT_DIR/assets/AppIcon.icns" "$CONTENTS/Resources/AppIcon.icns"
fi
if [ -f "$PROJECT_DIR/assets/icon.png" ]; then
  mkdir -p "$RES/assets"
  cp "$PROJECT_DIR/assets/icon.png" "$RES/assets/icon.png"
fi
find "$RES" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>IPTV直播源测试</string>
  <key>CFBundleDisplayName</key><string>IPTV直播源测试</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIdentifier</key><string>cn.local.iptvupdater</string>
  <key>CFBundleIconFile</key><string>AppIcon.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSUIElement</key><false/>
</dict>
</plist>
PLIST

cat > "$CONTENTS/MacOS/launcher" <<'LAUNCHER'
#!/bin/bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_RES="$HERE/../Resources/app"
export IPTV_HOME="${IPTV_HOME:-$HOME/Library/Application Support/IPTVUpdater}"
mkdir -p "$IPTV_HOME/config" "$IPTV_HOME/output/backup" "$IPTV_HOME/logs"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

PYTHON_BIN=""
if [ -n "${IPTV_PYTHON:-}" ] && [ -x "$IPTV_PYTHON" ]; then
  PYTHON_BIN="$IPTV_PYTHON"
fi
if [ -z "$PYTHON_BIN" ]; then
  for candidate in "$HOME"/.workbuddy/binaries/python/versions/*/bin/python3 \
                   /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$candidate" ]; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
if [ -z "$PYTHON_BIN" ]; then
  /usr/bin/osascript -e 'display alert "缺少 Python 3" message "请安装 Python 3 后重新打开本应用。"' || true
  exit 1
fi

cd "$APP_RES" || exit 1
exec "$PYTHON_BIN" gui.py >> "$IPTV_HOME/logs/launcher.log" 2>&1
LAUNCHER
chmod +x "$CONTENTS/MacOS/launcher"

xattr -cr "$APP_DIR" 2>/dev/null || true
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true
fi

echo "已生成：$APP_DIR"
echo "双击即可运行；数据目录：\$HOME/Library/Application Support/IPTVUpdater"
