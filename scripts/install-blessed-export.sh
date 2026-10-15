#!/usr/bin/env bash
# Install the signed public blessed-cicd script export (public-consumer-core)
# into this repository. Everything below the pin block is the consumer block
# from blessed-cicd scripts/INSTALL.md ("Install in a public repo"), verbatim:
# it verifies the signature with OUR committed trust store, the closed file
# set, every digest and the signed SOURCE.json identity before it touches the
# tree. Pins live in .blessed/export.env; the trust store is
# .blessed/export-trust-store.json, confirmed with the owner out of band.
# Usage: make sync-scripts   (then review and commit the result)
set -euo pipefail
cd "$(dirname "$0")/.."
PIN_FILE=.blessed/export.env
[ -f "$PIN_FILE" ] || { echo "refused: $PIN_FILE is missing (no export is pinned yet)" >&2; exit 1; }
# shellcheck disable=SC1090
. "$PIN_FILE"
for v in MIRROR EXPORT TAG KEY_ID SOURCE_REPO; do
  [ -n "${!v:-}" ] || { echo "refused: $PIN_FILE does not set $v" >&2; exit 1; }
done
STORE=.blessed/export-trust-store.json
[ -f "$STORE" ] || { echo "refused: $STORE is missing" >&2; exit 1; }
# ---- verbatim from blessed-cicd scripts/INSTALL.md (export 'public-consumer-core') ----
OPENSSL="${OPENSSL_BIN:-openssl}"              # OpenSSL >= 3 (macOS: Homebrew openssl@3)
tmp="$(mktemp -d)"
gh release download "$TAG" -R "$MIRROR" -D "$tmp" \
  -p "$TAG.tar.gz" -p MANIFEST.sha256 -p MANIFEST.sha256.sig -p SOURCE.json
# 1. Origin: the signature over MANIFEST.sha256 must verify with YOUR pinned key.
#    (Plain OpenSSL, so trust does not depend on a verifier inside the export.)
#    The key is the one YOU pinned; the downloaded SOURCE.json does not choose it.
pub="$(jq -r --arg k "$KEY_ID" '.keys[$k] | select(.status == "active") | .public_key_base64' "$STORE")"
grep -qxE '[A-Za-z0-9+/]{43}=' <<<"$pub" || { echo "refused: $KEY_ID is not an active key in $STORE" >&2; exit 1; }
printf -- '-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA%s\n-----END PUBLIC KEY-----\n' "$pub" >"$tmp/pub.pem"
"$OPENSSL" base64 -d -A <"$tmp/MANIFEST.sha256.sig" >"$tmp/sig.raw"
"$OPENSSL" pkeyutl -verify -pubin -inkey "$tmp/pub.pem" -rawin -sigfile "$tmp/sig.raw" -in "$tmp/MANIFEST.sha256"
# 2. Closed set. Nothing touches this repository until every check passes: the
#    tarball must hold exactly the signed manifest's entries, i.e. the regular
#    files it lists (with their digests) and the directories those imply.
refuse() { echo "refused: $*" >&2; exit 1; }
tgz="$tmp/$TAG.tar.gz"
# 2a. The signed manifest itself: "<sha256>  <plain relative path>" lines only.
if grep -Evxq '[0-9a-f]{64}  [A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*' "$tmp/MANIFEST.sha256"; then
  refuse "MANIFEST.sha256 has a line that is not '<sha256>  <relative path>'"
fi
listed="$(cut -c67- "$tmp/MANIFEST.sha256" | LC_ALL=C sort)"
if grep -Eq '(^|/)\.\.?(/|$)' <<<"$listed"; then refuse "MANIFEST.sha256 lists a '.' or '..' path"; fi
[ -z "$(uniq -d <<<"$listed")" ] || refuse "MANIFEST.sha256 lists a path twice"
while IFS= read -r f; do
  case "$f" in release/* | signing/* | checkers/* | LICENSE | SOURCE.json) ;; *) refuse "MANIFEST.sha256 lists $f" ;; esac
done <<<"$listed"
# want: every entry the export may hold, typed (f = regular file, d = directory).
want="$({
  printf 'd %s\nf %s/MANIFEST.sha256\n' "$EXPORT" "$EXPORT"
  awk -v e="$EXPORT" '{ print "f " e "/" $0; p = e; n = split($0, s, "/"); for (i = 1; i < n; i++) { p = p "/" s[i]; print "d " p } }' <<<"$listed"
} | LC_ALL=C sort -u)"
# 2b. The archive listing: no link or special entry (tar -tv type column is '-' or
#     'd'), and every name is a wanted entry, so no absolute, '..' or outside path.
types="$(tar -tvzf "$tgz")"
names="$(tar -tzf "$tgz")"
[ "$(wc -l <<<"$types")" -eq "$(wc -l <<<"$names")" ] || refuse "the archive listing is ambiguous"
if grep -vq '^[-d]' <<<"$types"; then refuse "archive entry is a link or special file: $(grep -v '^[-d]' <<<"$types" | head -n 1)"; fi
while IFS= read -r n; do
  grep -Fxq -e "d ${n%/}" -e "f $n" <<<"$want" || refuse "archive entry is not in the signed manifest: $n"
done <<<"$names"
# 2c. Extract into a fresh private staging directory, then prove the staged tree
#     (find never follows links) is exactly the wanted set and the digests match.
stage="$(mktemp -d "$tmp/stage.XXXXXX")"
tar -xzf "$tgz" -C "$stage"
odd="$(cd "$stage" && find . -mindepth 1 ! -type d ! \( -type f -links 1 \))"
[ -z "$odd" ] || refuse "staged entry is a link, hard link or special file: $odd"
found="$(cd "$stage" && { find . -mindepth 1 -type d | sed 's#^\./#d #'; find . -type f | sed 's#^\./#f #'; } | LC_ALL=C sort)"
[ "$found" = "$want" ] || refuse "staged tree is not exactly the signed set:
$(diff <(printf '%s\n' "$want") <(printf '%s\n' "$found") | grep '^[<>]')"
cmp "$tmp/MANIFEST.sha256" "$stage/$EXPORT/MANIFEST.sha256"
(cd "$stage/$EXPORT" && shasum -a 256 -c MANIFEST.sha256 >/dev/null) || refuse "a file does not match its signed digest"
# 2d. Identity. The staged SOURCE.json is covered by the signature, and it must
#     name exactly what you pinned, so a validly signed OTHER export or version
#     cannot stand in for the one you asked for. The SOURCE.json release asset
#     must be the same bytes.
cmp -s "$tmp/SOURCE.json" "$stage/$EXPORT/SOURCE.json" || refuse "the SOURCE.json release asset is not the signed SOURCE.json"
mismatch="$(jq -r --arg e "$EXPORT" --arg t "$TAG" --arg m "$MIRROR" --arg r "$SOURCE_REPO" --arg k "$KEY_ID" '[
  (if .schema == "blessed/script-export-source/v1" then empty else "schema" end),
  (if .export == $e then empty else "export" end),
  (if .tag == $t then empty else "tag" end),
  (if ($e + "-v" + (.version | tostring)) == $t then empty else "version" end),
  (if .mirror_repo == $m then empty else "mirror_repo" end),
  (if .source_repo == $r then empty else "source_repo" end),
  (if .signing.profile == "ed25519-detached-v1" then empty else "signing.profile" end),
  (if .signing.key_id == $k then empty else "signing.key_id" end)
] | join(" ")' "$stage/$EXPORT/SOURCE.json")"
[ -z "$mismatch" ] || refuse "the signed SOURCE.json differs from the pinned identity: $mismatch"
# 3. Install: copy only the listed regular files (never cp -R of the staged tree).
#    Commit the result; CI then re-verifies offline with shasum -c.
rm -rf scripts/release scripts/signing tools/blessed .blessed/export
while IFS= read -r f; do
  case "$f" in checkers/*) dst="tools/blessed/${f#checkers/}" ;; release/* | signing/*) dst="scripts/$f" ;; *) dst=".blessed/export/$f" ;; esac
  mkdir -p "$(dirname "$dst")"
  cp "$stage/$EXPORT/$f" "$dst"
done <<<"$listed"
cp "$tmp/MANIFEST.sha256" "$tmp/MANIFEST.sha256.sig" .blessed/export/
