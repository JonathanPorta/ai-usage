#!/usr/bin/env bash
# Install the built app to a stable location so its login item survives build cleanups.
# Usage: install_app.sh [SOURCE_BUNDLE]   (default: macos/build/AI Usage.app)
# APP_INSTALL_DEST overrides the destination bundle; APP_INSTALL_NO_LAUNCH=1 skips launching.
# APP_INSTALL_PREVIOUS=<bundle> names one other running copy that may be stopped.
# APP_INSTALL_STOP_TIMEOUT is how long to wait for a stopped copy to exit (seconds, default 5).
# Nothing that works is touched until the staged copy is verified.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
src="${1:-$root/macos/build/AI Usage.app}"
dest="${APP_INSTALL_DEST:-$HOME/Applications/AI Usage.app}"
timeout="${APP_INSTALL_STOP_TIMEOUT:-5}"
launch=1
[ "${APP_INSTALL_NO_LAUNCH:-}" = "1" ] && launch=0

fail() { echo "error: $*" >&2; exit 1; }
resolve() { # absolute path with the parent's symlinks resolved
    local dir base
    dir="$(dirname "$1")"; base="$(basename "$1")"
    if [ -d "$dir" ]; then echo "$(cd "$dir" && pwd -P)/$base"; else echo "$1"; fi
}

[ -d "$src" ] || fail "no app bundle at $src (run make app-build)"
src="$(resolve "${src%/}")"
dest="$(resolve "${dest%/}")"
[ "$src" != "$dest" ] || fail "source and destination are the same bundle: $dest"

parent="$(dirname "$dest")"
previous="$dest.previous"
staging="$dest.staging"
aside="$dest.previous.old"
exe="$dest/Contents/MacOS/AIUsage"

# Running copies of the app, one "pid executable-path" per line.
# Paths are resolved the same way as the destination, so /var and /private/var compare equal.
running() {
    local pid path
    ps -axo pid=,comm= | awk '{pid=$1; $1=""; sub(/^ +/, ""); if ($0 ~ /\/Contents\/MacOS\/AIUsage$/) print pid " " $0}' |
        while read -r pid path; do echo "$pid $(resolve "$path")"; done
}
pids_for() { running | awk -v exe="$1" '{pid=$1; $1=""; sub(/^ +/, ""); if ($0 == exe) print pid}'; }

stop_exact() { # SIGTERM only processes running exactly this executable; never escalate
    local target="$1" pid waited
    for pid in $(pids_for "$target"); do
        kill -TERM "$pid" 2>/dev/null || true
        waited=0
        while kill -0 "$pid" 2>/dev/null; do
            if [ "$waited" -ge "$((timeout * 10))" ]; then
                rm -rf "$staging"
                fail "process $pid ($target) did not exit within ${timeout}s; nothing was changed. Quit it and retry."
            fi
            sleep 0.1; waited=$((waited + 1))
        done
    done
}

codesign --verify --deep --strict "$src" || fail "source signature does not verify: $src"

# Another running copy would receive the launch instead of the installed build.
allowed=""
if [ "$launch" = "1" ]; then
    if [ -n "${APP_INSTALL_PREVIOUS:-}" ]; then
        allowed="$(resolve "${APP_INSTALL_PREVIOUS%/}")/Contents/MacOS/AIUsage"
    fi
    others="$(running | awk -v exe="$exe" -v ok="$allowed" '{pid=$1; $1=""; sub(/^ +/, ""); if ($0 != exe && $0 != ok) print "  " pid " " $0}')"
    if [ -n "$others" ]; then
        fail "other copies of AI Usage are running, so the installed build would not be the one that opens:
$others
Quit them, or name the one to stop with APP_INSTALL_PREVIOUS=<bundle path>. Nothing was changed."
    fi
fi

mkdir -p "$parent"
rm -rf "$staging"
if ! ditto "$src" "$staging" || ! codesign --verify --deep --strict "$staging"; then
    rm -rf "$staging"
    fail "staged copy did not verify; nothing was changed"
fi

if [ -n "$allowed" ]; then stop_exact "$allowed"; fi
stop_exact "$exe"

# Swap with rollback: the old .previous is kept aside until the new install is in place.
had_dest=0; had_previous=0
if [ -e "$dest" ]; then had_dest=1; fi
if [ -e "$previous" ]; then had_previous=1; fi
rollback() {
    echo "error: install failed; restoring the earlier bundles" >&2
    if [ "$had_dest" = "1" ] && [ ! -e "$dest" ] && [ -e "$previous" ]; then mv "$previous" "$dest"; fi
    if [ "$had_dest" = "0" ] && [ -e "$dest" ]; then rm -rf "$dest"; fi
    if [ "$had_previous" = "1" ] && [ -e "$aside" ]; then rm -rf "$previous"; mv "$aside" "$previous"; fi
    rm -rf "$staging"
}
trap rollback ERR
rm -rf "$aside"
if [ "$had_previous" = "1" ]; then mv "$previous" "$aside"; fi
if [ "$had_dest" = "1" ]; then mv "$dest" "$previous"; fi
mv "$staging" "$dest"
trap - ERR
rm -rf "$aside"

echo "Installed: $dest"
if [ -e "$previous" ]; then echo "Rollback: rm -rf \"$dest\" && mv \"$previous\" \"$dest\""; fi
real_parent="$(cd "$parent" && pwd -P)"
if [ "$real_parent" != "$(cd /Applications 2>/dev/null && pwd -P)" ] \
    && [ "$real_parent" != "$(cd "$HOME/Applications" 2>/dev/null && pwd -P)" ]; then
    echo "Note: Open at login is unavailable from $parent (only /Applications or ~/Applications)."
fi

if [ "$launch" = "1" ]; then
    open -g "$dest"
    for _ in $(seq 1 50); do
        if [ -n "$(pids_for "$exe")" ]; then echo "Running: $exe"; exit 0; fi
        sleep 0.1
    done
    fail "the installed build did not start ($exe is not running)"
fi
