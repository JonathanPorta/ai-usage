# Tasks: Provider Plugin Core (Phase 1)

> Generated from [PRD: Provider Plugin Core](prd-provider-plugin-core.md). ACs approved 2026-10-20.
> Entry condition: #5 merged; `jp/f/provider-plugin-core` cut from that main. Each commit is one
> reviewable step: parity harness first, then one provider per commit.

## Acceptance Criteria Traceability

| AC | Short description | Tasks |
| --- | --- | --- |
| AC-1 | Five modules behind one interface, behavior kept | 2.0, 3.0 |
| AC-2 | Orchestration derives from the registry (architectural check) | 2.0, 4.0 |
| AC-3 | Parity with the merged pre-refactor revision | 1.0, 3.0 |
| AC-4 | Existing config/state/CSV usable, symlink fix kept | 1.0, 3.0 |
| AC-5 | Per-provider load/collect failure isolation | 2.0, 4.0 |
| AC-6 | Install lifecycle incl. rollback and uninstall | 5.0 |
| AC-7 | All distribution paths; runs outside the checkout | 5.0, 6.0 |
| AC-8 | Full validation + fresh Proofglass CI | 7.0 |
| AC-9 | docs/PLUGINS.md, specs, task records | 8.0 |

## Relevant Files
- `ai_usage_service.py`: orchestration, config, install/uninstall, CLI; loses the provider code.
- `ai_usage_report.py`: `SourceDef`/`ProviderDef`/`PROVIDERS` come from the registry.
- `ai_usage_fixtures.py`: sandbox and app fixture; imports the service (unchanged API).
- `ai_usage_plugins/` (new): `__init__.py` registry, `base.py`, `support.py`, five provider modules.
- `test_ai_usage_service.py`, `test_ai_usage_report.py`: existing suites (`IsolatedHome` l.70).
- `tests/parity/` (new): driver, scenarios, golden files.
- `macos/scripts/build_app.sh`: bundle copy (l.21).
- `Makefile`: `py_compile` list (l.57), `check`, `fixture-check`, `app-build`, `release-build`.
- `.github/workflows/tests.yml`: required CI (macOS on Proofglass).
- `docs/PLUGINS.md` (new), `docs/RELEASE.md` (collector row l.13), `docs/CLI.md`, `docs/SPEC.yml`.

### Notes
- Run tests with `make check`. App: `make app-test`, `make app-build`. Fixture: `make fixture-check`.
- Lifecycle tests use `IsolatedHome` with a fake `AI_USAGE_LAUNCHCTL`. Never the personal `~/.ai-usage`.

## Tasks

- [x] 1.0 Parity harness and baseline (before any refactor)              <- Serves: AC-3, AC-4
  - [x] 1.1 `tests/parity/driver.py`:
    - loads a given revision's `ai_usage_service.py` by path, in a subprocess with `-I`, HOME, TZ
      and PATH fixed;
    - patches `utc_now` and `time.time` on every loaded `ai_usage*` module to `FIXED_NOW`;
    - runs `once`, `doctor --json` and `build_report(..., now=FIXED_NOW, service=<fixed>)`.
  - [x] 1.2 Scenarios, each with its own fake executables and data:
    - all five enabled with data;
    - one provider disabled;
    - executables missing;
    - a provider's command errors or times out;
    - Claude subscription cache fresh and stale;
    - Grok billing from the log;
    - antigravity events;
    - existing state and CSV carried in (incremental offsets);
    - a symlinked config.
  - [x] 1.3 `NORMALIZE` lists the fields to normalize, in one place: `transaction_id`, and
    sandbox root paths → `/sandbox`. A comparison reports the first differing row and field.
  - [x] 1.4 Capture the golden files at `BASELINE_SHA` (the #5 merge commit on main), recording
    the SHA in `tests/parity/BASELINE`. Commit the harness and golden files as the branch's
    first commit.
  - **Validates when:**
    - Running the driver twice at `BASELINE_SHA` gives byte-identical output (determinism).
    - A deliberate one-field change to a scenario fails the comparison and names the field
      (negative control).
    - `git merge-base --is-ancestor $(cat tests/parity/BASELINE) HEAD` exits 0.

- [x] 2.0 Interface, support module and registry                        <- Serves: AC-1, AC-2, AC-5
  - [x] 2.1 `ai_usage_plugins/base.py`:
    - `ProviderPlugin` with fields `id`, `default_settings`, `executable_names`, `collect`,
      `doctor_extra`, `path_roles`, optional `integration_plan`, `report`;
    - `CollectContext`;
    - `SourceDef`/`ProviderDef`, moved from the report with the same constructor signatures.
  - [x] 2.2 `ai_usage_plugins/support.py`: move the 30 shared helpers from the service
    unchanged. The service re-imports them under the same names.
  - [x] 2.3 Registry: `BUNDLED` in today's order, plus `load_plugins()`, which imports each
    module on its own and records `(id, error)` on failure.
  - **Validates when:**
    - `make check` passes, with no behavior change yet.
    - An AST test shows that no module in `ai_usage_plugins` imports `ai_usage_service` or
      `ai_usage_report`.
    - A sentinel test shows that importing `ai_usage_plugins` runs no subprocess and creates no
      file.

- [x] 3.0 Move each built-in, one commit per provider                   <- Serves: AC-1, AC-3, AC-4
  - [x] 3.1 Gemini CLI (the smallest), then 3.2 Antigravity (integration plan + status-line
    wrapper), 3.3 Codex (uses `version`), 3.4 Grok (uses `version`), 3.5 Claude (largest;
    subscription usage).
  - [x] Each move:
    - moves the collector code, `DEFAULT_CONFIG` entry, doctor `extra`, path roles, integration
      plan and `ProviderDef`;
    - keeps the same function bodies.
  - **Validates when:**
    - After each provider: the parity suite matches the golden files, and `make check` passes.
    - After 3.5: `DEFAULT_CONFIG` built from the registry equals the baseline `DEFAULT_CONFIG`.
    - After 3.5: the baseline revision's `config.json`, `state.json` and `usage.csv` load
      without a rewrite (files' mtimes and bytes unchanged after `load_config`).
    - After 3.5: `once` appends rows and updates state the same way as the baseline.

- [x] 4.0 Generic orchestration and failure isolation                   <- Serves: AC-2, AC-5
  - [x] 4.1 `collect_snapshot`, `detect_providers`, `configured_path_roles`,
    `prepare_integration_plans`, `parse_config_assignment` and `ai_usage_report.PROVIDERS`
    iterate the registry.
  - [x] 4.2 A load failure gives one `collection/unexpected_error` row for that id, and the
    report skips that provider without crashing.
  - [x] 4.3 An architectural test (AST, not a text grep) asserts that those functions contain
    no provider-id string literals and no `name == "<id>"` branches. Its documented exceptions
    are listed in the test, each with a reason.
  - **Validates when:**
    - The test from 4.3 passes, and fails when a `"claude"` branch is re-added (negative
      control).
    - Test: `claude.py` raising at import → one `unexpected_error` row for claude, and the other
      four providers' rows equal the parity golden rows.
    - Test: `collect()` raising → today's row, with the others unchanged.

- [x] 5.0 Installation lifecycle and the module set                     <- Serves: AC-6, AC-7
  - [x] 5.1 Each `ai_usage_plugins/*.py` becomes a managed file (`plugin_module:<name>`, 0600)
    in the existing transaction, plus the directory (0700). The collector sets
    `sys.dont_write_bytecode` before importing the package.
  - [x] 5.2 Obsolete owned module files: remove them on install and uninstall only if their hash
    matches the record.
  - [x] 5.3 Black-box tests (`IsolatedHome`, fake launchctl):
    - fresh install;
    - upgrade from a single-file install written by the `BASELINE_SHA` installer;
    - reinstall (no writes);
    - a failed upgrade, by injecting an error at the Nth managed write: every file is restored
      to the old bytes and no plugin file is left;
    - uninstall: removes owned, unchanged program files; keeps config, usage, state, backups and
      a modified module file; leaves integrations the installer doesn't own alone;
    - a symlinked config still updates its target.
  - **Validates when:** the 5.3 tests pass, and the rollback test fails if the rollback is
    disabled (negative control).

- [x] 6.0 Distribution paths and running outside the checkout           <- Serves: AC-7
  - [x] 6.1 `build_app.sh` copies `ai_usage_plugins/*.py` into
    `Resources/collector/ai_usage_plugins/`.
  - [x] 6.2 Test: after an install into an isolated HOME, run
    `python3 -I ~/.ai-usage/collector.py once` from an empty temporary cwd (no PYTHONPATH) and
    get rows for all five providers.
  - [x] 6.3 After `make app-build`, run the bundled `collector/ai_usage_report.py` the same way
    against the sandbox. Its report equals `make report-sandbox` output at the same `--now`.
  - **Validates when:**
    - The tests in 6.2 and 6.3 pass.
    - `make fixture-check` passes (the committed `report-fixture.json` is unchanged).
    - The snapshot renders match the accepted baselines under the same rendering conditions.

- [x] 7.0 Validation and CI evidence                                    <- Serves: AC-8
  - [x] 7.1 Add `ai_usage_plugins/*.py` and `tests/parity/*.py` to the Makefile `py_compile`
    list and to whatever lint or spec checks cover Python sources.
  - [x] 7.2 Run `make check`, `make app-test`, `make fixture-check`,
    `make design-check ARGS=--strict` and `make spec-check ARGS=--strict` locally.
  - [x] 7.3 Push the feature branch and open the PR. Record the CI run ID and the executor that
    actually ran the macOS job, from the job's runner labels and logs. A routing response alone
    doesn't count as evidence.
  - **Validates when:** every command in 7.2 exits 0, and the final revision's CI is green with
    the macOS job shown to have run on Proofglass.

- [x] 8.0 Documentation and records                                     <- Serves: AC-9
  - [x] 8.1 `docs/PLUGINS.md` covers the interface, registry, load and collect error behavior,
    packaging, compatibility guarantees, and "external plugins: phase 3, not implemented".
  - [x] 8.2 Update the collector row in `docs/RELEASE.md`, plus `docs/CLI.md`/`docs/SPEC.yml`
    wherever they describe the single-file collector.
  - [x] 8.3 Mark the tasks done, update the session state, and run the phase-gate audit
    (rule 13) before the PR description is written.
  - **Validates when:** `make spec-check ARGS=--strict` exits 0, and the PR description maps
    each AC to its evidence.

## Implementation notes (2026-10-20)

How the work differed from the plan above. None of it changes an acceptance criterion.
- **1.0:**
  - Time is frozen more broadly than planned. `tests/parity/child.py` replaces the `datetime` and `time` modules in every loaded `ai_usage*` module and in `logging`, not only `utc_now`/`time.time`.
  - The golden files were recaptured once from BASELINE (`da111fb`) after a harness fix: the status-line step now sends real bytes. They were never captured from refactored code.
- **3.0:** one commit (`eb1c20e`), not five. The move was mechanical: each top-level definition's source text was copied as is.
  - `VerbatimMoveTests` checks every moved definition against BASELINE by AST. The one allowed edit is `VERSION` → `client_version` for Codex and Grok.
  - Parity matched for every scenario.
- **4.3:** the check is stricter than planned. The whole of `ai_usage_service.py` holds no provider id. `ai_usage_report.py` names providers only in the rules listed in `REPORT_PROVIDER_RULES`.
- **5.x:**
  - Install refuses a source tree whose modules don't all load.
  - The rollback test fails every write in turn. It found a leftover empty recovery directory when the very first write failed, fixed in `65ddbfe`.
  - `scripts/launchd_integration_check.py` (explicitly invoked, real launchd) now copies the package with its variant collectors. It was not run: phase 1 uses only the fake launchctl.
- **6.3:** `make app-build` runs `scripts/check_app_bundle.py`. It compares the bundled reporter's report for the synthetic sandbox at a fixed time with the checkout's, byte for byte.
- **6.0 renders:** `make app-snapshot-check` (local only, not in CI) fails on this Mac (macOS 27.0.1) against the committed `SHA256SUMS` for **both** the baseline `ddb923f` and this branch: 50 of 50. Rendered on the same machine, this branch's 52 renders are byte-identical to the baseline's, which is the "matching rendering conditions" comparison. The reference mismatch predates phase 1.
- **CI:** the Linux job fetches full history and sets `AI_USAGE_REQUIRE_PARITY_HISTORY=1`, so the ancestry, carry-forward and verbatim tests run there instead of skipping.
- **`VERSION`** stays 2.2.0. Phase 1 changes no behavior, and nothing is released.
- **7.3:** PR #10. Final head `c821a35`: run 39513826018 is green, and the macOS lane ran on ProofGlass (job `job_01M5DSK1WAHRE6XN0NBCBYEPJT`). The review findings are addressed in `c821a35`.
