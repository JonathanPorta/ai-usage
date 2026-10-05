# Tasks: Native macOS menu-bar app

> Generated from [PRD: Native macOS menu-bar app](prd-macos-menu-bar-app.md)

## Acceptance Criteria Traceability

| AC | Tasks |
| --- | --- |
| AC-1–4 | 1.1–1.5 |
| AC-5–14 | 2.1–2.6 |
| AC-15–22 | 3.1–3.8, 4.1–4.3 |

## Relevant Files
- `docs/design/app/**`, `DESIGN.md`, `DESIGN_SYSTEM.md`, `docs/*.md`, `docs/SPEC.yml`, `tools/blessed/`
- `ai_usage_report.py`, `test_ai_usage_report.py`
- `macos/Package.swift`, `macos/Sources/{AIUsageCore,AIUsageDesign,AIUsageApp}`, `macos/Tests/AIUsageCoreTests`
- `macos/scripts/{generate_design_tokens.py,build_app.sh}`, `Makefile`, `.github/workflows/tests.yml`

## Tasks

- [x] 1.0 Intern design handoff 0.1.1
  - [x] 1.1 Verify the archive SHA-256, then copy the archive and its extracted contents into `docs/design/app/source/`. *Validation:* `shasum` matches; `shasum -c` reports 15 OK.
  - [x] 1.2 Write `DESIGN.md`, `DESIGN_SYSTEM.md`, the app and icon buckets, and the spec pack (IDEA, MVP, DECISIONS, SPEC.yml, APP, CLI, RELEASE). *Validation:* `make spec-check ARGS=--strict` exits 0.
  - [x] 1.3 Add the canonical DTCG tokens, the generator and the generated Swift. *Validation:* `make design-build-check` passes, and fails against an edited copy.
  - [x] 1.4 Vendor the Blessed checkers and add the Make targets and CI job. *Validation:* `make design-check ARGS=--strict` exits 0 and fails on a bad checksum (negative control).
  - [x] 1.5 Commit the import on its own. *Validation:* `git log --stat`.
- [ ] 2.0 Reporting layer (`ai_usage_report.py`)
  - [ ] 2.1 Read the CSV in a single pass, group transactions and work out attempt times. *Validation:* unit test of attempt and summary values.
  - [ ] 2.2 Aggregate days by `record_kind` and assign day states. *Validation:* tests for sums, latest period total, zero vs missing vs not collected.
  - [ ] 2.3 Quota windows: readings, resets, per-window freshness, limits, omitted and retired windows. *Validation:* mixed-freshness tests in both directions, plus `reset_passed` boundary tests.
  - [ ] 2.4 Sources and failures, cache preservation, disabled providers, log cycle failures. *Validation:* partial-failure and disabled tests.
  - [ ] 2.5 Costs, models, service and schedule. *Validation:* tests; service probing skipped in tests.
  - [ ] 2.6 Read-only guarantee and performance. *Validation:* sentinel-binary and file-snapshot test; timed real run under 10 s.
- [ ] 3.0 Native app core
  - [ ] 3.1 SwiftPM package (Core, Design, App) and a bundle script. *Validation:* `make app-build` produces the `.app`.
  - [ ] 3.2 Report models and decoding against the committed fixture. *Validation:* `make app-test`.
  - [ ] 3.3 `ReportClient` and `CollectorClient`, resolved from the plist plus environment overrides. *Validation:* unit tests with a temporary plist.
  - [ ] 3.4 `AppStore`: cached load, refresh policy, single-flight Collect now, observers. *Validation:* store tests, AC-16 and AC-20.
  - [ ] 3.5 Popover overview (status, notice, cards, footer). *Validation:* run the app; walk the accessibility tree.
  - [ ] 3.6 Provider detail. *Validation:* manual walkthrough.
  - [ ] 3.7 History window. *Validation:* manual walkthrough, plus an update test while it is open.
  - [ ] 3.8 Menu-bar glyph states. *Validation:* manual check.
- [ ] 4.0 Validation and documentation
  - [ ] 4.1 `make check`, `make app-test`, and the strict checks in CI.
  - [ ] 4.2 Run against real data (read-only) and against the sandbox (Collect now). Record the results in the design.md verification log.
  - [ ] 4.3 Update DESIGN_SYSTEM implementation status and the component manifest status.
- [ ] 5.0 Later tasks (accepted design, not in milestone 1)
  - [ ] 5.1 Monitoring view: service, schedule, collection attempts, failures, sources.
  - [ ] 5.2 Service start and stop through `launchctl bootstrap`/`bootout`, with confirmation. Needs a decision on handoff §10 Q1.
  - [ ] 5.3 Pause and resume: a new collector config key read between polls. The collector keeps running and Collect now stays available. Needs §10 Q2.
  - [ ] 5.4 Settings: interval, provider enablement and monthly prices, written to `config.json` atomically. History accounting updates live.
  - [ ] 5.5 Per-provider Collect now progress: requires `once --progress` JSON-lines output from the collector.
  - [ ] 5.6 Notifications for limits and failures (§10 Q5).
  - [ ] 5.7 Open at login (`SMAppService.mainApp`).
  - [ ] 5.8 Accepted native visual baselines in `docs/design/app/references/`, which retires the waiver.
