#!/usr/bin/env bash
# bootstrap-domain.sh — bootstrap ONE authority domain into exactly the BWS
# project blessed.yml declares for it (secrets.bwsm-authority-domains@1;
# releases.release-pr@1 RP-7). The one provisioning entry point for every
# role domain, in every consumer.
#
#   bash scripts/release/bootstrap-domain.sh [--dry-run] DOMAIN [-- EXTRA BOOTSTRAP ARGS]
#   (canonical Makefile target: make bws-bootstrap-domain DOMAIN=<d> [DRY_RUN=1] [ARGS="..."])
#
# The vendored scripts/bws/bootstrap.sh derives the BWS project and the
# machine account from --app-name; a repository's default APP_NAME is its
# repository-level project, never a role domain's. So every value comes from
# blessed.yml secret_authorities.<DOMAIN>, and before anything external runs:
#   · the domain is bwsm and Environment-scoped to the Environment named DOMAIN;
#   · bwsm_project is set, is the domain's only allowed project
#     (bwsm_allowed_projects, when declared), no other domain declares it, and
#     it is not the repository-level project (secret_authorities.default, or
#     blessed.yml `project`);
#   · machine_account is <bwsm_project>-ci (what bootstrap.sh creates);
#   · .bws/<DOMAIN>.list declares exactly the domain's injects, and
#     .bws/<DOMAIN>.env exists;
#   · EXTRA does not re-bind --app-name, --secrets-list, --project-id-file or
#     --gh-environments.
# Then it runs (with --dry-run, prints) exactly:
#   scripts/bws/bootstrap.sh --app-name <bwsm_project> --secrets-list .bws/<DOMAIN>.list \
#     --project-id-file .bws/<DOMAIN>.env --gh-environments <DOMAIN> [EXTRA...]
# Exit 0 · 1 refused · 2 usage.
set -euo pipefail
refuse() {
  echo "bootstrap-domain: REFUSED — $1" >&2
  exit 1
}
usage() {
  echo "usage: bootstrap-domain.sh [--dry-run] DOMAIN [-- EXTRA BOOTSTRAP ARGS]" >&2
  exit 2
}
dry=0
if [ "${1:-}" = --dry-run ]; then
  dry=1
  shift
fi
domain="${1:-}"
[ -n "$domain" ] || usage
shift
if [ "${1:-}" = -- ]; then shift; fi
[[ "$domain" =~ ^[a-z0-9][a-z0-9-]*$ ]] || refuse "'$domain' is not a domain name"
command -v yq >/dev/null 2>&1 || refuse "mikefarah yq v4 is required"
[ -f blessed.yml ] || refuse "run from the repository root (no blessed.yml)"
for a in "$@"; do
  case "$a" in
    --app-name | --app-name=* | --secrets-list | --secrets-list=* | --project-id-file | --project-id-file=* | --gh-environments | --gh-environments=*)
      refuse "$a is bound by blessed.yml for domain $domain; do not pass it"
      ;;
  esac
done

q() { yq -r ".secret_authorities.\"$domain\".$1 // \"\"" blessed.yml; }
[ "$(yq -r ".secret_authorities | has(\"$domain\")" blessed.yml)" = true ] ||
  refuse "blessed.yml declares no secret_authorities.$domain"
[ "$domain" != default ] || refuse "the repository-level domain is not a role domain; use make bws-bootstrap"
project="$(q bwsm_project)"
machine="$(q machine_account)"
[ "$(q authority_type)" = bwsm ] || refuse "$domain is not a bwsm domain"
[ "$(q github_scope)" = environment ] || refuse "$domain is not Environment-scoped"
[ "$(q github_environment)" = "$domain" ] || refuse "$domain's Environment is '$(q github_environment)', not '$domain'"
[[ "$project" =~ ^[a-z0-9][a-z0-9.-]*$ ]] || refuse "$domain declares no valid bwsm_project ('$project')"
allowed="$(yq -r ".secret_authorities.\"$domain\".bwsm_allowed_projects // [] | join(\" \")" blessed.yml)"
[ -z "$allowed" ] || [ "$allowed" = "$project" ] || refuse "$domain's bwsm_allowed_projects is [$allowed], not exactly [$project]"
others="$(yq -r ".secret_authorities | to_entries[] | select(.key != \"$domain\") | .value.bwsm_project // \"\"" blessed.yml)"
if grep -Fxq -- "$project" <<<"$others"; then refuse "another domain also declares BWS project $project"; fi
repo_project="$(yq -r '.secret_authorities.default.bwsm_project // .project // ""' blessed.yml)"
[ "$project" != "$repo_project" ] || refuse "$project is the repository-level project; a role domain has its own"
[ "$machine" = "$project-ci" ] || refuse "$domain's machine_account is '$machine'; bootstrap.sh would create '$project-ci'"
list=".bws/$domain.list" envf=".bws/$domain.env"
[ -f "$list" ] || refuse "no $list"
[ -f "$envf" ] || refuse "no $envf"
# Each line is exactly NAME [type:TYPE]: required (no `?`), local to the
# declared project (no `shared` / `shared:<project>`), nothing else — so
# bootstrap.sh creates every role key in THIS domain's project.
bad_lines="$(grep -Ev '^[[:space:]]*(#|$)' "$list" | grep -Ev '^[A-Z][A-Z0-9_]*([[:space:]]+type:[a-z0-9_]+)?[[:space:]]*$' || true)"
[ -z "$bad_lines" ] || refuse "$list line(s) are not plain required, project-local keys (NAME [type:TYPE]): $(tr '\n' ' ' <<<"$bad_lines")"
want="$(yq -r ".secret_authorities.\"$domain\".injects[]" blessed.yml | sort)"
have="$(grep -Ev '^[[:space:]]*(#|$)' "$list" | awk '{print $1}' | tr -d '?' | sort)"
[ "$want" = "$have" ] || refuse "$list lists [$(tr '\n' ' ' <<<"$have")], blessed.yml injects [$(tr '\n' ' ' <<<"$want")]"

cmd=(bash scripts/bws/bootstrap.sh --app-name "$project" --secrets-list "$list" --project-id-file "$envf" --gh-environments "$domain" "$@")
echo "bootstrap-domain: $domain → BWS project $project, machine account $machine, Environment $domain"
if [ "$dry" = 1 ]; then
  printf 'would run:'
  printf ' %q' "${cmd[@]}"
  echo
  exit 0
fi
exec "${cmd[@]}"
