#!/usr/bin/env bash
# release-sign.sh — the candidate-signing steps of the release ceremony
# (releases.candidate@1, signing.ed25519-detached@1; release-pr.md RP-3).
#
#   release-sign.sh --check-only --candidate DIR --plan PLAN --private-key-env NAME \
#                   [--loader FILE] [--trust-store FILE]
#   release-sign.sh --stage artifacts|final --candidate DIR --plan PLAN \
#                   --private-key-env NAME --private-key-encoding pkcs8-pem|ed25519-seed-public-base64
#
# --check-only (before the key is loaded): the domain is provisioned (--loader);
# the trust store holds the plan's key_id as an active key (--trust-store); the
# plan validates; the candidate holds exactly the planned files, the public
# notes and signatures of planned artifacts — nothing else.
#
# --stage artifacts: sign every planned artifact (signing.artifacts: required),
# so a later keyless job may derive files from the signatures.
# --stage final: sign every planned artifact still unsigned when the plan
# requires artifact signatures; assemble blessed/release-candidate/v1 over the
# FINAL bytes with the shipped build-candidate.sh; validate it with no trust
# store; sign the manifest.
#
# KEY CONFINEMENT: the key is copied out of the environment into an unexported
# variable and unset before anything runs; it reaches only the shipped
# scripts/signing/sign.sh, one call at a time. Exit 0 · 1 refused · 2 usage.
# shellcheck disable=SC2016 # jq programs
set -euo pipefail
set +x
__keyname="" __check=0
__i=1
while [ "$__i" -le "$#" ]; do
  case "${!__i}" in
    --check-only) __check=1 ;;
    --private-key-env)
      __j=$((__i + 1))
      __keyname="${!__j-}"
      ;;
  esac
  __i=$((__i + 1))
done
case "$__keyname" in
  "" | [!A-Za-z_]* | *[!A-Za-z0-9_]* | __*)
    printf 'release-sign: --private-key-env NAME is required (a plain variable name)\n' >&2
    exit 2
    ;;
esac
__key=""
if [ "$__check" = 0 ]; then
  __key="${!__keyname-}"
  unset "$__keyname"
fi
unset __i __j

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIGNING="$HERE/../signing"
die() {
  printf 'release-sign: %s\n' "$1" >&2
  exit 2
}
refuse() {
  printf '::error::release-sign: REFUSED — %s\n' "$1" >&2
  exit 1
}
stage="" cand="" plan="" enc="" loader="" trust=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check-only)
      shift
      continue
      ;;
    --stage) stage="${2:-}" ;;
    --candidate) cand="${2:-}" ;;
    --plan) plan="${2:-}" ;;
    --private-key-env) : ;;
    --private-key-encoding) enc="${2:-}" ;;
    --loader) loader="${2:-}" ;;
    --trust-store) trust="${2:-}" ;;
    *) die "unknown argument: $1" ;;
  esac
  shift 2 || die "missing value for $1"
done
{ [ -d "$cand" ] && [ -f "$plan" ]; } || die "--candidate DIR and --plan FILE are required"
bash "$HERE/validate-plan.sh" --plan "$plan" >/dev/null 2>&1 || refuse "the plan does not validate"
notes="$(jq -r .public_notes "$plan")"
required="$(jq -r .signing.artifacts "$plan")"

# The candidate holds only planned files, the notes and planned signatures.
closed() {
  local allowed
  allowed="$( (
    echo "$notes"
    jq -r '.artifacts[] | .filename, (.sbom // empty), (.filename + ".sig")' "$plan"
  ) | sort -u)"
  (cd "$cand" && find . -type f | sed 's#^\./##' | sort) | comm -23 - <(printf '%s\n' "$allowed")
}

if [ "$__check" = 1 ]; then
  if [ -n "$loader" ]; then
    bash "$HERE/release-provisioned.sh" --loader "$loader" "$__keyname" >/dev/null || refuse "the release-signing domain is not provisioned"
  fi
  if [ -n "$trust" ]; then
    [ -f "$trust" ] || refuse "no trust store at $trust"
    jq -e --arg k "$(jq -r .signing.key_id "$plan")" '.keys[$k].status == "active"' "$trust" >/dev/null 2>&1 ||
      refuse "the trust store does not hold the plan's key $(jq -r .signing.key_id "$plan") as active"
  fi
  extra="$(closed)"
  [ -z "$extra" ] || refuse "the candidate holds unplanned file(s): $(tr '\n' ' ' <<<"$extra")"
  while IFS= read -r f; do [ -f "$cand/$f" ] || refuse "planned $f is missing"; done < <(jq -r '.artifacts[].filename' "$plan")
  echo "release-sign: preflight OK — $(jq '.artifacts | length' "$plan") planned artifact(s), key $(jq -r .signing.key_id "$plan")"
  exit 0
fi

case "$stage" in artifacts | final) ;; *) die "--stage must be artifacts or final" ;; esac
case "$enc" in pkcs8-pem | ed25519-seed-public-base64) ;; *) die "--private-key-encoding must be pkcs8-pem or ed25519-seed-public-base64" ;; esac
[ -n "$__key" ] || refuse "the private key variable $__keyname is empty or unset"
[ "$stage" = final ] || [ "$required" = required ] || refuse "--stage artifacts needs signing.artifacts: required"
extra="$(closed)"
[ -z "$extra" ] || refuse "the candidate holds unplanned file(s): $(tr '\n' ' ' <<<"$extra")"
[ ! -e "$cand/release-candidate.json" ] || refuse "the candidate already has a manifest"

sign() { # input → input.sig; the key reaches sign.sh only, for this command
  RELEASE_SIGN_ONCE="$__key" bash "$SIGNING/sign.sh" --profile ed25519-detached-v1 --input "$1" \
    --private-key-env RELEASE_SIGN_ONCE --private-key-encoding "$enc" --output "$1.sig" || refuse "sign.sh refused $(basename "$1")"
}
n=0
if [ "$required" = required ]; then
  while IFS= read -r f; do
    [ -f "$cand/$f" ] || refuse "planned artifact $f is not in the candidate"
    if [ -e "$cand/$f.sig" ]; then
      [ "$stage" = final ] || refuse "$f is already signed"
      continue
    fi
    sign "$cand/$f"
    n=$((n + 1))
  done < <(jq -r '.artifacts[].filename' "$plan")
fi
if [ "$stage" = artifacts ]; then
  __key=""
  [ "$n" -gt 0 ] || refuse "the plan declares no artifact to sign"
  echo "release-sign: signed $n planned artifact(s)"
  exit 0
fi
bash "$HERE/build-candidate.sh" --plan "$plan" --candidate-dir "$cand" --output "$cand/release-candidate.json" ||
  refuse "build-candidate.sh refused the candidate"
bash "$HERE/validate-candidate.sh" --candidate "$cand/release-candidate.json" --candidate-dir "$cand" ||
  refuse "the assembled candidate does not validate"
sign "$cand/release-candidate.json"
__key=""
echo "release-sign: signed $n artifact(s) and the manifest over $(jq '.files | length' "$cand/release-candidate.json") file(s)"
