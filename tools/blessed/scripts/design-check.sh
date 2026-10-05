#!/usr/bin/env bash
# design-check.sh — report-only validator for blessed/design-handoff/v1.
# Document shape is owned by the checked-in JSON Schema; this script owns
# filesystem, Makefile, cross-file, and waiver-expiry semantics.
# Exit 0 unless --strict and unwaived findings exist.
# Usage: make design-check [ARGS="--strict"]
set -uo pipefail

STRICT=false
QUIET=false
EXPLICIT_HANDOFF=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict)
      STRICT=true
      ;;
    --quiet)
      QUIET=true
      ;;
    --handoff)
      shift
      EXPLICIT_HANDOFF="${1:-}"
      ;;
    --handoff=*)
      EXPLICIT_HANDOFF="${1#*=}"
      ;;
    *)
      echo "design-check: unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

for t in yq jq; do
  command -v "$t" >/dev/null 2>&1 || {
    echo "$t not found — cannot run design-check" >&2
    exit 2
  }
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091  # runtime path; lib is always sibling of this script
source "$SCRIPT_DIR/lib-json-schema.sh"

REPO_ROOT="${REPO_ROOT:-$(pwd)}"
SCHEMA_PATH="${DESIGN_HANDOFF_SCHEMA:-$SCRIPT_DIR/../standards/design-system/references/design-handoff.schema.json}"
TODAY="${DESIGN_CHECK_TODAY:-$(date -u +%Y-%m-%d)}"

findings=0
waived=0
scanned=0
required_surface_missing=0

note() {
  if [[ "$QUIET" == false ]]; then
    echo "$1"
  fi
}

pass() {
  if [[ "$QUIET" == false ]]; then
    echo "  ✓ $1"
  fi
}

warn() {
  if [[ "$QUIET" == false ]]; then
    echo "  ⚠ $1"
  fi
  findings=$((findings + 1))
}

waive() {
  if [[ "$QUIET" == false ]]; then
    echo "  ~ $1 (waived)"
  fi
  waived=$((waived + 1))
}

# Calendar date: YYYY-MM-DD that date(1) accepts and normalizes to itself.
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

safe_relative_path() {
  local p="$1"
  [[ -n "$p" ]] || return 1
  [[ "$p" != /* ]] || return 1
  [[ "$p" != *\\* ]] || return 1
  [[ "$p" != *$'\n'* && "$p" != *$'\r'* && "$p" != *$'\t'* ]] || return 1
  case "$p" in
    *[[:cntrl:]]*) return 1 ;;
  esac
  case "/$p/" in
    */../* | */./* | *//*) return 1 ;;
  esac
  return 0
}

sha256_file() {
  local path="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  else
    echo "design-check: shasum or sha256sum required for accepted source checksum" >&2
    return 2
  fi
}

makefile_has_target() {
  local makefile="$1" target="$2"
  [[ -f "$makefile" ]] || return 1
  # Match a real rule head (not .PHONY alone): target at line start, then ':' not ':='.
  grep -E "^${target}[[:space:]]*:[^=]" "$makefile" >/dev/null 2>&1 ||
    grep -E "^${target}[[:space:]]*:$" "$makefile" >/dev/null 2>&1
}

# Active waiver: required fields + real calendar review_by >= TODAY.
valid_waiver() {
  local json="$1"
  local check="$2"
  local today="$TODAY"
  local entries review_by
  entries="$(jq -c --arg c "$check" '
    (.waivers // [])
    | map(select(
        .check == $c
        and (.reason | type == "string" and length > 0)
        and (.owner | type == "string" and length > 0)
        and (.review_by | type == "string" and length > 0)
      ))
  ' <<<"$json" 2>/dev/null)" || return 1
  [[ "$(jq -r 'length' <<<"$entries")" -gt 0 ]] || return 1

  while IFS= read -r review_by; do
    [[ -n "$review_by" ]] || continue
    if ! is_calendar_date "$review_by"; then
      continue
    fi
    # ISO dates compare lexicographically when calendar-valid.
    if [[ "$review_by" < "$today" ]]; then
      continue
    fi
    return 0
  done < <(jq -r '.[].review_by' <<<"$entries")
  return 1
}

# True if a structurally matching waiver exists but is expired (past review_by).
expired_waiver_exists() {
  local json="$1"
  local check="$2"
  local today="$TODAY"
  local review_by
  while IFS= read -r review_by; do
    [[ -n "$review_by" ]] || continue
    is_calendar_date "$review_by" || continue
    if [[ "$review_by" < "$today" ]]; then
      return 0
    fi
  done < <(jq -r --arg c "$check" '
    (.waivers // [])[]?
    | select(
        .check == $c
        and (.reason | type == "string" and length > 0)
        and (.owner | type == "string" and length > 0)
        and (.review_by | type == "string" and length > 0)
      )
    | .review_by
  ' <<<"$json" 2>/dev/null)
  return 1
}

finding_or_waiver() {
  local json="$1"
  local check="$2"
  local message="$3"
  if valid_waiver "$json" "$check"; then
    waive "$message"
  else
    if expired_waiver_exists "$json" "$check"; then
      warn "expired waiver for '$check' (review_by before $TODAY); $message"
    else
      warn "$message"
    fi
  fi
}

check_declared_path() {
  local json="$1"
  local expr="$2"
  local label="$3"
  local check="$4"
  local required="${5:-false}"
  local path
  path="$(jq -r "$expr // empty" <<<"$json")"
  if [[ -z "$path" ]]; then
    if [[ "$required" == true ]]; then
      finding_or_waiver "$json" "$check" "$label: missing path declaration"
    fi
    return
  fi
  if ! safe_relative_path "$path"; then
    warn "$label: unsafe/non-relative path '$path'"
    return
  fi
  if [[ ! -e "$REPO_ROOT/$path" ]]; then
    finding_or_waiver "$json" "$check" "$label: declared path not found: $path"
  else
    pass "$label: $path"
  fi
}

validate_waivers() {
  local json="$1"
  local line check review_by
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    check="${line%%|*}"
    review_by="${line#*|}"
    if [[ -z "$check" || "$check" == "<missing-check>" ]]; then
      warn "waiver: requires check, reason, owner, and ISO review_by"
      continue
    fi
    if [[ -z "$review_by" || "$review_by" == "null" ]]; then
      warn "waiver '$check': requires check, reason, owner, and ISO review_by"
      continue
    fi
    if ! is_calendar_date "$review_by"; then
      warn "waiver '$check': review_by '$review_by' is not a real calendar date"
      continue
    fi
  done < <(jq -r '
    (.waivers // [])[]?
    | select(
        (.check | type != "string" or length == 0)
        or (.reason | type != "string" or length == 0)
        or (.owner | type != "string" or length == 0)
        or (.review_by | type != "string" or length == 0)
      )
    | ((.check // "<missing-check>") + "|" + (.review_by // ""))
  ' <<<"$json" 2>/dev/null || true)

  # Flag malformed review_by on otherwise complete waivers (date shape/calendar).
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    check="${line%%|*}"
    review_by="${line#*|}"
    if ! is_calendar_date "$review_by"; then
      warn "waiver '$check': review_by '$review_by' is not a real calendar date"
    fi
  done < <(jq -r '
    (.waivers // [])[]?
    | select(
        (.check | type == "string" and length > 0)
        and (.reason | type == "string" and length > 0)
        and (.owner | type == "string" and length > 0)
        and (.review_by | type == "string" and length > 0)
      )
    | (.check + "|" + .review_by)
  ' <<<"$json" 2>/dev/null || true)
}

validate_make_checks() {
  local json="$1"
  local rel="$2"
  local check target makefile
  makefile="$REPO_ROOT/Makefile"
  while IFS= read -r check; do
    [[ -n "$check" ]] || continue
    if [[ "$check" =~ ^make[[:space:]]+([A-Za-z0-9][A-Za-z0-9_-]*)$ ]]; then
      target="${BASH_REMATCH[1]}"
      if [[ ! -f "$makefile" ]]; then
        warn "$rel: checks declare 'make $target' but Makefile is missing"
      elif makefile_has_target "$makefile" "$target"; then
        pass "make target exists: $target"
      else
        warn "$rel: checks declare 'make $target' but Makefile has no '$target' target"
      fi
    fi
  done < <(jq -r '.checks[]? // empty' <<<"$json")
}

require_accepted_app_web_contract() {
  local json="$1"
  local rel="$2"

  # package manager (string, not a path)
  local pm
  pm="$(jq -r '.stack.package_manager // empty' <<<"$json")"
  if [[ -z "$pm" ]]; then
    finding_or_waiver "$json" "stack.package_manager" "$rel: accepted app/web handoff requires stack.package_manager"
  else
    pass "package_manager: $pm"
  fi

  check_declared_path "$json" '.implementation.screen_inventory' "screen inventory" "implementation.screen_inventory" true
  check_declared_path "$json" '.implementation.interaction_state_inventory' "interaction/state inventory" "implementation.interaction_state_inventory" true
  check_declared_path "$json" '.implementation.responsive_contract' "responsive contract" "implementation.responsive_contract" true
  check_declared_path "$json" '.implementation.accessibility_contract' "accessibility contract" "implementation.accessibility_contract" true
  check_declared_path "$json" '.implementation.asset_inventory' "asset inventory" "implementation.asset_inventory" true
  # component_manifest is always required (schema + authority path check above).
  check_declared_path "$json" '.implementation.visual_baselines' "visual baselines" "implementation.visual_baselines" true
  check_declared_path "$json" '.implementation.migration_notes' "migration notes" "implementation.migration_notes" true

  # Build/test authority: nonempty checks already schema-enforced; ensure at least
  # one non-design-check command for implementable build/test authority.
  if ! jq -e '
    (.checks // [])
    | map(select(. != "make design-check"))
    | length > 0
  ' <<<"$json" >/dev/null 2>&1; then
    finding_or_waiver "$json" "checks.build_test" \
      "$rel: accepted app/web handoff requires build/test commands beyond make design-check"
  fi
}

# Required UI surfaces from docs/SPEC.yml (app/web only).
required_ui_surfaces=()
if [[ -f "$REPO_ROOT/docs/SPEC.yml" ]]; then
  while IFS= read -r key; do
    case "$key" in
      app | web)
        required_ui_surfaces+=("$key")
        ;;
    esac
  done < <(yq -r '.surfaces | to_entries[]? | select(.value.required == true) | .key' \
    "$REPO_ROOT/docs/SPEC.yml" 2>/dev/null || true)
fi

handoffs=()
if [[ -n "$EXPLICIT_HANDOFF" ]]; then
  handoffs+=("$EXPLICIT_HANDOFF")
elif [[ -d "$REPO_ROOT/docs/design" ]]; then
  while IFS= read -r path; do
    if [[ -n "$path" ]]; then
      handoffs+=("${path#"$REPO_ROOT/"}")
    fi
  done < <(find "$REPO_ROOT/docs/design" -type f -name handoff.yml | LC_ALL=C sort)
fi

# Required surfaces must not silently NOT_ADOPT.
if [[ ${#required_ui_surfaces[@]} -gt 0 ]]; then
  for surface in "${required_ui_surfaces[@]}"; do
    expected="docs/design/$surface/handoff.yml"
    found=false
    for rel in ${handoffs[@]+"${handoffs[@]}"}; do
      if [[ "$rel" == "$expected" ]]; then
        found=true
        break
      fi
    done
    if [[ "$found" == false ]]; then
      required_surface_missing=1
      warn "required surface '$surface': missing $expected (SPEC.yml surfaces.$surface.required)"
    fi
  done
fi

if [[ ${#handoffs[@]} -eq 0 ]]; then
  if [[ "$required_surface_missing" -eq 0 ]]; then
    note "design-check: NOT_ADOPTED — no docs/design/*/handoff.yml found"
    exit 0
  fi
  note "design-check: required UI surface declared but no handoff present"
  note "------------------------------------------------------------"
  note "design-check: $findings unwaived finding(s), $waived waived (0 handoff(s) checked)"
  if [[ "$STRICT" == true && "$findings" -gt 0 ]]; then
    exit 1
  fi
  exit 0
fi

if [[ ! -f "$SCHEMA_PATH" ]]; then
  echo "design-check: schema not found at $SCHEMA_PATH" >&2
  exit 2
fi

note "design-check: scanning $REPO_ROOT (today=$TODAY)"

for rel in "${handoffs[@]}"; do
  scanned=$((scanned + 1))
  file="$REPO_ROOT/$rel"
  note "handoff: $rel"

  if [[ ! -f "$file" ]]; then
    warn "$rel: file not found"
    continue
  fi

  if ! json="$(yq -o=json '.' "$file" 2>/dev/null)"; then
    warn "$rel: invalid YAML"
    continue
  fi
  if ! jq -e 'type == "object"' <<<"$json" >/dev/null 2>&1; then
    warn "$rel: root must be an object"
    continue
  fi

  # Schema owns document shape (including additionalProperties: false).
  # Validate via a temp file so jq never depends on stdin/--argjson quoting.
  schema_out=""
  schema_tmp="$(mktemp)"
  printf '%s\n' "$json" >"$schema_tmp"
  if ! schema_out="$(json_schema_validate_file "$schema_tmp" "$SCHEMA_PATH" 2>&1)"; then
    rm -f "$schema_tmp"
    warn "$rel: schema validation failed to run: $schema_out"
    continue
  fi
  rm -f "$schema_tmp"
  if [[ -n "$schema_out" ]]; then
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      warn "$rel: schema: $line"
    done <<<"$schema_out"
  else
    pass "schema: blessed/design-handoff/v1"
  fi

  surface="$(jq -r '.surface // empty' <<<"$json")"
  status="$(jq -r '.status // empty' <<<"$json")"
  profile="$(jq -r '.stack.styling.profile // empty' <<<"$json")"

  # Bind docs/design/<bucket>/handoff.yml to .surface (unwaivable).
  # Path-bound surface drives app/web contract evaluation so a mismatched
  # discriminator cannot skip the accepted implementation-handoff lanes.
  expected_surface=""
  if [[ "$rel" =~ ^docs/design/([A-Za-z0-9_-]+)/handoff\.yml$ ]]; then
    bucket="${BASH_REMATCH[1]}"
    case "$bucket" in
      app | web | cli | icon | infra)
        expected_surface="$bucket"
        ;;
    esac
  fi
  if [[ -n "$expected_surface" ]]; then
    if [[ "$surface" != "$expected_surface" ]]; then
      warn "$rel: surface '$surface' does not match design bucket '$expected_surface' (unwaivable)"
    else
      pass "surface matches design bucket: $expected_surface"
    fi
    # Evaluate semantics against the canonical bucket, not a lying discriminator.
    surface="$expected_surface"
  fi

  validate_waivers "$json"

  check_declared_path "$json" '.authority.design' "design authority" "authority.design" true
  check_declared_path "$json" '.authority.engineering' "engineering authority" "authority.engineering" true
  check_declared_path "$json" '.authority.tokens' "token authority" "authority.tokens" true
  check_declared_path "$json" '.authority.production_components' "production components" "authority.production_components" true
  check_declared_path "$json" '.implementation.design_system_root' "design-system root" "implementation.design_system_root" true
  check_declared_path "$json" '.implementation.component_manifest' "component manifest" "implementation.component_manifest" true

  # Optional paths when present (draft or non-app/web).
  for pair in \
    '.implementation.screen_inventory|screen inventory|implementation.screen_inventory' \
    '.implementation.interaction_state_inventory|interaction/state inventory|implementation.interaction_state_inventory' \
    '.implementation.responsive_contract|responsive contract|implementation.responsive_contract' \
    '.implementation.accessibility_contract|accessibility contract|implementation.accessibility_contract' \
    '.implementation.asset_inventory|asset inventory|implementation.asset_inventory' \
    '.implementation.visual_baselines|visual baselines|implementation.visual_baselines' \
    '.implementation.migration_notes|migration notes|implementation.migration_notes'; do
    expr="${pair%%|*}"
    rest="${pair#*|}"
    label="${rest%%|*}"
    check="${rest#*|}"
    # Skip re-check when accepted app/web will require them below.
    if [[ "$status" == "accepted" && ("$surface" == "app" || "$surface" == "web") ]]; then
      continue
    fi
    if [[ "$(jq -r "$expr // empty" <<<"$json")" != "" ]]; then
      check_declared_path "$json" "$expr" "$label" "$check" false
    fi
  done

  if [[ "$profile" == "tailwind-v4" ]]; then
    tokens_kind="$(jq -r '.stack.styling.canonical_tokens // empty' <<<"$json")"
    [[ "$tokens_kind" == "dtcg" || "$tokens_kind" == "css" ]] || warn "$rel: tailwind-v4 canonical_tokens must be dtcg or css"
    check_declared_path "$json" '.stack.styling.generated_theme' "generated Tailwind theme" "stack.styling.generated_theme" true
  elif [[ "$surface" == "app" || "$surface" == "web" ]]; then
    if valid_waiver "$json" "tailwind-v4-profile"; then
      waive "tailwind-v4 default: profile '$profile'"
    elif expired_waiver_exists "$json" "tailwind-v4-profile"; then
      warn "$rel: expired waiver for 'tailwind-v4-profile' (review_by before $TODAY); app/web profile '$profile' requires a valid tailwind-v4-profile waiver"
    else
      warn "$rel: app/web profile '$profile' requires a valid tailwind-v4-profile waiver"
    fi
  fi

  if ! jq -e '.checks | type == "array" and length > 0' <<<"$json" >/dev/null 2>&1; then
    warn "$rel: checks must be a nonempty array"
  elif ! jq -e '.checks | index("make design-check") != null' <<<"$json" >/dev/null 2>&1; then
    warn "$rel: checks must include 'make design-check'"
  else
    pass "checks include make design-check"
  fi

  if [[ "$profile" == "tailwind-v4" ]] && ! jq -e '.checks | index("make design-build-check") != null' <<<"$json" >/dev/null 2>&1; then
    warn "$rel: tailwind-v4 checks must include 'make design-build-check'"
  fi

  validate_make_checks "$json" "$rel"

  if [[ "$status" == "accepted" ]]; then
    accepted_at="$(jq -r '.accepted_at // empty' <<<"$json")"
    if [[ -z "$accepted_at" ]]; then
      warn "$rel: accepted handoff requires accepted_at ISO date"
    elif ! is_calendar_date "$accepted_at"; then
      warn "$rel: accepted_at '$accepted_at' is not a real calendar date"
    else
      pass "accepted_at: $accepted_at"
    fi

    archive="$(jq -r '.source.archive // empty' <<<"$json")"
    expected_sha="$(jq -r '.source.sha256 // empty' <<<"$json")"

    if [[ -z "$archive" ]]; then
      finding_or_waiver "$json" "source.archive" "$rel: accepted handoff requires source.archive"
    elif ! safe_relative_path "$archive"; then
      warn "$rel: unsafe source.archive '$archive'"
    elif [[ ! -f "$REPO_ROOT/$archive" ]]; then
      finding_or_waiver "$json" "source.archive" "$rel: source archive not found: $archive"
    else
      pass "source archive: $archive"
      if [[ ! "$expected_sha" =~ ^[A-Fa-f0-9]{64}$ ]]; then
        finding_or_waiver "$json" "source.sha256" "$rel: accepted handoff requires 64-hex source.sha256"
      else
        actual_sha="$(sha256_file "$REPO_ROOT/$archive")" || exit 2
        actual_sha_lc="$(printf '%s' "$actual_sha" | tr '[:upper:]' '[:lower:]')"
        expected_sha_lc="$(printf '%s' "$expected_sha" | tr '[:upper:]' '[:lower:]')"
        if [[ "$actual_sha_lc" != "$expected_sha_lc" ]]; then
          warn "$rel: source archive checksum mismatch"
        else
          pass "source archive checksum"
        fi
      fi
    fi

    if [[ "$surface" == "app" || "$surface" == "web" ]]; then
      require_accepted_app_web_contract "$json" "$rel"
    fi
  fi
done

note "------------------------------------------------------------"
note "design-check: $findings unwaived finding(s), $waived waived ($scanned handoff(s) checked)"

if [[ "$STRICT" == true && "$findings" -gt 0 ]]; then
  exit 1
fi
exit 0
