#!/usr/bin/env bash
# release-publish.sh — publish the signed candidate as the release
# (standards/releases/release-pr.md RP-4). GITHUB_TOKEN with contents:write
# (and pull-requests:write for the race reconciliation); no signing key.
#
#   release-publish.sh --repo OWNER/REPO --tag vX.Y.Z --sha COMMIT \
#                      --candidate-dir DIR --trust-store FILE
#
#   1. the candidate validates against the committed trust store, and DIR holds
#      exactly its manifest's files plus the manifest and its signature;
#   2. release-please's Release for --tag exists. While it is a DRAFT: upload
#      exactly the candidate (--clobber), delete any other asset, point it at
#      --sha. A PUBLISHED release is never modified here;
#   3. publish-verified-draft.sh publishes the draft only if it is exactly the
#      candidate, tagless and at --sha — publication creates the tag — or
#      accepts an identical replay;
#   4. read back: published, the asset count is the candidate's, the tag is --sha;
#   5. release-please ran before the tag existed, so it may have opened a
#      spurious release PR; it is closed only when main has no commit past the
#      tag.
# Exit 0 published or identical replay · 1 refused · 2 usage.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
refuse() {
  echo "::error::release-publish: $1" >&2
  exit 1
}
repo="" tag="" sha="" cand="" trust=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="${2:-}" ;;
    --tag) tag="${2:-}" ;;
    --sha) sha="${2:-}" ;;
    --candidate-dir) cand="${2:-}" ;;
    --trust-store) trust="${2:-}" ;;
    *)
      echo "release-publish: unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift 2 || exit 2
done
for v in repo tag sha cand trust; do [ -n "${!v}" ] || {
  echo "release-publish: --$v is required" >&2
  exit 2
}; done
export GH_REPO="$repo"

bash "$HERE/validate-candidate.sh" --candidate "$cand/release-candidate.json" --candidate-dir "$cand" \
  --trust-store "$trust" --signing-dir "$HERE/../signing" || refuse "the candidate does not validate against $trust"
listed="$(jq -r '.files | length' "$cand/release-candidate.json")"
want=$((listed + 2))
on_disk="$(find "$cand" -type f | wc -l | tr -d ' ')"
[ "$on_disk" -eq "$want" ] || refuse "$cand holds $on_disk file(s); the manifest lists $listed (+ itself and its signature)"
[ -f "$cand/release-candidate.json.sig" ] || refuse "the manifest is unsigned"

draft="$(gh release view "$tag" --json isDraft --jq .isDraft)" || refuse "no Release $tag: release-please did not create the draft"
if [ "$draft" = true ]; then
  gh release upload "$tag" "$cand"/* --clobber
  (cd "$cand" && ls) | sort >"${RUNNER_TEMP:-/tmp}/release-names.txt"
  gh release view "$tag" --json assets --jq '.assets[].name' | sort | comm -13 "${RUNNER_TEMP:-/tmp}/release-names.txt" - |
    while IFS= read -r stray; do
      echo "release-publish: removing stray draft asset $stray"
      gh release delete-asset "$tag" "$stray" --yes
    done
  gh release edit "$tag" --target "$sha" >/dev/null
fi
bash "$HERE/publish-verified-draft.sh" --repo "$repo" --tag "$tag" --sha "$sha" --candidate-dir "$cand" ||
  refuse "publish-verified-draft.sh refused"
n="$(gh release view "$tag" --json isDraft,assets --jq 'if .isDraft then -1 else (.assets | length) end')"
[ "$n" -eq "$want" ] || refuse "published $n asset(s) (-1 = still a draft), expected $want"
ref="$(gh api "repos/$repo/git/ref/tags/$tag" --jq .object.sha)"
[ "$ref" = "$sha" ] || refuse "tag $tag points at $ref, not the release commit $sha"
echo "release-publish: $tag published at $sha with $n asset(s)"

ahead="$(gh api "repos/$repo/compare/$tag...main" --jq .ahead_by)"
if [ "$ahead" != 0 ]; then
  echo "release-publish: main is $ahead commit(s) past $tag; any open release PR is legitimate"
  exit 0
fi
pr="$(gh pr list --state open --head release-please--branches--main --json number --jq '.[0].number // empty')"
if [ -n "$pr" ]; then
  gh pr close "$pr" --comment "Closed: release-please opened this before $tag existed (draft release), and main has no commit past $tag."
  echo "release-publish: closed spurious release PR #$pr"
fi
