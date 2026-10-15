#!/usr/bin/env bash
# app-token-prepare.sh — verify a PR-opening GitHub App against its role's
# declaration, then hand its key to the token-minting step only
# (standards/releases/release-pr.md RP-6/RP-7).
#
#   app-token-prepare.sh --role release-pr|admission-pr --env-prefix PREFIX
#
# Reads <PREFIX>_APP_CLIENT_ID and <PREFIX>_APP_PRIVATE_KEY (the key stored by
# the BWS bootstrapper as single-line base64, type github_app_private_key) from
# the job environment the secret loader wrote. Then, with the key and before any
# installation token for real work exists, it proves the App IS what the role
# declares, from GitHub's own records:
#
#   · the App's permissions are exactly the role's (references/github-app-roles.json)
#     and it subscribes to no webhook events;
#   · it has exactly ONE installation, on this repository's owner, with
#     "selected" repositories and exactly the role's permissions;
#   · that installation reaches exactly ONE repository: this one (a short-lived,
#     metadata:read token lists them, and is revoked at once).
#
# A token minted later is restricted to this repository anyway; this check is
# about the KEY, which could mint for every repository the App is installed
# on. An App installed on a second repository, or granted a wider permission,
# fails here — every run — before anything is minted.
#
# Only then is the PEM written to $GITHUB_ENV as <PREFIX>_APP_PEM, each line
# masked, and the base64 form cleared; the job's next steps mint the token and
# clear the PEM (key-env.sh). No key or token is ever placed in a command
# argument. Exit 0 verified · 1 refused · 2 usage/tooling.
# shellcheck disable=SC2016 # jq programs
set -euo pipefail
set +x
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
refuse() {
  echo "::error::app-token-prepare: $1" >&2
  exit 1
}
die() {
  echo "app-token-prepare: $1" >&2
  exit 2
}
role="" prefix=""
while [ $# -gt 0 ]; do
  case "$1" in
    --role) role="${2:-}" ;;
    --env-prefix) prefix="${2:-}" ;;
    *) die "unknown argument: $1" ;;
  esac
  shift 2 || die "missing value for $1"
done
want="$(jq -c --arg r "$role" '.roles[$r].permissions // empty' "$HERE/references/github-app-roles.json")"
[ -n "$want" ] || die "unknown role: $role"
[[ "$prefix" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "--env-prefix must be an upper-case name"
for v in GITHUB_ENV GITHUB_REPOSITORY GITHUB_REPOSITORY_OWNER RUNNER_TEMP; do
  [ -n "${!v:-}" ] || die "$v is not set (this runs in a GitHub Actions job)"
done
for t in jq curl openssl base64 od; do command -v "$t" >/dev/null 2>&1 || die "required tool not found: $t"; done
API="${GITHUB_API_URL:-https://api.github.com}"
id_var="${prefix}_APP_CLIENT_ID" key_var="${prefix}_APP_PRIVATE_KEY"
cid="${!id_var:-}"
b64="${!key_var:-}"
unset "$key_var"
[ -n "$cid" ] || refuse "$id_var is empty: the $role domain did not load"
[[ "$cid" =~ ^[A-Za-z0-9.]+$ ]] || refuse "$id_var is not a GitHub App Client ID"
[ -n "$b64" ] || refuse "$key_var is empty: the $role domain did not load"

umask 077
tmp="$(mktemp -d "$RUNNER_TEMP/app.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
printf '%s' "$b64" | base64 -d >"$tmp/key.pem" 2>/dev/null ||
  refuse "$key_var is not base64 (store the key base64-encoded on one line; bootstrapper type github_app_private_key)"
b64=""
head -1 "$tmp/key.pem" | grep -Eq '^-----BEGIN (RSA )?PRIVATE KEY-----$' ||
  refuse "the decoded $key_var is not a PEM private key"
awk 1 "$tmp/key.pem" | while IFS= read -r line; do
  [ -z "$line" ] || echo "::add-mask::$line"
done

# ── a JWT for the App (RS256), never on argv ───────────────────────────────
b64url() { base64 | tr -d '\n=' | tr '+/' '-_'; }
now="$(date +%s)"
hdr="$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)"
pl="$(jq -cn --arg iss "$cid" --argjson n "$now" '{iat: ($n - 60), exp: ($n + 300), iss: $iss}' | b64url)"
sig="$(printf '%s.%s' "$hdr" "$pl" | openssl dgst -sha256 -sign "$tmp/key.pem" -binary | b64url)" ||
  refuse "the key could not sign (not a usable RSA key)"
echo "::add-mask::$sig"
printf 'Authorization: Bearer %s.%s.%s\n' "$hdr" "$pl" "$sig" >"$tmp/jwt.h"
sig=""
api() { # header-file method path [body] → body on stdout; fails on HTTP >= 400
  local a=(-fsS -X "$2" -H @"$1" -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28')
  if [ -n "${4:-}" ]; then a+=(-H 'Content-Type: application/json' --data "$4"); fi
  curl "${a[@]}" "$API$3"
}

app="$(api "$tmp/jwt.h" GET /app)" || refuse "GitHub refused the App's JWT (wrong Client ID or key?)"
slug="$(jq -r .slug <<<"$app")"
jq -e --argjson w "$want" '.permissions == $w' <<<"$app" >/dev/null ||
  refuse "App $slug has permissions $(jq -c .permissions <<<"$app"); the $role role is exactly $want"
jq -e '(.events // []) | length == 0' <<<"$app" >/dev/null ||
  refuse "App $slug subscribes to webhook events $(jq -c .events <<<"$app"); the $role role subscribes to none"

inst="$(api "$tmp/jwt.h" GET '/app/installations?per_page=100')" || refuse "cannot list App $slug's installations"
jq -e 'length == 1' <<<"$inst" >/dev/null || refuse "App $slug has $(jq length <<<"$inst") installations; it must be installed exactly once"
jq -e --arg o "$GITHUB_REPOSITORY_OWNER" --argjson w "$want" '.[0] | .account.login == $o
  and .repository_selection == "selected" and .permissions == $w and .suspended_at == null' <<<"$inst" >/dev/null ||
  refuse "App $slug's installation is not on $GITHUB_REPOSITORY_OWNER with selected repositories and exactly the role's permissions: $(jq -c '.[0] | {account: .account.login, repository_selection, permissions, suspended_at}' <<<"$inst")"
iid="$(jq -r '.[0].id' <<<"$inst")"
[[ "$iid" =~ ^[0-9]+$ ]] || refuse "unexpected installation id"
tok="$(api "$tmp/jwt.h" POST "/app/installations/$iid/access_tokens" '{"permissions":{"metadata":"read"}}' | jq -r '.token // empty')" ||
  refuse "cannot mint a metadata:read token to list the installation's repositories"
[ -n "$tok" ] || refuse "no metadata:read token"
echo "::add-mask::$tok"
printf 'Authorization: Bearer %s\n' "$tok" >"$tmp/tok.h"
tok=""
repos="$(api "$tmp/tok.h" GET '/installation/repositories?per_page=100')" || refuse "cannot list the installation's repositories"
api "$tmp/tok.h" DELETE /installation/token >/dev/null 2>&1 || echo "::warning::app-token-prepare: could not revoke the listing token (it expires within the hour)"
rm -f "$tmp/tok.h" "$tmp/jwt.h"
jq -e --arg r "$GITHUB_REPOSITORY" '.total_count == 1 and .repositories[0].full_name == $r' <<<"$repos" >/dev/null ||
  refuse "App $slug is installed on $(jq -r '[.repositories[].full_name] | join(", ")' <<<"$repos"); it must be installed on $GITHUB_REPOSITORY only"
echo "app-token-prepare: App $slug — permissions $want, no events, one installation, on $GITHUB_REPOSITORY only"

# ── hand the PEM to the minting step only ─────────────────────────────────
d="PEM_$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
{
  printf '%s_APP_PEM<<%s\n' "$prefix" "$d"
  awk 1 "$tmp/key.pem"
  printf '%s\n' "$d"
  printf '%s=\n' "$key_var"
} >>"$GITHUB_ENV"
