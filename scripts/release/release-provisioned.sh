#!/usr/bin/env bash
# release-provisioned.sh — refuse, before any secret is loaded, while a domain
# is unprovisioned (standards/releases/release-pr.md RP-7).
#
#   release-provisioned.sh --loader .github/actions/load-secrets/action.yml NAME...
#
# Each NAME must be mapped in the loader exactly once, by a BWS secret UUID that
# is not the bootstrapper's all-zero placeholder (00000000-0000-0000-0000-…).
# Exit 0 provisioned · 1 refused · 2 usage.
set -euo pipefail
refuse() {
  echo "::error::release-provisioned: $1" >&2
  exit 1
}
if [ "${1:-}" != --loader ] || [ -z "${2:-}" ] || [ $# -lt 3 ]; then
  echo "usage: release-provisioned.sh --loader FILE NAME..." >&2
  exit 2
fi
loader="$2"
shift 2
[ -f "$loader" ] || refuse "no secret loader at $loader"
uuid='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
for name in "$@"; do
  [[ "$name" =~ ^[A-Z][A-Z0-9_]*$ ]] || {
    echo "release-provisioned: invalid name: $name" >&2
    exit 2
  }
  n="$(grep -Ec "^[[:space:]]*${uuid}[[:space:]]*>[[:space:]]*${name}[[:space:]]*\$" "$loader" || true)"
  [ "$n" = 1 ] || refuse "the loader maps $name $n time(s), not exactly once"
  if grep -Eq "^[[:space:]]*00000000-0000-0000-0000-[0-9a-fA-F]{12}[[:space:]]*>[[:space:]]*${name}[[:space:]]*\$" "$loader"; then
    refuse "$name is not provisioned (the loader holds the bootstrapper's placeholder UUID): nothing is loaded and nothing proceeds"
  fi
done
echo "release-provisioned: $* mapped to provisioned BWS secrets"
