#!/usr/bin/env bash
# verify-provisioning.sh — read back a repository's live GitHub configuration
# and compare it with its declarations (standards/releases/release-pr.md
# RP-5/RP-6/RP-7). Read-only; run by an operator with `gh` authenticated as a
# repository admin (Environment secrets and rulesets are not readable by the
# workflow token).
#
#   verify-provisioning.sh --repo OWNER/REPO [--root DIR] --required-check NAME...
#
# For each role domain the managed workflows use (release-pr, rpm-signing,
# release-signing; admission-pr, candidate-ingest):
#   · the Environment exists, deploys from selected branches only, and its one
#     branch policy is main (no tag policy);
#   · its only secret is BWS_ACCESS_TOKEN and it has no variables.
# For the repository:
#   · an active ruleset on the default branch requires a PR, blocks deletion and
#     force-push, requires the named checks (strict), and has NO bypass actor;
#   · "Allow GitHub Actions to create and approve pull requests" is OFF.
#
# What this cannot read, and where it is proven instead: a GitHub App's
# installation scope and permissions (app-token-prepare.sh checks them from
# GitHub's records on every run that uses the key); a BWS machine account's
# project grants (the operator confirms them in the vault: one project, Can
# read). Exit 0 conforms · 1 does not · 2 usage.
# shellcheck disable=SC2016 # jq programs
set -uo pipefail
repo="" root="." checks=()
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      repo="${2:-}"
      shift 2
      ;;
    --root)
      root="${2:-}"
      shift 2
      ;;
    --required-check)
      checks+=("${2:-}")
      shift 2
      ;;
    *)
      echo "verify-provisioning: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done
if ! [[ "$repo" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || [ "${#checks[@]}" -eq 0 ]; then
  echo "usage: verify-provisioning.sh --repo OWNER/REPO [--root DIR] --required-check NAME..." >&2
  exit 2
fi
fails=0
bad() {
  printf '  ✗ %s\n' "$1" >&2
  fails=$((fails + 1))
}
good() { printf '  ✓ %s\n' "$1"; }
domains=""
[ -f "$root/.github/workflows/release.yml" ] && domains="release-pr release-signing"
grep -q 'environment: rpm-signing' "$root/.github/workflows/release.yml" 2>/dev/null && domains="$domains rpm-signing"
[ -f "$root/.github/workflows/admit-candidate.yml" ] && domains="$domains admission-pr candidate-ingest"
[ -n "$domains" ] || {
  echo "verify-provisioning: no managed workflow in $root" >&2
  exit 2
}

echo "verify-provisioning: $repo Environments"
for d in $domains; do
  before=$fails
  env="$(gh api "repos/$repo/environments/$d" 2>/dev/null)" || {
    bad "Environment $d does not exist"
    continue
  }
  jq -e '.deployment_branch_policy == {"protected_branches": false, "custom_branch_policies": true}' <<<"$env" >/dev/null ||
    bad "Environment $d does not deploy from selected branches only: $(jq -c .deployment_branch_policy <<<"$env")"
  # Every read must be OBSERVED: a failed read is a finding, never a default.
  if ! pols="$(gh api "repos/$repo/environments/$d/deployment-branch-policies" --jq '[.branch_policies[] | "\(.type // "branch"):\(.name)"] | sort | join(",")')"; then
    bad "Environment $d: cannot read its deployment branch policies"
  elif [ "$pols" != "branch:main" ]; then
    bad "Environment $d deploys from [$pols], not branch main only"
  fi
  if ! secs="$(gh api "repos/$repo/environments/$d/secrets" --jq '[.secrets[].name] | sort | join(",")')"; then
    bad "Environment $d: cannot read its secrets"
  elif [ "$secs" != BWS_ACCESS_TOKEN ]; then
    bad "Environment $d holds secrets [$secs], not exactly BWS_ACCESS_TOKEN"
  fi
  if ! vars="$(gh api "repos/$repo/environments/$d/variables" --jq '.total_count')" || ! [[ "$vars" =~ ^[0-9]+$ ]]; then
    bad "Environment $d: cannot read its variables"
  elif [ "$vars" != 0 ]; then
    bad "Environment $d has $vars variable(s)"
  fi
  [ "$fails" != "$before" ] || good "Environment $d: branch main only; only BWS_ACCESS_TOKEN"
done

echo "verify-provisioning: $repo default-branch protection"
if ! rs="$(gh api "repos/$repo/rules/branches/main")"; then
  bad "cannot read main's effective rules"
  rs='[]'
fi
for t in pull_request deletion non_fast_forward required_status_checks; do
  jq -e --arg t "$t" 'any(.[]; .type == $t)' <<<"$rs" >/dev/null || bad "main has no active $t rule"
done
req="$(jq -r '[.[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context] | .[]' <<<"$rs")"
for c in "${checks[@]}"; do grep -Fxq -- "$c" <<<"$req" || bad "main does not require the check '$c'"; done
jq -e 'all(.[] | select(.type == "required_status_checks"); .parameters.strict_required_status_checks_policy == true)' <<<"$rs" >/dev/null ||
  bad "main's required checks are not strict (up to date with main)"
for id in $(jq -r '[.[].ruleset_id] | unique | .[]' <<<"$rs"); do
  if ! n="$(gh api "repos/$repo/rulesets/$id" --jq '.bypass_actors | length')"; then
    bad "cannot read ruleset $id"
  elif [ "$n" != 0 ]; then
    bad "ruleset $id has $n bypass actor(s)"
  fi
done
good "main: PR, required checks [$(tr '\n' ' ' <<<"$req")], no deletion or force-push"
if ! can="$(gh api "repos/$repo/actions/permissions/workflow" --jq .can_approve_pull_request_reviews)"; then
  bad "cannot read the Actions workflow-permission setting"
elif [ "$can" != false ]; then
  bad "Actions may create and approve pull requests ($can); RP-6 forbids it"
fi

if [ "$fails" -gt 0 ]; then
  echo "verify-provisioning: $fails finding(s)" >&2
  exit 1
fi
echo "verify-provisioning: $repo conforms"
