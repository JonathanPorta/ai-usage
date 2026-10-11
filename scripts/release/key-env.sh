#!/usr/bin/env bash
# key-env.sh — remove key material from a GitHub Actions job's environment.
#
#   key-env.sh clear NAME...         every LATER step sees NAME empty
#   key-env.sh assert-clear NAME...  fail if this step still sees NAME set
#
# The secret loader exports keys through $GITHUB_ENV, which reaches every later
# step of the job; `clear` overrides each with an empty value (a later
# assignment wins, including over a multi-line value). Run `clear` with
# `if: always()`, then `assert-clear` in the next step. Exit 0 · 1 still set · 2 usage.
set -euo pipefail
mode="${1:-}"
shift || true
[ $# -ge 1 ] || {
  echo "usage: key-env.sh clear|assert-clear NAME..." >&2
  exit 2
}
for n in "$@"; do
  [[ "$n" =~ ^[A-Z][A-Z0-9_]*$ ]] || {
    echo "key-env: invalid name: $n" >&2
    exit 2
  }
done
case "$mode" in
  clear)
    : "${GITHUB_ENV:?key-env: GITHUB_ENV is not set}"
    for n in "$@"; do printf '%s=\n' "$n" >>"$GITHUB_ENV"; done
    echo "key-env: cleared $* for every later step"
    ;;
  assert-clear)
    for n in "$@"; do
      if [ -n "${!n:-}" ]; then
        echo "::error::key-env: $n is still set in the job environment"
        exit 1
      fi
    done
    echo "key-env: $* absent from this and every later step"
    ;;
  *)
    echo "usage: key-env.sh clear|assert-clear NAME..." >&2
    exit 2
    ;;
esac
