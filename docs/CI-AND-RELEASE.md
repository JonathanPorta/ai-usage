# CI, release, signing and secrets

Current state, 2026-10-10. Checked against blessed-cicd `23819bb`
(`standards/registry.yml`), Proof Glass `4a3c555` (deployed; `docs/ci-offload.md`)
and the Workroom producer `0198647` (the `releases.pipeline` reference).

## Summary

| Area | State | Remaining dependency |
| --- | --- | --- |
| Hosted CI | Linux only: Python 3.9 and 3.14 checks, spec/design checks, vendored-script and candidate-plan checks | — |
| macOS lane | Never on a hosted runner. Runs only when Proof Glass displaces `ai-usage-macos@1` into a disposable VM; otherwise `PR test gate` fails with the reason | Proof Glass enrollment (draft PR [proofglass#154](https://github.com/JonathanPorta/proofglass/pull/154)), operator steps below, and a golden image that can compile XCTest |
| Protected main | A push is green only when its commit is a merged PR whose head passed both required checks, and main still requires them | The ruleset below (not created yet) |
| Merge protection | **None active**: `rulesets` is `[]`, branch protection 404 | Create the ruleset after PR #4 merges |
| Releases | Opted into the `releases.pipeline` and `releases.candidate` **pilots**: candidate-only, publication disabled | A production manifest key (release-signing domain) |
| Signing | Manifest: `signing.ed25519-detached` **pilot**. App: ad-hoc only | Apple Developer ID finalizer upstream (draft PR [blessed-cicd#350](https://github.com/JonathanPorta/blessed-cicd/pull/350)) and Apple account artifacts |
| Secrets | None stored. BWS (released bootstrap) planned for the release-signing domain only | Owner's BWS admin token, Environment creation, and the `bws` vendoring decision |

## What is accepted, and what is a pilot

Under blessed-cicd's lifecycle (`standards/lifecycle.md`) only `status: standard` is
accepted for normal use; a `pilot` is copied only when a repository opts in, and a
`proposal` is not adopted. No CI, release, signing or secrets document is a
`standard`. On 2026-10-10 the owner opted ai-usage into these **pilots** (recorded
in `blessed.yml`; they are not fleet-wide standards):

| Document | Status | Tooling (pinned, manifest-verified) |
| --- | --- | --- |
| `releases.pipeline` | pilot | `release-v0.8.0` in `scripts/release/` |
| `releases.candidate` | pilot | `release-v0.8.0` |
| `signing.ed25519-detached` | pilot | `signing-v0.4.0` in `scripts/signing/` |

Not adopted: `releases.release-pr`, `.surfaces`, `.promotion-handshake`,
`.app-gated` (proposals); `secrets.bwsm-ci-loader` (an unimplemented proposal).
`secrets.bwsm-authority-domains` is a proposal; the planned release-signing domain
follows its shape only because that is how the released BWS bootstrap
(`CENTRALIZED-SECRETS.md`, `scripts/bws/INSTALL.md` deployment profile,
`scripts/release/bootstrap-domain.sh`) provisions a domain.

**Installation.** `scripts/sync-blessed-scripts.sh` (the `scripts/INSTALL.md`
mechanism, with the `release` line Workroom also adds) downloads each pinned
category from `scripts/.blessed-scripts-version`, checks every file against the
category's `MANIFEST.sha256`, and swaps it in atomically. `make verify-scripts`
re-checks the committed copies in CI. The older design/spec checkers in
`tools/blessed/` are a separate hand-vendored copy (no released category exists
for them).

## CI and Proof Glass

`.github/workflows/tests.yml`:

- **Triggers:** `pull_request` and pushes to `main` only. No `pull_request_target`,
  `workflow_run` or other privileged downstream workflow; one run per PR update.
- **Token:** `contents: read` at workflow level. `plan` and `gate` add `id-token:
  write` (Proof Glass OIDC) and `gate` adds read-only `pull-requests` and `checks`.
  No job has a write scope; the contract test in `test_ai_usage_service.py` fails if
  one appears, or if a hosted macOS runner or a privileged trigger is added.
- **plan** checks out nothing and runs no repository code. For a same-repository PR
  with `PROOFGLASS_CI_ROUTE_URL` set, it asks Proof Glass (OIDC) whether
  `ai-usage-macos@1` is displaced. Forks never reach the call (Proof Glass refuses
  them too). Errors, shadow mode or an empty `offloaded` list mean "not displaced".
- **check-linux** and **spec-and-design** are the only jobs that run repository
  code, with the read-only token, no secrets, no caches and no artifacts.
- **gate** (`PR test gate`) reports the hosted outcome to Proof Glass (bounded
  retry with a fresh OIDC token, as in docsort) and then:
  - on a PR, fails unless every hosted leg passed **and** the macOS lane was
    displaced;
  - on a push to main, fails unless the commit is the merge result of exactly one
    merged PR into main whose head passed `PR test gate` (GitHub Actions app) and
    `proofglass / pr-gate` (Proof Glass App 3987831), and main's active rules
    require a pull request and both checks with the strict (up-to-date) policy.

**What proves the macOS run.** A successful route answer is only a routing
decision. The verdict is the pair of required checks on the PR head:
`proofglass / pr-gate` succeeds only if the hosted report succeeded and every job
Proof Glass took over passed (every profile step exited 0, bound to the PR, head,
base, tested merge SHA, run and attempt); `PR test gate` succeeds only if the
lane was actually displaced. When nothing is displaced, `pr-gate` alone would
conclude success ("Hosted CI runs every shard"), which is why `PR test gate`
fails closed and both must be required.

**Coverage today.** PR #5 (head on `jp/f/macos-menu-bar-app`) runs the Linux legs
and reports the macOS lane BLOCKED: the repository is not enrolled. Main has no
coverage requirement yet because no ruleset exists, and main still carries the
pre-PR-#5 workflow (with hosted macOS jobs) until PR #5 merges.

### Enrollment (draft PR [proofglass#154](https://github.com/JonathanPorta/proofglass/pull/154))

Repository-side work is done. The Proof Glass side is a draft PR that adds the
typed profile `ai-usage-macos@1` (steps `make check`, `make fixture-check`,
`make app-test`, `make app-build` with `PYTHON=/usr/bin/python3`), its probe, the
policy entry (`repo_id` 1320204030, branch `main`, required context
`PR test gate`, owner actor only, lane in **shadow** first) and the
`proofglass_allowed_repos` entry.

**Toolchain blocker.** The golden image installs only the Xcode Command Line
Tools. They ship Swift Testing but not `XCTest.framework` (checked on this host:
only overlay `libswiftXCTest` dylibs), and the app's tests `import XCTest`. The
profile's probe compiles a trivial XCTest target, so the supervisor advertises
`ai-usage-macos@1` only if that works in the guest. The probe passed locally with
Xcode; it has not run inside the golden image, so the failure there is expected but
unconfirmed. If it fails, the image needs a full Xcode or the tests must move to
Swift Testing. That is the remaining toolchain decision.

**Admission rules that shape the merge sequence.**

- Proof Glass admits PRs whose base is the configured branch (`main`). PR #5's base
  is PR #4's branch, so it is refused ("does not target main") until it targets
  main.
- Only the owner's actor id is admitted. Fork PRs and other contributors are
  refused, so their PRs show `PR test gate` failing with the reason.
- Code from an admitted PR is still untrusted inside the VM: a fresh disposable VM
  per attempt, no tokens, mounts or shared directories, a minimal environment,
  internet egress with host and private ranges blocked; the clone token stays on
  the supervisor.

**Operator steps after the Proof Glass PR merges** (merging auto-deploys the
Worker and policy):

1. Install the Proof Glass App (`proof-glass-runner`) on ai-usage.
2. Set the repository variable `PROOFGLASS_CI_ROUTE_URL=https://api.proofglass.io/api/ci/route`.
3. Mint a new `pgs_` supervisor token in the console whose `allowed_repos` adds
   ai-usage, then reinstall this Mac's supervisor with the existing installer
   (`deploy/macos/install-supervisor.sh install --token-stdin`, a drained upgrade)
   and confirm `/v1/status` advertises `ai-usage-macos@1`.
4. Shadow trial on PR #5, then flip `pull_requests_mode` to `displace`.

Capacity: the Mac runs one guest, shared with docsort. A spill (queue timeout,
supervisor offline) routes the lane back to hosted CI, where ai-usage has no macOS
leg, so the PR goes red rather than green. Re-run when capacity returns.

### Ruleset (to create after PR #4 merges)

A ruleset on `main` with: a `pull_request` rule; required status checks
`PR test gate` and `proofglass / pr-gate` (integration id 3987831) with
`strict_required_status_checks_policy: true`; no bypass actors. Creating it before
PR #4 merges would block #4, whose checks are the old hosted jobs.

### Contributor validation

External contributors (forks) are never admitted to Proof Glass and receive no
secrets. Their PRs run the Linux legs; `PR test gate` fails with "fork pull
requests are never admitted". A maintainer reviews the change, then validates it by
pushing the reviewed commits to a branch in this repository and opening a
same-repository PR (which Proof Glass admits for the owner), or by running
`make check app-test app-build` locally. Nothing built from a contributor's PR is
cached, uploaded or promoted.

## Releases

Candidate-only producer (`release.yml`), modelled on Workroom:

| Target | What it does | Secrets |
| --- | --- | --- |
| `make release-plan-check` | Generates and validates the candidate plan | none (runs in Linux CI) |
| `make release-build` | Clean tree required; `make app-build`; deterministic archive `ai-usage_<version>_arm64.app.tar.gz` | none |
| `make release-assemble` | Notes, plan, `build-candidate.sh` (manifest, `provenance.json`, `SHA256SUMS`) | none |
| `make release-sign` | `sign.sh --profile ed25519-detached-v1 --private-key-env NAME` | manifest key, by env-var name only |
| `make release-validate` | `validate-candidate.sh`: schema, closure, on-disk hashes, signature | none |
| `make release-check` | All of the above with an **ephemeral** key and a temporary trust store | none |
| `make publish` | Refuses | — |

Evidence (2026-10-10, this Mac, `1919888`, OpenSSL 3 via `OPENSSL_BIN`):
`make release-check` → `release: candidate VALID` (schema, closure, on-disk hashes,
closed directory, signature by `ai-usage-ephemeral`). Appending one byte to the
archive makes `release-validate` fail at "on-disk hash recompute".

This is secret-free validation only. A real candidate needs the production key
`ai-usage-release-2026-10` (its public half goes into `release-trusted-keys.json`,
which is empty today) and, for distribution, Developer ID signing.

**Publication stays disabled.** The repository is public, so any GitHub Release,
even a prerelease, would be public. `release.yml` declares no surfaces.

Versioning: the candidate version is the app's `CFBundleShortVersionString`
(`macos/Support/Info.plist`, 0.1.0); the bundled collector keeps its own `VERSION`
(2.2.0). The app has no third-party dependencies, so no SBOM is declared.

## Signing

- **Manifest:** `signing.ed25519-detached` (pilot). The private key will exist only
  in the release-signing domain.
- **App:** ad-hoc (`codesign --sign -`). No shared Apple capability exists at any
  lifecycle status ("not Apple/Authenticode/…/notarization",
  `scripts/signing/README.md`).
- **Apple Developer ID gap:** draft PR [blessed-cicd#350](https://github.com/JonathanPorta/blessed-cicd/pull/350) proposes the smallest
  extension. A macOS finalizer, modelled on the RPM finalizer (LP-9), runs in its
  own authority domain and environment (`apple-signing`). It checks the input
  digest before it can reach a key, signs with the hardened runtime and a secure
  timestamp, notarizes, staples and verifies. It then writes a finalization
  record, and only then does assembly list the new digest and the manifest get
  signed. Detached manifest signing does not replace native signing; signing and
  stapling change the bytes, so they must come before sealing.

## Secrets

Nothing is stored today: 0 Actions secrets, 0 variables, 0 environments; no
`ai-usage-*` BWS project exists (checked read-only with `bws project list`).

Planned, through the released mechanism only (`CENTRALIZED-SECRETS.md`; the BWS
deployment profile in `scripts/bws/INSTALL.md`, or
`make bws-bootstrap-domain DOMAIN=release-signing` from `release-v0.8.0`):

| Domain | BWS project | Machine account | GitHub | Injects |
| --- | --- | --- | --- | --- |
| `release-signing` | `ai-usage-release-signing` | `ai-usage-release-signing-ci` (read-only) | Environment `release-signing`, branch `release` only | `AI_USAGE_RELEASE_SIGNING_KEY` |
| `apple-signing` (after the upstream extension) | `ai-usage-apple-signing` | its own | Environment `apple-signing` | Developer ID `.p12` and password, App Store Connect API key, key id, issuer id |

PR validation never sees either domain: PR workflows have no environment, no
secrets and a read-only token.

## Owner decisions and actions

1. **Vendoring `bws` into this public repository.** The released `bws` category embeds
   organization identifiers (the shared project UUID and some secret ids). Vendor it
   as released, or provision the domain from a private checkout.
2. **Private, unlicensed blessed-cicd code in a public repository.** `scripts/release`,
   `scripts/signing` and `tools/blessed` are copies of a private repository with no
   license. Confirm that publishing them here is intended.
3. **Release-signing branch policy:** `release` (Workroom, the authority-domain
   rules) or `main`.
4. **Golden image:** add a full Xcode, or move the tests to Swift Testing.
5. **Apple:** Developer Program membership, a Developer ID Application certificate,
   an App Store Connect API key and the Team ID.

Optional hardening (repository settings, unchanged): set fork-PR approval to
"all outside collaborators", and allow only SHA-pinned or selected actions.
