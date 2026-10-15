#!/usr/bin/env bash
# rpm-finalize-set.sh — natively sign every RPM of a staged candidate
# (standards/releases/linux-packaging.md LP-9) with the vendored blessed
# scripts/signing/rpm-finalize.sh, before the candidate is signed and sealed.
#
#   rpm-finalize-set.sh [--check-only] --candidate DIR --inputs FILE \
#       --public-key FILE --fingerprint-file FILE --private-key-env NAME \
#       [--loader FILE] [--records DIR]
#
#   --inputs FILE        {"<name>.rpm": "<sha256>"} recorded by the secret-free
#                        stage job and handed over as a job output
#   --public-key FILE    the declared public key (armored, exactly one key)
#   --fingerprint-file   its fingerprint (40 hex); a placeholder refuses
#   --private-key-env    the env var holding the armored private key
#   --loader FILE        with --check-only: NAME must be mapped, provisioned
#   --records DIR        receives <name>.rpm-finalization.json per RPM
#                        (blessed/rpm-finalization/v1); must not exist
#
# PREFLIGHT, the whole of --check-only and the first thing a real run does
# again: the fingerprint is 40 hex; the public key file holds EXACTLY ONE
# primary key and it is that one; the inputs are a non-empty object of 64-hex
# digests; the candidate's RPM set equals the inputs' set; every RPM is still
# the recorded bytes.
#
# KEY CONFINEMENT. Before argument parsing or any subprocess, the key is copied
# out of the environment into an unexported shell variable and the variable is
# unset, so no tool this script runs inherits it. It is handed only to
# rpm-finalize.sh, one call at a time, through a variable assigned for that
# command. The candidate's RPMs are replaced only after every one finalized.
# Exit 0 · 1 refused · 2 usage/tooling.
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
    printf 'rpm-finalize-set: --private-key-env NAME is required (a plain variable name)\n' >&2
    exit 2
    ;;
esac
__key=""
if [ "$__check" = 0 ]; then
  __key="${!__keyname-}"
  unset "$__keyname"
fi
unset __i __j
# From here on no subprocess can see the key: it lives only in $__key.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIGNING="$HERE/../signing"
die() {
  printf 'rpm-finalize-set: %s\n' "$1" >&2
  exit 2
}
refuse() {
  printf '::error::rpm-finalize-set: REFUSED — %s\n' "$1" >&2
  exit 1
}
cand="" inputs="" pub="" fprf="" loader="" records=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check-only)
      shift
      continue
      ;;
    --candidate) cand="${2:-}" ;;
    --inputs) inputs="${2:-}" ;;
    --public-key) pub="${2:-}" ;;
    --fingerprint-file) fprf="${2:-}" ;;
    --private-key-env) : ;;
    --loader) loader="${2:-}" ;;
    --records) records="${2:-}" ;;
    *) die "unknown argument: $1" ;;
  esac
  shift 2 || die "missing value for $1"
done
for v in cand inputs pub fprf; do [ -n "${!v}" ] || die "--${v} is required"; done
for t in jq sha256sum gpg awk find; do command -v "$t" >/dev/null 2>&1 || die "required tool not found: $t"; done

# ── preflight ─────────────────────────────────────────────────────────────
if [ -n "$loader" ]; then
  bash "$HERE/release-provisioned.sh" --loader "$loader" "$__keyname" >/dev/null || refuse "the rpm-signing domain is not provisioned"
fi
[ -f "$fprf" ] || refuse "the fingerprint file $fprf does not exist"
fpr="$(tr -d '[:space:]' <"$fprf")"
[[ "$fpr" =~ ^[0-9A-F]{40}$ ]] || refuse "the RPM signing key is not provisioned: $fprf holds '$fpr', not a 40-hex fingerprint"
[ -f "$pub" ] || refuse "the public key file $pub does not exist"
gh="$(mktemp -d)"
trap 'rm -rf "$gh"' EXIT
list="$(GNUPGHOME="$gh" gpg --batch --with-colons --show-keys "$pub" 2>/dev/null)" ||
  refuse "the RPM signing public key $pub is not a readable OpenPGP key"
npub="$(printf '%s\n' "$list" | grep -c '^pub:' || true)"
[ "$npub" = 1 ] || refuse "the public key file $pub holds $npub primary keys, not exactly the declared one"
got="$(printf '%s\n' "$list" | awk -F: '/^pub:/{p=1; next} p && /^fpr:/{print $10; exit}')"
[ "$got" = "$fpr" ] || refuse "the public key file $pub is ${got:-<none>}, not the declared $fpr"
[ -f "$inputs" ] || refuse "the build's RPM input digests ($inputs) are missing"
jq -e 'type == "object" and length > 0 and all(.[]; type == "string" and test("^[0-9a-f]{64}$"))
       and all(keys[]; test("^[A-Za-z0-9][A-Za-z0-9._+-]*\\.rpm$"))' "$inputs" >/dev/null 2>&1 ||
  refuse "the build's RPM input digests ($inputs) are empty or malformed"
[ -d "$cand" ] || refuse "no candidate directory: $cand"
want="$(jq -r 'keys[]' "$inputs" | sort)"
have="$(cd "$cand" && find . -maxdepth 1 -name '*.rpm' | sed 's#^\./##' | sort)"
[ "$want" = "$have" ] || refuse "the candidate's RPMs ($(tr '\n' ' ' <<<"$have")) are not the build's ($(tr '\n' ' ' <<<"$want"))"
while IFS= read -r f; do
  { [ -f "$cand/$f" ] && [ ! -L "$cand/$f" ]; } || refuse "$f is not a regular file"
  [ "$(sha256sum "$cand/$f" | cut -d' ' -f1)" = "$(jq -r --arg f "$f" '.[$f]' "$inputs")" ] ||
    refuse "$f is not the bytes the build recorded"
done <<<"$want"
if [ "$__check" = 1 ]; then
  printf 'rpm-finalize-set: preflight OK — %s RPM(s) as built, key %s declared%s\n' \
    "$(wc -l <<<"$want" | tr -d ' ')" "$fpr" "${loader:+, domain provisioned}"
  exit 0
fi

# ── finalize ──────────────────────────────────────────────────────────────
[ -n "$records" ] || die "--records is required"
[ ! -e "$records" ] || die "--records must not exist: $records"
[ -n "$__key" ] || refuse "the private key variable $__keyname is empty or unset"
work="$(mktemp -d)"
trap 'rm -rf "$gh" "$work"' EXIT
mkdir -p "$records" "$work/final"
while IFS= read -r f; do
  # The ONLY process that receives the key: assigned for this one command.
  __RPM_FINALIZE_KEY="$__key" bash "$SIGNING/rpm-finalize.sh" \
    --input "$cand/$f" \
    --input-sha256 "$(jq -r --arg f "$f" '.[$f]' "$inputs")" \
    --key-fingerprint "$fpr" \
    --public-key "$pub" \
    --private-key-env __RPM_FINALIZE_KEY \
    --output "$work/final/$f" \
    --record "$records/$f.rpm-finalization.json" || refuse "rpm-finalize refused $f"
done <<<"$want"
__key=""
while IFS= read -r f; do mv -f "$work/final/$f" "$cand/$f"; done <<<"$want"
printf 'rpm-finalize-set: OK — %s RPM(s) natively signed by %s\n' "$(wc -l <<<"$want" | tr -d ' ')" "$fpr"
