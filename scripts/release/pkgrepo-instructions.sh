#!/usr/bin/env bash
# pkgrepo-instructions.sh — the public setup and install instructions of a
# package-repository-surface, rendered from ONE source: the committed
# inventory, through the same helpers the generator uses for the client
# configuration it serves (lib/pkgrepo-lib.sh). So the landing page, the
# README and the served .sources/.repo files cannot disagree on a URL, a
# keyring path or a fingerprint.
#
#   pkgrepo-instructions.sh --inventory INV [--html FILE] [--readme FILE] [--check]
#
#   --html FILE    the landing page (served at the surface root, `/`)
#   --readme FILE  the block between the markers
#                    <!-- BEGIN pkgrepo-instructions … -->
#                    <!-- END pkgrepo-instructions -->
#                  is replaced (the markers must exist exactly once)
#   --check        write nothing; exit 1 if either file differs from its
#                  rendering (required CI: the instructions cannot drift)
#
# Only what is stable across admissions is rendered — keys, fingerprints,
# configuration URLs, package names, supported architectures and distro
# releases — never versions, so admitting a release does not change them.
#
# Exit: 0 written / up to date · 1 differs (--check) · 2 usage.
# shellcheck disable=SC2016 # jq programs
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/lib/pkgrepo-lib.sh
. "$HERE/lib/pkgrepo-lib.sh"
JQ="${JQ_BIN:-jq}"
die() {
  printf 'pkgrepo-instructions: %s\n' "$1" >&2
  exit 2
}
inv="" html="" readme="" check=0
while [ $# -gt 0 ]; do
  case "$1" in
    --inventory) inv="${2:-}" && shift 2 ;;
    --html) html="${2:-}" && shift 2 ;;
    --readme) readme="${2:-}" && shift 2 ;;
    --check) check=1 && shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -f "$inv" ] || die "--inventory INV is required"
[ -n "$html" ] || [ -n "$readme" ] || die "give --html and/or --readme"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

base="$("$JQ" -r .public_base_url "$inv")"
host="${base#https://}"
host="${host%%/*}"
spaced() { sed -E 's/(.{4})/\1 /g; s/ $//' <<<"$1"; }
distros() { "$JQ" -r '[.targets[] | (if .distro == "debian" then "Debian" elif .distro == "fedora" then "Fedora" elif .distro == "raspberry-pi-os" then "Raspberry Pi OS" else .distro end) + " " + .release] | join(", ")' <<<"$1"; }
keyring="$(pkgrepo_apt_sources "$inv" "$("$JQ" -r '[.repositories[] | select(.format == "apt")][0].id // empty' "$inv")" 2>/dev/null | sed -n 's/^Signed-By: //p')"

# ── the model: one list of sections, rendered twice ───────────────────────
{
  printf 'KEYS\n'
  printf 'KEY\t%s%s\tAPT InRelease / Release.gpg, DNF repomd.xml.asc (repository metadata)\t%s\n' "$base" \
    "$("$JQ" -r .repository_key.public_key_path "$inv")" "$("$JQ" -r .repository_key.fingerprint "$inv")"
  while IFS=$'\t' read -r p f prod; do
    printf 'KEY\t%s%s\t%s RPM packages\t%s\n' "$base" "$p" "${prod:-$f}" "$f"
  done < <("$JQ" -r '.repositories as $r | .producer_keys[] | . as $k
    | [.public_key_path, .fingerprint, ([$r[] | select(.producer_repo == $k.producer_repo) | .product] | unique | join(", "))] | @tsv' "$inv")
  while IFS= read -r prod; do
    printf 'PRODUCT\t%s\n' "$prod"
    while IFS= read -r repo; do
      id="$("$JQ" -r .id <<<"$repo")"
      names="$("$JQ" -r --arg id "$id" '[.packages[] | select(.repository == $id) | .name] | unique | join(" ")' "$inv")"
      if [ "$("$JQ" -r .format <<<"$repo")" = apt ]; then
        printf 'APT\t%s\t%s\t%s\t%s\n' "$base$(pkgrepo_apt_sources_path "$inv" "$id")" "$names" \
          "$("$JQ" -r '.architectures | join(", ")' <<<"$repo")" "$(distros "$repo")"
      fi
    done < <("$JQ" -c --arg p "$prod" '.repositories[] | select(.product == $p)' "$inv")
    dnf="$("$JQ" -c --arg p "$prod" '[.repositories[] | select(.product == $p and .format == "dnf")]' "$inv")"
    if [ "$("$JQ" length <<<"$dnf")" -gt 0 ]; then
      rp="$(pkgrepo_dnf_repo_path "$inv" "$prod")" || die "$prod: its DNF repositories are not laid out as <prefix><arch>/"
      ids="$("$JQ" -r '[.[].id]' <<<"$dnf")"
      printf 'DNF\t%s\t%s\t%s\t%s\n' "$base$rp" \
        "$("$JQ" -r --argjson ids "$ids" '[.packages[] | select(.repository | IN($ids[])) | .name] | unique | join(" ")' "$inv")" \
        "$("$JQ" -r '[.[].arch] | join(", ")' <<<"$dnf")" "$(distros "$("$JQ" -c '.[0]' <<<"$dnf")")"
    fi
  done < <("$JQ" -r '[.repositories[].product] | unique | .[]' "$inv")
} >"$work/model"
grep -q '^APT\|^DNF' "$work/model" || die "the inventory declares no repository"
rkey_url="$base$("$JQ" -r .repository_key.public_key_path "$inv")"
rkey_fpr="$("$JQ" -r .repository_key.fingerprint "$inv")"

# ── Markdown (README block) ───────────────────────────────────────────────
md() {
  printf '<!-- BEGIN pkgrepo-instructions (generated from inventory/inventory.json by scripts/release/pkgrepo-instructions.sh; do not edit) -->\n'
  printf 'Signed APT and DNF repositories at <%s>. Verify each key before trusting it; each\nfile holds exactly one primary key.\n\n' "$base"
  printf '| Key | Signs | Fingerprint |\n|---|---|---|\n'
  while IFS=$'\t' read -r t a b c; do
    [ "$t" = KEY ] || continue
    printf '| [`%s`](%s) | %s | `%s` |\n' "${a#"$base"}" "$a" "$b" "$(spaced "$c")"
  done <"$work/model"
  while IFS=$'\t' read -r t a b c d; do
    case "$t" in
      PRODUCT) printf '\n### %s\n' "$a" ;;
      APT)
        printf '\n**APT** — %s (%s):\n\n```sh\n' "$d" "$c"
        printf 'sudo install -d -m 0755 /etc/apt/keyrings\n'
        printf 'sudo curl -fsSLo %s %s\n' "$keyring" "$rkey_url"
        printf 'gpg --show-keys %s   # expect %s\n' "$keyring" "$rkey_fpr"
        printf 'sudo curl -fsSLo /etc/apt/sources.list.d/%s %s\n' "$(basename "$a")" "$a"
        printf 'sudo apt update && sudo apt install %s\n```\n' "$b"
        ;;
      DNF)
        printf '\n**DNF** — %s (%s):\n\n```sh\n' "$d" "$c"
        printf 'sudo curl -fsSLo /etc/yum.repos.d/%s %s\n' "$(basename "$a")" "$a"
        printf 'sudo dnf install %s   # dnf shows each key'"'"'s fingerprint before trusting it\n```\n' "$b"
        ;;
    esac
  done <"$work/model"
  printf '\nPackage and repository metadata signatures are both verified (DNF `gpgcheck=1`,\n`repo_gpgcheck=1`; APT `Signed-By`). Upgrades arrive through `apt upgrade` /\n`dnf upgrade`.\n'
  printf '<!-- END pkgrepo-instructions -->\n'
}

# ── HTML (landing page) ───────────────────────────────────────────────────
esc() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }
page() {
  printf '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
  printf '<title>%s</title>\n' "$(esc "$host")"
  printf '<style>\n:root{color-scheme:light dark;--fg:#1b1b1b;--bg:#fff;--mute:#5b5b5b;--code:#f3f3f3;--rule:#ddd}\n'
  printf '@media (prefers-color-scheme:dark){:root{--fg:#e8e8e8;--bg:#141414;--mute:#a8a8a8;--code:#222;--rule:#333}}\n'
  printf 'body{margin:0 auto;max-width:52rem;padding:1.5rem 1rem 3rem;font:16px/1.55 system-ui,sans-serif;color:var(--fg);background:var(--bg)}\n'
  printf 'pre{background:var(--code);padding:.75rem;overflow-x:auto;border-radius:6px}code{font-family:ui-monospace,monospace;font-size:.9em}\n'
  printf 'table{border-collapse:collapse;width:100%%;display:block;overflow-x:auto}th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid var(--rule);vertical-align:top}\n'
  printf 'p.note{color:var(--mute)}\n</style>\n</head>\n<body>\n'
  printf '<h1>%s</h1>\n' "$(esc "$host")"
  printf '<p>Signed APT and DNF repositories. Verify each key before trusting it; each file holds exactly one primary key.</p>\n'
  printf '<h2>Keys</h2>\n<table>\n<thead><tr><th>Key</th><th>Signs</th><th>Fingerprint</th></tr></thead>\n<tbody>\n'
  while IFS=$'\t' read -r t a b c; do
    [ "$t" = KEY ] || continue
    printf '<tr><td><a href="%s">%s</a></td><td>%s</td><td><code>%s</code></td></tr>\n' "$(esc "$a")" "$(esc "${a#"$base"}")" "$(esc "$b")" "$(spaced "$c")"
  done <"$work/model"
  printf '</tbody>\n</table>\n'
  while IFS=$'\t' read -r t a b c d; do
    case "$t" in
      PRODUCT) printf '<h2>%s</h2>\n' "$(esc "$a")" ;;
      APT)
        printf '<p><strong>APT</strong> — %s (%s):</p>\n<pre><code>' "$(esc "$d")" "$(esc "$c")"
        printf '%s' "$(esc "sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSLo $keyring $rkey_url
gpg --show-keys $keyring   # expect $rkey_fpr
sudo curl -fsSLo /etc/apt/sources.list.d/$(basename "$a") $a
sudo apt update && sudo apt install $b")"
        printf '</code></pre>\n'
        ;;
      DNF)
        printf '<p><strong>DNF</strong> — %s (%s):</p>\n<pre><code>' "$(esc "$d")" "$(esc "$c")"
        printf '%s' "$(esc "sudo curl -fsSLo /etc/yum.repos.d/$(basename "$a") $a
sudo dnf install $b   # dnf shows each key's fingerprint before trusting it")"
        printf '</code></pre>\n'
        ;;
    esac
  done <"$work/model"
  printf '<p class="note">Package and repository metadata signatures are both verified (DNF <code>gpgcheck=1</code>, <code>repo_gpgcheck=1</code>; APT <code>Signed-By</code>). Upgrades arrive through <code>apt upgrade</code> / <code>dnf upgrade</code>.</p>\n'
  printf '</body>\n</html>\n'
}

rc=0
emit() { # rendered-file dest label
  if [ "$check" = 1 ]; then
    if cmp -s "$1" "$2"; then
      printf 'pkgrepo-instructions: %s is the rendering of %s\n' "$3" "$inv" >&2
    else
      printf 'pkgrepo-instructions: DIFFERS — %s is not the rendering of %s (run: bash scripts/release/pkgrepo-instructions.sh)\n' "$3" "$inv" >&2
      diff -u "$2" "$1" 2>/dev/null | head -30 >&2 || true
      rc=1
    fi
  else
    mkdir -p "$(dirname "$2")"
    cp "$1" "$2"
    printf 'pkgrepo-instructions: wrote %s\n' "$3" >&2
  fi
}
if [ -n "$html" ]; then
  page >"$work/index.html"
  emit "$work/index.html" "$html" "$html"
fi
if [ -n "$readme" ]; then
  [ -f "$readme" ] || die "$readme does not exist"
  if [ "$(grep -c '^<!-- BEGIN pkgrepo-instructions' "$readme")" != 1 ] || [ "$(grep -c '^<!-- END pkgrepo-instructions -->$' "$readme")" != 1 ]; then
    die "$readme must contain the BEGIN/END pkgrepo-instructions markers exactly once"
  fi
  md >"$work/block"
  awk -v blk="$work/block" '
    /^<!-- BEGIN pkgrepo-instructions/ { while ((getline l < blk) > 0) print l; skip = 1; next }
    /^<!-- END pkgrepo-instructions -->$/ { skip = 0; next }
    !skip' "$readme" >"$work/readme"
  emit "$work/readme" "$readme" "$readme"
fi
exit "$rc"
