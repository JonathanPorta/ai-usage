# CI, release, signing and secrets: conformance record

Checked on 2026-10-10 against JonathanPorta/blessed-cicd `9a9d6e9` (registry
`standards/registry.yml`) and Proof Glass `4a3c555` (`docs/ci-offload.md`).

## What is accepted

Under blessed-cicd's lifecycle (`standards/lifecycle.md`), only `status:
standard` is "accepted for normal use"; `pilot` is copied only when a repository
opts in, and `proposal` is not adopted. No CI, release, signing or secrets
document is at `standard`:

| Area | Documents (status) |
| --- | --- |
| GitHub Actions | `github-actions.secret-boundaries`, `.dependabot-secret-guards`, `.audit-artifact-contract` (proposal); `docs/workflow-policy.md`, `docs/sha-pinning.md` (no status; checker in `scripts/`) |
| Releases | `releases.pipeline`, `.candidate` (pilot); `releases.release-pr`, `.surfaces`, `.promotion-handshake`, `.app-gated` (proposal); tooling `release-v0.8.0` |
| Signing | `signing.ed25519-detached` (pilot, `signing-v0.4.0`). Apple Developer ID signing and notarization are explicitly out of scope everywhere ("not Apple/Authenticode/cosign/notarization", `scripts/signing/README.md`). |
| Secrets | `secrets.bwsm-authority-domains`, `secrets.bwsm-ci-loader` (proposal); BWS loader tooling 1.12.0 |
| CI offload | Not a blessed-cicd standard. Defined by Proof Glass `docs/ci-offload.md` ("pull-request displacement is live; protected-main is shadow"); reference consumer: docsort `.github/workflows/test.yml`. |

So nothing here is mandatory; adopting a pilot or proposal is the owner's
opt-in. This repository adopts no new release, signing or secret mechanism.

## Conformance

| Area | Status | Implementation | Evidence |
| --- | --- | --- | --- |
| Workflow triggers and permissions | Conforms to the checked rules | `.github/workflows/tests.yml`: `pull_request` plus `push` to `main` only; `contents: read`; `id-token: write` only on `plan` and `gate`, which check out nothing; no `pull_request_target`, `workflow_run`, caches or artifacts; per-PR concurrency, cancel in progress on PRs | blessed-cicd `scripts/check-workflow-policy.py --strict`: 0 findings; `scripts/check-workflow-secret-boundaries.sh --strict`: 0 findings; `actionlint`: clean |
| Action pinning | Conforms (`sha-pinning` capability in `blessed.yml`) | `actions/checkout` and `actions/setup-python` pinned to 40-character SHAs | `unpinned-remote-use` rule: no findings |
| Proof Glass routing | Integrated; **blocked on enrollment** | `plan` makes the Proof Glass route call (OIDC, same-repository PRs only) and reads lane `ai-usage-macos@1`; `gate` reports the hosted outcome when admitted. The macOS lane (Swift build and tests, macOS Python checks) never runs on a hosted runner; when not displaced, `PR test gate` fails with the reason | Route logic reused from docsort's fail-closed parser; current result: `PROOFGLASS_CI_ROUTE_URL is not set`, so the gate reports the macOS lane blocked |
| Hosted runner usage | Linux only | `check-linux` (Python 3.9 and 3.14: `make check`, `make fixture-check`), `spec-and-design`, `plan`, `gate` on `ubuntu-latest` | No `macos-*` runner remains in any workflow |
| Untrusted pull requests | Isolated | Fork PRs get the read-only token and no OIDC route call (Proof Glass refuses forks too); no secrets exist; nothing PR-built is cached, uploaded or promoted | Repository: 0 Actions secrets, 0 variables, 0 environments; default workflow token `read`; fork approval policy `first_time_contributors` |
| Releases | **Gap**: no accepted standard | None. No release is published; `make app-build` produces a local, ad-hoc signed bundle only | Owner decision: opt into the `releases.pipeline` pilot (a `candidate-only` producer, like the Workroom example) |
| macOS signing and notarization | **Gap**: no blessed method at any status | None; local builds stay ad-hoc signed, and nothing is notarized | Proposed path: a new upstream standard (squirrel, then proposal) for Developer ID signing on an isolated signer, reusing `releases.pipeline`'s secret-free build and digest-verified signing rules |
| Secrets | Nothing to store | The project needs no CI secret. No `.bws-secrets-list`, loader or token was added | When a signing or release domain is adopted: one BWS project per domain via `make bws-bootstrap`, delivered to a main-only GitHub Environment (proposal `secrets.bwsm-authority-domains`) |
| Personal data | Kept out of the repository | `usage-report/` is ignored; renders and the review package use synthetic fixtures only | `git grep` for credential patterns and key/certificate files: none tracked |

## Unresolved dependencies (operator-side, not repository changes)

Running the macOS lane needs Proof Glass enrollment, all outside this repository:

1. A policy entry in Proof Glass `examples/ci-policy.json` for `JonathanPorta/ai-usage`: lane `ai-usage-macos@1`, `pull_requests_mode: displace`, the owner's actor id, required context `proofglass / pr-gate`.
2. A typed `ai-usage-macos@1` profile in the supervisor (`supervisor/src/profiles.ts`), with fixed commands `make check`, `make fixture-check`, `make app-test`, `make app-build`, run in a fresh Tart VM from an image with Xcode and Python 3. The project fetches no packages, so it works with the guest's restricted egress.
3. The repository in `PROOFGLASS_ALLOWED_REPOS`, the Proof Glass App installed with Checks read and write, and the repository variable `PROOFGLASS_CI_ROUTE_URL` (plus `PROOFGLASS_OIDC_AUDIENCE` if not `proofglass`).
4. A decision on admitting a **public** repository: the Proof Glass docs don't address it. Same-repository PRs only; forks stay refused.
5. Then a ruleset requiring `PR test gate` and `proofglass / pr-gate`.

Until then, every pull request shows `PR test gate` failing with "macOS lane BLOCKED". The macOS evidence comes from local runs, recorded in `docs/design/app/design.md#verification`.

Optional hardening (repository settings, not changed): set fork-PR approval to "all outside collaborators", and allow only SHA-pinned or selected actions.
