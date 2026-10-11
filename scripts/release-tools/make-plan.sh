#!/usr/bin/env bash
# make-plan.sh — emit the explicit blessed/release-candidate-plan/v1 for ai-usage.
#
# The plan is the producer's artifact declaration: exactly the artifacts an
# ai-usage candidate contains. scripts/release/build-candidate.sh verifies each
# file exists, hashes it, and seals the closed set; nothing is inferred from
# filenames. Adapted from the Workroom producer (releases.pipeline reference).
#
# ai-usage has no third-party dependencies (Python standard library; SwiftPM
# package with no external packages), so no per-artifact SBOM is declared.
#
# Deterministic: every value comes from the repository, the git commit, or the
# caller. No clock is read.
#
# Usage: make-plan.sh --version X.Y.Z --source-sha SHA --build-sha SHA \
#                     --created-at RFC3339 --key-id ID [--output FILE]
set -euo pipefail

version="" source_sha="" build_sha="" created_at="" key_id="" output=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version) version="${2:-}"; shift 2 ;;
    --source-sha) source_sha="${2:-}"; shift 2 ;;
    --build-sha) build_sha="${2:-}"; shift 2 ;;
    --created-at) created_at="${2:-}"; shift 2 ;;
    --key-id) key_id="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    -h | --help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'plan: unexpected arg: %s\n' "$1" >&2; exit 2 ;;
  esac
done
for v in version source_sha build_sha created_at key_id; do
  [ -n "${!v}" ] || { printf 'plan: --%s is required\n' "${v//_/-}" >&2; exit 2; }
done
case "$version" in
  [0-9]*.[0-9]*.[0-9]*) : ;;
  *) printf 'plan: --version is not MAJOR.MINOR.PATCH: %s\n' "$version" >&2; exit 2 ;;
esac
for v in source_sha build_sha; do
  val="${!v}"
  case "$val" in
    *[!0-9a-f]* | "") printf 'plan: --%s must be a full 40-hex commit sha: %s\n' "${v//_/-}" "$val" >&2; exit 2 ;;
  esac
  [ "${#val}" -eq 40 ] || { printf 'plan: --%s must be 40 hex characters: %s\n' "${v//_/-}" "$val" >&2; exit 2; }
done
case "$created_at" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) : ;;
  *) printf 'plan: --created-at must be RFC3339 UTC (YYYY-MM-DDTHH:MM:SSZ): %s\n' "$created_at" >&2; exit 2 ;;
esac
case "$key_id" in
  *[!A-Za-z0-9._-]* | "") printf 'plan: --key-id has an unexpected shape: %s\n' "$key_id" >&2; exit 2 ;;
esac
command -v jq >/dev/null 2>&1 || { printf 'plan: jq required\n' >&2; exit 2; }

# macOS arm64 only: one deterministic archive of the ad-hoc-signed app bundle.
# A Developer ID signed and notarized archive replaces it once the Apple
# finalizer exists upstream (docs/CI-AND-RELEASE.md).
plan="$(jq -n \
  --arg version "$version" \
  --arg tag "v$version" \
  --arg source_sha "$source_sha" \
  --arg build_sha "$build_sha" \
  --arg created_at "$created_at" \
  --arg key_id "$key_id" \
  '{
     schema: "blessed/release-candidate-plan/v1",
     project: "ai-usage",
     component: "menu-bar-app",
     version: $version,
     tag: $tag,
     producer_repo: "JonathanPorta/ai-usage",
     source_sha: $source_sha,
     build_sha: $build_sha,
     created_at: $created_at,
     signing: {
       profile: "ed25519-detached-v1",
       key_id: $key_id,
       artifacts: "optional"
     },
     public_notes: "release-notes-public.md",
     artifacts: [
       {
         id: "macos-arm64-app-archive",
         filename: ("ai-usage_" + $version + "_arm64.app.tar.gz"),
         platform: "darwin/aarch64",
         install_kind: "archive"
       }
     ]
   }')"

if [ -n "$output" ]; then
  [ -L "$output" ] && { printf 'plan: refusing to write through a symlink\n' >&2; exit 2; }
  printf '%s\n' "$plan" > "$output.tmp" && mv -f "$output.tmp" "$output"
  printf 'plan: wrote %s\n' "$output" >&2
else
  printf '%s\n' "$plan"
fi
