#!/bin/bash
# Wrap the payload (runtime/ + node/) into Story Dubbing.app and a compressed .dmg.
# Usage: make_app.sh <payload_dir> <version> <output_dir>
set -euo pipefail

PAYLOAD="$1"
VERSION="$2"
OUT="$3"
HERE="$(cd "$(dirname "$0")" && pwd)"
APPNAME="Story Dubbing"
APP="$OUT/$APPNAME.app"

mkdir -p "$OUT"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp -R "$PAYLOAD/runtime" "$APP/Contents/Resources/runtime"
cp -R "$PAYLOAD/node" "$APP/Contents/Resources/node"
sed "s/__VERSION__/$VERSION/g" "$HERE/Info.plist" > "$APP/Contents/Info.plist"
cp "$HERE/launch.sh" "$APP/Contents/MacOS/StoryDubbing"
chmod +x "$APP/Contents/MacOS/StoryDubbing"
chmod +x "$APP/Contents/Resources/node/bin/node" 2>/dev/null || true
chmod +x "$APP/Contents/Resources/runtime/bin/"* 2>/dev/null || true

# Ad-hoc signature (no Apple Developer cert). Lets the app run after the user clears the
# download quarantine (right-click -> Open, or: xattr -dr com.apple.quarantine <app>).
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "warning: ad-hoc codesign skipped"

DMG="$OUT/StoryDubbing-$VERSION-macos-arm64.dmg"
rm -f "$DMG"
hdiutil create -volname "$APPNAME" -srcfolder "$APP" -ov -format UDZO "$DMG"
echo "built: $DMG"
