#!/usr/bin/env bash
# spec-scaffold.sh — create starter spec-pack docs from templates.
# Safe: never overwrites existing files.
#
# Usage:
#   scripts/spec-scaffold.sh [TYPE=static-site] [SURFACES=web,infra]
#   make spec-scaffold TYPE=cli
#
# The script is intentionally small. It emits the clerical skeleton defined by
# spec-pack.core@1. Human fills the product truth.
set -uo pipefail

TYPE="${TYPE:-static-site}"
SURFACES="${SURFACES:-}"

# Operate on the current working directory (the adopting repo).
# When the script lives in <repo>/scripts/, we still prefer $PWD so that
# `cd /some/other/repo && /path/to/scripts/spec-scaffold.sh` works.
REPO_ROOT="${REPO_ROOT:-$(pwd)}"
# Default to no external templates dir. Embedded minimal templates (below)
# are used for consumer repos that only vendor the scripts/ subtree.
# Set TPL_DIR env var (or have agent-skills/spec-check/templates in the tree)
# to prefer real templates when available.
TPL_DIR="${TPL_DIR:-}"
DOCS_DIR="$REPO_ROOT/docs"

created=()

ensure_dir() { mkdir -p "$1"; }

write_if_absent() {
  local dst="$1" src="$2" label="$3"
  if [[ -f "$dst" ]]; then
    echo "  (exists) $label"
    return 0
  fi
  ensure_dir "$(dirname "$dst")"
  if [[ -f "$src" ]]; then
    if ! cp "$src" "$dst"; then
      echo "ERROR: failed to copy template $src to $dst" >&2
      exit 1
    fi
  else
    # Fallback to embedded minimal templates so that spec-scaffold works
    # for consumer repos that vendor only the scripts/ subtree (no full
    # agent-skills/ tree). When templates are present (blessed-cicd dev or
    # full tree), the real ones are preferred for fidelity.
    case "$label" in
      "docs/IDEA.md")
        cat >"$dst" <<'EOD'
---
status: draft
owner: <name>
updated: <YYYY-MM-DD>
source-template: agent-skills/spec-check/templates/docs/IDEA.md
---

# IDEA

Raw origin story, constraints, and non-negotiables. Do not sanitize.

## The spark
(What prompted this? A problem statement, a tweet, a customer request, a personal itch...)

## Users
(Who is this for? Personas or jobs-to-be-done.)

## Constraints & non-negotiables
- ...
- ...

## Inspirations / references
- ...
EOD
        ;;
      "docs/MVP.md")
        cat >"$dst" <<'EOD'
---
status: draft
owner: <name>
updated: <YYYY-MM-DD>
source-template: agent-skills/spec-check/templates/docs/MVP.md
---

# MVP

Approved build contract. Human owns the truth here.

## Problem
(One paragraph: the job the user is hiring the product to do.)

## Users & journeys (MVP scope)
- Primary: ...
- Secondary (in or out?): ...

## In scope for MVP
- ...
- ...

## Non-goals / out of scope (MVP)
- ...

## Acceptance criteria
- [ ] Criterion 1 (measurable or demonstrable)
- [ ] Criterion 2
- ...

## Success signal
(What number or qualitative change tells us the MVP worked?)

## Risks & unknowns
- ...
EOD
        ;;
      "docs/DECISIONS.md")
        cat >"$dst" <<'EOD'
---
status: draft
owner: <name>
updated: <YYYY-MM-DD>
source-template: agent-skills/spec-check/templates/docs/DECISIONS.md
---

# DECISIONS

Lightweight log. One entry per material choice. Include date and reason.

## YYYY-MM-DD — <Short decision title>
**Choice**: ...
**Rejected**: ...
**Reason**: ...
**Revisit if**: ...

## YYYY-MM-DD — <Another>
...
EOD
        ;;
      "docs/SPEC.yml")
        cat >"$dst" <<'EOD'
schema: blessed/spec-pack/v1
repo_type: <static-site|cli|...>
status: draft

core:
  idea: docs/IDEA.md
  mvp: docs/MVP.md
  decisions: docs/DECISIONS.md

surfaces:
  # Example surfaces; edit to match reality.
  # web:
  #   required: true
  #   spec: docs/WEB.md
  #   design_bucket: docs/design/web
  # infra:
  #   required: true
  #   spec: docs/DEPLOYMENT.md

checks:
  status_blocks: required
  acceptance_criteria: required
  design_brief: optional
  asset_inventory: optional
  build_instructions: optional

exceptions:
  # Example waiver entry (see spec-pack.adoption.md):
  # - path: docs/design/icon
  #   reason: Icons not in MVP scope for this prototype
  #   owner: <name>
  #   review_by: <YYYY-MM-DD>
EOD
        ;;
      "blessed.yml (starter)")
        cat >"$dst" <<'EOD'
# blessed/repo/v1
# Minimal starter manifest for a repo adopting spec-pack + makefile.core.
# Hand-edit after scaffolding; keep it small and truthful.

schema: blessed/repo/v1

project: <your-project>
component: <your-component-or-main-surface>
repo_type: static-site   # or: cli, cloudflare-worker, desktop-app, library, service-api, ...
stage: prototype         # spike | prototype | active | production | ...

stack:
  - <primary-tech-e.g. hugo>
  - <terraform or wrangler or ...>

standards:
  - makefile.core@1
  - spec-pack.core@1

capabilities:
  # Add only what you can justify today. Examples:
  # - bwsm
  # - ruam
  # - sha-pinning
  # - repo-protection
  # - static-site-terraform

tracked_dependencies:
  ai_rules:
    origin: https://github.com/JonathanPorta/ai-rules
    tag: null   # or a real tag once pinned
  blessed_scripts:
    bws: null
    ruam: null

exceptions: []

notes: |
  Fill real notes after the human reviews the spec pack.
  This file declares intent for catalog + fleet audit.
EOD
        ;;
      *)
        echo "ERROR: no template for $label and no src $src" >&2
        exit 1
        ;;
    esac
  fi
  created+=("$label")
  echo "  wrote  $label"
}

echo "spec-scaffold: TYPE=$TYPE SURFACES=${SURFACES:-<default>}"

# Core pack (always)
write_if_absent "$DOCS_DIR/IDEA.md" "$TPL_DIR/docs/IDEA.md" "docs/IDEA.md"
write_if_absent "$DOCS_DIR/MVP.md" "$TPL_DIR/docs/MVP.md" "docs/MVP.md"
write_if_absent "$DOCS_DIR/DECISIONS.md" "$TPL_DIR/docs/DECISIONS.md" "docs/DECISIONS.md"
write_if_absent "$DOCS_DIR/SPEC.yml" "$TPL_DIR/docs/SPEC.yml" "docs/SPEC.yml"

# Minimal blessed.yml at root if absent (do not overwrite consumer's)
write_if_absent "$REPO_ROOT/blessed.yml" "$TPL_DIR/blessed.yml" "blessed.yml (starter)"

# Surface hints (very lightweight for v1)
if [[ -n "$SURFACES" ]]; then
  IFS=',' read -ra surfs <<<"$SURFACES"
  for s in "${surfs[@]}"; do
    s="$(echo "$s" | tr -d ' ')"
    case "$s" in
      web)
        ensure_dir "$DOCS_DIR/design/web"
        # A tiny placeholder so the bucket is visible; real content is human.
        if [[ ! -f "$DOCS_DIR/design/web/README.md" ]]; then
          echo "# web design bucket" >"$DOCS_DIR/design/web/README.md"
          created+=("docs/design/web/README.md")
          echo "  wrote  docs/design/web/README.md (placeholder)"
        fi
        ;;
      icon)
        ensure_dir "$DOCS_DIR/design/icon"
        if [[ ! -f "$DOCS_DIR/design/icon/README.md" ]]; then
          echo "# icon design bucket" >"$DOCS_DIR/design/icon/README.md"
          created+=("docs/design/icon/README.md")
          echo "  wrote  docs/design/icon/README.md (placeholder)"
        fi
        ;;
      infra)
        if [[ ! -f "$DOCS_DIR/DEPLOYMENT.md" ]]; then
          cat >"$DOCS_DIR/DEPLOYMENT.md" <<'EOD'
---
status: draft
owner: <name>
updated: <YYYY-MM-DD>
---

# DEPLOYMENT / infra surface

Document workspaces, promotion, and any infra-specific contracts here.
EOD
          created+=("docs/DEPLOYMENT.md")
          echo "  wrote  docs/DEPLOYMENT.md (starter)"
        fi
        ;;
    esac
  done
fi

echo "------------------------------------------------------------"
if [[ ${#created[@]} -eq 0 ]]; then
  echo "spec-scaffold: nothing new created (all core files present)"
else
  echo "spec-scaffold: created ${#created[@]} file(s):"
  for f in "${created[@]}"; do echo "  - $f"; done
  echo ""
  echo "Next: edit the drafted files (status/owner/acceptance criteria/MVP scope)."
  echo "Then: make spec-check"
fi
exit 0
