#!/usr/bin/env bash
# pkgrepo-generate.sh — build one UNSIGNED generation of a package-repository-surface
# from its reviewed inventory (standards/releases/package-repositories.md PR-3, PR-4,
# PR-7, PR-9, PR-14).
#
# Input is the inventory and the package bytes it names — nothing else. Every
# package is checked against its inventory digest and size before it is used;
# the repository and producer public keys are checked against the fingerprints
# the inventory declares. Output is a complete site tree plus a generation
# manifest (blessed/package-repository-generation/v1) classifying every object
# as IMMUTABLE — shared, published once at its advertised path (packages,
# by-hash indexes, checksum-named repodata) — or ENTRYPOINT — published under
# the generation's own prefix and reached at its stable URL through the
# activation pointer (APT Release and plain indexes, DNF repomd.xml, client
# configuration, public keys; pkgrepo-publish.sh, pkgrepo-router.js). Signing is a
# separate, isolated step (pkgrepo-sign.sh); this step holds no secret.
#
# DETERMINISM. Given the same inventory, package bytes, keys, --timestamp and
# tool versions, the output is byte-identical. That is what lets an interrupted
# publication resume by regenerating, and makes a generation reviewable.
#
# APT: Packages/Packages.gz per architecture (fields read with dpkg-deb, plus
#   Filename/Size/SHA256), a Release with `Acquire-By-Hash: yes`, and by-hash
#   copies of every index. DNF: createrepo_c --no-database --unique-md-filenames
#   with its revision and timestamps set from --timestamp.
#
# LP-2: versions of one package are refused unless the native comparator
# orders them exactly as their upstream release tags (see below).
#
# Exit: 0 generated · 1 refused (inventory, bytes, keys, mapping, ordering) · 2 tooling/usage.
# shellcheck disable=SC2016  # jq programs: $-names are jq variables, not shell
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JQ="${JQ_BIN:-jq}"

die() {
  printf 'pkgrepo-generate: %s\n' "$1" >&2
  exit 2
}
refuse() {
  printf 'pkgrepo-generate: REFUSED — %s\n' "$1" >&2
  exit 1
}
usage() {
  cat >&2 <<'EOF'
Usage:
  pkgrepo-generate.sh --inventory INVENTORY.json --pool DIR --keys-dir DIR \
                      --timestamp EPOCH --tool-pins PINS --out DIR

  --pool       directory holding every inventory package at its inventory `file` path
  --keys-dir   directory holding every public key at its inventory `public_key_path`
  --timestamp  generation time (APT Release Date, DNF revision); pass the inventory
               commit time so regeneration is byte-identical
  --tool-pins  KEY=VERSION lines; dpkg-deb and createrepo_c must match exactly
  --out        must not exist
EOF
  exit 2
}

inv="" pool="" keys="" ts="" pins="" out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --inventory)
      inv="${2:-}"
      shift 2
      ;;
    --pool)
      pool="${2:-}"
      shift 2
      ;;
    --keys-dir)
      keys="${2:-}"
      shift 2
      ;;
    --timestamp)
      ts="${2:-}"
      shift 2
      ;;
    --tool-pins)
      pins="${2:-}"
      shift 2
      ;;
    --out)
      out="${2:-}"
      shift 2
      ;;
    -h | --help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
done
if [ -z "$inv" ] || [ -z "$pool" ] || [ -z "$keys" ] || [ -z "$ts" ] || [ -z "$pins" ] || [ -z "$out" ]; then usage; fi
case "$ts" in *[!0-9]* | "") die "--timestamp must be a Unix epoch" ;; esac
[ -d "$pool" ] || die "--pool is not a directory"
[ -d "$keys" ] || die "--keys-dir is not a directory"
[ -f "$pins" ] || die "--tool-pins not found"
[ ! -e "$out" ] || die "--out already exists: $out"
for t in "$JQ" sha256sum gzip gpg; do command -v "$t" >/dev/null 2>&1 || die "required tool not found: $t"; done
# shellcheck source=scripts/release/lib/pkgrepo-lib.sh
. "$HERE/lib/pkgrepo-lib.sh" || die "packaged library not found: $HERE/lib/pkgrepo-lib.sh"
export JQ

# ── the inventory is valid before anything is read from it ─────────────────
bash "$HERE/validate-package-inventory.sh" --file "$inv" >/dev/null 2>"${TMPDIR:-/tmp}/pkgrepo-inv.$$" || {
  sed 's/^/pkgrepo-generate: /' "${TMPDIR:-/tmp}/pkgrepo-inv.$$" >&2
  rm -f "${TMPDIR:-/tmp}/pkgrepo-inv.$$"
  refuse "the inventory is not valid"
}
rm -f "${TMPDIR:-/tmp}/pkgrepo-inv.$$"

pin() { sed -n "s/^$1=//p" "$pins" | head -1; }
needs_apt="$("$JQ" -r '[.repositories[] | select(.format == "apt")] | length' "$inv")"
needs_dnf="$("$JQ" -r '[.repositories[] | select(.format == "dnf")] | length' "$inv")"
if [ "$needs_apt" -gt 0 ]; then
  command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not found (APT repositories need it)"
  want="$(pin dpkg-deb)"
  [ -n "$want" ] || die "--tool-pins has no dpkg-deb pin"
  have="$(dpkg-deb --version | sed -n '1s/.*version \([^ ]*\).*/\1/p')"
  [ "$have" = "$want" ] || die "dpkg-deb $have is not the pinned $want"
fi
if [ "$needs_dnf" -gt 0 ]; then
  command -v createrepo_c >/dev/null 2>&1 || die "createrepo_c not found (DNF repositories need it)"
  command -v rpm >/dev/null 2>&1 || die "rpm not found (DNF repositories need it to read package headers)"
  want="$(pin createrepo_c)"
  [ -n "$want" ] || die "--tool-pins has no createrepo_c pin"
  have="$(createrepo_c --version | sed -n '1s/^Version: \([0-9.]*\).*/\1/p')"
  [ "$have" = "$want" ] || die "createrepo_c $have is not the pinned $want"
fi

sha() { sha256sum "$1" | cut -d' ' -f1; }
size() { wc -c <"$1" | tr -d ' '; }
stage="$(mktemp -d)" || die "cannot create a staging directory"
trap 'rm -rf "$stage"' EXIT
site="$stage/site"
mkdir -p "$site"
objects="$stage/objects.jsonl"
: >"$objects"
# record CLASS PATH CONTENT-TYPE — one manifest line per published object
record() {
  "$JQ" -cn --arg p "$2" --arg c "$1" --arg t "$3" --arg h "$(sha "$site/$2")" --argjson s "$(size "$site/$2")" \
    '{path: $p, class: $c, content_type: $t, sha256: $h, size: $s}' >>"$objects"
}

# ── keys: the published public keys are the declared ones ──────────────────
while IFS=$'\t' read -r kp kf; do
  [ -f "$keys/$kp" ] || refuse "public key $kp is missing from --keys-dir"
  why="$(pkgrepo_exact_key "$keys/$kp" "$kf")" || refuse "public key $kp $why"
  mkdir -p "$site/$(dirname "$kp")"
  cp "$keys/$kp" "$site/$kp"
  record entrypoint "$kp" "application/pgp-keys"
done < <("$JQ" -r '[.repository_key] + .producer_keys | .[] | [.public_key_path, .fingerprint] | @tsv' "$inv")

# ── packages: every byte is the inventory's byte ───────────────────────────
while IFS=$'\t' read -r f h s; do
  if [ ! -f "$pool/$f" ] || [ -L "$pool/$f" ]; then refuse "package $f is missing from --pool"; fi
  got="$(sha "$pool/$f")"
  [ "$got" = "$h" ] || refuse "package $f has sha256 $got, but the inventory records $h"
  [ "$(size "$pool/$f")" = "$s" ] || refuse "package $f has size $(size "$pool/$f"), but the inventory records $s"
  mkdir -p "$site/$(dirname "$f")"
  cp "$pool/$f" "$site/$f"
done < <("$JQ" -r '.packages[] | [.file, .sha256, (.size | tostring)] | @tsv' "$inv")

# ── LP-2: native ordering agrees with the upstream release order ───────────
# Within every set of versions one client can see (an APT index: its
# architecture plus `all`; a DNF repository: its arch plus noarch), each
# package's versions ordered by their candidates' upstream tags (SemVer
# precedence; a tag that is not strict SemVer is refused) must be STRICTLY increasing under the native comparator (dpkg --compare-versions; rpm's vercmp). A pre-release
# packaged without `~` (1.1.0-rc.1 sorts AFTER 1.1.0 natively), or two
# versions the client cannot tell apart, is refused before anything is built.
native_lt() { # format a b → 0 if a < b natively
  case "$1" in
    apt) dpkg --compare-versions "$2" lt "$3" ;;
    dnf)
      local r
      r="$(rpm --eval "%{lua: print(rpm.vercmp('$2', '$3'))}" 2>/dev/null)" || return 2
      [ "$r" = -1 ]
      ;;
  esac
}
lp2_rows="$("$JQ" -r '
  # Upstream order is SemVer 2.0.0 precedence, and only a tag that IS strict
  # SemVer (v-prefixed) has one: anything else is refused, never coerced.
  # Numeric identifiers compare numerically and below alphanumeric ones; a
  # release ranks above every pre-release of it; build metadata is ignored, so
  # two distinct tags of equal precedence have no order and are refused too.
  def semre: "^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(-(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(\\.(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(\\+[0-9A-Za-z-]+(\\.[0-9A-Za-z-]+)*)?$";
  def ids: split(".") | map(if test("^[0-9]+$") then [0, tonumber] else [1, .] end);
  def semkey: ltrimstr("v") | sub("\\+.*$"; "") | capture("^(?<core>[0-9.]+)(-(?<pre>.+))?$") as $c
    | ($c.core | split(".") | map(tonumber)) + (if $c.pre then [0, ($c.pre | ids)] else [1] end);
  . as $inv
  | [$inv.packages[] | select(.candidate.tag | test(semre) | not) | ["!tag", .name, .candidate.tag]] as $bad
  | if ($bad | length) > 0 then $bad[] | @tsv else
    # Every set of versions ONE client can see: an APT index per architecture
    # holds that architecture plus `all`; a DNF repository holds its arch plus
    # noarch.
    [$inv.repositories[] as $r
      | if $r.format == "apt" then
          ($r.architectures[] as $a
            | {fmt: "apt", view: "\($r.id)/\($a)", pk: [$inv.packages[] | select(.repository == $r.id and (.arch == $a or .arch == "all"))]})
        else {fmt: "dnf", view: "\($r.id)/\($r.arch)", pk: [$inv.packages[] | select(.repository == $r.id)]} end]
    | .[] | .fmt as $f | .view as $v
    | .pk | group_by(.name)[]
    # Repackagings of one tag (LP-2: same upstream version, revision 1, 2, …)
    # are ordered by their integer revision, never by inventory row order; a
    # revision that is not an integer, or one tag packaged as two upstream
    # versions, has no order and is refused.
    | map(. + {k: [(.candidate.tag | semkey), (.revision | if test("^[0-9]+$") then tonumber else null end)]})
    | sort_by(.k)
    | "\($v)/\(.[0].name)" as $g
    | ([group_by(.candidate.tag)[] | select(length > 1)
        | if (map(.version) | unique | length) > 1 then
            ["!ver", $g, "\(.[0].candidate.tag) \(map(.version) | unique | join(" "))"]
          elif any(.k[1] == null) then
            ["!rev", $g, "\(.[0].candidate.tag) \(map(.revision) | join(" "))"]
          else empty end] | .[] | @tsv),
      ([range(1; length) as $i | select(.[$i].k[0] == .[$i - 1].k[0] and .[$i].candidate.tag != .[$i - 1].candidate.tag)
        | ["!tie", $g, "\(.[$i - 1].candidate.tag) \(.[$i].candidate.tag)"]] | .[] | @tsv),
      ([$f, $g, (map("\(.version)-\(.revision)") | join(" "))] | @tsv)
  end' "$inv")" || die "could not compute the LP-2 version sets from the inventory"
lp2_sets=0 lp2_pairs=0
while IFS=$'\t' read -r fmt group versions; do
  case "$fmt" in
    "") continue ;;
    '!tag') refuse "$group: candidate tag $versions is not strict SemVer (vMAJOR.MINOR.PATCH[-PRE][+BUILD]), so it has no upstream order (LP-2)" ;;
    '!ver') refuse "$group: candidate tag ${versions%% *} is packaged as more than one upstream version (${versions#* }), so they have no order (LP-2)" ;;
    '!rev') refuse "$group: candidate tag ${versions%% *} is repackaged with revisions (${versions#* }) that are not all integers, so they have no order (LP-2)" ;;
    '!tie') refuse "$group: candidate tags $versions have equal SemVer precedence, so they have no upstream order (LP-2)" ;;
  esac
  lp2_sets=$((lp2_sets + 1))
  [ "$fmt" = dnf ] || command -v dpkg >/dev/null 2>&1 || die "dpkg not found (LP-2 ordering needs dpkg --compare-versions)"
  prev=""
  for v in $versions; do
    [[ "$v" =~ ^[A-Za-z0-9.+~^_-]+$ ]] || refuse "$group: version $v is not a plain native version"
    if [ -n "$prev" ]; then
      native_lt "$fmt" "$prev" "$v" ||
        refuse "$group: $prev is released before $v, but the native comparator does not order $prev < $v (LP-2; a pre-release needs '~')"
      lp2_pairs=$((lp2_pairs + 1))
    fi
    prev="$v"
  done
done <<<"$lp2_rows"
[ "$lp2_sets" -gt 0 ] || die "LP-2 found no client-visible version set in a non-empty inventory"
printf 'pkgrepo-generate: LP-2 native ordering — %s client-visible version set(s), %s adjacent pair(s) strictly increasing\n' "$lp2_sets" "$lp2_pairs" >&2

surface="$("$JQ" -r .surface_id "$inv")"
rfc2822="$(LC_ALL=C date -u -d "@$ts" '+%a, %d %b %Y %H:%M:%S UTC' 2>/dev/null || LC_ALL=C date -u -r "$ts" '+%a, %d %b %Y %H:%M:%S UTC')"

# ── APT repositories ───────────────────────────────────────────────────────
while IFS= read -r repo; do
  id="$("$JQ" -r .id <<<"$repo")"
  rpath="$("$JQ" -r .path <<<"$repo")"
  suite="$("$JQ" -r .suite <<<"$repo")"
  comp="$("$JQ" -r .component <<<"$repo")"
  product="$("$JQ" -r .product <<<"$repo")"
  dists="$rpath"dists/"$suite"
  mkdir -p "$site/$dists"
  shaidx="$stage/$id.release-sha256"
  : >"$shaidx"
  for arch in $("$JQ" -r '.architectures[]' <<<"$repo"); do
    bdir="$comp/binary-$arch"
    mkdir -p "$site/$dists/$bdir/by-hash/SHA256"
    pk="$site/$dists/$bdir/Packages"
    : >"$pk"
    # Deterministic order: name, then version, then file.
    while IFS=$'\t' read -r f; do
      rel="${f#"$rpath"}"
      fields="$(dpkg-deb -f "$site/$f")" || refuse "dpkg-deb cannot read $f"
      pkgname="$(dpkg-deb -f "$site/$f" Package)"
      pkgver="$(dpkg-deb -f "$site/$f" Version)"
      pkgarch="$(dpkg-deb -f "$site/$f" Architecture)"
      want="$("$JQ" -r --arg f "$f" '.packages[] | select(.file == $f) | "\(.name) \(.version)-\(.revision) \(.arch)"' "$inv")"
      [ "$pkgname $pkgver $pkgarch" = "$want" ] ||
        refuse "package $f declares '$pkgname $pkgver $pkgarch' in its control file, but the inventory says '$want'"
      {
        printf '%s\n' "$fields"
        printf 'Filename: %s\nSize: %s\nSHA256: %s\n\n' "$rel" "$(size "$site/$f")" "$(sha "$site/$f")"
      } >>"$pk"
    done < <("$JQ" -r --arg id "$id" --arg a "$arch" \
      '[.packages[] | select(.repository == $id and (.arch == $a or .arch == "all"))] | sort_by(.name, .version, .revision, .file) | .[].file' "$inv")
    gzip -9 -n -c "$pk" >"$pk.gz"
    for idx in Packages Packages.gz; do
      h="$(sha "$site/$dists/$bdir/$idx")"
      cp "$site/$dists/$bdir/$idx" "$site/$dists/$bdir/by-hash/SHA256/$h"
      record immutable "$dists/$bdir/by-hash/SHA256/$h" "application/octet-stream"
      printf ' %s %16s %s\n' "$h" "$(size "$site/$dists/$bdir/$idx")" "$bdir/$idx" >>"$shaidx"
      # The index itself under its plain name is an ENTRYPOINT convenience copy;
      # by-hash clients never read it.
      record entrypoint "$dists/$bdir/$idx" "$([ "$idx" = Packages.gz ] && echo application/gzip || echo text/plain)"
    done
  done
  {
    printf 'Origin: %s\nLabel: %s\nSuite: %s\nCodename: %s\nDate: %s\n' "$surface" "$product" "$suite" "$suite" "$rfc2822"
    printf 'Architectures: %s\nComponents: %s\nAcquire-By-Hash: yes\n' "$("$JQ" -r '.architectures | join(" ")' <<<"$repo")" "$comp"
    printf 'Description: %s packages (%s)\nSHA256:\n' "$product" "$("$JQ" -r .channel <<<"$repo")"
    cat "$shaidx"
  } >"$site/$dists/Release"
  record entrypoint "$dists/Release" "text/plain"
  # Client configuration: deb822 with a scoped Signed-By. Never trusted=yes.
  cfg="$(pkgrepo_apt_sources_path "$inv" "$id")"
  pkgrepo_apt_sources "$inv" "$id" >"$site/$cfg"
  record entrypoint "$cfg" "text/plain"
done < <("$JQ" -c '.repositories[] | select(.format == "apt")' "$inv")

# ── DNF repositories ───────────────────────────────────────────────────────
while IFS= read -r repo; do
  id="$("$JQ" -r .id <<<"$repo")"
  rpath="$("$JQ" -r .path <<<"$repo")"
  arch="$("$JQ" -r .arch <<<"$repo")"
  while IFS=$'\t' read -r f want; do
    got="$(rpm -qp --nosignature --qf '%{NAME} %{VERSION}-%{RELEASE} %{ARCH}' "$site/$f" 2>/dev/null)" || refuse "rpm cannot read $f"
    [ "$got" = "$want" ] || refuse "package $f is '$got' in its header, but the inventory says '$want'"
  done < <("$JQ" -r --arg id "$id" '.packages[] | select(.repository == $id) | [.file, "\(.name) \(.version)-\(.revision) \(.arch)"] | @tsv' "$inv")
  # createrepo_c records each package's file mtime in primary metadata; pin it
  # to the generation timestamp so regeneration is byte-identical.
  while IFS= read -r f; do touch -d "@$ts" "$site/$f" 2>/dev/null || touch -t "$(date -u -r "$ts" +%Y%m%d%H%M.%S)" "$site/$f"; done \
    < <("$JQ" -r --arg id "$id" '.packages[] | select(.repository == $id) | .file' "$inv")
  createrepo_c --quiet --no-database --unique-md-filenames --general-compress-type=gz \
    --revision "$ts" --set-timestamp-to-revision "$site/$rpath" >/dev/null 2>"$stage/createrepo.err" || {
    sed 's/^/pkgrepo-generate: createrepo_c: /' "$stage/createrepo.err" >&2
    die "createrepo_c failed for $id"
  }
  for md in "$site/$rpath"repodata/*; do
    rel="${md#"$site/"}"
    case "$(basename "$md")" in
      repomd.xml) record entrypoint "$rel" "application/xml" ;;
      *) record immutable "$rel" "$(case "$md" in *.gz) echo application/gzip ;; *) echo application/xml ;; esac)" ;;
    esac
  done
  [ "$arch" != "" ] || die "repository $id has no arch"
done < <("$JQ" -c '.repositories[] | select(.format == "dnf")' "$inv")

# One .repo per product, with $basearch, when its DNF repositories are laid out
# as <prefix><arch>/. Anything else would need per-arch configuration a user
# cannot pick correctly, so it is refused.
while IFS= read -r product; do
  body="$(pkgrepo_dnf_repo "$inv" "$product")" || refuse "$body"
  cfg="$(pkgrepo_dnf_repo_path "$inv" "$product")"
  printf '%s\n' "$body" >"$site/$cfg"
  record entrypoint "$cfg" "text/plain"
done < <("$JQ" -r '[.repositories[] | select(.format == "dnf") | .product] | unique | .[]' "$inv")

# Packages are recorded last so their manifest order is stable and grouped.
"$JQ" -r '.packages[].file' "$inv" | while IFS= read -r f; do
  case "$f" in *.deb) record immutable "$f" "application/vnd.debian.binary-package" ;; *) record immutable "$f" "application/x-rpm" ;; esac
done

# ── the generation manifest ────────────────────────────────────────────────
inv_sha="$(sha "$inv")"
pins_sha="$(sort "$pins" | sha256sum | cut -d' ' -f1)"
# A generation is identified by its inventory and the content of every object
# (lib: pkgrepo_generation_id) — the verifier recomputes it from the tree and
# the signed metadata, so the id is never taken on the manifest's word. Replay
# compares inventories (PR-11); resume regenerates the SAME id and bytes.
gen_id="$(pkgrepo_generation_id "$inv_sha" "$objects")"
"$JQ" -s --arg s "$surface" --arg inv "$inv_sha" --arg g "$gen_id" --argjson ts "$ts" --arg pins "$pins_sha" \
  '{schema: "blessed/package-repository-generation/v1", surface_id: $s,
    generation_id: $g, inventory_sha256: $inv, timestamp: $ts, tool_pins_sha256: $pins,
    signed: false, objects: (sort_by(.path))}' "$objects" >"$site/.generation.json"
mkdir -p "$(dirname "$out")"
mv "$site" "$out" || die "cannot move the generation into place"
printf 'pkgrepo-generate: OK %s — generation %s, %s objects (unsigned)\n' "$out" "${gen_id:0:12}" "$("$JQ" '.objects | length' "$out/.generation.json")"
