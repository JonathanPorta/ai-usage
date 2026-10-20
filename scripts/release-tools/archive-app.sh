#!/usr/bin/env bash
# archive-app.sh — deterministic .tar.gz of a built AI Usage.app.
#
# Determinism here is of the ARCHIVE STEP: given identical .app bytes, the
# archive bytes are identical. Entries are sorted (LC_ALL=C), ownership is
# numeric 0/0, every mtime is normalized to SOURCE_DATE_EPOCH, and gzip stores
# no name or timestamp. It is NOT a claim that the Swift build itself is
# bit-reproducible.
#
# Adapted from the Workroom producer (JonathanPorta/workroom
# scripts/release-tools/archive-app.sh, the releases.pipeline reference).
#
# Usage: archive-app.sh --app "PATH/AI Usage.app" --output DIR/ai-usage_X.Y.Z_arm64.app.tar.gz
#        [--source-date-epoch N]   (default: SOURCE_DATE_EPOCH, else 0)
set -euo pipefail

app="" output="" epoch="${SOURCE_DATE_EPOCH:-0}"
while [ $# -gt 0 ]; do
  case "$1" in
    --app) app="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --source-date-epoch) epoch="${2:-}"; shift 2 ;;
    -h | --help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'archive: unexpected arg: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[ -n "$app" ] && [ -n "$output" ] || { printf 'archive: --app and --output are required\n' >&2; exit 2; }
[ -d "$app" ] || { printf 'archive: app bundle not found: %s\n' "$app" >&2; exit 1; }
case "$epoch" in *[!0-9]* | "") printf 'archive: --source-date-epoch must be an integer\n' >&2; exit 2 ;; esac
[ -L "$output" ] && { printf 'archive: refusing to write through a symlink\n' >&2; exit 2; }
# The archive is written from inside the staging directory, so a relative
# output path would land there instead of where the caller asked.
out_dir="$(cd "$(dirname "$output")" && pwd)" || {
  printf 'archive: output directory does not exist: %s\n' "$(dirname "$output")" >&2
  exit 1
}
output="$out_dir/$(basename "$output")"

stamp="$(date -u -r "$epoch" +%Y-%m-%dT%H:%M:%SZ)"
work="$(mktemp -d)"
list=""
trap 'rm -rf "$work" "$list"' EXIT INT TERM HUP

# Copy preserving symlinks and permissions, strip macOS extended attributes
# (com.apple.provenance and friends carry per-copy values that would otherwise
# land in the archive), then normalize every mtime.
/bin/cp -R "$app" "$work/$(basename "$app")"
xattr -cr "$work" 2>/dev/null || true
find "$work" -print0 | xargs -0 touch -h -d "$stamp"

# The list lives OUTSIDE the staged tree (writing into it would bump the staging
# directory's own mtime), and the staging root itself is never an entry: its
# mode and mtime are mktemp's, not the bundle's.
list="$(mktemp)"
trap 'rm -rf "$work" "$list"' EXIT INT TERM HUP
( cd "$work" && find . -mindepth 1 -print | LC_ALL=C sort ) > "$list"
( cd "$work" \
  && tar --no-recursion --no-mac-metadata --no-xattrs --numeric-owner --uid 0 --gid 0 -T "$list" -cf - \
  | gzip -n -9 > "$output.tmp" )
mv -f "$output.tmp" "$output"
printf 'archive: wrote %s\n' "$output" >&2
