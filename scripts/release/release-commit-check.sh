#!/usr/bin/env bash
# release-commit-check.sh — the job is working on the release commit
# (standards/releases/release-pr.md RP-3/RP-5).
#
#   release-commit-check.sh [--signing]
#
# Env: RELEASE_VERSION, RELEASE_TAG, RELEASE_SHA (release-please's outputs),
# GITHUB_SHA, GITHUB_REF. Always: the tag is "v" + the version; the release
# commit is this run's commit; the checkout is that commit. With --signing (a
# key-bearing job, RP-5): the run is for refs/heads/main and the commit is an
# ancestor of origin/main as the checkout fetched it (fetch-depth: 0). Exit 0 ok · 1 refused · 2 usage.
set -euo pipefail
refuse() {
  echo "::error::release-commit-check: $1" >&2
  exit 1
}
signing=0
case "${1:-}" in
  --signing) signing=1 ;;
  "") ;;
  *)
    echo "usage: release-commit-check.sh [--signing]" >&2
    exit 2
    ;;
esac
for v in RELEASE_VERSION RELEASE_TAG RELEASE_SHA GITHUB_SHA GITHUB_REF; do
  [ -n "${!v:-}" ] || refuse "$v is not set"
done
[ "$RELEASE_TAG" = "v$RELEASE_VERSION" ] || refuse "release-please tag $RELEASE_TAG is not v$RELEASE_VERSION"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || refuse "the release commit '$RELEASE_SHA' is not a full SHA"
[ "$RELEASE_SHA" = "$GITHUB_SHA" ] || refuse "release-please released $RELEASE_SHA, but this run is for $GITHUB_SHA"
head="$(git rev-parse HEAD)"
[ "$head" = "$RELEASE_SHA" ] || refuse "checked out $head, not the release commit $RELEASE_SHA"
if [ "$signing" = 1 ]; then
  [ "$GITHUB_REF" = refs/heads/main ] || refuse "key-bearing jobs run for main only, not $GITHUB_REF"
  # The job's checkout (fetch-depth: 0) already fetched every branch, so
  # origin/main is local. No second fetch: the checkout keeps no credentials
  # (persist-credentials: false), and a private repository refuses an
  # unauthenticated fetch.
  git rev-parse -q --verify refs/remotes/origin/main >/dev/null ||
    refuse "no origin/main in this checkout (the key-bearing job must check out with fetch-depth: 0)"
  git merge-base --is-ancestor "$RELEASE_SHA" refs/remotes/origin/main ||
    refuse "$RELEASE_SHA is not on origin/main — refusing to sign unreviewed code"
fi
if [ "$signing" = 1 ]; then where=", on main"; else where=""; fi
echo "release-commit-check: $RELEASE_TAG at $RELEASE_SHA$where"
