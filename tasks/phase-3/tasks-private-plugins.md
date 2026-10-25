# Tasks: Private Plugins From a Local Folder (Phase 3)

> Generated from [PRD: Private Plugins](prd-private-plugins.md). The analysis, D1–D4 and AC-1 to
> AC-13 were approved by the owner on 2026-10-24, with five corrections made in the PRD.
> Branch `jp/f/private-plugins`, cut from the approved PRD branch (#22).

## Acceptance Criteria Traceability

| AC | Short description | Tasks |
| --- | --- | --- |
| AC-1 | Discovery and manifest | 2.0 |
| AC-2 | Owner-only permissions, symlinks, no repairs | 2.0 |
| AC-3 | Enablement on every path, the rows table, `configure` scope | 4.0 |
| AC-4 | Protocol, collection, report values end to end | 3.0, 5.0 |
| AC-5 | Timeouts | 3.0 |
| AC-6 | Output limits and privacy | 3.0 |
| AC-7 | Process cleanup | 3.0 |
| AC-8 | Malformed responses and crashes | 3.0 |
| AC-9 | Lifecycle and install | 4.0, 6.0 |
| AC-10 | Phase-2 behavior preserved | 1.0 |
| AC-11 | Version compatibility and app | 5.0, 7.0 |
| AC-12 | Docs, validation, privacy | 8.0 |
| AC-13 | Owner's work laptop (pending until the owner performs it) | 8.0 (recorded as pending) |

## Relevant Files
- `ai_usage_plugins/external.py` (new): discovery, permission checks, manifest, runner, response validation.
- `ai_usage_plugins/base.py`: `ProviderDef.quota_basis` and `external`.
- `ai_usage_plugins/__init__.py`: `MODULES` gains `external`.
- `ai_usage_service.py`: accounts, collection, doctor, `plugin` CLI, `configure`, version 2.4.0.
- `ai_usage_report.py`: catalogue, quota basis, setup notes, capability, `plugin_errors`.
- `scripts/check_app_bundle.py`: refuse a bundled `plugins/` folder.
- `tests/test_external_plugins.py` (new); `tests/parity/{driver,test_parity}.py`; `tests/parity/PHASE2`.
- `macos/Sources/AIUsageCore/Report.swift`, `macos/Sources/AIUsageApp/SettingsView.swift`, the snapshot renderer, XCTest, `ai_usage_fixtures.py`.
- `docs/PLUGINS.md`, `docs/CLI.md`, `docs/APP.md`, `docs/SPEC.yml`.

### Notes
- Every test uses an isolated HOME and sandbox. Plugins are generated at test time with the
  shebang set to `sys.executable`. Nothing reads the owner's `~/.ai-usage`, a real plugin, a
  credential or a log.
- App renders come from synthetic fixtures through the offscreen renderer; the owner's running
  app and its config are never touched.
- The phase-1 golden files are never rewritten.

## Tasks

- [ ] 1.0 Parity contract for phase 3 (validation first)                    <- Serves: AC-10
  - [ ] 1.1 `driver.INTENTIONAL_CHANGES` gains exactly `report.collector.capabilities.external_plugins`; `VERSION` becomes 2.4.0. `test_parity` asserts the list.
  - [ ] 1.2 Parity scenarios also run with an empty `plugins/` folder and with one holding only unconfigured and invalid plugins; built-in and added-instance output must match.
  - [ ] 1.3 `tests/parity/PHASE2` (`357bcee`): its first poll, then this tree, matches the expected document from the handoff on.
  - **Validates when:** before feature code, the new tests fail only on the declared differences; after 2.0–5.0 they pass.

- [ ] 2.0 Discovery, permissions and the manifest                           <- Serves: AC-1, AC-2
  - [ ] 2.1 Tests first: each invalid-manifest case, each mode case (0750/0705/0755 folders; 0640/0604/0755 files), symlinks accepted and refused, foreign owner via injected `stat`, no repairs (before/after `lstat` and hashes), discovery never runs a plugin (canary), reasons never echo values.
  - [ ] 2.2 `external.discover()` and `ProviderDef` derivation (profiles, quota basis, per-source error metrics).
  - **Validates when:** the 2.1 tests pass.

- [ ] 3.0 Runner and response validation                                    <- Serves: AC-4, AC-5, AC-6, AC-7, AC-8
  - [ ] 3.1 Tests first: request and environment, row stamping, state round trip; `hang`, closed-stdout, `stderr-flood` timeouts; `chatty` cap; 1 MiB stderr success with byte count; `secret-printer` in four places; `forker`, `pipe-holder`; every malformed case; size refusals.
  - [ ] 3.2 `external.run()`: selectors over stdin/stdout/stderr, one deadline, 1 s pipe grace after exit, `terminate_provider_process` always, pipes closed; fixed reason texts.
  - [ ] 3.3 `external.validate_response()`: the row table, roles, limits, `state`.
  - **Validates when:** the 3.1 tests pass, and the poll-time bounds hold.

- [ ] 4.0 Collector integration                                             <- Serves: AC-3, AC-9
  - [ ] 4.1 Tests first: the rows table (each row), enable refusals on `plugin enable` and `configure` (unknown, invalid, missing, older installed collector; file bytes unchanged), disable for valid/invalid/missing with an older collector, re-enable resumes, `configure` scope, order, removed folder and restore, upgrade keeps state, install/reinstall/uninstall leave `plugins/` byte-identical.
  - [ ] 4.2 `accounts()` with external ids; `account_settings`; `collect_snapshot`; `detect_providers`; `plugin list/enable/disable`; `configure`; `VERSION` 2.4.0; `MODULES`; `check_app_bundle.py`.
  - **Validates when:** the 4.1 tests pass and 1.0 still passes.

- [ ] 5.0 Report                                                             <- Serves: AC-4, AC-11
  - [ ] 5.1 Tests first: end-to-end daily totals (today, yesterday, replacement), used-percent window (label, basis, percent, resets, current, low), events with checkpoint, remaining-percent window, no cross-basis display, "Not turned on", older installed collector note, `plugin_errors` entries.
  - [ ] 5.2 Catalogue entries, `external`, `quota_basis` in `build_windows`, setup notes selected by kind, capability.
  - **Validates when:** the 5.1 tests pass and 1.0 still passes.

- [ ] 6.0 Install lifecycle                                                  <- Serves: AC-9
  - [ ] 6.1 The install tests cover `external.py` as a managed module, and a sandbox `plugins/` folder left byte-identical.
  - **Validates when:** `tests/test_install_lifecycle.py` passes.

- [ ] 7.0 App                                                                <- Serves: AC-11
  - [ ] 7.1 XCTest first: decode `external` and `capabilities.external_plugins`; today's fixtures decode unchanged; the toggle is hidden without the capability.
  - [ ] 7.2 `Report.swift`, Settings; a synthetic external-provider fixture; renders of the overview card, the detail and Settings.
  - **Validates when:** `make app-test` passes and the renders are produced offscreen.

- [ ] 8.0 Docs, validation and the phase gate                               <- Serves: AC-12, AC-13
  - [ ] 8.1 `docs/PLUGINS.md` (contract, privacy, trust statement word for word), `docs/CLI.md`, `docs/APP.md`, `docs/SPEC.yml`, the `plugin` help.
  - [ ] 8.2 `make check`, `make app-test`, `make spec-check ARGS=--strict`, `make design-check ARGS=--strict`; the privacy scan of the diff.
  - [ ] 8.3 The phase-gate audit; the session state; the PR with evidence. AC-13 recorded as pending.
  - **Validates when:** every command passes and fresh required CI is green.
