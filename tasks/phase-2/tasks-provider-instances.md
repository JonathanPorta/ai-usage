# Tasks: Multiple Provider Instances, Plus claude-az (Phase 2)

> Generated from [PRD: Provider Instances](prd-provider-instances.md). ACs approved 2026-10-20.
> Entry condition: phase 1 merged as `1b60b5f`, main CI green (Proofglass job
> `job_01M5E28PRY63TZXWP7V2QX06HS`). Branch `jp/f/provider-instances` cut from that main.

## Acceptance Criteria Traceability

| AC | Short description | Tasks |
| --- | --- | --- |
| AC-1 | Config contract and cross-account path checks | 2.0 |
| AC-2 | `instance add/list/enable/disable`, locked writes, version boundary | 4.0 |
| AC-3 | Collection per account, order, disabled marker, isolation | 3.0 |
| AC-4 | State namespace, duplicate ids, carry-forward from `1b60b5f`, disable/re-enable | 1.0, 3.0 |
| AC-5 | `CLAUDE_CONFIG_DIR` isolation, derived paths, no stats cache | 3.0 |
| AC-6 | Report per account, type-based rules, id-based lookups | 5.0 |
| AC-7 | Canonical type documented | 8.0 |
| AC-8 | Settings accounts and "Add account…" states with renders | 6.0 |
| AC-9 | 2.3.0, `instances` capability, older-collector visibility, decoding | 4.0, 5.0, 6.0 |
| AC-10 | Owner's Mac check after merge and reinstall (pending) | 8.0 (recorded as pending) |
| AC-11 | Validation, CI, docs, phase gate | 7.0, 8.0 |

## Relevant Files
- `ai_usage_plugins/base.py`: `ProviderPlugin.instance_settings`, `instance_only_settings`, `ProviderDef.type`.
- `ai_usage_plugins/claude.py`: `config_dir`, derived paths, `usage_cache_file` path role, `/usage` environment.
- `ai_usage_service.py`: instance validation, account entries, collection, doctor, progress, `instance` CLI, `configure`, version boundary.
- `ai_usage_report.py`: per-account catalogue, `type`, rules by type, lookups by id, capability, older-collector note.
- `tests/parity/driver.py`, `tests/parity/test_parity.py`: the declared intentional differences, and the carry-forward from `1b60b5f`.
- `tests/test_provider_instances.py` (new): config, CLI, collection, state, isolation, report.
- `macos/Sources/AIUsageCore/{Report,Control,Presentation}.swift`, `macos/Sources/AIUsageApp/SettingsView.swift` and a new `AddAccountView.swift`, plus the snapshot renderer and XCTest.
- `docs/PLUGINS.md`, `docs/CLI.md`, `docs/APP.md`, `docs/SPEC.yml`, `docs/DECISIONS.md`.

### Notes
- Tests use isolated HOMEs, sandboxes, a fake `claude` and a fake launchctl. Never the owner's
  `~/.claude`, `~/.claude-az` or `~/.ai-usage`.
- The phase-1 golden files are never rewritten.

## Tasks

- [x] 1.0 Parity contract for phase 2 (validation first)                   <- Serves: AC-4, AC-11
  - [x] 1.1 `driver.INTENTIONAL_CHANGES` declares the exact differences from the golden files (PRD, Compatibility). `driver.expected(name)` applies them to the golden document; nothing else is normalized.
  - [x] 1.2 `test_parity` compares every scenario with `expected(name)`. It asserts that the declared list is exactly these four changes, and that the golden files' bytes are unchanged (sha256 recorded in the test).
  - [x] 1.3 A carry-forward from `1b60b5f` (`tests/parity/PHASE1`): the phase-1 tree's first poll, then this tree, equals `expected("all_providers")`. Required in CI through `AI_USAGE_REQUIRE_PARITY_HISTORY`.
  - **Validates when:** before any feature code, the new tests fail only on the declared differences, which shows the list is complete and minimal. After 2.0–5.0 they pass.

- [x] 2.0 Config contract                                                   <- Serves: AC-1
  - [x] 2.1 Tests first, in `tests/test_provider_instances.py`:
    - every rejection: id pattern, duplicate, bundled id, unknown plugin, a plugin without instance support, empty label, non-object settings, unknown key, a missing or empty `config_dir`, and `providers.claude.config_dir`;
    - a collision between accounts (the same `projects_dir`; the same `usage_cache_file` through a symlink alias);
    - the default account's `usage_cache_file` checked as a file role.
  - [x] 2.2 `base.py`: `instance_settings`, `instance_only_settings`. `claude.py`: implement them; add `usage_cache_file` to `path_roles`.
  - [x] 2.3 `ai_usage_service.py`: `validate_instances`; `account_entries(config)` (built-ins, then instances); `account_settings(config, id)`; path roles over every account.
  - **Validates when:** the 2.1 tests pass, and the parity scenarios still pass (no config without `instances` changes).

- [x] 3.0 Collection, state and account isolation                           <- Serves: AC-3, AC-4, AC-5
  - [x] 3.1 Tests first:
    - two Claude accounts with a fake `claude` that records `CLAUDE_CONFIG_DIR`, argv and the cache it refreshes;
    - the collector's environment has `CLAUDE_CONFIG_DIR` set to the added account's folder;
    - shared message ids;
    - an added folder without `stats-cache.json`;
    - row order and `--progress` order;
    - a disabled account's marker row;
    - disable, append data, re-enable: no recount, and the new data counted once;
    - a failing account isolated.
  - [x] 3.2 `collect_snapshot` / `detect_providers` / progress iterate the account entries. Instances get `state["instances"][id]`, and their rows are stamped with the instance id.
  - [x] 3.3 `claude_usage_environment(config_dir)`: remove `CLAUDE_CONFIG_DIR`; set it to the absolute expanded folder for an added account.
  - **Validates when:** the 3.1 tests and parity pass.

- [x] 4.0 CLI, configure and the version boundary                          <- Serves: AC-2, AC-9
  - [x] 4.1 Tests first:
    - add, list, enable, disable;
    - each refusal leaves the bytes unchanged;
    - the lock is shared with `configure`;
    - a symlinked config stays linked, and the mode is kept;
    - the version refusal with a 2.2.0 LaunchAgent script, acceptance with 2.3.0, and refusal with no LaunchAgent;
    - `configure instances.<id>.enabled` and `monthly_subscription_usd`;
    - install and uninstall leave `instances` and the instance state alone.
  - [x] 4.2 `VERSION = "2.3.0"`. An `update_config` helper is shared with `configure`. `installed_collector_version` moves into the service, and the report reuses it.
  - **Validates when:** the 4.1 tests pass.

- [x] 5.0 Report                                                           <- Serves: AC-6, AC-9
  - [x] 5.1 Tests first:
    - two accounts, each with its own quota windows (per-model weekly), stats-cache note, costs and price;
    - `type` on every provider;
    - the order table;
    - an older installed collector gives `not_configured` with a note;
    - `capabilities.instances`.
    - A negative control: a lookup left on `"claude"` fails the two-account test.
  - [x] 5.2 A per-config catalogue. Rules use `definition.type`; lookups use `definition.id` (the stats-cache rows; Grok's models, billing and API cost). `REPORT_PROVIDER_RULES` reasons are updated.
  - **Validates when:** the 5.1 tests and parity pass.

- [x] 6.0 App                                                              <- Serves: AC-8, AC-9
  - [x] 6.1 XCTest first:
    - decoding of today's fixture, with no `type` or `instances`, and of a two-account fixture;
    - `ConfigChange` assignments for an instance;
    - id proposal from a label (slug, a prefix when one is needed, uniqueness, never a bundled id);
    - the add-account validation states;
    - the `instance add` arguments.
  - [x] 6.2 `Report.swift` (`type`, `capabilities.instances`); `Control.swift` (instance assignments, `InstanceAdding`); `AddAccountView` in Settings; the fixture gains a second Claude account.
  - [x] 6.3 Snapshot renders of each state, plus Settings with two accounts. Design rules 30–38 reviewed.
  - **Validates when:** `make app-test` and `make app-build` pass, and renders exist for every state.

- [x] 7.0 Validation and CI                                                <- Serves: AC-11
  - [x] 7.1 `make check`; the suite under Python 3.9 and 3.14; `fixture-check`, `app-test`, `app-build`, `design-build-check`, `verify-scripts`, `spec-check` and `design-check --strict`.
  - [x] 7.2 Push and open a regular ready-for-review PR. Record the CI run and the executor that actually ran.
  - **Validates when:** every 7.1 command exits 0, and the final revision's CI is green with the macOS lane run on Proofglass.

- [x] 8.0 Documentation and records                                        <- Serves: AC-7, AC-10, AC-11
  - [x] 8.1 `docs/PLUGINS.md` (instances, the type definition, isolation); `docs/CLI.md` (`instance`, the report additions); `docs/APP.md`; `docs/SPEC.yml`; `docs/DECISIONS.md`.
  - [x] 8.2 Record AC-10 as pending the owner's check after merge and reinstall. Update the session state, and run the phase-gate audit (rule 13) before the PR description.
  - **Validates when:** `make spec-check ARGS=--strict` exits 0, and the PR description maps each AC to its evidence.

## Implementation notes (2026-10-20)

How the work differed from the plan above. None of it changes an acceptance criterion.
- **1.0:** `driver.expected(name, changed_from)` applies the declared changes only to the steps this tree runs. In a carry-forward, the first steps run on the older revision. The golden files are pinned by SHA-256.
- **3.0:**
  - An added account's missing `stats-cache.json` writes no row, so its daily-stats source shows as waiting instead of a failure on every check. The default account is unchanged.
  - `VerbatimMoveTests.PHASE2_CHANGED` lists the four Claude functions phase 2 changes on purpose. The test fails if a listed function no longer differs.
- **4.0:** `instance add` requires `--id`; the app proposes one. The installed-version check reads the LaunchAgent's script. If no collector is installed, the add is refused as well.
- **6.0:**
  - A second committed fixture, `report-fixture-two-accounts.json`, carries the added account, so every existing render keeps its fixture.
  - The pre-phase-2 fixture is kept as a test resource (`report-collector-2.2.0.json`) to prove decoding.
  - There are 24 new renders: Settings and the overview with two accounts, the added account's quota detail, and the 9 add-account states, each in light and dark. The owner accepted them on 2026-10-20, after the merge, and they were then added to `docs/design/app/references/SHA256SUMS`.
- **Review** (independent, read-only), fixed in `bf9eca1`:
  - a relative `--config-dir` resolved against the daemon's working directory;
  - an account accepted for a plugin that failed to load;
  - a config-file collision checked only after the write;
  - nested account folders;
  - `CLAUDE_CONFIG_DIR` was symlink-resolved, which Claude Code may tie to the login; it's now kept as named.

  Each fix has a regression test that failed before it.
- **AC-10** is pending: the owner checks `claude` and `claude-az` on their Mac after the merge and an approved reinstall.

## Corrective work after the merge (2026-10-20)

An independent review after #11 merged found three defects. They are fixed on `jp/b/fix-account-isolation-findings`. Each regression failed before its fix and passes after it. No AC changes.
- [x] C1 Refresh-folder collisions hidden by read-path overrides <- AC-1, AC-5
  - The plugin hook `refresh_dir` and `validate_refresh_dirs` cover equal and nested folders, symlink aliases, and the default account's `~/.claude`.
  - Accounts that never refresh aren't compared.
- [x] C2 `instance add` saves the expanded absolute folder <- AC-2
  - Symlinks are kept; unset variables are refused.
  - With the variable absent, list, `doctor`, `once` and disable all work.
- [x] C3 The add-account result is the operation's own <- AC-8
  - `AccountSaveResult`, `isAddingAccount` and `AccountSubmission`.
  - XCTests use a held refresh with other actions running in between.
  - Renders are unchanged.
- [ ] Owner: merge the corrective PR. Then the reinstall and AC-10, per `procedure-reinstall-ac10.md`.
