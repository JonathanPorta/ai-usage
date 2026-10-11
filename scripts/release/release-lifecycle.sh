#!/usr/bin/env bash
# release-lifecycle.sh — one row of the producer publication gate
# (standards/releases/linux-packaging.md LP-6), run by the generated release
# workflow's secret-free `lifecycle` job.
#
#   release-lifecycle.sh --row ID --declaration release.yml
#     --candidate-dir DIR --trust-store FILE --signing-dir DIR
#     ( --repo OWNER/REPO --tag TAG [--releases-json FILE]
#     | --baseline-dir DIR [--baseline-tag TAG] )
#     --out DIR
#
# 1. The candidate: DIR must validate against the committed trust store
#    (validate-candidate.sh: the signed manifest, and every file's digest) —
#    these are the FINAL bytes, after RPM finalization and signing. The row's
#    artifact (release.yml workflow.lifecycle.rows[].artifact) must be exactly
#    one manifest artifact.
# 2. The baseline (N of N→N+1): the newest PUBLISHED, non-prerelease release
#    whose version precedes the candidate's — never a draft — downloaded and
#    validated the same way, whose signed manifest names THAT tag (a release
#    carrying another release's candidate is refused), and carrying the same
#    row artifact. A row without a baseline is refused: LP-6 requires the
#    adjacent-version upgrade.
# 3. `make release-lifecycle` with:
#      LIFECYCLE_ROW LIFECYCLE_PLATFORM LIFECYCLE_EVIDENCE_LEVEL
#      LIFECYCLE_NEW (dir) LIFECYCLE_NEW_FILE LIFECYCLE_NEW_VERSION
#      LIFECYCLE_OLD (dir) LIFECYCLE_OLD_FILE LIFECYCLE_OLD_VERSION
#      LIFECYCLE_EVIDENCE (file the product must write)
#    The product's test runs the row's system under test; it must write
#    {"row": ID, "proven": [STEP...]} covering every step the row declares in
#    `proves`. An exit 0 without that file, for another row, or missing a
#    step, is a failure.
# 4. Both directories are validated AGAIN after the test: a test that changed
#    the bytes it was given proves nothing about them.
# 5. Only then: DIR/result.json (blessed/lifecycle-result/v1), binding the row
#    to the candidate's manifest digest, both artifacts' digests and the
#    proven steps. release-lifecycle-check.sh requires one per declared row
#    before publication.
#
# Exit: 0 passed (result written) · 1 failed · 2 usage/tooling.
# shellcheck disable=SC2016 # jq programs
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() {
  printf 'release-lifecycle: %s\n' "$1" >&2
  exit 2
}
fail() {
  printf 'release-lifecycle: FAIL — %s\n' "$1" >&2
  exit 1
}
row="" decl="" cand="" trust="" sigdir="" repo="" tag="" rels="" base="" base_tag="" out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --row) row="${2:-}" && shift 2 ;;
    --declaration) decl="${2:-}" && shift 2 ;;
    --candidate-dir) cand="${2:-}" && shift 2 ;;
    --trust-store) trust="${2:-}" && shift 2 ;;
    --signing-dir) sigdir="${2:-}" && shift 2 ;;
    --repo) repo="${2:-}" && shift 2 ;;
    --tag) tag="${2:-}" && shift 2 ;;
    --releases-json) rels="${2:-}" && shift 2 ;;
    --baseline-dir) base="${2:-}" && shift 2 ;;
    --baseline-tag) base_tag="${2:-}" && shift 2 ;;
    --out) out="${2:-}" && shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
for v in row decl cand trust sigdir out; do [ -n "${!v}" ] || die "--${v/decl/declaration} is required"; done
[ -n "$base" ] || [ -n "$repo" ] || die "give --repo/--tag (fetch the baseline) or --baseline-dir"
for t in jq yq sha256sum make; do command -v "$t" >/dev/null 2>&1 || die "required tool not found: $t"; done
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

yq -o=json '.' "$decl" >"$work/decl.json" 2>/dev/null || die "$decl is not valid YAML"
jq -e --arg r "$row" '.workflow.lifecycle.rows[] | select(.id == $r)' "$work/decl.json" >"$work/row.json" ||
  die "$decl declares no lifecycle row '$row'"
platform="$(jq -r .platform "$work/row.json")"
level="$(jq -r .evidence "$work/row.json")"
akind="$(jq -r .artifact.install_kind "$work/row.json")"
aplat="$(jq -r .artifact.platform "$work/row.json")"

validate() { # dir → 0 if its signed manifest and every file verify
  bash "$HERE/validate-candidate.sh" --candidate "$1/release-candidate.json" --candidate-dir "$1" \
    --trust-store "$trust" --signing-dir "$sigdir" >"$work/validate.out" 2>&1
}
pick() { # dir → the row artifact's filename (exactly one)
  jq -r --arg p "$aplat" --arg k "$akind" '[.artifacts[] | select(.platform == $p and .install_kind == $k) | .filename]
    | if length == 1 then .[0] else error("\(length) artifacts are \($p) \($k)") end' "$1/release-candidate.json" 2>"$work/pick.err"
}

# ── 1. the candidate: the final bytes ─────────────────────────────────────
validate "$cand" || fail "the candidate does not validate against the trust store: $(tail -3 "$work/validate.out")"
cand="$(cd "$cand" && pwd)"
new_v="$(jq -r .version "$cand/release-candidate.json")"
new_tag="$(jq -r .tag "$cand/release-candidate.json")"
[ -z "$tag" ] || [ "$tag" = "$new_tag" ] || fail "the candidate is $new_tag, not the release tag $tag"
new_f="$(pick "$cand")" || fail "candidate $new_tag: $(sed 's/^jq: error[^:]*: //' "$work/pick.err")"

# ── 2. the baseline: the previous PUBLISHED release ───────────────────────
if [ -z "$base" ]; then
  if [ -n "$rels" ]; then
    cp "$rels" "$work/releases.json"
  else
    command -v gh >/dev/null 2>&1 || die "gh is required to fetch the baseline"
    gh api --paginate "repos/$repo/releases" --jq '.[] | {tag_name, draft, prerelease}' | jq -s . >"$work/releases.json" ||
      fail "cannot list $repo's releases"
  fi
  # SemVer precedence over tags v<major>.<minor>.<patch>[-pre]; drafts and
  # prereleases are never a baseline.
  base_tag="$(jq -r --arg cur "$new_v" '
    def key: ltrimstr("v") | capture("^(?<c>[0-9]+\\.[0-9]+\\.[0-9]+)(-(?<p>.+))?$") as $m
      | ($m.c | split(".") | map(tonumber)) + (if $m.p then [0, $m.p] else [1, ""] end);
    [.[] | select(.draft == false and .prerelease == false) | .tag_name
      | select(test("^v[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.-]+)?$"))
      | select(key < ("v" + $cur | key))] | sort_by(key) | last // empty' "$work/releases.json")"
  [ -n "$base_tag" ] || fail "$repo has no published release before $new_tag to upgrade from (LP-6 requires N→N+1)"
  base="$work/baseline"
  mkdir -p "$base"
  gh release download "$base_tag" -R "$repo" -D "$base" >/dev/null 2>&1 || fail "cannot download baseline $base_tag"
fi
[ -f "$base/release-candidate.json" ] || fail "the baseline has no release-candidate.json"
validate "$base" || fail "the baseline does not validate against the trust store: $(tail -3 "$work/validate.out")"
base="$(cd "$base" && pwd)"
old_v="$(jq -r .version "$base/release-candidate.json")"
old_tag="$(jq -r .tag "$base/release-candidate.json")"
old_f="$(pick "$base")" || fail "baseline $old_tag: $(sed 's/^jq: error[^:]*: //' "$work/pick.err") — no N for this row's N→N+1"
[ "$old_tag" != "$new_tag" ] || fail "the baseline is the candidate itself ($new_tag)"
[ -z "$base_tag" ] || [ "$old_tag" = "$base_tag" ] ||
  fail "release $base_tag carries a candidate for $old_tag: the baseline must be the selected published release itself"

# ── 3. the product's lifecycle test ───────────────────────────────────────
mkdir -p "$out"
out="$(cd "$out" && pwd)"
ev="$work/evidence.json"
printf 'release-lifecycle: row %s (%s, %s): %s %s → %s %s\n' "$row" "$platform" "$level" "$old_tag" "$old_f" "$new_tag" "$new_f"
if ! make release-lifecycle LIFECYCLE_ROW="$row" LIFECYCLE_PLATFORM="$platform" LIFECYCLE_EVIDENCE_LEVEL="$level" \
  LIFECYCLE_NEW="$cand" LIFECYCLE_NEW_FILE="$new_f" LIFECYCLE_NEW_VERSION="$new_v" \
  LIFECYCLE_OLD="$base" LIFECYCLE_OLD_FILE="$old_f" LIFECYCLE_OLD_VERSION="$old_v" \
  LIFECYCLE_EVIDENCE="$ev"; then
  fail "make release-lifecycle failed for row $row"
fi
[ -s "$ev" ] || fail "make release-lifecycle exited 0 but wrote no evidence ($row): a test that reports nothing proves nothing"
jq -e --arg r "$row" '.row == $r and (.proven | type == "array")' "$ev" >/dev/null 2>&1 ||
  fail "the evidence is not {\"row\": \"$row\", \"proven\": [...]}"
missing="$(jq -r --slurpfile e "$ev" '.proves - $e[0].proven | join(", ")' "$work/row.json")"
[ -z "$missing" ] || fail "row $row declares it proves [$missing], but the test did not report them proven"

# ── 4. the inputs are still the bytes that were validated ─────────────────
validate "$cand" || fail "the candidate changed during the test: $(tail -2 "$work/validate.out")"
validate "$base" || fail "the baseline changed during the test: $(tail -2 "$work/validate.out")"

# ── 5. the result ─────────────────────────────────────────────────────────
sha() { sha256sum "$1" | cut -d' ' -f1; }
jq -n --arg row "$row" --arg plat "$platform" --arg lvl "$level" --slurpfile e "$ev" \
  --arg nt "$new_tag" --arg nm "$(sha "$cand/release-candidate.json")" --arg nf "$new_f" --arg ns "$(sha "$cand/$new_f")" \
  --arg ot "$old_tag" --arg om "$(sha "$base/release-candidate.json")" --arg of "$old_f" --arg os "$(sha "$base/$old_f")" \
  --arg run "${GITHUB_RUN_ID:-local}" --arg att "${GITHUB_RUN_ATTEMPT:-1}" --arg gr "${GITHUB_REPOSITORY:-}" \
  '{schema: "blessed/lifecycle-result/v1", row: $row, platform: $plat, evidence: $lvl, result: "pass",
    candidate: {tag: $nt, manifest_sha256: $nm, artifact: $nf, sha256: $ns},
    baseline: {tag: $ot, manifest_sha256: $om, artifact: $of, sha256: $os},
    proven: ($e[0].proven | unique), run: {repository: $gr, run_id: $run, run_attempt: $att}}' >"$out/result.json"
cp "$ev" "$out/evidence.json"
printf 'release-lifecycle: PASS — row %s (%s): %s → %s proven [%s]\n' "$row" "$level" "$old_tag" "$new_tag" "$(jq -r '.proven | join(", ")' "$out/result.json")"
