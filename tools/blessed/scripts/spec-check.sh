#!/usr/bin/env bash
# spec-check.sh — report-only checker for spec-pack presence and basic shape.
# Reads docs/SPEC.yml when present; otherwise applies core defaults.
#
# Exit 0 (report-only) unless --strict and unwaived required items are missing.
#
# Usage:
#   make spec-check
#   ARGS="--strict" make spec-check
set -uo pipefail

STRICT=false
QUIET=false
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=true ;;
    --quiet) QUIET=true ;;
    *) ;;
  esac
done

# shellcheck disable=SC2034  # LIB_DIR kept for future parity with other checkers / sourcing
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer explicit REPO_ROOT, fall back to PWD (adopting repo context).
# STANDARDS_ROOT is intentionally ignored here; spec-check is repo-content, not the blessed-cicd source tree.
REPO_ROOT="${REPO_ROOT:-$(pwd)}"
TODAY="${SPEC_CHECK_TODAY:-${DESIGN_CHECK_TODAY:-$(date -u +%Y-%m-%d)}}"

for t in yq jq; do
  command -v "$t" >/dev/null 2>&1 || {
    echo "$t not found — cannot run spec-check" >&2
    exit 2
  }
done

DOCS="$REPO_ROOT/docs"
SPEC="$DOCS/SPEC.yml"

findings=0
waived=0
scanned_core=0

have() { [[ -f "$1" ]] && echo "present" || echo "MISSING"; }

is_missing() { [[ "$1" == "MISSING" ]]; }

report() {
  local label="$1" state="$2"
  if $QUIET; then return; fi
  if is_missing "$state"; then
    echo "  ⚠ $label: $state"
  else
    echo "  ✓ $label: $state"
  fi
}

is_calendar_date() {
  local d="$1" normalized
  [[ "$d" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || return 1
  if normalized="$(date -u -j -f "%Y-%m-%d" "$d" "+%Y-%m-%d" 2>/dev/null)"; then
    [[ "$normalized" == "$d" ]]
    return
  fi
  if normalized="$(date -u -d "$d" "+%Y-%m-%d" 2>/dev/null)"; then
    [[ "$normalized" == "$d" ]]
    return
  fi
  return 1
}

# Active SPEC.yml path waiver: reason, owner, review_by (today or later).
# Iterates all matching structurally valid waivers — any active match waives.
# Mirrors design-check: do not stop at the first historical record.
# Note: mikefarah yq has no jq-style --arg; convert to JSON and filter with jq.
spec_path_waived() {
  local path="$1"
  [[ -f "$SPEC" ]] || return 1
  local review_by spec_json
  if ! spec_json="$(yq -o=json '.' "$SPEC" 2>/dev/null)"; then
    return 1
  fi
  while IFS= read -r review_by; do
    [[ -n "$review_by" && "$review_by" != "null" ]] || continue
    is_calendar_date "$review_by" || continue
    # ISO dates compare lexicographically when calendar-valid.
    if [[ ! "$review_by" < "$TODAY" ]]; then
      return 0
    fi
    # Type-safe like design-check: non-string reason/owner/review_by cannot waive.
  done < <(jq -r --arg p "$path" '
    ((.waivers // .exceptions) // [])[]?
    | select(
        (.path | type == "string")
        and .path == $p
        and (.reason | type == "string" and length > 0)
        and (.owner | type == "string" and length > 0)
        and (.review_by | type == "string" and length > 0)
      )
    | .review_by
  ' <<<"$spec_json" 2>/dev/null || true)
  return 1
}

missing_or_waived() {
  local rel="$1"
  local label="${2:-$rel}"
  local state
  state="$(have "$REPO_ROOT/$rel")"
  report "$label" "$state"
  if is_missing "$state"; then
    if spec_path_waived "$rel"; then
      waived=$((waived + 1))
      [[ "$QUIET" == false ]] && echo "      (waived in SPEC.yml)"
      return 0
    fi
    findings=$((findings + 1))
    return 1
  fi
  return 0
}

# Load declared requirements from SPEC.yml if it exists.
# We keep v1 simple: core files + optional surfaces + basic checks flags.
core_files=(IDEA.md MVP.md DECISIONS.md SPEC.yml)
surface_hints=()

if [[ -f "$SPEC" ]]; then
  # surfaces keys that are marked required:true (portable for bash 3.2)
  while IFS= read -r key; do
    [[ -n "$key" ]] && surface_hints+=("$key")
  done < <(yq -r '.surfaces | to_entries[] | select(.value.required == true) | .key' "$SPEC" 2>/dev/null || true)
fi
# Guard under set -u
if [[ ${#surface_hints[@]} -eq 0 ]]; then
  surface_hints=()
fi

echo "spec-check: scanning $REPO_ROOT"

# Core
for f in "${core_files[@]}"; do
  scanned_core=$((scanned_core + 1))
  missing_or_waived "docs/$f" "docs/$f" || true
done

# Basic shape checks when files exist
if [[ -f "$DOCS/MVP.md" ]]; then
  if ! grep -qi 'acceptance criteria' "$DOCS/MVP.md" 2>/dev/null; then
    [[ "$QUIET" == false ]] && echo "  ⚠ docs/MVP.md: missing 'acceptance criteria' section"
    findings=$((findings + 1))
  fi
fi

if [[ -f "$DOCS/SPEC.yml" ]]; then
  if ! yq -e '.schema' "$DOCS/SPEC.yml" >/dev/null 2>&1; then
    [[ "$QUIET" == false ]] && echo "  ⚠ docs/SPEC.yml: missing or invalid schema"
    findings=$((findings + 1))
  fi
fi

# Surfaces
for s in ${surface_hints[@]+"${surface_hints[@]}"}; do
  [[ -z "$s" ]] && continue
  case "$s" in
    web | app)
      # Full design package for required UI surfaces (or active SPEC waiver per path).
      for asset in brief.md design.md handoff.yml assets.yml; do
        missing_or_waived "docs/design/$s/$asset" "surface $s: docs/design/$s/$asset" || true
      done
      # When handoff.yml exists, its declared .surface must match the bucket.
      # Mismatch is unwaivable — presence alone must not satisfy a required surface.
      # Tool/parser failure must not be converted into document data.
      handoff_rel="docs/design/$s/handoff.yml"
      if [[ -f "$REPO_ROOT/$handoff_rel" ]]; then
        declared_surface=""
        yq_err=""
        # mikefarah yq: default-value uses a literal (""), not jq's `empty`.
        if ! declared_surface="$(yq -r '.surface // ""' "$REPO_ROOT/$handoff_rel" 2>&1)"; then
          yq_err="$declared_surface"
          [[ "$QUIET" == false ]] &&
            echo "  ⚠ surface $s: unable to parse $handoff_rel${yq_err:+ ($yq_err)}"
          findings=$((findings + 1))
        elif [[ "$declared_surface" != "$s" ]]; then
          [[ "$QUIET" == false ]] &&
            echo "  ⚠ surface $s: $handoff_rel declares surface '$declared_surface' (must be '$s'; unwaivable)"
          findings=$((findings + 1))
        else
          [[ "$QUIET" == false ]] && echo "  ✓ surface $s: handoff surface matches bucket"
        fi
      fi
      ;;
    infra)
      if [[ ! -f "$DOCS/DEPLOYMENT.md" ]]; then
        if spec_path_waived "docs/DEPLOYMENT.md"; then
          waived=$((waived + 1))
          [[ "$QUIET" == false ]] && echo "  ~ surface infra: docs/DEPLOYMENT.md (waived)"
        else
          [[ "$QUIET" == false ]] && echo "  ⚠ surface infra: expected docs/DEPLOYMENT.md"
          findings=$((findings + 1))
        fi
      fi
      ;;
    *)
      # Unknown required surface: keep light v1 behavior (no extra requirement).
      :
      ;;
  esac
done

echo "------------------------------------------------------------"
echo "spec-check: $findings unwaived finding(s), $waived waived ($scanned_core core items checked)"

if [[ "$STRICT" == true && "$findings" -gt 0 ]]; then
  exit 1
fi
exit 0
