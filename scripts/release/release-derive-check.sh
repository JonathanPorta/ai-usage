#!/usr/bin/env bash
# release-derive-check.sh — bound what `make release-derive` may do (no secret).
#
#   release-derive-check.sh record --candidate DIR --plan PLAN --out BEFORE.json
#   release-derive-check.sh check  --candidate DIR --plan PLAN --before BEFORE.json
#
# A product's derive step may only ADD planned files built from the signed set
# (e.g. an updater index that embeds artifact signatures). `check` refuses:
# any recorded file changed or removed; any recorded plan entry changed or
# removed; a new file that is not a new planned artifact (or its SBOM), or a
# new planned artifact without its file; any new signature (only the signing
# job signs); a change to the plan's identity or signing fields.
# Exit 0 · 1 refused · 2 usage.
# shellcheck disable=SC2016 # jq programs
set -euo pipefail
refuse() {
  echo "::error::release-derive-check: REFUSED — $1" >&2
  exit 1
}
mode="${1:-}"
shift || true
cand="" plan="" out="" before=""
while [ $# -gt 0 ]; do
  case "$1" in
    --candidate) cand="${2:-}" ;;
    --plan) plan="${2:-}" ;;
    --out) out="${2:-}" ;;
    --before) before="${2:-}" ;;
    *)
      echo "release-derive-check: unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift 2 || exit 2
done
if [ ! -d "$cand" ] || [ ! -f "$plan" ]; then
  echo "usage: release-derive-check.sh record|check --candidate DIR --plan PLAN (--out|--before) FILE" >&2
  exit 2
fi
files() { (cd "$cand" && find . -type f | sed 's#^\./##' | sort | while IFS= read -r f; do
  printf '%s\t%s\n' "$f" "$(sha256sum "$f" | cut -d' ' -f1)"
done) | jq -R -s 'split("\n") | map(select(length > 0) | split("\t") | {(.[0]): .[1]}) | add // {}'; }
case "$mode" in
  record)
    [ -n "$out" ] || exit 2
    jq -n --argjson f "$(files)" --slurpfile p "$plan" '{files: $f, plan: $p[0]}' >"$out"
    ;;
  check)
    [ -f "$before" ] || exit 2
    now="$(files)"
    B="$before"
    changed="$(jq -r --argjson n "$now" '.files | to_entries[] | select($n[.key] != .value) | .key' "$B")"
    [ -z "$changed" ] || refuse "derive changed or removed: $(tr '\n' ' ' <<<"$changed")"
    jq -e --slurpfile p "$plan" '(.plan | del(.artifacts)) == ($p[0] | del(.artifacts))' "$B" >/dev/null ||
      refuse "derive changed the plan's identity or signing fields"
    jq -e --slurpfile p "$plan" '.plan.artifacts as $old | all($old[]; . as $a | $p[0].artifacts | any(. == $a))' "$B" >/dev/null ||
      refuse "derive changed or removed a planned artifact"
    newp="$(jq -r --slurpfile p "$plan" '.plan.artifacts as $old | $p[0].artifacts[] | select(. as $a | $old | any(. == $a) | not) | .filename, (.sbom // empty)' "$B" | sort)"
    newf="$(jq -r --argjson n "$now" '.files as $o | $n | keys[] | select($o[.] == null)' "$B" | sort)"
    [ -n "$newp" ] || refuse "derive added no planned artifact"
    [ "$newp" = "$newf" ] || refuse "derive's new files ($(tr '\n' ' ' <<<"$newf")) are not its new planned artifacts ($(tr '\n' ' ' <<<"$newp"))"
    if grep -q '\.sig$' <<<"$newf"; then refuse "derive may not add signatures"; fi
    echo "release-derive-check: derive added $(wc -l <<<"$newp" | tr -d ' ') planned file(s) and changed nothing"
    ;;
  *)
    echo "usage: release-derive-check.sh record|check ..." >&2
    exit 2
    ;;
esac
