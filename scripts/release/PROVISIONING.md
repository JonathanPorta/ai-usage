# Provisioning a PR-opening App (`releases.release-pr@1` RP-6/RP-7)

One walkthrough for every PR-opening App. The role's settings are canonical
(`references/github-app-roles.json`); only the values in the table below are
per repository. Do it in the GitHub and Bitwarden web UIs and one terminal; no
secret is pasted into chat, a command argument or a file in the repository.

| Value | `release-pr` (a producer) | `admission-pr` (a package surface) |
|---|---|---|
| App name | `<repo>-release-pr` | `<surface>-admission-pr` (dots → hyphens) |
| Installed on | that producer repository only | that surface repository only |
| Repository permissions | Contents: Read and write · Issues: Read and write · Pull requests: Read and write · Metadata: Read-only | Contents: Read and write · Pull requests: Read and write · Metadata: Read-only |
| BWS project / machine account | `<repo>-release-pr` / `<repo>-release-pr-ci` | `<surface>-admission-pr` / `<surface>-admission-pr-ci` |
| BWS secrets | `RELEASE_PR_APP_CLIENT_ID`, `RELEASE_PR_APP_PRIVATE_KEY` | `ADMISSION_PR_APP_CLIENT_ID`, `ADMISSION_PR_APP_PRIVATE_KEY` |
| GitHub Environment | `release-pr` (branch `main` only; holds only `BWS_ACCESS_TOKEN`) | `admission-pr` (same) |
| The job that loads the key | `release.yml` → `release-please` | `admit-candidate.yml` → `open-pr` |

## Who can use the key, precisely

- **The private key** can mint a token for every repository the App is
  installed on, with every permission the App has. That is why each App is
  installed on one repository and has only its role's permissions — and why
  `app-token-prepare.sh` re-reads both from GitHub on every run and refuses
  before minting if either has widened.
- **A minted token** is narrower: this repository only, the role's
  permissions, revoked when the job ends.
- **Reading the key** requires the Environment's `BWS_ACCESS_TOKEN`, which
  GitHub gives to any job of any workflow that runs for `main` and declares the
  Environment. The Environment does not name a job. Exactly one job declares
  it because (1) the managed workflow is generated, (2) the conformance check —
  a required check — fails any other job that declares the Environment or loads
  the profile, and (3) the `main` ruleset requires a PR, its checks and an
  independent review for every change, with no bypass. The App is never a
  reviewer, CODEOWNER or bypass actor, and the Actions setting that lets
  workflows approve PRs stays off.

## Steps (about five minutes per App)

1. **Register.** <https://github.com/settings/apps/new>:
   GitHub App name from the table · Homepage URL: the repository's URL ·
   **Webhook: uncheck "Active"** (no URL, no events) · Repository permissions
   exactly the table's row, everything else "No access" · Account
   permissions: none · "Only on this account" → **Create GitHub App**.
2. **Client ID.** On the App's General page, copy the **Client ID** (it
   starts with `Iv`). The workflow passes it to `client-id`. The bootstrapper's
   prompt hint for this secret type says "numeric App ID" — paste the
   **Client ID** anyway; the numeric App ID is not used.
3. **Private key.** Private keys → **Generate a private key**. A `.pem` file
   downloads; note its path.
4. **Install.** Install App → your account → **Only select repositories** →
   exactly the table's one repository → Install.
5. **BWS project, machine account and secrets** — in the repository's clone,
   from updated `main` on a fresh branch, without touching your other work:

   ```sh
   cd ~/devel/portaj/<repo>
   git fetch origin
   git worktree add -b provision/<role> ../<repo>-provision-<role> origin/main
   cd ../<repo>-provision-<role>
   read -rs BWS_ACCESS_TOKEN && export BWS_ACCESS_TOKEN   # your Bitwarden ADMIN token; nothing echoes
   make bws-bootstrap-domain DOMAIN=<role> DRY_RUN=1                 # prints the exact command; check the project name
   make bws-bootstrap-domain DOMAIN=<role> ARGS="--plan"
   make bws-bootstrap-domain DOMAIN=<role> ARGS="--no-secret-values"
   ```

   When it pauses for the web vault:
   - **Machine account** `<project>-ci`: Projects → add `<project>` with
     **Can read**, and nothing else. Create one access token and paste it at the
     bootstrapper's hidden prompt (it becomes the Environment's
     `BWS_ACCESS_TOKEN`).
   - **In project `<project>`, two secrets:**
     - `*_APP_CLIENT_ID` ← the Client ID from step 2.
     - `*_APP_PRIVATE_KEY` ← the key, **base64 on one line** (a raw
       multi-line PEM breaks the loader). Copy it with
       `/usr/bin/base64 -i "<path to the .pem>" | /usr/bin/tr -d '\n' | /usr/bin/pbcopy`,
       paste it into the vault, then clear the clipboard:
       `/usr/bin/pbcopy </dev/null`.

   Re-run the `--no-secret-values` line. `git status --short` then shows
   **two** changed files: `.bws/<role>.env` (the project id) and
   `.github/actions/load-secrets/action.yml` (the regenerated loader, now
   mapping the two secrets' ids). Leave them uncommitted. End the session's
   admin token: `unset BWS_ACCESS_TOKEN`.
6. **Confirm the grant** in the vault: the machine account's Projects tab
   lists exactly one project, with Can read. (No workflow can read machine
   account grants; this is the one step verified by eye.)
7. **Tell the agent "<App> done".** It commits the two files through review,
   runs `verify-provisioning.sh`, and proves the App on its first real PR: the
   PR's author is the App, and the required checks ran and passed. The first
   run of the PR-opening job also proves, from GitHub's records, the App's
   permissions and its one-repository installation. That first run is also the
   first live use of the App's JWT (issued with the Client ID): if it fails at
   "GitHub refused the App's JWT", check the Client ID and the key's encoding
   before suspecting the App's settings.
8. **Delete the `.pem`** only after that proof: `rm "<path to the .pem>"`.
   This removes the file; it does not guarantee the bytes are erased from an
   SSD. The key's real protection is that GitHub holds only its public half and
   it can be revoked at any time (App → Private keys → Delete).
