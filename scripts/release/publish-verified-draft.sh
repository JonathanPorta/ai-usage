#!/usr/bin/env bash
# publish-verified-draft.sh — publish a release draft only if it is exactly the
# candidate; publishing is what creates the version tag
# (standards/releases/release-pr.md RP-4; pipeline.md trigger: release-branch).
#
# The candidate directory IS the closed asset set: every regular file in it must
# be an asset of the draft, with the same name and SHA-256, and the draft must
# carry nothing else. Before publishing:
#   · the Release for --tag exists and is a DRAFT whose tag does NOT exist yet
#     (a draft that already has its tag was not produced by this flow);
#   · its target_commitish is --sha;
#   · its asset set is exactly the candidate (names and digests).
# Then the draft is published (creating refs/tags/<tag> at --sha) and read back.
#
# Replay: if the Release is already PUBLISHED, is exactly the candidate and its
# tag points at --sha, nothing changes (exit 0) — a lost publication response is
# not a draft. Anything else is refused; published bytes are never replaced.
#
# Uses `gh api` (GH_BIN overrides the binary) with the caller's token.
# Exit: 0 published or identical replay · 1 refused · 2 usage/tooling.
# shellcheck disable=SC2016  # jq programs: $-names are jq variables, not shell
set -uo pipefail
GH="${GH_BIN:-gh}"
JQ="${JQ_BIN:-jq}"
die() {
  printf 'publish-verified-draft: %s\n' "$1" >&2
  exit 2
}
refuse() {
  printf 'publish-verified-draft: REFUSED — %s\n' "$1" >&2
  exit 1
}
say() { printf 'publish-verified-draft: %s\n' "$1" >&2; }
usage() {
  printf 'Usage: publish-verified-draft.sh --repo OWNER/REPO --tag vX.Y.Z --sha COMMIT --candidate-dir DIR\n' >&2
  exit 2
}
repo="" tag="" sha="" dir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      repo="${2:-}"
      shift 2
      ;;
    --tag)
      tag="${2:-}"
      shift 2
      ;;
    --sha)
      sha="${2:-}"
      shift 2
      ;;
    --candidate-dir)
      dir="${2:-}"
      shift 2
      ;;
    -h | --help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
done
if [ -z "$repo" ] || [ -z "$tag" ] || [ -z "$sha" ] || [ -z "$dir" ]; then usage; fi
case "$sha" in *[!0-9a-f]* | "") die "--sha must be a full lowercase commit SHA" ;; esac
[ "${#sha}" -eq 40 ] || die "--sha must be a full 40-hex commit SHA"
[ -d "$dir" ] || die "--candidate-dir is not a directory"
for t in "$GH" "$JQ" sha256sum find; do command -v "$t" >/dev/null 2>&1 || die "required tool not found: $t"; done

work="$(mktemp -d)" || die "cannot create a work directory"
trap 'rm -rf "$work"' EXIT

# ── the candidate: the closed set of names and digests ─────────────────────
odd="$(cd "$dir" && find . -mindepth 1 ! -type f | head -3)"
[ -z "$odd" ] || refuse "the candidate directory holds something other than flat regular files (release assets are flat): $odd"
(cd "$dir" && find . -maxdepth 1 -type f | sed 's#^\./##' | LC_ALL=C sort | while IFS= read -r f; do
  printf '%s %s\n' "$f" "$(sha256sum "$f" | cut -d' ' -f1)"
done) >"$work/want"
[ -s "$work/want" ] || refuse "the candidate directory is empty"

# ── the Release for the tag (drafts included) ──────────────────────────────
"$GH" api --paginate "repos/$repo/releases?per_page=100" >"$work/all" 2>"$work/err" ||
  die "cannot list releases of $repo: $(head -c 300 "$work/err")"
"$JQ" -s --arg t "$tag" '[.[][] | select(.tag_name == $t)]' "$work/all" >"$work/matches" || die "unreadable releases listing"
n="$("$JQ" length "$work/matches")"
[ "$n" -ge 1 ] || refuse "no Release (draft or published) exists for $tag"
[ "$n" -eq 1 ] || refuse "$n Releases carry $tag; exactly one is expected"
"$JQ" '.[0]' "$work/matches" >"$work/rel"
id="$("$JQ" -r .id "$work/rel")"
draft="$("$JQ" -r .draft "$work/rel")"

tag_sha() { # prints the commit the tag points at, or nothing if the tag does not exist
  local out rc=0
  out="$("$GH" api "repos/$repo/git/ref/tags/$tag" 2>&1)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    case "$out" in *"Not Found"* | *"HTTP 404"*) return 0 ;; esac
    die "cannot read refs/tags/$tag: $(printf '%s' "$out" | head -c 300)"
  fi
  local type obj
  type="$("$JQ" -r .object.type <<<"$out")"
  obj="$("$JQ" -r .object.sha <<<"$out")"
  if [ "$type" = tag ]; then
    obj="$("$GH" api "repos/$repo/git/tags/$obj" --jq .object.sha)" || die "cannot dereference annotated tag $tag"
  fi
  printf '%s' "$obj"
}

# asset_set RELEASE_JSON → writes "name sha256" lines (sorted) to $work/have.
# Every asset must be OBSERVED: a digest from the API or its downloaded bytes.
# Any failed download or unreadable listing stops the run (no partial set can
# ever be compared), and the observed count must equal the listed count.
asset_set() {
  local aid name dg h listed
  "$JQ" -r '.assets[] | [.id, .name, (.digest // "")] | @tsv' "$1" >"$work/listing" ||
    die "the release's asset listing is unreadable"
  listed="$("$JQ" '.assets | length' "$1")" || die "the release's asset listing is unreadable"
  : >"$work/have.unsorted"
  while IFS=$'\t' read -r aid name dg; do
    case "$dg" in
      sha256:*) h="${dg#sha256:}" ;;
      *)
        "$GH" api -H 'Accept: application/octet-stream' "repos/$repo/releases/assets/$aid" >"$work/asset" 2>"$work/err" ||
          die "cannot download asset '$name' to observe its bytes; refusing to judge a partially observed release"
        h="$(sha256sum "$work/asset" | cut -d' ' -f1)"
        ;;
    esac
    printf '%s %s\n' "$name" "$h" >>"$work/have.unsorted"
  done <"$work/listing"
  [ "$(wc -l <"$work/have.unsorted" | tr -d ' ')" = "$listed" ] ||
    die "observed $(wc -l <"$work/have.unsorted" | tr -d ' ') of $listed assets; refusing to judge a partially observed release"
  LC_ALL=C sort "$work/have.unsorted" >"$work/have"
}
same_set() { # compare $work/have with $work/want; print the difference
  if ! diff -u "$work/want" "$work/have" >"$work/diff"; then
    sed -n '3,$p' "$work/diff" | grep '^[-+]' | sed 's/^-/  missing or different: /; s/^+/  unexpected: /' | head -10
    return 1
  fi
}

if [ "$draft" = false ]; then
  asset_set "$work/rel"
  d="$(same_set)" || refuse "$tag is already PUBLISHED and is not exactly this candidate; published bytes are never replaced:
$d"
  got="$(tag_sha)"
  [ "$got" = "$sha" ] || refuse "$tag is published but points at ${got:-<no tag>}, not $sha"
  say "$tag is already published as exactly this candidate at ${sha:0:12}; identical replay, nothing changed"
  exit 0
fi

[ "$draft" = true ] || die "unexpected release state for $tag"
existing="$(tag_sha)"
[ -z "$existing" ] || refuse "the draft for $tag already has its tag (at $existing); a draft created by this flow is tagless"
target="$("$JQ" -r '.target_commitish // empty' "$work/rel")"
[ "$target" = "$sha" ] || refuse "the draft for $tag targets '${target:-<none>}', not the release commit $sha"
asset_set "$work/rel"
d="$(same_set)" || refuse "the draft for $tag is not exactly the candidate:
$d"

"$GH" api -X PATCH "repos/$repo/releases/$id" -F draft=false >"$work/pub" 2>"$work/err" ||
  die "publishing the draft failed: $(head -c 300 "$work/err") — re-run: an identical published release is a replay"

# ── read back ──────────────────────────────────────────────────────────────
"$GH" api "repos/$repo/releases/$id" >"$work/rel2" || die "cannot read the published release back"
[ "$("$JQ" -r .draft "$work/rel2")" = false ] || refuse "the release is still a draft after publishing"
asset_set "$work/rel2"
d="$(same_set)" || refuse "after publishing, the release is not exactly the candidate:
$d"
got="$(tag_sha)"
[ "$got" = "$sha" ] || refuse "after publishing, $tag points at ${got:-<no tag>}, not $sha"
say "published $tag at ${sha:0:12}: $(wc -l <"$work/want" | tr -d ' ') assets, exactly the candidate; the tag was created by publication"
