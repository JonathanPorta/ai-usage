#!/usr/bin/env bash
# check-conformance.sh — the ONE conformance check every release-PR producer
# and admitting surface runs in required CI (standards/releases/release-pr.md
# RP-9). It answers: does what executes match the shared implementation at the
# pinned version, and do the declarations agree with it?
#
#   check-conformance.sh [--root DIR]
#
#   1. PIN. scripts/.blessed-scripts-version pins bws, signing and release;
#      each vendored scripts/<category>/ is exactly its MANIFEST.sha256 (no file
#      missing, changed or added) at the pinned VERSION; blessed.yml's
#      tracked_dependencies.blessed_scripts names the same versions.
#   2. GENERATED. Every managed workflow is byte-identical to its rendering by
#      THIS (vendored, verified) release category from the declarations
#      (render-workflows.sh --check). The rendering carries the version.
#   3. DOMAINS. Each role domain in blessed.yml is Environment-scoped to the
#      Environment of the same name, main only, no tags, and injects exactly
#      the names the workflow uses; the secret loader maps each domain's names
#      in that domain's profile and in no other.
#   4. ISOLATION. No workflow job outside the managed workflows declares a
#      role Environment or loads a role profile (any job that did would be a
#      second reader of that key), and the product's setup action loads no
#      secret.
#   5. RELEASE PR. release-please makes a DRAFT (tagless) release (RP-4).
#   6. SHARED ENTRY POINTS. The canonical make targets run the shared scripts.
#   7. LIFECYCLE (LP-6). A producer that declares lifecycle rows has a
#      runnable `make release-lifecycle` (the generated workflow runs it on the
#      final bytes before publication).
#   8. PUBLIC INSTRUCTIONS. An admitting package-repository surface serves
#      site/index.html and documents README.md exactly as rendered from
#      inventory/inventory.json (pkgrepo-instructions.sh): they cannot drift.
#
# Role domains: release-pr, rpm-signing, release-signing (producer);
# admission-pr, candidate-ingest (surface). Exit 0 conforms · 1 does not
# (every finding is printed) · 2 usage/tooling.
# shellcheck disable=SC2016 # jq programs
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="."
while [ $# -gt 0 ]; do
  case "$1" in
    --root)
      root="${2:-}"
      shift 2
      ;;
    *)
      echo "check-conformance: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done
for t in jq yq sha256sum find; do command -v "$t" >/dev/null 2>&1 || {
  echo "check-conformance: required tool not found: $t" >&2
  exit 2
}; done
yq --version 2>/dev/null | grep -qi mikefarah || {
  echo "check-conformance: yq must be mikefarah/yq v4" >&2
  exit 2
}
fails=0
bad() {
  printf '  ✗ %s\n' "$1" >&2
  fails=$((fails + 1))
}
good() { printf '  ✓ %s\n' "$1"; }
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$root" || exit 2

# ── 1. pin ─────────────────────────────────────────────────────────────────
echo "check-conformance: pinned tooling"
pinf=scripts/.blessed-scripts-version
if [ ! -f "$pinf" ]; then
  bad "$pinf is missing (the canonical pin file; .bws-scripts-version is legacy)"
else
  for c in bws signing release; do
    C="$(tr '[:lower:]' '[:upper:]' <<<"$c")"
    ver="$(sed -n "s/^${C}_VERSION=//p" "$pinf" | tr -d '[:space:]')"
    tag="$(sed -n "s/^${C}_TAG=//p" "$pinf" | tr -d '[:space:]')"
    have="$(tr -d '[:space:]' <"scripts/$c/VERSION" 2>/dev/null)"
    if [ -z "$ver" ] || [ "$tag" != "$c-v$ver" ]; then
      bad "$pinf: ${C}_VERSION/${C}_TAG are not one pinned release ($ver, $tag)"
    elif [ "$have" != "$ver" ]; then
      bad "scripts/$c/VERSION is '$have', but $pinf pins $ver"
    elif [ ! -f "scripts/$c/MANIFEST.sha256" ]; then
      bad "scripts/$c has no MANIFEST.sha256"
    else
      listed="$(sed 's/^[0-9a-f]\{64\} [ *]//' "scripts/$c/MANIFEST.sha256" | sort)"
      present="$(cd "scripts/$c" && find . -type f ! -path ./MANIFEST.sha256 | sed 's#^\./##' | sort)"
      if [ -n "$(find "scripts/$c" -type l -print -quit)" ]; then
        bad "scripts/$c contains a symlink (never shipped)"
      elif [ "$listed" != "$present" ]; then
        bad "scripts/$c is not exactly its MANIFEST: $(diff <(echo "$listed") <(echo "$present") | grep '^[<>]' | head -5 | tr '\n' ' ')"
      elif ! (cd "scripts/$c" && sha256sum -c --quiet MANIFEST.sha256 >/dev/null 2>&1); then
        bad "scripts/$c differs from its MANIFEST.sha256 (a vendored file was edited)"
      else
        good "scripts/$c = $tag, verified"
      fi
    fi
    dep="$(yq -r ".tracked_dependencies.blessed_scripts.$c // \"\"" blessed.yml 2>/dev/null)"
    [ "$dep" = "$ver" ] || bad "blessed.yml tracked_dependencies.blessed_scripts.$c is '$dep', not the pinned $ver"
  done
  # release-sign.sh passes --private-key-encoding, which signing 0.4.0 added.
  sv="$(tr -d '[:space:]' <scripts/signing/VERSION 2>/dev/null)"
  [ "$(printf '%s\n%s\n' 0.4.0 "$sv" | sort -V | head -1)" = 0.4.0 ] ||
    bad "this release category needs signing >= 0.4.0; scripts/signing is $sv"
  [ "$(tr -d '[:space:]' <scripts/release/VERSION 2>/dev/null)" = "$(tr -d '[:space:]' <"$HERE/VERSION")" ] ||
    bad "this checker is not the vendored scripts/release (run scripts/release/check-conformance.sh)"
fi

# ── 2. generated ──────────────────────────────────────────────────────────
echo "check-conformance: managed workflows"
if bash "$HERE/render-workflows.sh" --root . --check 2>"$work/render.err"; then
  sed 's/^render-workflows: /  ✓ /' "$work/render.err"
  managed="$(sed -n 's#^render-workflows: \.github/workflows/\([a-z-]*\)\.yml is the rendering.*#\1#p' "$work/render.err")"
else
  sed 's/^/    /' "$work/render.err" >&2
  bad "managed workflows are not the rendering of their declarations (run: bash scripts/release/render-workflows.sh)"
  managed="$(sed -n 's#^render-workflows: \(DIFFERS — \)\{0,1\}\.github/workflows/\([a-z-]*\)\.yml.*#\2#p' "$work/render.err")"
fi
# Template invariants, checked on what actually runs (a guard against a
# template regression, independent of the byte comparison): a job loads only
# the profile of its own Environment, a job without one loads nothing, and
# every App token is minted for this repository with at most its role's
# permissions.
for m in $managed; do
  yq -o=json '.' ".github/workflows/$m.yml" >"$work/m.json" 2>/dev/null || continue
  inv="$(jq -r --slurpfile roles "$HERE/references/github-app-roles.json" '.jobs | to_entries[] | .key as $j
    | (.value.environment | if type == "object" then .name else . end) as $env
    | .value.steps[]?
    | if ((.uses // "") | test("load-secrets")) and ((.with.profile // "default") != $env) then
        "job \($j) loads profile \(.with.profile // "default") but its Environment is \($env // "none")"
      elif ((.uses // "") | test("create-github-app-token")) then
        (if .with.repositories != "${{ github.event.repository.name }}" then "job \($j) mints an App token not restricted to this repository" else empty end),
        (((.with | keys | map(select(startswith("permission-")) | ltrimstr("permission-") | gsub("-"; "_")))
          - ($roles[0].roles[$env].permissions // {} | to_entries | map(select(.value != "read" or .key != "metadata")) | map(.key)))
          | if length > 0 then "job \($j) mints an App token with permissions beyond its role: \(join(", "))" else empty end)
      else empty end' "$work/m.json")"
  if [ -n "$inv" ]; then
    while IFS= read -r l; do bad ".github/workflows/$m.yml: $l"; done <<<"$inv"
  else
    good ".github/workflows/$m.yml: each job loads only its own Environment's profile; App tokens are this repository's, within role"
  fi
done
role_domains=""
case " $managed " in *" release "*) role_domains="release-pr rpm-signing release-signing" ;; esac
case " $managed " in *" admit-candidate "*) role_domains="$role_domains admission-pr candidate-ingest" ;; esac
# One reader per KEY: the PR-opening App keys and the signing keys. The
# surface's read-only candidate token is shape-checked like every role domain,
# but the surface's own publication also fetches with it, by design.
single_reader="${role_domains/candidate-ingest/}"
[ -n "$role_domains" ] || bad "no managed workflow: declare release.yml workflow: (producer) or an admitted package-repository surface"

# ── 3. domains ────────────────────────────────────────────────────────────
echo "check-conformance: authority domains"
yq -o=json '.' blessed.yml >"$work/blessed.json" 2>/dev/null || bad "blessed.yml is not valid YAML"
loader=.github/actions/load-secrets/action.yml
# profile → mapped names, from the loader the bootstrapper generated
if yq -o=json '.' "$loader" >"$work/loader.json" 2>/dev/null; then
  jq -r '.runs.steps[] | ((.if // "") | capture("inputs[.]profile *== *.(?<p>[a-z0-9-]+).")? | .p) as $p
    | (.env.INPUT_SECRETS // "") | split("\n")[] | select(test(">")) | [$p, (split(">")[1] | gsub("^ +| +$"; ""))] | @tsv' \
    "$work/loader.json" 2>/dev/null >"$work/maps.tsv" || bad "$loader: cannot read its profiles"
else
  bad "$loader is missing or not YAML"
  : >"$work/maps.tsv"
fi
for d in $role_domains; do
  [ "$d" = rpm-signing ] && ! jq -e '.secret_authorities | has("rpm-signing")' "$work/blessed.json" >/dev/null && continue
  if ! jq -e --arg d "$d" '.secret_authorities[$d] | .authority_type == "bwsm" and .github_scope == "environment"
      and .github_environment == $d and .github_secret == "BWS_ACCESS_TOKEN"
      and .allowed_refs.branches == ["main"] and ((.allowed_refs.tags // []) == [])
      and (.injects | type == "array" and length > 0)' "$work/blessed.json" >/dev/null; then
    bad "blessed.yml secret_authorities.$d must be bwsm, Environment-scoped to Environment $d (BWS_ACCESS_TOKEN), branch main only, no tags, with its injects"
    continue
  fi
  want="$(jq -r --arg d "$d" '.secret_authorities[$d].injects | sort | .[]' "$work/blessed.json")"
  have="$(awk -F'\t' -v d="$d" '$1 == d {print $2}' "$work/maps.tsv" | sort)"
  if [ "$want" != "$have" ]; then
    bad "the loader's $d profile maps [$(tr '\n' ' ' <<<"$have")], but blessed.yml declares [$(tr '\n' ' ' <<<"$want")]"
    continue
  fi
  elsewhere="$(awk -F'\t' -v d="$d" '$1 != d' "$work/maps.tsv" | cut -f2 | grep -Fx -f <(echo "$want") || true)"
  [ -z "$elsewhere" ] || {
    bad "the loader maps $d's $(tr '\n' ' ' <<<"$elsewhere")in another profile too"
    continue
  }
  good "$d: Environment $d (main only) → profile $d → $(tr '\n' ' ' <<<"$want")"
done

# ── 4. isolation ──────────────────────────────────────────────────────────
echo "check-conformance: one reader per key"
for wf in .github/workflows/*.yml .github/workflows/*.yaml; do
  [ -f "$wf" ] || continue
  base="$(basename "$wf")"
  case " $managed " in *" ${base%.y*ml} "*) continue ;; esac
  yq -o=json '.' "$wf" >"$work/wf.json" 2>/dev/null || {
    bad "$wf is not valid YAML"
    continue
  }
  # GitHub Environment names are case-insensitive, and an expression can
  # resolve to any of them: compare case-folded, and treat an expression
  # (Environment or loader profile) as possibly every role domain.
  for d in $single_reader; do
    hit="$(jq -r --arg d "$d" 'def may($v): ($v | type == "string") and (($v | ascii_downcase) == $d or ($v | test("\\$\\{\\{")));
      .jobs // {} | to_entries[]
      | select(may(.value.environment | if type == "object" then .name else . end)
        or any(.value.steps[]?; ((.uses // "") | test("load-secrets")) and may(.with.profile // "default")))
      | .key' "$work/wf.json")"
    [ -z "$hit" ] || bad "$wf job(s) $(tr '\n' ' ' <<<"$hit")declare Environment $d or load profile $d (case-insensitively, or by an expression that may resolve to it): only the generated workflow may"
  done
done
if [ -f .github/actions/release-setup/action.yml ]; then
  if grep -Eq 'load-secrets|BWS_|secrets\.|scripts/signing|_SIGNING_KEY|_APP_PRIVATE_KEY|_APP_PEM' .github/actions/release-setup/action.yml; then
    bad ".github/actions/release-setup/action.yml references secrets, keys or the loader; it may set up toolchains only"
  else
    good ".github/actions/release-setup loads no secret"
  fi
fi
[ "$fails" -gt 0 ] || good "no other workflow job declares a role Environment or loads a role profile"

# ── 5. release PR ─────────────────────────────────────────────────────────
case " $managed " in *" release "*)
  echo "check-conformance: release-please"
  if jq -e '(.draft == true) or (.packages["."].draft == true)' release-please-config.json >/dev/null 2>&1 &&
    [ -f .release-please-manifest.json ]; then
    good "release-please creates a tagless draft (draft: true)"
  else
    bad "release-please-config.json must set draft: true (publication creates the tag, RP-4), with .release-please-manifest.json"
  fi
  ;;
esac

# ── 6. shared entry points ────────────────────────────────────────────────
# What each canonical target would RUN (make -n), not what its text mentions:
# a commands line must start with the shared script, and no line may run the
# raw BWS bootstrapper.
echo "check-conformance: shared entry points"
for te in release-workflows:render-workflows.sh release-conformance:check-conformance.sh bws-bootstrap-domain:bootstrap-domain.sh; do
  t="${te%%:*}" e="${te#*:}"
  if ! cmds="$(make -n -s "$t" DOMAIN=release-pr DRY_RUN=1 2>/dev/null)"; then
    bad "Makefile has no runnable target $t (INSTALL.md)"
  elif ! grep -Eq "^(bash )?(\./)?scripts/release/$e( |\$)" <<<"$cmds"; then
    bad "make $t does not run scripts/release/$e (it would run: $(head -3 <<<"$cmds" | tr '\n' ';'))"
  elif grep -Eq 'scripts/bws/bootstrap\.sh' <<<"$cmds"; then
    bad "make $t runs the raw BWS bootstrapper"
  else
    good "make $t runs scripts/release/$e"
  fi
done

# ── 7. lifecycle ──────────────────────────────────────────────────────────
case " $managed " in *" release "*)
  if [ "$(yq '.workflow.lifecycle | tag' release.yml 2>/dev/null)" = '!!map' ]; then
    echo "check-conformance: lifecycle gate (LP-6)"
    if make -n -s release-lifecycle LIFECYCLE_ROW=conformance LIFECYCLE_PLATFORM=linux/amd64 LIFECYCLE_EVIDENCE_LEVEL=native \
      LIFECYCLE_NEW=/nonexistent LIFECYCLE_NEW_FILE=x LIFECYCLE_NEW_VERSION=0.0.1 LIFECYCLE_OLD=/nonexistent \
      LIFECYCLE_OLD_FILE=x LIFECYCLE_OLD_VERSION=0.0.0 LIFECYCLE_EVIDENCE=/nonexistent >/dev/null 2>&1; then
      good "make release-lifecycle is runnable for the $(yq '.workflow.lifecycle.rows | length' release.yml) declared row(s)"
    else
      bad "release.yml declares lifecycle rows but the Makefile has no runnable release-lifecycle target"
    fi
  fi
  ;;
esac

# ── 8. public instructions ────────────────────────────────────────────────
case " $managed " in *" admit-candidate "*)
  echo "check-conformance: public instructions"
  if [ ! -f inventory/inventory.json ]; then
    bad "an admitting surface needs inventory/inventory.json"
  elif bash "$HERE/pkgrepo-instructions.sh" --inventory inventory/inventory.json --html site/index.html --readme README.md --check 2>"$work/instr.err"; then
    good "site/index.html and README.md are the rendering of inventory/inventory.json"
  else
    sed 's/^/    /' "$work/instr.err" >&2
    bad "the public instructions are not the rendering of the inventory (run: bash scripts/release/pkgrepo-instructions.sh --inventory inventory/inventory.json --html site/index.html --readme README.md)"
  fi
  ;;
esac

if [ "$fails" -gt 0 ]; then
  echo "check-conformance: $fails finding(s) — this repository does not run the shared release process as declared" >&2
  exit 1
fi
echo "check-conformance: conforms (release-v$(tr -d '[:space:]' <"$HERE/VERSION"))"
