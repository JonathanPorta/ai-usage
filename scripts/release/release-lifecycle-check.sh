#!/usr/bin/env bash
# release-lifecycle-check.sh — the publication side of the LP-6 gate
# (standards/releases/linux-packaging.md), run by the generated `publish` job
# immediately before the draft is published.
#
#   release-lifecycle-check.sh --declaration release.yml --results DIR
#     --candidate candidate/release-candidate.json
#
# DIR holds one sub-directory per downloaded `lifecycle-result-<row>`
# artifact, each with result.json (release-lifecycle.sh). Publication is
# allowed only when the results are EXACTLY the declared rows — none missing,
# none extra, none duplicated — and every one passed on THIS candidate: its
# manifest digest, tag and row artifact digest. An empty declaration, an empty
# or partial result set, or a result for another candidate is refused. (A
# skipped or failed matrix job uploads no result, so it is a missing row.)
#
# Exit: 0 every declared row passed on this candidate · 1 refused · 2 usage.
# shellcheck disable=SC2016 # jq programs
set -uo pipefail
die() {
  printf 'release-lifecycle-check: %s\n' "$1" >&2
  exit 2
}
decl="" res="" cand=""
while [ $# -gt 0 ]; do
  case "$1" in
    --declaration) decl="${2:-}" && shift 2 ;;
    --results) res="${2:-}" && shift 2 ;;
    --candidate) cand="${2:-}" && shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
{ [ -n "$decl" ] && [ -n "$res" ] && [ -n "$cand" ]; } || die "usage: --declaration release.yml --results DIR --candidate release-candidate.json"
for t in jq yq sha256sum; do command -v "$t" >/dev/null 2>&1 || die "required tool not found: $t"; done
[ -f "$cand" ] || die "no candidate manifest at $cand"
rows="$(yq -o=json '.workflow.lifecycle.rows // [] | map(.id)' "$decl" 2>/dev/null)" || die "$decl is not valid YAML"
[ "$(jq length <<<"$rows")" -gt 0 ] || {
  echo "release-lifecycle-check: REFUSED — $decl declares no lifecycle rows; nothing proves this candidate" >&2
  exit 1
}
msha="$(sha256sum "$cand" | cut -d' ' -f1)"
ctag="$(jq -r .tag "$cand")"
found="[]"
if [ -d "$res" ]; then
  found="$(find "$res" -name result.json -type f -print0 | xargs -0 -r cat | jq -s '.')" || die "a result.json is not JSON"
fi
problems="$(jq -r -n --argjson rows "$rows" --argjson found "$found" --arg m "$msha" --arg t "$ctag" --slurpfile c "$cand" '
  ($found | map(.row)) as $got
  | ($rows - $got | .[] | "row \(.): no result (failed, skipped or never ran)"),
    ($got - $rows | unique | .[] | "row \(.): a result for a row this release does not declare"),
    ($got | group_by(.) | map(select(length > 1) | .[0]) | .[] | "row \(.): more than one result"),
    ($found[] | select(.schema != "blessed/lifecycle-result/v1" or .result != "pass") | "row \(.row): did not pass"),
    ($found[] | select(.candidate.manifest_sha256 != $m or .candidate.tag != $t)
      | "row \(.row): passed on another candidate (\(.candidate.tag) \(.candidate.manifest_sha256[0:12])), not \($t) \($m[0:12])"),
    ($found[] | . as $r | select(([$c[0].artifacts[] | select(.filename == $r.candidate.artifact and .sha256 == $r.candidate.sha256)] | length) != 1)
      | "row \(.row): its artifact \(.candidate.artifact) is not this candidate'"'"'s bytes")')"
if [ -n "$problems" ]; then
  printf 'release-lifecycle-check: REFUSED — publication needs a passing lifecycle result for every declared row on this candidate:\n' >&2
  printf '%s\n' "$problems" | sed 's/^/  ✗ /' >&2
  exit 1
fi
jq -r -n --argjson found "$found" '$found | sort_by(.row)[] | "  ✓ \(.row) (\(.evidence), \(.platform)): \(.baseline.tag) → \(.candidate.tag) \(.candidate.artifact): \(.proven | join(", "))"'
printf 'release-lifecycle-check: OK — %s row(s), all passed on %s (manifest %s)\n' "$(jq length <<<"$rows")" "$ctag" "${msha:0:12}"
