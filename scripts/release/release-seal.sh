#!/usr/bin/env bash
# release-seal.sh — seal the RPM finalization records INTO the candidate
# (standards/releases/linux-packaging.md LP-9: "the finalization record is
# present in the sealed candidate"). No secret.
#
#   release-seal.sh --candidate DIR --plan PLAN.json --records DIR --fingerprint-file FILE
#
# If DIR (rpm-finalize-set.sh's records) exists, each record is copied into the
# candidate as <rpm>.rpm-finalization.json and declared in the plan as an
# artifact (install_kind "other", its RPM's platform), so it is signed, digested
# by the manifest and published inside the CLOSED candidate. If it does not
# exist, the records were sealed by an earlier job and are only re-checked.
# Either way, before returning: every planned RPM has exactly one sealed,
# planned record, and no record lacks its RPM; each record is
# blessed/rpm-finalization/v1, names the RPM's current bytes as its output
# (never its input) and the declared key fingerprint. Exit 0 · 1 refused · 2 usage.
# shellcheck disable=SC2016 # jq programs
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() {
  printf 'release-seal: %s\n' "$1" >&2
  exit 2
}
refuse() {
  printf '::error::release-seal: REFUSED — %s\n' "$1" >&2
  exit 1
}
cand="" plan="" records="" fprf=""
while [ $# -gt 0 ]; do
  case "$1" in
    --candidate) cand="${2:-}" ;;
    --plan) plan="${2:-}" ;;
    --records) records="${2:-}" ;;
    --fingerprint-file) fprf="${2:-}" ;;
    *) die "unknown argument: $1" ;;
  esac
  shift 2 || die "missing value for $1"
done
if [ ! -d "$cand" ] || [ ! -f "$plan" ] || [ -z "$records" ] || [ ! -f "$fprf" ]; then
  die "usage: --candidate DIR --plan FILE --records DIR --fingerprint-file FILE"
fi
fpr="$(tr -d '[:space:]' <"$fprf")"
[[ "$fpr" =~ ^[0-9A-F]{40}$ ]] || refuse "$fprf does not hold a 40-hex fingerprint"
SUF=.rpm-finalization.json

if [ -d "$records" ]; then
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  cp "$plan" "$work/plan.json"
  while IFS= read -r r; do
    name="$(basename "$r")"
    rpm="${name%"$SUF"}"
    [ ! -e "$cand/$name" ] || refuse "$name is already in the candidate"
    jq -e --arg n "$name" 'any(.artifacts[]; .filename == $n)' "$work/plan.json" >/dev/null &&
      refuse "$name is already declared in the plan"
    plat="$(jq -r --arg f "$rpm" '[.artifacts[] | select(.filename == $f and .install_kind == "rpm") | .platform] | if length == 1 then .[0] else empty end' "$work/plan.json")"
    [ -n "$plat" ] || refuse "$rpm is not exactly one planned rpm artifact"
    jq -e --arg p "$plat" 'any(.artifacts[]; .platform == $p and .install_kind == "other")' "$work/plan.json" >/dev/null &&
      refuse "the plan already declares a ($plat, other) artifact"
    cp "$r" "$cand/$name"
    id="rpm-finalization.$(printf '%s' "$rpm" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-')"
    jq --arg id "$id" --arg n "$name" --arg p "$plat" \
      '.artifacts += [{id: $id, filename: $n, platform: $p, install_kind: "other"}] | .artifacts |= sort_by(.id)' \
      "$work/plan.json" >"$work/next.json"
    mv "$work/next.json" "$work/plan.json"
  done < <(find "$records" -maxdepth 1 -name "*$SUF" -type f | sort)
  bash "$HERE/validate-plan.sh" --plan "$work/plan.json" >/dev/null 2>&1 || refuse "the amended plan does not validate"
  cp "$work/plan.json" "$plan"
fi

# ── the sealed state, whoever sealed it ───────────────────────────────────
rpms="$(jq -r '.artifacts[] | select(.install_kind == "rpm") | .filename' "$plan" | sort)"
[ -n "$rpms" ] || refuse "the plan declares no RPM"
recs="$(jq -r --arg s "$SUF" '.artifacts[] | select(.filename | endswith($s)) | .filename | rtrimstr($s)' "$plan" | sort)"
[ "$rpms" = "$recs" ] || refuse "planned RPMs ($(tr '\n' ' ' <<<"$rpms")) and sealed records ($(tr '\n' ' ' <<<"$recs")) differ"
while IFS= read -r f; do
  r="$cand/$f$SUF"
  [ -f "$r" ] || refuse "$f$SUF is planned but not in the candidate"
  jq -e --arg h "$(sha256sum "$cand/$f" | cut -d' ' -f1)" --arg k "$fpr" '.schema == "blessed/rpm-finalization/v1"
    and .output_sha256 == $h and .input_sha256 != .output_sha256 and .key_fingerprint == $k' "$r" >/dev/null 2>&1 ||
    refuse "$f is not the finalized output its sealed record names (digest, or key $fpr)"
done <<<"$rpms"
printf 'release-seal: OK — %s finalized RPM(s), each with its sealed record\n' "$(wc -l <<<"$rpms" | tr -d ' ')"
