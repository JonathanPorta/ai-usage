#!/usr/bin/env bash
# Install the built app to a stable location so its login item survives build cleanups.
# Usage: install_app.sh [SOURCE_BUNDLE]   (default: macos/build/AI Usage.app)
# APP_INSTALL_DEST overrides the destination bundle; APP_INSTALL_NO_LAUNCH=1 skips launching.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
src="${1:-$root/macos/build/AI Usage.app}"
dest="${APP_INSTALL_DEST:-$HOME/Applications/AI Usage.app}"
previous="$dest.previous"
exe="$dest/Contents/MacOS/AIUsage"

[ -d "$src" ] || { echo "error: no app bundle at $src (run make app-build)" >&2; exit 1; }
codesign --verify --deep --strict "$src"

# Stop only a process running exactly the destination's executable.
pids="$(ps -axo pid=,comm= | awk -v exe="$exe" '{pid=$1; $1=""; sub(/^ /, ""); if ($0 == exe) print pid}')"
for pid in $pids; do
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
    done
done

mkdir -p "$(dirname "$dest")"
if [ -e "$dest" ]; then
    rm -rf "$previous"
    mv "$dest" "$previous"
fi
ditto "$src" "$dest"
codesign --verify --deep --strict "$dest"

echo "Installed: $dest"
if [ -e "$previous" ]; then
    echo "Rollback: rm -rf \"$dest\" && mv \"$previous\" \"$dest\""
fi
if [ "${APP_INSTALL_NO_LAUNCH:-}" != "1" ]; then
    open -g "$dest"
fi
