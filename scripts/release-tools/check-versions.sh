#!/usr/bin/env bash
# check-versions.sh — print the app version a candidate is built for, failing
# closed unless it is MAJOR.MINOR.PATCH and matches any expected version or tag.
#
# The candidate's identity is the app's CFBundleShortVersionString
# (macos/Support/Info.plist). The bundled collector keeps its own VERSION
# (ai_usage_service.py), recorded in the release notes, not in the tag.
#
# Usage: check-versions.sh [--expect-version X.Y.Z] [--expect-tag vX.Y.Z]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

expect_version="" expect_tag=""
while [ $# -gt 0 ]; do
  case "$1" in
    --expect-version) expect_version="${2:-}"; shift 2 ;;
    --expect-tag) expect_tag="${2:-}"; shift 2 ;;
    -h | --help) sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'versions: unexpected arg: %s\n' "$1" >&2; exit 2 ;;
  esac
done

plist="$ROOT/macos/Support/Info.plist"
version="$(plutil -extract CFBundleShortVersionString raw -o - "$plist" 2>/dev/null \
  || python3 -c 'import plistlib,sys; print(plistlib.load(open(sys.argv[1],"rb"))["CFBundleShortVersionString"])' "$plist")"
case "$version" in
  [0-9]*.[0-9]*.[0-9]*) : ;;
  *) printf 'versions: %s: CFBundleShortVersionString is not MAJOR.MINOR.PATCH: %s\n' "$plist" "$version" >&2; exit 1 ;;
esac
if [ -n "$expect_version" ] && [ "$expect_version" != "$version" ]; then
  printf 'versions: expected %s but Info.plist declares %s\n' "$expect_version" "$version" >&2; exit 1
fi
if [ -n "$expect_tag" ] && [ "$expect_tag" != "v$version" ]; then
  printf 'versions: tag %s does not match version %s (tag must be v%s)\n' "$expect_tag" "$version" "$version" >&2; exit 1
fi
printf '%s\n' "$version"
