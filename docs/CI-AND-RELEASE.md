# CI, release, signing and secrets

Current state: Proof Glass enrollment 2026-10-14, everything else 2026-10-12. Checked against blessed-cicd `master` (OD-2, #362), Proof Glass #154/#157
(`standards/registry.yml`), Proof Glass `abfd57c` (`docs/ci-offload.md`) and
proofglass-supervisor (`docs/DECISIONS.md`, `docs/RELEASE.md`).

## Summary

| Area | State | Remaining dependency |
| --- | --- | --- |
| Hosted CI | Linux only: Python 3.9 and 3.14, spec/design checks, vendored-script and candidate-plan checks | — |
| macOS lane, pull requests | Never on a hosted runner. Runs only when Proof Glass displaces `ai-usage-macos@1`; verdict `proofglass / pr-gate`. Otherwise `PR test gate` fails with the reason | Enrollment ([proofglass#154](https://github.com/JonathanPorta/proofglass/pull/154) and [proofglass-supervisor#15](https://github.com/JonathanPorta/proofglass-supervisor/pull/15) merged; supervisor upgrade blocked on a probe-name fix); a real control-plane shadow trial; then a separately approved promotion to displace. The XCTest-capable image exists and passed local guest verification (2026-10-12) |
| macOS lane, main | The Proof Glass protected-main contract: the push is routed and judged by `proofglass / overall`. Not displaced means BLOCKED, so **main is red until the lane is displaced on main** | The above, plus Proof Glass's ruleset inspector token extended to ai-usage (operator) |
| Merge protection | **Transitional ruleset active** (id 24873836): pull request required, `check (ubuntu-latest, 3.9)` and `check (ubuntu-latest, 3.14)` strict, no bypass. PR #4 merged as `9946386`; its merge push was deliberately **not run** | Final ruleset before PR #5 merges |
| Releases | Opted into the `releases.pipeline` and `releases.candidate` **pilots**: candidate-only, publication disabled | Production manifest key; redistributable tooling (below) |
| Signing | Manifest: `signing.ed25519-detached` **pilot**. App: ad-hoc only | OD-2 settles the executor (Proof Glass). Implementation in progress upstream (proofglass#157, blessed-cicd#361); H-1 is still open; Apple credential creation is on hold |
| Secrets | None stored | Release-signing domain via the released BWS tooling from a private checkout |
| Public tooling | 199 private, unlicensed blessed-cicd files are in this public branch's history (not on `main`); updates paused | Licensing and disposition decision (below); the export in [blessed-cicd#359](https://github.com/JonathanPorta/blessed-cicd/pull/359) |

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

- **Proof Glass (shared contract, policy, allowlist, docs):** merged
  [proofglass#154](https://github.com/JonathanPorta/proofglass/pull/154). Policy
  entry: `repo_id` 1320204030, branch `main`, required context `PR test gate`, owner
  actor only, lane `ai-usage-macos@1` in **shadow** for main and PRs.
- **Supervisor (profile, probe, guest verification):** per proofglass-supervisor
  D-001 and `docs/RELEASE.md`, the supervisor is owned by proofglass-supervisor;
  proofglass's copy is frozen and may carry interim mirrors before cut-over.
  The canonical change is merged
  [proofglass-supervisor#15](https://github.com/JonathanPorta/proofglass-supervisor/pull/15)
  (profile, probe, `xcode` image key, provisioning script, `verify-tart-ai-usage`);
  #154 carries the shared contract and a traceable interim mirror of the #15 change. Ownership is
  decided (D-001); the freeze notice for proofglass's `supervisor/` copy and the
  cut-over are steps of the existing procedure, not open decisions.
- **Upstream sequence:**
  1. ~~Merge #154~~: **done 2026-10-14**, merge `4952eca`, deploy run 38552949418 (0 add / 1 change / 0 destroy, no migration). ai-usage is admitted in **shadow**; the other repositories' entries are unchanged. `/health` 200, `/api/ci/route` 401 without OIDC.
  2. ~~Re-pin #15 and merge it~~: **done 2026-10-14**, merge `a219416`, contract pinned to `4952eca`.
  3. Upgrade this Mac's supervisor and configure access (table below). **Blocked**, see below.
  4. Get a successful **shadow** run against a real ai-usage pull request.
  5. Separately review a policy change from shadow to **displace**.
  6. Replace the transitional ruleset with the final ai-usage gates.

  **Shadow mode never satisfies the required macOS gate.** PR evidence (`proofglass / pr-gate` on the PR head) and protected-main evidence (`proofglass / overall` on the pushed `main` commit) are separate, and each is tied to its own commit.
- **Supervisor upgrade blocked, 2026-10-14.** A drained upgrade from proofglass `main` `e6a7b25` was installed and rolled back within 4 minutes.
  - Proofglass #149 names the second startup probe guest `pg-probe-<stamp>-1`. The Tart driver accepts only one segment after `pg-probe`, so it refused the name. The supervisor then counted the never-created guest as quarantined, which filled its only slot: no docsort job could be claimed, and `docsort-native-macos@1` and `ai-usage-macos@1` were not offered. Any Mac with two or more images hits this; the Xcode image is not needed to trigger it.
  - The supervisor is back on `13e6c72` from a backup (app and launchd file byte-identical), with `config.env` as before, so the Xcode image is not configured yet. All three docsort lanes are offered again.
  - A probe-name fix for both supervisor copies is being prepared for review. The upgrade is retried only after it merges.
- **Access state, 2026-10-14:** `PROOFGLASS_CI_ROUTE_URL` is unset on ai-usage, so its CI behaves exactly as before enrollment. Whether App 3987831 includes ai-usage is unknown from here (`gh` cannot read the installation). No new supervisor token has been minted.
- **CI-cost exception.** The proofglass-supervisor repository's own `check.yaml` still runs its macOS (`macos-15`) and Windows (`windows-latest`) client jobs on GitHub-hosted runners. The existing migration path is proofglass-supervisor `docs/ci-offload-plan.md` ("plan; nothing is activated"). It keeps the Linux supervisor and release jobs hosted on purpose, for bootstrap and recovery. ai-usage does not take on that migration. These reruns were avoided: #15's checks are reused at `d4511d3`.
- **Local guest verification, 2026-10-12.** This is the production guest path on this Mac, not a control-plane shadow run.
  - **Image:** `proofglass-macos-xcode-golden`, a separate image; the existing golden images are untouched. It was built by `provision-xcode-golden.sh --xcode-xip`:
    - source `Xcode_26.5_Apple_silicon.xip`, sha256 `728fbab6e4580c03b44769a911cedcb3599042f9d7fbff0add751da4953236e2`, signed Apple Software;
    - base `macos-tahoe-base@sha256:1b093499…a201e`, macOS 26.6.2 arm64;
    - `/opt/pg/xcode.json` records Xcode 26.5, build 17F42, Swift 6.3.2;
    - timing check: an untimed cold XCTest build and test as `pgci` (41 s), then a clean warm build that must finish under 90 s (14 s).
  - **Probe:** `ai-usage-macos@1` passed and was advertised: Xcode 26.5 17F42, Python 3.9.6, XCTest build ok, no `sudo`.
  - **Attempt** on ai-usage `f4e539dfcbe70daff7a624cdf84c8cc9d7ec8224`: passed in 264 s, with every step exiting 0:
    - checkout, toolchain;
    - `make check`, `make fixture-check`;
    - `make app-test` (48 XCTest tests, 0 failures);
    - `make app-build`.
  - **Cleanup:** every guest was destroyed and confirmed gone. The supervisor was paused only while idle and resumed after each run.
  - **Integration fixes needed to get there**, first made in `95997c1` (#15) and `f8b5101` (#154's mirror), now merged with both:
    - re-enter the expansion directory after `xip`, which replaces it;
    - run the XCTest warm-up from `/`;
    - give the verify clone the branch ref that staging fetches.
  - **Not yet proven:** a control-plane shadow run, which needs the supervisor upgrade and access to be configured first.
  - **Earlier, 2026-10-11:** the Command Line Tools image fails closed ("no such module 'XCTest'") and is not re-probed.

Remaining steps (each needs the step before it):

| Step | Who | Needs |
| --- | --- | --- |
| Probe-name fix reviewed and merged in proofglass `main` (the install source until the cut-over) | Proof Glass owners | — |
| Add `PROOFGLASS_TART_XCODE_GOLDEN=proofglass-macos-xcode-golden` to `config.env`, then a drained upgrade from a recorded proofglass `main` commit; `/v1/status` must list `ai-usage-macos@1` and all three docsort lanes. The image was built and verified on 2026-10-12 | Agent (operator) | The fix |
| Confirm the Proof Glass App (`proof-glass-runner`, id 3987831) includes ai-usage | Owner (GitHub UI) | — |
| New supervisor token whose `allowed_repos` keeps every repository the Mac serves today and adds ai-usage (token scope cannot be edited); installed through `--token-stdin`, and the old token revoked only after the new one works | Owner (console), agent installs | — |
| `gh variable set PROOFGLASS_CI_ROUTE_URL -R JonathanPorta/ai-usage -b https://api.proofglass.io/api/ci/route` | Agent | The upgrade and token |
| Extend `PROOFGLASS_CI_RULESET_TOKEN` (fine-grained, comms-core only today, Administration read and write) to ai-usage | Owner | Only for protected-main evidence, so it is deferred to the displace phase and not needed for the PR shadow trial |
| Shadow trial on a same-repository PR into main: the advisory `proofglass / ai-usage-macos [shadow]` check runs; ai-usage's gate stays red by design | Agent | All of the above |
| Promote: a reviewed policy change sets `pull_requests_mode` and main `mode` to `displace` | Owner approval | A passing shadow trial |

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

Current state, 2026-10-12.

- **Manifest:** `signing.ed25519-detached` (pilot). **App:** ad-hoc (`codesign --sign -`). Publication is disabled.
- **Standard:** `signing.apple-macos` is a merged **proposal** (blessed-cicd #352), not released tooling. Its requirements include:
  - a build with no signing authority;
  - inputs verified before signing authority is granted;
  - signing, notarization and stapling before the candidate digests;
  - separate code-signing, notarization and publication authorities;
  - an App Store Connect API key, never an account password.
- **Executor (decided, OD-2, blessed-cicd #362, merged):** Proof Glass is the only macOS and Windows executor. That covers builds, tests, signing, notarization, stapling and credential canaries, with no hosted fallback. Ordinary build and test guests stay credential-free. Signing runs in a dedicated native-finalization workload.
- **Implementation, in progress upstream (ai-usage adds nothing of its own):**
  - **proofglass#157 (PRD 18)** is the control plane for that workload, stacked on #154. It has OIDC admission bound to `main`, the exact workflow, Environment and run attempt, and its predecessors' evidence digests. It defines authority-scoped profiles (`macos-devid-sign` with `apple-codesign`, `macos-devid-notarize` with `apple-notary`, `macos-devid-staple`, the canaries) and authority-scoped supervisor tokens.
  - The supervisor half and the Blessed integration are separate, later PRs.
  - #157 deploys on merge and needs approval of its saved Terraform plan.
  - **blessed-cicd#361 (S-1)** is the shared finalization chain. It waits for that Proof Glass integration, which will replace its GitHub-Environment readers and canaries before it merges. #351 is superseded as the dependency.
- **The hosted-executor patch is obsolete.** A local branch (`jp/f/native-macos-finalization-executor`, `8680582`, never pushed) made the hosted runner an explicit setting. That doesn't satisfy OD-2, so it is kept locally only, for reference, until its disposition is clear.
- **Owner decision still open:** H-1, the pipeline property-2 amendment in #361. The executor decision (OD-2) is already made.
- **Credentials (blessed-cicd `tasks/native-distribution-credentials.md`):**
  - **Allowed now:** inspection only. Action 1 (team `52KRCFBNVN` membership and Account Holder), and listing existing Developer ID Application certificates and App Store Connect API keys (public metadata only).
  - **On hold under OD-2:** creating or exporting a certificate or `.p12`, creating or downloading a `.p8`, and actions 4, 5 and 7 (Environments, BWS domains, canaries). These wait until each credential has an approved storage destination and recovery path. The superseded GitHub-hosted reader path is not provisioned.
  - **Read-only checks so far:** this Mac's keychain has no Developer ID Application identity, and the local BWS token sees no Apple project. That token's view is limited, so absence there proves nothing.

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

Current state, 2026-10-12.

**Inventory.** One count, taken from the tracked files and commits:
- **What was copied:** 199 files from the private, unlicensed blessed-cicd repository. That's 166 in `scripts/release` and 25 in `scripts/signing` (commit `b97eda2`), plus 8 in `tools/blessed` (commit `5ccb2ee`).
- **Where they are:** only on this PR's public branch, never on `main`.
- **Earlier counts:** "201" also counted `scripts/sync-blessed-scripts.sh` and `scripts/.blessed-scripts-version`, which ai-usage wrote itself and which have no blessed-cicd origin. ai-usage's own `scripts/release-tools/` (3 files) is not a copy either.
- **Integrity:** the copies match their manifests, so there is no drift. `make sync-scripts` refuses further copying.

| Class | Files | Notes |
| --- | --- | --- |
| Used by CI and release targets | 20 | release: `validate-plan.sh`, `build-candidate.sh`, `validate-candidate.sh`, `lib/release-lib.sh`, `lib/lib-json-schema.sh`, two schemas, `VERSION`, `MANIFEST.sha256`; signing: `sign.sh`, `keygen.sh`, `verify.sh`, `lib/signing-lib.sh`, `VERSION`, `MANIFEST.sha256`; tools/blessed: `spec-check.sh`, `design-check.sh`, `lib-json-schema.sh`, the handoff schema, `VENDOR-SHA256SUMS.txt` |
| Copied but unused | 179 | Other products' publication, package-repository and promotion tooling, templates, tests and fixtures |
| Internal metadata (overlaps the rows above) | about 45 | Another product's domain, repository names, and its public updater key with real signatures in test fixtures; the private upstream's name in schema ids; a personal host name in a changelog. None is a credential |
| Authentication material | 0 | No private key, token or password |

**The proposed replacement:** [blessed-cicd#359](https://github.com/JonathanPorta/blessed-cicd/pull/359), a draft.
- **What it exports.** `public-consumer-core` has 21 files:
  - 9 from `release-v0.8.0`;
  - 6 from `signing-v0.4.0`;
  - 6 from a new private `checkers` category. Their hashes equal ai-usage's copies.

  That covers everything the targets above use; the manifests and `VENDOR-SHA256SUMS.txt` are regenerated, not copied.
- **Credentials.** Two BWS authority domains, `export-signing` and `export-publish`, replace a direct GitHub secret. `export-publish` is a fine-grained token for exactly the mirror, with contents write. Its runtime check passes only on GitHub's explicit 404 denial for the private repository, and fails closed on any other error. That fix is local commit `1847e1e`, not pushed; see the hand-off.
- **Provenance.**
  - The detached signature proves that the export key's holder signed the manifest, but only if the consumer pinned that key out of band.
  - `SOURCE.json` is a signed claim that a public reader cannot check independently.
  - The mirror's attestation proves only that the mirror processed those bytes.
- **What merging does.** Merging #359 **can publish the private `checkers-v0.1.0` category release** through the existing workflow. It does **not** enable or publish the public export, which stays off until a license, both authority domains and an explicit variable exist.
- **Not yet clean.** One upstream schema still names another product's key id; it must be fixed before any export.

**Owner decision (proposal; nothing here is granted):**
1. **ai-usage's own code: Apache-2.0.** A root `LICENSE` in ai-usage would cover ai-usage's own files. It must not be read as licensing the copied blessed-cicd files.
2. **The export: Apache-2.0, at `scripts/exports/public-consumer-core/LICENSE` in blessed-cicd only.** It covers exactly those 21 files. There is no root license in private blessed-cicd, and no third-party material is relicensed.
3. **Distribution:** the public mirror `JonathanPorta/blessed-cicd-scripts`. Updates arrive as signed draft releases that the owner publishes. ai-usage would pin the export key and replace its 199 copies with the export.
4. **The earlier copies, decided separately.** Licensing the export does not cover the 199 earlier copies. Deleting them in a new commit removes them from the current tree only; every earlier public copy stays in history and in any clone or fork. The choices for those copies:
   - accept their historical disclosure as is;
   - license those exact historical files;
   - approve a history rewrite of this unmerged branch, which can't recall existing clones.

   Each needs its own approval.

## Owner decisions and actions

See the PR #5 description for the ordered approval list and its effects.
