# shellcheck shell=sh
# lifecycle-lib.sh — assertions a product's LP-6 lifecycle test sources INSIDE
# its system under test (POSIX sh; no bash required). The LP-7 installation
# ownership record is checked the same way by every product:
#
#   . /path/to/scripts/release/lifecycle-lib.sh
#   lifecycle_owner_is deb /usr/lib/corpus-cx/install-owner     # after install, after upgrade
#   lifecycle_owner_gone /usr/lib/corpus-cx/install-owner       # after remove/erase
#   lifecycle_owner_claimed deb corpus-cx /usr/lib/corpus-cx/install-owner
#
# The record (standards/releases/linux-packaging.md LP-7) is a file the
# PACKAGE installs, so the native database owns it, whose content is exactly
# the installation mechanism — `rpm`, `deb` or `tarball` — and nothing about
# who installed it. Each assertion prints one line and exits non-zero (in a
# subshell: returns non-zero) on failure.

lifecycle_fail() {
  printf 'lifecycle: ✗ %s\n' "$1" >&2
  return 1
}

# lifecycle_owner_is MECHANISM PATH — the record exists and says exactly MECHANISM.
lifecycle_owner_is() {
  case "$1" in rpm | deb | tarball) ;; *) lifecycle_fail "unknown installation mechanism '$1'" || return 1 ;; esac
  [ -f "$2" ] || {
    lifecycle_fail "no installation ownership record at $2"
    return 1
  }
  _lo="$(cat "$2")"
  [ "$_lo" = "$1" ] || {
    lifecycle_fail "$2 records '$_lo', not '$1'"
    return 1
  }
  printf 'lifecycle: ✓ %s records owner %s\n' "$2" "$1"
}

# lifecycle_owner_claimed MECHANISM PACKAGE PATH — the native database owns the
# record (so it is part of the package, not written by a script).
lifecycle_owner_claimed() {
  case "$1" in
    deb) dpkg-query -S "$3" 2>/dev/null | grep -q "^$2[:a-z0-9]*: $3\$" ;;
    rpm) rpm -qf --qf '%{NAME}\n' "$3" 2>/dev/null | grep -qx "$2" ;;
    *) false ;;
  esac || {
    lifecycle_fail "$3 is not owned by package $2 in the $1 database"
    return 1
  }
  printf 'lifecycle: ✓ %s is owned by %s (%s database)\n' "$3" "$2" "$1"
}

# lifecycle_owner_gone PATH — removal took the record with it.
lifecycle_owner_gone() {
  [ ! -e "$1" ] || {
    lifecycle_fail "$1 survived removal"
    return 1
  }
  printf 'lifecycle: ✓ %s removed with the package\n' "$1"
}

# lifecycle_evidence FILE ROW STEP... — write the evidence the shared gate
# (release-lifecycle.sh) requires: the steps this test PROVED for ROW. Call it
# last, only after every assertion passed.
lifecycle_evidence() {
  _lf="$1" _lr="$2"
  shift 2
  _ls=""
  for _s in "$@"; do _ls="$_ls${_ls:+, }\"$_s\""; done
  printf '{"row": "%s", "proven": [%s]}\n' "$_lr" "$_ls" >"$_lf"
}
