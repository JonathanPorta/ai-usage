#!/usr/bin/env bash
# render-workflows.sh — render a consumer's MANAGED workflows from the canonical
# templates in this category (standards/releases/release-pr.md RP-9).
#
#   render-workflows.sh [--root DIR] [--check]
#
# Which workflows a repository manages follows from its declarations alone:
#
#   producer  release.yml (blessed/release/v1) with a `workflow:` block
#             → .github/workflows/release.yml  (templates/release.yml.tmpl)
#   surface   release-surfaces.yaml declaring a package-repository-surface
#             whose admission is workflow_dispatch, and blessed.yml declaring
#             the admission-pr domain
#             → .github/workflows/admit-candidate.yml
#
# Product values come from those files only — cells, required artifacts, the
# RPM public key, key ENV NAMES (from blessed.yml secret_authorities), the
# producer list. Jobs, Environments, permissions, credential handling,
# verification and publication come from the templates and nothing else, so a
# changed declaration re-renders cleanly and any hand edit is a difference.
#
# The output is deterministic (no clock, no environment), and carries the
# category version that rendered it. --check renders to a temporary directory
# and fails, naming each file, when the committed workflow is not byte-identical.
#
# Exit 0 rendered / identical · 1 a declaration is invalid, or (--check) a
# workflow differs · 2 usage/tooling.
# shellcheck disable=SC2016 # jq programs: $-names are jq variables
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TPL="$HERE/workflows"
# shellcheck source=scripts/release/lib/release-lib.sh
. "$HERE/lib/release-lib.sh"

die() {
  printf 'render-workflows: %s\n' "$1" >&2
  exit 2
}
refuse() {
  printf 'render-workflows: REFUSED — %s\n' "$1" >&2
  exit 1
}
root="." check=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root)
      root="${2:-}"
      shift 2
      ;;
    --check)
      check=1
      shift
      ;;
    -h | --help)
      sed -n '2,27p' "${BASH_SOURCE[0]}" >&2
      exit 2
      ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -d "$root" ] || die "no such directory: $root"
for t in jq yq awk cmp; do command -v "$t" >/dev/null 2>&1 || die "required tool not found: $t"; done
yq --version 2>/dev/null | grep -qi mikefarah || die "yq must be mikefarah/yq v4"
version="$(tr -d '[:space:]' <"$HERE/VERSION")"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

to_json() { yq -o=json '.' "$1" 2>/dev/null || refuse "$1 is not valid YAML"; }

# ── declarations ───────────────────────────────────────────────────────────
[ -f "$root/blessed.yml" ] || refuse "no blessed.yml"
to_json "$root/blessed.yml" >"$work/blessed.json"
B="$work/blessed.json"
# The env NAME a domain injects; exactly one, or exactly the listed names.
domain_inject() { # domain → its single injected name
  jq -r --arg d "$1" '.secret_authorities[$d].injects // [] | if length == 1 then .[0] else empty end' "$B"
}
domain_injects_exactly() { # domain, expected names (sorted, space-separated)
  [ "$(jq -r --arg d "$1" '.secret_authorities[$d].injects // [] | sort | join(" ")' "$B")" = "$2" ]
}
safe() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] && [[ "$1" != *..* ]]; }

outputs=""
: >"$work/vars"
setvar() { printf '%s\t%s\n' "$1" "$2" >>"$work/vars"; }
setvar RELEASE_VERSION "$version"

# ── producer ───────────────────────────────────────────────────────────────

if [ -f "$root/release.yml" ] && [ "$(yq '.workflow | tag' "$root/release.yml" 2>/dev/null)" = '!!map' ]; then
  to_json "$root/release.yml" >"$work/release.json"
  R="$work/release.json"
  errs="$(rel_schema_validate "$R" "$HERE/references/release-declaration.schema.json")"
  [ -z "$errs" ] || refuse "release.yml does not conform to blessed/release/v1:
$errs"
  jq -e '.candidate.trigger == "release-branch"' "$R" >/dev/null ||
    refuse "release.yml: candidate.trigger must be release-branch (releases.release-pr@1 RP-1)"
  domain_injects_exactly release-pr "RELEASE_PR_APP_CLIENT_ID RELEASE_PR_APP_PRIVATE_KEY" ||
    refuse "blessed.yml: secret_authorities.release-pr must inject exactly RELEASE_PR_APP_CLIENT_ID and RELEASE_PR_APP_PRIVATE_KEY"
  sk="$(domain_inject release-signing)"
  [[ "$sk" =~ ^[A-Z][A-Z0-9_]*$ ]] || refuse "blessed.yml: secret_authorities.release-signing must inject exactly one key name"
  setvar SIGNING_KEY_ENV "$sk"
  setvar SIGNING_KEY_ENCODING "$(jq -r '.workflow.signing_key_encoding' "$R")"
  has_rpm=0 has_derive=0 has_verify=0 has_setup=0
  jq -e '.workflow.rpm' "$R" >/dev/null && has_rpm=1
  jq -e '.workflow.derive == true' "$R" >/dev/null && has_derive=1
  jq -e '.workflow.verify == true' "$R" >/dev/null && has_verify=1
  jq -e '.workflow.setup_action == true' "$R" >/dev/null && has_setup=1
  rpm_required="$(jq '[.workflow.required_artifacts[] | select(.install_kind == "rpm")] | length' "$R")"
  if [ "$has_rpm" = 1 ]; then
    [ "$rpm_required" -gt 0 ] || refuse "release.yml: workflow.rpm is declared but no rpm is a required artifact"
    rk="$(domain_inject rpm-signing)"
    [[ "$rk" =~ ^[A-Z][A-Z0-9_]*$ ]] || refuse "blessed.yml: secret_authorities.rpm-signing must inject exactly one key name"
    setvar RPM_KEY_ENV "$rk"
    for f in public_key fingerprint_file; do
      v="$(jq -r --arg f "$f" '.workflow.rpm[$f]' "$R")"
      safe "$v" || refuse "release.yml: workflow.rpm.$f is not a plain repository path: $v"
      setvar "RPM_$(printf '%s' "$f" | tr '[:lower:]' '[:upper:]')" "$v"
    done
  else
    [ "$rpm_required" = 0 ] || refuse "release.yml: an rpm is a required artifact but workflow.rpm is not declared (LP-9 finalization)"
    jq -e '.secret_authorities | has("rpm-signing") | not' "$B" >/dev/null ||
      refuse "blessed.yml declares rpm-signing but release.yml declares no workflow.rpm"
  fi
  if [ "$has_derive" = 1 ]; then
    jq -e '.candidate.signing.artifacts == "required"' "$R" >/dev/null ||
      refuse "release.yml: workflow.derive builds on artifact signatures, so candidate.signing.artifacts must be required"
    if [ "$has_rpm" = 1 ]; then
      setvar SIGN_ARTIFACTS_AFTER rpm-finalize
      setvar SIGN_ARTIFACTS_INPUT candidate-rpm-finalized
    else
      setvar SIGN_ARTIFACTS_AFTER stage
      setvar SIGN_ARTIFACTS_INPUT candidate-staged
    fi
    setvar SIGN_AFTER derive
    setvar SIGN_INPUT candidate-derived
  elif [ "$has_rpm" = 1 ]; then
    setvar SIGN_AFTER rpm-finalize
    setvar SIGN_INPUT candidate-rpm-finalized
  else
    setvar SIGN_AFTER stage
    setvar SIGN_INPUT candidate-staged
  fi
  if [ "$has_setup" = 1 ]; then
    [ -f "$root/.github/actions/release-setup/action.yml" ] ||
      refuse "release.yml: workflow.setup_action is true but .github/actions/release-setup/action.yml does not exist"
  fi
  jq -r '.workflow.cells[] | "          - cell: \(.id)\n            runner: \(.runner)"' "$R" >"$work/cells"
  jq -e '([.workflow.cells[].id] | unique | length) == (.workflow.cells | length)' "$R" >/dev/null ||
    refuse "release.yml: workflow.cells ids are not unique"
  # LP-6: every required deb/rpm is a lifecycle row or declared build-only;
  # a row names a required artifact; emulation is declared, never implied.
  has_lifecycle=0
  pkg_required="$(jq '[.workflow.required_artifacts[] | select(.install_kind == "deb" or .install_kind == "rpm")] | length' "$R")"
  if jq -e '.workflow.lifecycle' "$R" >/dev/null; then
    has_lifecycle=1
    lerr="$(jq -r '.workflow as $w | ($w.required_artifacts | map({platform, install_kind})) as $req
      | ($w.lifecycle.rows | map(.artifact)) as $rows | ($w.lifecycle.build_only // []) as $bo
      | (if ([$w.lifecycle.rows[].id] | unique | length) != ($w.lifecycle.rows | length) then "workflow.lifecycle.rows ids are not unique" else empty end),
        ($w.lifecycle.rows[] | select((.artifact | IN($req[])) | not) | "lifecycle row \(.id): \(.artifact.platform) \(.artifact.install_kind) is not a required artifact"),
        ($bo[] | select(IN($req[]) | not) | "build_only \(.platform) \(.install_kind) is not a required artifact"),
        ($bo[] | select(IN($rows[])) | "\(.platform) \(.install_kind) is both a lifecycle row and build_only"),
        ($req[] | select(.install_kind == "deb" or .install_kind == "rpm") | select((IN($rows[]) or IN($bo[])) | not)
          | "required \(.platform) \(.install_kind) has no lifecycle row and is not declared build_only (LP-6)"),
        ($w.lifecycle.rows[] | select((["install", "upgrade", "function", "remove"] - .proves) | length > 0)
          | "lifecycle row \(.id) must prove install, upgrade, function and remove"),
        ($w.lifecycle.rows[] | select((.platform == "linux/amd64") != (.evidence == "native"))
          | "lifecycle row \(.id): the runner is x86_64, so \(.platform) is \(if .platform == "linux/amd64" then "native" else "emulated" end), not \(.evidence)")' "$R")"
    [ -z "$lerr" ] || refuse "release.yml: $lerr"
    jq -r '.workflow.lifecycle.rows[] | "          - row: \(.id)\n            platform: \(.platform)\n            evidence: \(.evidence)\n            qemu: \({"linux/amd64": "none", "linux/arm64": "arm64", "linux/arm/v7": "arm"}[.platform])"' "$R" >"$work/lifecycle"
  else
    [ "$pkg_required" = 0 ] || refuse "release.yml: deb/rpm packages are required artifacts but workflow.lifecycle is not declared (linux-packaging LP-6)"
    : >"$work/lifecycle"
  fi
  printf 'rpm=%s derive=%s verify=%s setup=%s lifecycle=%s\n' "$has_rpm" "$has_derive" "$has_verify" "$has_setup" "$has_lifecycle" >"$work/conds.release"
  outputs="$outputs release"
fi

# ── surface ────────────────────────────────────────────────────────────────
if [ -f "$root/release-surfaces.yaml" ] && jq -e '.secret_authorities | has("admission-pr")' "$B" >/dev/null; then
  to_json "$root/release-surfaces.yaml" >"$work/surfaces.json"
  S="$work/surfaces.json"
  errs="$(rel_schema_validate "$S" "$HERE/references/release-surfaces.schema.json")"
  [ -z "$errs" ] || refuse "release-surfaces.yaml does not conform to blessed/release-surfaces/v1:
$errs"
  n="$(jq '[.surfaces[] | select(.kind == "package-repository-surface" and .promotion_transport == "workflow_dispatch")] | length' "$S")"
  [ "$n" = 1 ] || refuse "release-surfaces.yaml must declare exactly one package-repository-surface admitted by workflow_dispatch (found $n)"
  domain_injects_exactly admission-pr "ADMISSION_PR_APP_CLIENT_ID ADMISSION_PR_APP_PRIVATE_KEY" ||
    refuse "blessed.yml: secret_authorities.admission-pr must inject exactly ADMISSION_PR_APP_CLIENT_ID and ADMISSION_PR_APP_PRIVATE_KEY"
  ct="$(domain_inject candidate-ingest)"
  [[ "$ct" =~ ^[A-Z][A-Z0-9_]*$ ]] || refuse "blessed.yml: secret_authorities.candidate-ingest must inject exactly one token name"
  setvar CANDIDATE_TOKEN_ENV "$ct"
  jq -r '.surfaces[] | select(.kind == "package-repository-surface") | .producer_repos[]' "$S" | while IFS= read -r p; do
    [[ "$p" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || refuse "release-surfaces.yaml: producer repo '$p' is not owner/repo"
    printf '          - %s\n' "$p"
  done >"$work/producers"
  [ -s "$work/producers" ] || refuse "release-surfaces.yaml: the surface declares no producer_repos"
  printf 'rpm=0 derive=0 verify=0 setup=0 lifecycle=0\n' >"$work/conds.admit-candidate"
  outputs="$outputs admit-candidate"
fi
[ -n "$outputs" ] || refuse "nothing to render: no release.yml workflow block and no admitted package-repository surface"
while IFS=$'\t' read -r _ v; do
  [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || refuse "unsafe template value: $v"
done <"$work/vars"

# ── render ─────────────────────────────────────────────────────────────────
render() { # name → $work/out/<name>.yml
  mkdir -p "$work/out"
  awk -v name="$1" -v tpl="$TPL" -v varsfile="$work/vars" -v cellsfile="$work/cells" \
    -v producersfile="$work/producers" -v lifecyclefile="$work/lifecycle" -v conds="$(cat "$work/conds.$1")" '
    function subst(s, args, nargs,    k, i, all) {
      for (k in V) gsub("@@" k "@@", V[k], s)
      all = ""
      for (i = 1; i <= nargs; i++) { gsub("@@" i "@@", args[i], s); all = all (i > 1 ? " " : "") args[i] }
      gsub("@@[*]@@", all, s)
      return s
    }
    function emitfile(f,    l) {
      while ((getline l < f) > 0) print l
      close(f)
    }
    function render(f, args, nargs,    l, d, act, taken, parts, n, i, sa, sn, w, none) {
      d = 0; act[0] = 1
      while ((getline l < f) > 0) {
        if (l ~ /^#@if /) { w = substr(l, 6); d++; act[d] = act[d-1] && (C[w] == 1); taken[d] = (C[w] == 1); continue }
        if (l ~ /^#@else$/) { act[d] = act[d-1] && !taken[d]; continue }
        if (l ~ /^#@end$/) { d--; continue }
        if (!act[d]) continue
        if (l ~ /^#@cells$/) { emitfile(cellsfile); continue }
        if (l ~ /^#@producers$/) { emitfile(producersfile); continue }
        if (l ~ /^#@lifecycle$/) { emitfile(lifecyclefile); continue }
        if (l ~ /^#@include /) {
          l = subst(substr(l, 11), args, nargs)
          n = split(l, parts, " ")
          sn = 0
          for (i = 2; i <= n; i++) sa[++sn] = parts[i]
          render(tpl "/partials/" parts[1] ".tmpl", sa, sn)
          continue
        }
        if (l ~ /^#@/) continue
        print subst(l, args, nargs)
      }
      close(f)
      if (d != 0) { print "render-workflows: unbalanced #@if in " f > "/dev/stderr"; exit 3 }
    }
    BEGIN {
      while ((getline l < varsfile) > 0) { split(l, kv, "\t"); V[kv[1]] = kv[2] }
      n = split(conds, cs, " ")
      for (i = 1; i <= n; i++) { split(cs[i], kv, "="); C[kv[1]] = kv[2] + 0 }
      render(tpl "/" name ".yml.tmpl", none, 0)
    }' >"$work/out/$1.yml" || die "rendering $1 failed"
  if grep -n '@@' "$work/out/$1.yml" >&2; then die "unresolved template variable in $1"; fi
}
rc=0
for o in $outputs; do
  render "$o"
  dest="$root/.github/workflows/$o.yml"
  if [ "$check" = 1 ]; then
    if [ ! -f "$dest" ]; then
      printf 'render-workflows: DIFFERS — .github/workflows/%s.yml is missing\n' "$o" >&2
      rc=1
    elif ! cmp -s "$work/out/$o.yml" "$dest"; then
      printf 'render-workflows: DIFFERS — .github/workflows/%s.yml is not the rendering of its declarations (release-v%s):\n' "$o" "$version" >&2
      diff -u "$dest" "$work/out/$o.yml" | head -40 >&2 || true
      rc=1
    else
      printf 'render-workflows: .github/workflows/%s.yml is the rendering (release-v%s)\n' "$o" "$version" >&2
    fi
  else
    mkdir -p "$root/.github/workflows"
    cp "$work/out/$o.yml" "$dest"
    printf 'render-workflows: wrote .github/workflows/%s.yml (release-v%s)\n' "$o" "$version" >&2
  fi
done
exit "$rc"
