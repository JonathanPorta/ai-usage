#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Canonical pin file, with legacy fallback for gradual fleet migration.
VERSION_FILE="$REPO_ROOT/scripts/.blessed-scripts-version"
[[ -f "$VERSION_FILE" ]] || VERSION_FILE="$REPO_ROOT/scripts/.bws-scripts-version"

[[ -f "$VERSION_FILE" ]] || {
  echo "ERROR: no scripts/.blessed-scripts-version (or legacy .bws-scripts-version)" >&2
  exit 1
}

# shellcheck disable=SC1090
. "$VERSION_FILE"

BCICD_REPO_SLUG="${BCICD_REPO_SLUG:-JonathanPorta/blessed-cicd}"
# Stage on the destination filesystem so installation uses directory renames.
STAGING_DIR="$(mktemp -d "$REPO_ROOT/scripts/.blessed-scripts.XXXXXX")"
CATEGORIES=""
NEW_CATEGORIES=""

cleanup() {
  local status=$?
  local cat installed
  local restore_failed=0
  if [[ "$status" -ne 0 ]]; then
    for cat in $CATEGORIES; do
      installed="$REPO_ROOT/scripts/$cat"
      if [[ -e "$STAGING_DIR/$cat/previous" || -L "$STAGING_DIR/$cat/previous" ]]; then
        if ! { rm -rf "$installed" && mv "$STAGING_DIR/$cat/previous" "$installed"; }; then
          echo "ERROR: restore failed; previous scripts/$cat retained at $STAGING_DIR/$cat/previous" >&2
          restore_failed=1
        fi
      fi
    done
    # Categories that did not exist before this update have no backup to restore.
    for cat in $NEW_CATEGORIES; do
      rm -rf "$REPO_ROOT/scripts/$cat"
    done
  fi
  [[ "$restore_failed" -eq 0 ]] || return 1
  rm -rf "$STAGING_DIR"
  return "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

stage_category() {
  local cat="$1"
  local version="$2"
  local tag="$3"
  local archive="${cat}-v${version}.tar.gz"
  local staging="$STAGING_DIR/$cat"
  mkdir -p "$staging/candidate"

  echo "==> scripts/$cat ← $tag"
  gh release download "$tag" \
    --repo "$BCICD_REPO_SLUG" \
    --pattern "$archive" \
    --dir "$staging" \
    --clobber

  [[ -f "$staging/$archive" ]] || {
    echo "ERROR: expected release asset $archive from $BCICD_REPO_SLUG@$tag" >&2
    exit 1
  }

  tar -xzf "$staging/$archive" -C "$staging/candidate"
  ( cd "$staging/candidate/$cat" && shasum -a 256 -c MANIFEST.sha256 )
  CATEGORIES="$CATEGORIES $cat"
}

# Verify every pinned category before replacing any installed toolkit.
if [[ -n "${BWS_VERSION:-}" ]];     then stage_category bws     "$BWS_VERSION"     "$BWS_TAG";     fi
if [[ -n "${RUAM_VERSION:-}" ]];    then stage_category ruam    "$RUAM_VERSION"    "$RUAM_TAG";    fi
if [[ -n "${SIGNING_VERSION:-}" ]]; then stage_category signing "$SIGNING_VERSION" "$SIGNING_TAG"; fi
if [[ -n "${RELEASE_VERSION:-}" ]]; then stage_category release "$RELEASE_VERSION" "$RELEASE_TAG"; fi

# Preserve backups until every rename succeeds. The EXIT trap rolls back a
# failed or interrupted installation, including categories already replaced.
for cat in $CATEGORIES; do
  installed="$REPO_ROOT/scripts/$cat"
  if [[ -e "$installed" || -L "$installed" ]]; then
    mv "$installed" "$STAGING_DIR/$cat/previous"
  else
    NEW_CATEGORIES="$NEW_CATEGORIES $cat"
  fi
  mv "$STAGING_DIR/$cat/candidate/$cat" "$installed"
done
