#!/usr/bin/env bash
# validate-candidate.sh — validate an assembled candidate. Runs schema + semantic
# closure + on-disk hash recompute always; when --trust-store and --signing-dir are
# given, ALSO verifies release-candidate.json.sig (and per-artifact sigs when
# signing.artifacts==required) through the signing category's CLI (API v1).
#
# Crypto is delegated: this script calls signing/verify.sh and never sources
# signing-lib.sh or invokes openssl, so a signing-side fix can't create a second
# cryptographic interpretation here.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/lib/release-lib.sh
. "$HERE/lib/release-lib.sh"

usage() {
  cat >&2 <<'EOF'
Usage: validate-candidate.sh --candidate-dir DIR
                             [--candidate DIR/release-candidate.json]
                             [--trust-store STORE.json --signing-dir scripts/signing]
                             [--declaration release.yml | --native-policy NATIVE.json]

Without --trust-store/--signing-dir: schema + closure + on-disk recompute (pre-sign).
With them: also verifies the manifest signature (and required artifact sigs).

Native finalization (blessed/native-finalization/v1) is checked against a
TRUSTED policy the caller supplies, never against the candidate: the
producer's release.yml from a trusted revision (--declaration; its
workflow.native, or none) or that workflow.native object alone
(--native-policy, JSON or YAML). With a policy, every declared native artifact
must carry exactly one sealed record, of the declared team, bundle id, minimum
OS, architectures and nested code, and no other record may exist. Without one,
a candidate that carries ANY native record is INVALID (fail closed).
EOF
  exit 2
}

cdir="" candidate="" store="" signing_dir="" decl="" npol=""
while [ $# -gt 0 ]; do
  case "$1" in
    --candidate-dir)
      cdir="${2:-}"
      shift 2
      ;;
    --candidate)
      candidate="${2:-}"
      shift 2
      ;;
    --trust-store)
      store="${2:-}"
      shift 2
      ;;
    --signing-dir)
      signing_dir="${2:-}"
      shift 2
      ;;
    --declaration)
      decl="${2:-}"
      shift 2
      ;;
    --native-policy)
      npol="${2:-}"
      shift 2
      ;;
    -h | --help) usage ;;
    *)
      printf 'release: unexpected arg: %s\n' "$1" >&2
      usage
      ;;
  esac
done
[ -n "$cdir" ] || usage
[ -d "$cdir" ] || {
  printf 'release: candidate dir not found: %s\n' "$cdir" >&2
  exit 1
}
[ -n "$candidate" ] || candidate="$cdir/release-candidate.json"
[ -f "$candidate" ] || {
  printf 'release: candidate manifest not found: %s\n' "$candidate" >&2
  exit 1
}
jq -e . "$candidate" >/dev/null 2>&1 || {
  printf 'release: candidate is not valid JSON\n' >&2
  exit 1
}
if [ -n "$decl" ] && [ -n "$npol" ]; then
  printf 'release: give --declaration or --native-policy, not both\n' >&2
  exit 2
fi
if [ -n "$store$signing_dir" ] && { [ -z "$store" ] || [ -z "$signing_dir" ]; }; then
  printf 'release: signature verification needs BOTH --trust-store and --signing-dir\n' >&2
  exit 2
fi

fail=0
report() { # $1=label  $2=violations
  if [ -n "$2" ]; then
    printf 'release: FAIL %s:\n%s\n' "$1" "$2" >&2
    fail=1
  else
    printf 'release: OK   %s\n' "$1" >&2
  fi
}

report "schema (blessed/release-candidate/v1)" "$(rel_schema_validate "$candidate" "$HERE/references/release-candidate.schema.json")"
report "semantic closure" "$(rel_semantic_checks "$candidate")"
report "on-disk hash recompute" "$(rel_recompute_check "$candidate" "$cdir")"
report "closed candidate dir (no unlisted files)" "$(rel_closed_dir_check "$candidate" "$cdir")"

# Native finalization records (pipeline.md property 2, the declared native
# exception; blessed/native-finalization/v1), checked at every receiver with no
# Apple tooling: each sealed record conforms, names exactly one artifact of
# this candidate, and that artifact's sealed — and signed — digest is the
# record's output, distinct from its unsigned input and its notarized
# submission. Which artifacts REQUIRE a record, and their identity, come only
# from the caller's trusted policy (--declaration / --native-policy): every
# declared native artifact carries exactly one record, of the declared team,
# bundle id and MAC-1 identity, and no undeclared record exists. A candidate
# cannot vouch for itself, so with no policy any record is refused.
NSUF=.native-finalization.json
native_policy() { # → $1: the trusted workflow.native as JSON (null = none declared); prints violations
  local raw="$1.raw"
  if [ -n "$decl" ]; then
    if ! { [ -f "$decl" ] && yq -o=json '.' "$decl" >"$raw" 2>/dev/null && jq -e 'type == "object"' "$raw" >/dev/null 2>&1; }; then
      echo "--declaration $decl is not a readable YAML declaration"
      return 0
    fi
    jq '.workflow.native // null' "$raw" >"$1"
  else
    if ! { [ -f "$npol" ] && yq -o=json '.' "$npol" >"$1" 2>/dev/null; }; then
      echo "--native-policy $npol is not readable JSON or YAML"
      return 0
    fi
  fi
  jq -r 'if . == null then empty
    elif (type == "object" and .platform == "macos" and .route == "developer-id-zip"
      and (.artifacts | type == "array" and length > 0 and all(.[]; type == "string")) and (.team_id | type == "string" and test("^[A-Z0-9]{10}$"))
      and (.bundle_id | type == "string" and length > 0) and (.minimum_os | type == "string" and length > 0)
      and (.architectures | type == "array" and length > 0 and all(.[]; IN("arm64", "x86_64")))
      and ((.nested_code // []) | type == "array" and all(.[]; (.path | type == "string") and (.identifier | type == "string")))) | not
    then "the native policy is not a developer-id-zip workflow.native (platform, route, artifacts, team_id, bundle_id, minimum_os, architectures, nested_code)"
    else empty end' "$1"
}
native_checks() {
  local rn r f i pj
  while IFS= read -r rn; do
    [ -n "$rn" ] || continue
    r="$cdir/$rn"
    f="${rn%"$NSUF"}"
    if [ ! -f "$r" ]; then
      echo "$rn: missing"
      continue
    fi
    if ! jq -e 'type == "object"' "$r" >/dev/null 2>&1; then
      echo "$rn: not a JSON object"
      continue
    fi
    rel_schema_validate "$r" "$HERE/references/native-finalization.schema.json" | sed "s#^#$rn: #"
    i="$(jq -r '.artifact_id // ""' "$r" 2>/dev/null)"
    jq -r --arg i "$i" --arg f "$f" --arg rn "$rn" --slurpfile r "$r" '
      [.artifacts[] | select(.id == $i and .filename == $f)] as $a
      | if ($a | length) != 1 then "\($rn): names \($i) (\($f)), not exactly one artifact of this candidate"
        elif $r[0].output_sha256 != $a[0].sha256 then "\($rn): output_sha256 is not the signed digest of \($f)"
        elif ([$r[0].unsigned_sha256, $r[0].submission_sha256, $r[0].output_sha256] | unique | length) != 3
          then "\($rn): unsigned, submission and output digests are not three distinct values"
        else empty end' "$candidate"
  done < <(jq -r --arg s "$NSUF" '.artifacts[] | select(.filename | endswith($s)) | .filename' "$candidate")
  if [ -z "$decl$npol" ]; then
    jq -r --arg s "$NSUF" '.artifacts[] | select(.filename | endswith($s))
      | "\(.filename): a native finalization record, but no trusted native policy was supplied (--declaration or --native-policy): refused, not trusted from the candidate"' "$candidate"
    return 0
  fi
  pj="$(mktemp)"
  native_policy "$pj"
  if ! jq -e 'type == "object" or . == null' "$pj" >/dev/null 2>&1; then
    rm -f "$pj" "$pj.raw"
    return 0
  fi
  jq -r --slurpfile n "$pj" --arg s "$NSUF" '. as $c | ($n[0] // {artifacts: []}).artifacts[] as $id
    | ([$c.artifacts[] | select(.id == $id)] | first) as $a
    | if $a == null then "declared native artifact \($id) is not in this candidate"
      elif ([$c.artifacts[] | select(.filename == ($a.filename + $s))] | length) != 1 then "declared native artifact \($id) (\($a.filename)) carries no sealed finalization record"
      else empty end' "$candidate"
  while IFS= read -r rn; do
    [ -f "$cdir/$rn" ] || continue
    jq -r --slurpfile n "$pj" --arg rn "$rn" '$n[0] as $n
      | if $n == null then "\($rn): sealed, but the trusted policy declares no workflow.native"
        else (if .signer.team_id != $n.team_id then "\($rn): team \(.signer.team_id) is not the declared \($n.team_id)" else empty end),
          (if .app.bundle_id != $n.bundle_id then "\($rn): bundle id \(.app.bundle_id) is not the declared \($n.bundle_id)" else empty end),
          (if .app.minimum_os != $n.minimum_os then "\($rn): minimum OS \(.app.minimum_os) is not the declared \($n.minimum_os)" else empty end),
          (if .app.architectures != ($n.architectures | sort) then "\($rn): architectures \(.app.architectures | join("+")) are not the declared \($n.architectures | sort | join("+"))" else empty end),
          (if [.app.nested_code[]? | {path, identifier}] != [($n.nested_code // [])[] | {path, identifier}]
            then "\($rn): nested code \([.app.nested_code[]? | "\(.path)=\(.identifier)"] | join(" ")) is not the declared \([($n.nested_code // [])[] | "\(.path)=\(.identifier)"] | join(" "))" else empty end),
          (if [.app.nested_code[]? | .architectures] | any(. != ($n.architectures | sort)) then "\($rn): nested code is not of the declared architectures" else empty end),
          (if (.artifact_id | IN($n.artifacts[])) | not then "\($rn): artifact \(.artifact_id) is not declared native" else empty end)
        end' "$cdir/$rn" 2>/dev/null
  done < <(jq -r --arg s "$NSUF" '.artifacts[] | select(.filename | endswith($s)) | .filename' "$candidate")
  rm -f "$pj" "$pj.raw"
}
if [ -n "$decl$npol" ] || jq -e --arg s "$NSUF" 'any(.artifacts[]; .filename | endswith($s))' "$candidate" >/dev/null 2>&1; then
  report "native finalization records (blessed/native-finalization/v1)" "$(native_checks)"
fi

# Signature verification (surface-side), delegated to the signing category.
if [ -n "$store" ]; then
  if rel_require_signing_api "$signing_dir"; then
    key_id="$(jq -r '.signing.key_id' "$candidate")"
    sig="$cdir/release-candidate.json.sig"
    if [ ! -f "$sig" ]; then
      report "manifest signature present" "release-candidate.json.sig is missing"
    elif bash "$signing_dir/verify.sh" --profile ed25519-detached-v1 \
      --input "$candidate" --signature "$sig" --trust-store "$store" --key-id "$key_id" >/dev/null 2>&1; then
      report "manifest signature (ed25519-detached-v1, $key_id)" ""
    else
      report "manifest signature (ed25519-detached-v1, $key_id)" "verification failed"
    fi
    # Every per-artifact signature present is verified, whatever the mode
    # (releases.release-pr@1 RP-10): a .sig that is only hash-bound would be
    # an unchecked claim. `required` additionally means none may be absent
    # (enforced at assembly and by the semantic checks).
    #
    # An artifact MAY declare its own runtime-native signature profile/key id
    # (releases.candidate@1 — the complete `signature` + `signature_profile` +
    # `signature_key_id` tuple enforced by rel_semantic_checks). Verification
    # dispatches on that EFFECTIVE profile/key, not on the manifest envelope's:
    # verifying a Minisign sidecar with the envelope profile and key would check
    # the wrong cryptographic contract. Absent an override, the artifact inherits
    # the envelope's profile/key. Every unroutable case fails closed.
    {
      env_profile="$(jq -r '.signing.profile' "$candidate")"
      while IFS="$(printf '\t')" read -r af afile aprofile akey; do
        [ -n "$af" ] || continue
        asig="$cdir/$af"
        ain="$cdir/$afile"
        label="artifact signature $af ($aprofile, $akey)"
        case "$aprofile" in
          ed25519-detached-v1)
            if bash "$signing_dir/verify.sh" --profile ed25519-detached-v1 --input "$ain" --signature "$asig" --trust-store "$store" --key-id "$akey" >/dev/null 2>&1; then
              report "$label" ""
            else
              report "$label" "verification failed"
            fi
            ;;
          tauri-minisign-v1)
            # The native profile is not verifiable by the ed25519-detached CLI.
            # Require the key id to be an active trust-store key bound to THIS
            # profile, then delegate the bytes to the profile's adapter. No
            # adapter == no verification == INVALID (never a silent pass).
            if ! jq -e --arg k "$akey" --arg p "$aprofile" \
              '.keys[$k] // empty | select(.profile == $p and .status == "active")' "$store" >/dev/null 2>&1; then
              report "$label" "key id is not an active $aprofile key in the trust store"
            elif [ ! -f "$signing_dir/verify-native.sh" ]; then
              report "$label" "no adapter for $aprofile (expected $signing_dir/verify-native.sh) — cannot verify, failing closed"
            elif bash "$signing_dir/verify-native.sh" --profile "$aprofile" --input "$ain" --signature "$asig" --trust-store "$store" --key-id "$akey" >/dev/null 2>&1; then
              report "$label" ""
            else
              report "$label" "verification failed"
            fi
            ;;
          *)
            report "$label" "unsupported artifact signature profile: $aprofile"
            ;;
        esac
      done < <(jq -r --arg dp "$env_profile" --arg dk "$key_id" '
        .artifacts[]
        | select(.signature != null)
        | [ .signature,
            .filename,
            (if ((.signature_profile // "") | length) > 0 then .signature_profile else $dp end),
            (if ((.signature_key_id // "") | length) > 0 then .signature_key_id else $dk end) ]
        | @tsv' "$candidate")
    }
  else
    fail=1
  fi
fi

if [ "$fail" -eq 0 ]; then
  printf 'release: candidate VALID\n' >&2
  exit 0
else
  printf 'release: candidate INVALID\n' >&2
  exit 1
fi
