#!/usr/bin/env bash
# release-stage.sh — fix the candidate's contents and plan once (no secret;
# standards/releases/release-pr.md RP-3, releases.candidate@1).
#
#   release-stage.sh --cells DIR --version X.Y.Z --sha COMMIT --repo OWNER/REPO \
#                    --candidate OUT --plan PLAN.json --rpm-inputs RPM-INPUTS.json \
#                    [--declaration release.yml] [--root .]
#
# DIR holds one directory per build cell, as downloaded (release-cell-<id>/),
# each with the cell's artifacts.json and the cell.json release-cell.sh wrote.
# Refused unless:
#   · every declared cell (release.yml workflow.cells) is present, and no other;
#   · every file is the bytes its cell recorded (transport integrity);
#   · no filename or artifact id is produced twice, and no (platform,
#     install_kind) pair twice — the candidate's semantic closure;
#   · every required artifact (workflow.required_artifacts) is present.
# Then OUT holds exactly the artifacts, their SBOMs and the public notes (this
# version's CHANGELOG.md section, or a one-line heading), and PLAN is the
# blessed/release-candidate-plan/v1 for them, validated. RPM-INPUTS is the
# unsigned RPMs' {name: sha256} ({} without RPMs): the digests the native
# finalizer checks before it can use the key (linux-packaging LP-9).
# created_at is the release commit's committer time, so a re-run is identical.
# Exit 0 · 1 refused · 2 usage.
# shellcheck disable=SC2016 # jq programs
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
refuse() {
  echo "::error::release-stage: $1" >&2
  exit 1
}
die() {
  echo "release-stage: $1" >&2
  exit 2
}
cells="" version="" sha="" repo="" cand="" plan="" rpmin="" decl="release.yml" root="."
while [ $# -gt 0 ]; do
  case "$1" in
    --cells) cells="${2:-}" ;;
    --version) version="${2:-}" ;;
    --sha) sha="${2:-}" ;;
    --repo) repo="${2:-}" ;;
    --candidate) cand="${2:-}" ;;
    --plan) plan="${2:-}" ;;
    --rpm-inputs) rpmin="${2:-}" ;;
    --declaration) decl="${2:-}" ;;
    --root) root="${2:-}" ;;
    *) die "unknown argument: $1" ;;
  esac
  shift 2 || die "missing value for $1"
done
for v in cells version sha repo cand plan rpmin; do [ -n "${!v}" ] || die "--$v is required"; done
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || refuse "version $version is not X.Y.Z"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || refuse "--sha is not a full commit SHA"
[ -d "$cells" ] || refuse "no cells directory $cells"
[ ! -e "$cand" ] || die "--candidate must not exist: $cand"
for t in jq yq git sha256sum; do command -v "$t" >/dev/null 2>&1 || die "required tool not found: $t"; done
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
yq -o=json '.' "$root/$decl" >"$work/decl.json" 2>/dev/null || refuse "$decl is not valid YAML"
D="$work/decl.json"

# ── every declared cell, exactly as it recorded itself ─────────────────────
want_cells="$(jq -r '.workflow.cells[].id' "$D" | sort)"
have_cells="$(find "$cells" -mindepth 1 -maxdepth 1 -type d -name 'release-cell-*' | sed 's#.*/release-cell-##' | sort)"
[ "$want_cells" = "$have_cells" ] || refuse "cells received ($(tr '\n' ' ' <<<"$have_cells")) are not the declared cells ($(tr '\n' ' ' <<<"$want_cells"))"
mkdir -p "$work/all"
echo '[]' >"$work/arts.json"
while IFS= read -r c; do
  d="$cells/release-cell-$c"
  { [ -f "$d/cell.json" ] && [ -f "$d/artifacts.json" ]; } || refuse "cell $c has no cell.json/artifacts.json"
  jq -e --arg c "$c" '.schema == "blessed/release-cell-record/v1" and .cell == $c and (.files | type == "object")' "$d/cell.json" >/dev/null ||
    refuse "cell $c's record is not a blessed/release-cell-record/v1 for $c"
  have="$(cd "$d" && find . -type f ! -name cell.json | sed 's#^\./##' | sort)"
  [ "$have" = "$(jq -r '.files | keys[]' "$d/cell.json" | sort)" ] || refuse "cell $c's files are not the files it recorded"
  while IFS= read -r f; do
    [ "$(sha256sum "$d/$f" | cut -d' ' -f1)" = "$(jq -r --arg f "$f" '.files[$f]' "$d/cell.json")" ] ||
      refuse "cell $c: $f is not the bytes the cell recorded"
    [ "$f" = artifacts.json ] && continue
    [ ! -e "$work/all/$f" ] || refuse "$f is produced by more than one cell"
    cp "$d/$f" "$work/all/$f"
  done <<<"$have"
  jq -s '.[0] + .[1].artifacts' "$work/arts.json" "$d/artifacts.json" >"$work/next.json"
  mv "$work/next.json" "$work/arts.json"
done <<<"$want_cells"
A="$work/arts.json"
jq -e '([.[].id] | unique | length) == length' "$A" >/dev/null || refuse "an artifact id is declared twice"
jq -e '([.[] | [.platform, .install_kind]] | unique | length) == length' "$A" >/dev/null ||
  refuse "a (platform, install_kind) pair is declared twice: $(jq -c '[group_by([.platform, .install_kind])[] | select(length > 1) | .[0] | [.platform, .install_kind]]' "$A")"
missing="$(jq -c --slurpfile a "$A" '[.workflow.required_artifacts[] | select(. as $r | $a[0] | any(.platform == $r.platform and .install_kind == $r.install_kind) | not)]' "$D")"
[ "$missing" = "[]" ] || refuse "required artifact(s) missing from the build: $missing"
notes="$(jq -r '.candidate.public_notes' "$D")"
jq -e --arg n "$notes" 'all(.[]; .filename != $n and (.sbom // "") != $n)' "$A" >/dev/null || refuse "an artifact is named like the public notes ($notes)"

# ── the candidate and its plan ─────────────────────────────────────────────
mkdir -p "$cand"
cp "$work/all/"* "$cand/"
if [ -f "$root/CHANGELOG.md" ]; then
  awk -v v="$version" '
    /^## / { if (p) exit; if (index($0, "[" v "]") || index($0, " " v " ") || $0 ~ (" " v "$")) p = 1 }
    p' "$root/CHANGELOG.md" >"$cand/$notes"
fi
[ -s "$cand/$notes" ] || printf '# %s v%s\n' "$(jq -r .project "$D")" "$version" >"$cand/$notes"
created="$(git -C "$root" show -s --format=%cI "$sha")" || refuse "cannot read the release commit $sha"
created="$(date -u -d "$created" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || printf '%s' "$created")"
jq -n --slurpfile d "$D" --slurpfile a "$A" --arg v "$version" --arg sha "$sha" --arg repo "$repo" --arg c "$created" '
  $d[0] as $d | {schema: "blessed/release-candidate-plan/v1", project: $d.project, component: $d.component,
    version: $v, tag: ("v" + $v), producer_repo: $repo, source_sha: $sha, build_sha: $sha, created_at: $c,
    signing: {profile: $d.candidate.signing.profile, key_id: $d.candidate.signing.key_id, artifacts: $d.candidate.signing.artifacts},
    public_notes: $d.candidate.public_notes,
    artifacts: ($a[0] | sort_by(.id))}' >"$plan"
bash "$HERE/validate-plan.sh" --plan "$plan" || refuse "the plan does not validate"
expect="$( (
  echo "$notes"
  jq -r '.artifacts[] | .filename, (.sbom // empty)' "$plan"
) | sort)"
[ "$expect" = "$(cd "$cand" && find . -type f | sed 's#^\./##' | sort)" ] || refuse "the staged candidate is not exactly the planned set"
jq '[.artifacts[] | select(.install_kind == "rpm") | .filename] | map({(.): null}) | add // {}' "$plan" >"$work/rpm-names.json"
jq -r 'keys[]' "$work/rpm-names.json" | while IFS= read -r f; do
  printf '%s\t%s\n' "$f" "$(sha256sum "$cand/$f" | cut -d' ' -f1)"
done | jq -R -s 'split("\n") | map(select(length > 0) | split("\t") | {(.[0]): .[1]}) | add // {}' >"$rpmin"
echo "release-stage: v$version — $(jq '.artifacts | length' "$plan") artifact(s) from $(wc -l <<<"$want_cells" | tr -d ' ') cell(s), $(jq length "$rpmin") unsigned RPM(s) recorded"
