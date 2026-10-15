#!/usr/bin/env bash
# admission-fetch.sh — fetch a PUBLISHED release's complete asset set for
# admission (package-repositories PR-2), with the surface's read-only candidate
# credential and nothing else.
#
#   admission-fetch.sh --repo OWNER/REPO --tag vX.Y.Z --token-env NAME --out DIR
#
# The token is taken from the environment by NAME, moved into an unexported
# variable and unset first; it reaches `gh` only through GH_TOKEN for the two
# calls below. A draft is refused (only published candidates are admitted);
# every asset is downloaded into an empty DIR. Exit 0 · 1 refused · 2 usage.
set -euo pipefail
set +x
__name=""
__i=1
while [ "$__i" -le "$#" ]; do
  if [ "${!__i}" = --token-env ]; then
    __j=$((__i + 1))
    __name="${!__j-}"
  fi
  __i=$((__i + 1))
done
case "$__name" in "" | [!A-Z]* | *[!A-Z0-9_]*)
  echo "admission-fetch: --token-env NAME is required" >&2
  exit 2
  ;;
esac
__tok="${!__name-}"
unset "$__name" __i __j
refuse() {
  echo "::error::admission-fetch: $1" >&2
  exit 1
}
repo="" tag="" out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="${2:-}" ;;
    --tag) tag="${2:-}" ;;
    --token-env) : ;;
    --out) out="${2:-}" ;;
    *)
      echo "admission-fetch: unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift 2 || exit 2
done
[[ "$repo" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || refuse "--repo is not owner/repo"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || refuse "--tag is not vX.Y.Z"
{ [ -n "$out" ] && [ ! -e "$out" ]; } || refuse "--out must name a directory that does not exist"
[ -n "$__tok" ] || refuse "$__name is empty: the candidate-ingest domain did not load"
draft="$(GH_TOKEN="$__tok" gh release view "$tag" --repo "$repo" --json isDraft --jq .isDraft)" ||
  refuse "cannot read $repo $tag with the candidate read token"
[ "$draft" = false ] || refuse "$repo $tag is a draft; only published candidates are admitted"
mkdir -p "$out"
GH_TOKEN="$__tok" gh release download "$tag" --repo "$repo" --dir "$out" || refuse "cannot download $repo $tag"
__tok=""
echo "admission-fetch: $repo $tag — $(find "$out" -type f | wc -l | tr -d ' ') asset(s)"
