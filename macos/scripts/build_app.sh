#!/usr/bin/env bash
# Assemble "AI Usage.app" from the SwiftPM build, bundling the read-only
# reporting module and the collector module it imports. Ad-hoc signed for
# local use only (see docs/RELEASE.md).
set -euo pipefail

CONFIGURATION="${CONFIGURATION:-release}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$PACKAGE/.." && pwd)"
OUT="${APP_OUT:-$PACKAGE/build}"
APP="$OUT/AI Usage.app"

swift build --package-path "$PACKAGE" -c "$CONFIGURATION" --product AIUsage >&2
BIN_DIR="$(swift build --package-path "$PACKAGE" -c "$CONFIGURATION" --show-bin-path)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/collector"
cp "$BIN_DIR/AIUsage" "$APP/Contents/MacOS/AIUsage"
cp "$PACKAGE/Support/Info.plist" "$APP/Contents/Info.plist"
cp "$REPO/ai_usage_report.py" "$REPO/ai_usage_service.py" "$APP/Contents/Resources/collector/"
for bundle in "$BIN_DIR"/*.bundle; do
  [[ -e "$bundle" ]] && cp -R "$bundle" "$APP/Contents/Resources/"
done
codesign --force --sign - --timestamp=none "$APP" >&2
codesign --verify --strict "$APP" >&2
echo "$APP"
