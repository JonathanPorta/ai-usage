# CI, release, signing and secrets

Current state, 2026-10-11. Checked against blessed-cicd `b46e5d2`
(`standards/registry.yml`), Proof Glass `abfd57c` (`docs/ci-offload.md`) and
proofglass-supervisor (`docs/DECISIONS.md`, `docs/RELEASE.md`).

## Summary

| Area | State | Remaining dependency |
| --- | --- | --- |
| Hosted CI | Linux only: Python 3.9 and 3.14, spec/design checks, vendored-script and candidate-plan checks | — |
| macOS lane, pull requests | Never on a hosted runner. Runs only when Proof Glass displaces `ai-usage-macos@1`; verdict `proofglass / pr-gate`. Otherwise `PR test gate` fails with the reason | Enrollment ([proofglass#154](https://github.com/JonathanPorta/proofglass/pull/154), [proofglass-supervisor#15](https://github.com/JonathanPorta/proofglass-supervisor/pull/15)), an XCTest-capable guest image, then promotion from shadow to displace |
| macOS lane, main | The Proof Glass protected-main contract: the push is routed and judged by `proofglass / overall`. Not displaced means BLOCKED, so **main is red until the lane is displaced on main** | The above, plus Proof Glass's ruleset inspector token extended to ai-usage (operator) |
| Merge protection | **Transitional ruleset active** (id 24873836): pull request required, `check (ubuntu-latest, 3.9)` and `check (ubuntu-latest, 3.14)` strict, no bypass. PR #4 merged as `9946386`; its merge push was deliberately **not run** | Final ruleset before PR #5 merges |
| Releases | Opted into the `releases.pipeline` and `releases.candidate` **pilots**: candidate-only, publication disabled | Production manifest key; redistributable tooling (below) |
| Signing | Manifest: `signing.ed25519-detached` **pilot**. App: ad-hoc only | `signing.apple-macos` is a merged **proposal** with no released tooling; Apple account artifacts |
| Secrets | None stored | Release-signing domain via the released BWS tooling from a private checkout |
| Public tooling | 201 private, unlicensed blessed-cicd files are in this public repository's branch history; updates paused | Owner decision (below) and the export in [blessed-cicd#359](https://github.com/JonathanPorta/blessed-cicd/pull/359) |

## What is accepted, and what is a pilot or proposal

Under blessed-cicd's lifecycle (`standards/lifecycle.md`) only `status: standard` is
accepted for normal use; a `pilot` is copied only when a repository opts in; a
`proposal` is not adopted. On 2026-10-10 the owner opted ai-usage into these
**pilots** (recorded in `blessed.yml`; not fleet-wide standards):

| Document | Status | Tooling (pinned, manifest-verified) |
| --- | --- | --- |
| `releases.pipeline` | pilot | `release-v0.8.0` in `scripts/release/` |
| `releases.candidate` | pilot | `release-v0.8.0` |
| `signing.ed25519-detached` | pilot | `signing-v0.4.0` in `scripts/signing/` |

Not adopted (proposals): `releases.release-pr`, `.surfaces`,
`.promotion-handshake`, `.app-gated`, `secrets.bwsm-authority-domains`,
`secrets.bwsm-ci-loader` (unimplemented), and the native-signing set merged in
blessed-cicd #352: `signing.native-application`, `signing.apple-macos`,
`releases.apple-distribution`. `signing.apple-macos` names ai-usage as an intended
first consumer and states that it is not yet qualified.

## CI and Proof Glass

`.github/workflows/tests.yml` follows the Proof Glass CI-offload contract on both
paths, as its reference consumers do (docsort for pull requests, comms-core for
protected main).

- **Triggers:** `pull_request` and pushes to `main` only. No `pull_request_target`,
  `workflow_run` or privileged downstream workflow.
- **Token:** `contents: read`; `plan` and `gate` add only `id-token: write` (Proof
  Glass OIDC). They check out nothing and run no repository code. The contract test
  fails on any other write scope, a hosted macOS runner, or a privileged trigger.
- **plan** asks Proof Glass (OIDC, body `{}`) about a same-repository PR into main
  or a push to main, if `PROOFGLASS_CI_ROUTE_URL` is set. Forks never reach the call.
  The answer counts as displaced only when it is admitted in `displace` mode with
  the verdict this event expects (`proofglass / pr-gate` on a PR,
  `proofglass / overall` on main) and a numeric check run, and exactly one
  `ai-usage-macos@1` lane in `displace` mode with a non-empty `offloaded` list.
- **check-linux** and **spec-and-design** are the only jobs that run repository
  code, read-only, with no secrets, caches or artifacts.
- **gate** (`PR test gate`) reports the hosted outcome to Proof Glass when admitted
  (bounded retry with a fresh OIDC token), then fails unless every hosted leg passed
  **and** the macOS lane was displaced, on PRs and on main alike.

**Routing is not execution.** The route answer is a decision. The verdict is the
Worker-owned check: `proofglass / pr-gate` (PR head) or `proofglass / overall`
(main commit). Each is bound by OIDC claims to the repository, SHA, ref, event, run
and attempt, and succeeds only when the hosted report succeeded and every displaced
job passed (every profile step exited 0). Shadow mode, a spill to hosted CI, a busy
or offline Mac, or a missing report never yields success here: `PR test gate` fails
because nothing was displaced, and Proof Glass's own verdict times out or fails.

**Protected main** is Proof Glass's contract, not ours. On a push, Proof Glass
admits only `main`, a full SHA on main (E3: `compare/main...sha` identical or
behind), the right repository (E2), and a repository whose ruleset requires a pull
request and the policy's contexts with the strict policy and no bypass actors (E4).
It inspects the ruleset with its own operator-held token; this workflow holds no
privileged credential. `proofglass / overall` is a post-push verdict and is not
listed in any ruleset (Proof Glass P10). ai-usage has no hosted macOS fallback, so
until main's lane is in `displace` mode, every push to main is red with the reason.

### Negative controls

- **Consumer side** (`test_ai_usage_service.py`,
  `test_proofglass_displacement_decision_rejects_everything_but_an_executed_lane`):
  the displacement decision is fed fourteen answers that must not count as an
  executed lane. They include:
  - a PR verdict on a push, or a main verdict on a PR;
  - no expected verdict (fork or other event);
  - not admitted;
  - shadow, either the run or only the lane;
  - spill or an older attempt (`offloaded: []`);
  - no verdict check, or a string or zero check-run id;
  - another profile version;
  - a duplicate or malformed lane.

  Removing any single clause of the predicate fails the test.
- **Controller side** (Proof Glass `worker/scripts/check-ci-offload.ts`): `npm run check:ci-offload` in #154: 532 pass, 0 fail. The cases that cover the
  adopted contract:
  - **Changed revision:** B6 (a spoofed SHA not on main is refused), P5 (a PR head that moved after admission is cancelled) and DB2 (a stale revision is refused).
  - **Missing evidence:** M9 (a missing hosted report makes the verdict time out, and a late report cannot turn it green) and K2–K7 (absent, null, malformed or non-empty bypass evidence is denied).
  - **Failed or incomplete execution:** M6 (a job lost twice times out, never succeeds), T7–T9 (a lost attempt is requeued once, an omitted job is never credited to hosted CI, and stale or late results are refused).
  - **Replay and shadow:** M8 (a stale attempt gets 409), C9 (OIDC replay gets 409) and M11 (shadow creates no verdict).

### Enrollment

- **Proof Glass (shared contract, policy, allowlist, docs):** draft
  [proofglass#154](https://github.com/JonathanPorta/proofglass/pull/154). Policy
  entry: `repo_id` 1320204030, branch `main`, required context `PR test gate`, owner
  actor only, lane `ai-usage-macos@1` in **shadow** for main and PRs.
- **Supervisor (profile, probe, guest verification):** per proofglass-supervisor
  D-001 and `docs/RELEASE.md`, the supervisor is owned by proofglass-supervisor;
  proofglass's copy is frozen and may carry interim mirrors before cut-over.
  The canonical change is draft
  [proofglass-supervisor#15](https://github.com/JonathanPorta/proofglass-supervisor/pull/15)
  (profile, probe, `xcode` image key, provisioning script, `verify-tart-ai-usage`);
  #154 carries the interim mirror and the shared contract. Merge order: #154, re-pin
  the contract to its merge commit, then #15. The written record is not yet matched by
  practice: the freeze notice for proofglass's `supervisor/` copy is unmerged and the
  cut-over steps are undone, so the owner should merge the freeze or supersede D-001.
- **Guest evidence (2026-10-11, real disposable Tart guests, owner's supervisor paused with `active_attempts 0` and resumed by trap each time; 0 leftover `pg-*` guests):**
  - **Run A:** the profile as first enrolled, image `proofglass-macos-golden`, ai-usage `e65d8e2`.
    - The probe hit its 120 s limit, so that result was inconclusive.
    - A diagnostic guest then failed to compile the XCTest target after 239 s.
  - **Run B:** the committed script, same image, ai-usage `b1aa8c3`. The probe fails closed: "no such module 'XCTest'".
    - The guest runs macOS 26.6.2 (25G83) arm64 as uid 1000.
    - The Command Line Tools are 26.6.0, with Swift 6.3.3.
    - There is no `XCTest.framework`, and `xcrun --find xctest` finds nothing.
  - So the profile is never advertised on today's image, and **no ai-usage build or test command has run in a guest yet**.
  - The probe now also requires `xcodebuild -version` and an `xcode` image key, so it cannot pass on the Command Line Tools image at all.
- **Xcode-capable image (prepared, not built):** `deploy/macos/provision-xcode-golden.sh` (in #15 and #154) builds a separate `proofglass-macos-xcode-golden` and leaves the existing golden images untouched. It builds from one of two sources:
  - the pinned Cirrus Labs image `ghcr.io/cirruslabs/macos-tahoe-xcode:26.5@sha256:923c98d3…831bd`;
  - a specific Xcode `.xip` with its sha256, streamed in over `tart exec` stdin, with no host mounts.

  It accepts the license and runs first launch in the guest and records the toolchain in `/opt/pg/xcode.json`. Its timing check works in three steps, all as `pgci`:
  1. It runs a cold `swift test` of a one-file XCTest package. This step is untimed and serves to warm the system caches.
  2. It deletes that package's `.build` directory.
  3. It times a clean `swift build --build-tests` with the system caches already warm, as the probe's fresh package would see them. The image is refused unless this build takes under 90 s, which leaves margin within the probe's 120 s budget.

  The cold build in step 1 has no time limit.

  Disk, as of 2026-10-11: 140.3 GiB free. The pinned Cirrus image needs 130.4 GiB unpacked, and the script keeps 60 GiB free, so `--from-cirrus` refuses. The `.xip` route needs the default base clone (about 33 GiB) plus about four times the `.xip` size, which fits. No `.xip` exists on this Mac. The host's installed Xcode is never copied into a guest. So the one missing input is an owner-downloaded Xcode `.xip` and its sha256. After that, on a quiet host: the provisioning script with `--xcode-xip`, then `make verify-tart-ai-usage` with the supervisor paused.

Prepared, not executed (each needs the step before it):

| Step | Who | Needs |
| --- | --- | --- |
| Merge #154 (auto-deploys the Worker and policy; ai-usage admitted in **shadow**) | Owner approval | — |
| Install the Proof Glass App (`proof-glass-runner`, id 3987831) on ai-usage | Owner (GitHub UI) | — |
| `gh variable set PROOFGLASS_CI_ROUTE_URL -R JonathanPorta/ai-usage -b https://api.proofglass.io/api/ci/route` | Agent, after #154 deploys | Deploy (before it, the variable only changes the BLOCKED reason) |
| New supervisor token whose `allowed_repos` keeps every repository the Mac serves today and adds ai-usage (token scope cannot be edited) | Owner (console) | Deploy (console only mints tokens for allowlisted repositories) |
| Build the Xcode golden image (`provision-xcode-golden.sh`), set `PROOFGLASS_TART_XCODE_GOLDEN`, run `make verify-tart-ai-usage` | Agent, after the owner picks the Xcode source and frees disk | #15 / #154 reviewed |
| Drained upgrade of this Mac's supervisor: `deploy/macos/install-supervisor.sh install --token-stdin`, then `/v1/status` lists `ai-usage-macos@1` | Agent, with the owner piping the token | Token, Xcode image |
| Extend `PROOFGLASS_CI_RULESET_TOKEN` (fine-grained, Administration read) to ai-usage, update it in BWS, redeploy | Owner | Main path only |
| Shadow trial on a same-repository PR into main: the advisory `proofglass / ai-usage-macos [shadow]` check runs; ai-usage's gate stays red by design | — | All of the above |
| Promote: a reviewed #154 follow-up sets `pull_requests_mode` and main `mode` to `displace` | Owner approval | A passing shadow trial |

Capacity: the Mac runs one guest, shared with docsort. A spill routes the lane to
hosted CI, where ai-usage has no macOS leg, so the gate goes red; re-run all jobs
when capacity returns.

### Merge protection

| Stage | Ruleset | Why |
| --- | --- | --- |
| Now (active, id 24873836) | Pull request required; `check (ubuntu-latest, 3.9)` and `check (ubuntu-latest, 3.14)` from GitHub Actions (15368), strict; deletion and force-push blocked; no bypass actors | These are the only checks both the current `main` workflow (PR #4) and the new workflow (PR #5) produce, so #4 can merge on its existing green checks |
| Before PR #5 merges | Replace with: pull request required; `PR test gate` (15368) and `proofglass / pr-gate` (3987831), strict; no bypass actors | `PR test gate` does not exist on `main` until #5 lands, so requiring it earlier would block every other PR |

PR #4 landed on 2026-10-11 as squash commit `9946386` (reviewed head `0146cf0`, unchanged since review; its required checks had passed). The sequence:
- **Ready for review.** #4 was still a draft, so it was marked ready for review first.
- **First merge attempt.** It was refused (HTTP 405, still a draft). The `tests` workflow (id 325661405) had already been switched from `active` to disabled; it was restored to `active` before the retry.
- **Merge.** The workflow's prior state was recorded as `active`. It was disabled, #4 was squash-merged at 23:58Z, and the workflow was restored to `active` at 23:59:13Z.
- **CI on `9946386`: NOT RUN.** The workflow was disabled for the merge push, so `actions/runs?head_sha=9946386…` returns 0 runs. The hosted macOS jobs never started. That push has no `tests` result and must not be counted as passed. Its only check run is the Socket Security project report.
- **#5.** It was retargeted from `jp/b/isolate-launchctl-in-tests` to `main`, and `main` was merged into its branch with a merge commit (no force-push). #5 stays unmerged.

Verified through the API on 2026-10-11:
- `GET /repos/JonathanPorta/ai-usage/rulesets/24873836` returns `enforcement: active`, `bypass_actors: []` and `current_user_can_bypass: never`, with the required checks and their integration ids.
- `GET /repos/JonathanPorta/ai-usage/rules/branches/main` (public, unauthenticated) lists `deletion`, `non_fast_forward`, `pull_request` and `required_status_checks`.

### Contributor validation (forks)

Fork pull requests are never admitted to Proof Glass and receive no secrets. Their
code runs only in the hosted Linux jobs (ephemeral GitHub runners, read-only token,
no caches or artifacts); `PR test gate` fails with "fork pull requests are never
admitted". Maintainer review does not make contributor code safe to run on a
persistent machine, so it is never run natively on the owner's Mac. To get macOS
coverage, the maintainer pushes the reviewed commits to a branch of this repository
and opens a same-repository PR; Proof Glass then runs it only inside a fresh
disposable VM (no tokens, mounts or shared directories; host and private networks
blocked; destroyed after the attempt). Nothing built from a contributor's PR is
cached, uploaded or promoted.

## Releases

Candidate-only producer (`release.yml`), modelled on Workroom:

| Target | What it does | Secrets |
| --- | --- | --- |
| `make release-plan-check` | Generates and validates the candidate plan | none (Linux CI) |
| `make release-build` | Clean tree required; `make app-build`; deterministic archive | none |
| `make release-assemble` | Notes, plan, `build-candidate.sh` (manifest, `provenance.json`, `SHA256SUMS`) | none |
| `make release-sign` | `sign.sh --profile ed25519-detached-v1 --private-key-env NAME` | manifest key, by env-var name only |
| `make release-validate` | Schema, closure, on-disk hashes, signature | none |
| `make release-check` | All of the above with an **ephemeral** key and trust store | none |
| `make publish` | Refuses | — |

Evidence (2026-10-10, `1919888`): `make release-check` → candidate VALID; one
appended byte fails "on-disk hash recompute"; validating against the committed
(empty) trust store fails the signature check; `release.yml` validates against the
release-declaration schema. Secret-free validation only: no production key exists,
`release-trusted-keys.json` is empty, and publication stays disabled (the repository
is public, so any GitHub Release would be public).

## Signing

- **Manifest:** `signing.ed25519-detached` (pilot).
- **App:** ad-hoc (`codesign --sign -`).
- **Apple Developer ID:** blessed-cicd #352 (merged) defines the canonical
  `signing.apple-macos` **proposal** (#350 is closed as superseded). In short:
  - **Secret-free build:** the build has no signing authority, and its inputs are
    verified before signing authority is granted (MAC-2).
  - **Order:** native signing, notarization, stapling and the final archive all
    happen before candidate digests and the detached manifest signature, and the
    finalization evidence is sealed into the candidate (MAC-5; the Ed25519 envelope
    is signed last).
  - **Separate authorities:** code signing, notarization authentication and
    publication are separately declared authorities (MAC-6). Their domain,
    Environment and secret names are not defined yet.
  - **Credentials:** an ephemeral job keychain, and an App Store Connect API key
    rather than an Apple account password.
- **What is merged vs usable:** the proposal is documentation. The shared checker is
  draft blessed-cicd #351, implementation-only, "not adoptable", with two open review
  defects. The finalizer job, the record schema, the sealing amendment and a release
  are still to come. No released `signing-*` or `release-*` tag contains Apple tooling.
  ai-usage's consumer shape (a SwiftPM `.app`, the ZIP route, Developer ID direct
  download, no pkg) is recorded on #351.
- **Consolidated prerequisite inventory:** planned upstream (Phase 2 of
  `tasks/tasks-native-app-distribution.md`) but not written yet. Until it exists,
  the account-side list is the proposal's human-only prerequisites:
  - Apple Developer Program membership;
  - a Developer ID Application certificate (an Installer certificate only if a pkg
    ships; none is planned);
  - an App Store Connect API key;
  - the Team ID.
- **Existing metadata checked (read-only):**
  - no Developer ID Application identity is in this Mac's keychain;
  - no Apple-related BWS project or secret name is visible to the local token.
  That token's view is limited, so absence there is not proof of absence, and no
  replacement material should be created on that basis.

Publication stays disabled until the shared implementation is released and a real
signed, notarized and stapled candidate has been validated.

## Secrets

Nothing is stored: 0 Actions secrets, variables and environments; no `ai-usage-*`
BWS project is visible to the local token. The release-signing domain will be
provisioned with the released BWS tooling (`CENTRALIZED-SECRETS.md`; the deployment
profile in `scripts/bws/INSTALL.md`, or `make bws-bootstrap-domain`) **run from a
private blessed-cicd checkout**. The `bws` category and any organization-specific
BWS configuration are never vendored into this public repository.

| Domain | BWS project | GitHub | Injects |
| --- | --- | --- | --- |
| `release-signing` (planned) | `ai-usage-release-signing` | Environment `release-signing`, branch `release` (Workroom's default; changeable) | `AI_USAGE_RELEASE_SIGNING_KEY` |
| Apple code signing, notarization (later) | names to be defined by the upstream implementation plan (MAC-6) | separate Environments | — |

PR validation never sees any domain: PR workflows have no environment, no secrets
and a read-only token.

## Public tooling

`scripts/release` (166 files), `scripts/signing` (25) and `tools/blessed` (8) are
copies of the private, unlicensed blessed-cicd repository. They were introduced in
`b97eda2` and `5ccb2ee`, are pushed on this public branch, and are not on `main`.
Integrity: 165, 24 and 5 files match their manifests; there is no drift. Further
copying is paused: `make sync-scripts` refuses.

A bounded inventory (details kept off the public record) found:

| Class | Files | Notes |
| --- | --- | --- |
| Runtime (CI and release targets need these) | 20 | release: `validate-plan.sh`, `build-candidate.sh`, `validate-candidate.sh`, `lib/release-lib.sh`, `lib/lib-json-schema.sh`, two schemas, `VERSION`, `MANIFEST.sha256`; signing: `sign.sh`, `keygen.sh`, `verify.sh`, `lib/signing-lib.sh`, `VERSION`, `MANIFEST.sha256`; tools/blessed: `spec-check.sh`, `design-check.sh`, `lib-json-schema.sh`, the handoff schema, `VENDOR-SHA256SUMS.txt` |
| Shipped but unused | 177 | Other products' publication, package-repository and promotion tooling, templates, tests and fixtures |
| Internal metadata | about 45 | Another product's domain, repository names and its public updater key with real signatures in test fixtures; the private upstream's name in schema ids; a personal host name in a changelog. None is a credential |
| Authentication material | 0 | No private key, token or password. Fixture keys were ephemeral and destroyed upstream |
| License | none | No license file or header; the upstream is private |

A forward deletion does not remove these files from history: anyone who fetched or
forked the branch keeps them. History is not rewritten.

**Owner decision (recommended: A).**

- **A. License a minimal export, and replace the vendored files with it.**
  - **Your actions:** choose a license for the exported files, add it upstream, and
    create the public mirror that draft [blessed-cicd#359](https://github.com/JonathanPorta/blessed-cicd/pull/359) publishes to (it adds `export-build.sh`, which copies 15 released files byte-for-byte after checking each against its category manifest, refuses to run without a license file, and runs a closure test; attestation runs in the mirror).
  - **What follows:** ai-usage then vendors only the attested runtime export, about 20
    files with their own manifest and provenance. It forward-deletes the 177 unused
    files and the other product's fixtures, and history keeps the earlier copies.
- **B. License the files already published, and keep all 201.**
- **C. Do not license.** Forward-delete all vendored tooling and run the release
  and checker targets only from a private checkout. Public CI then loses the
  spec/design, script and plan checks.

## Owner decisions and actions

See the PR #5 description for the ordered approval list and its effects.
