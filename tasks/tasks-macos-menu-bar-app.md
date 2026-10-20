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
- [x] 2.0 Reporting layer (`ai_usage_report.py`)
  - [x] 2.1 Read the CSV in a single pass, group transactions and work out attempt times. *Validation:* unit test of attempt and summary values.
  - [x] 2.2 Aggregate days by `record_kind` and assign day states. *Validation:* tests for sums, latest period total, zero vs missing vs not collected.
  - [x] 2.3 Quota windows: readings, resets, per-window freshness, limits, omitted and retired windows. *Validation:* mixed-freshness tests in both directions, plus `reset_passed` boundary tests.
  - [x] 2.4 Sources and failures, cache preservation, disabled providers, log cycle failures. *Validation:* partial-failure and disabled tests.
  - [x] 2.5 Costs, models, service and schedule. *Validation:* tests; service probing skipped in tests.
  - [x] 2.6 Read-only guarantee and performance. *Validation:* sentinel-binary and file-snapshot test; timed real run under 10 s.
- [x] 3.0 Native app core
  - [x] 3.1 SwiftPM package (Core, Design, App) and a bundle script. *Validation:* `make app-build` produces the `.app`.
  - [x] 3.2 Report models and decoding against the committed fixture. *Validation:* `make app-test`.
  - [x] 3.3 `ReportClient` and `CollectorClient`, resolved from the plist plus environment overrides. *Validation:* unit tests with a temporary plist.
  - [x] 3.4 `AppStore`: cached load, refresh policy, single-flight Collect now, observers. *Validation:* store tests, AC-16 and AC-20.
  - [x] 3.5 Popover overview (status, notice, cards, footer). *Validation:* run the app; walk the accessibility tree.
  - [x] 3.6 Provider detail. *Validation:* manual walkthrough.
  - [x] 3.7 History window. *Validation:* manual walkthrough, plus an update test while it is open.
  - [x] 3.8 Menu-bar glyph states. *Validation:* manual check.
- [x] 4.0 Validation and documentation
  - [x] 4.1 `make check`, `make app-test`, and the strict checks in CI.
  - [x] 4.2 Run against real data (read-only) and against the sandbox (Collect now). Record the results in the design.md verification log.
  - [x] 4.3 Update DESIGN_SYSTEM implementation status and the component manifest status.
- [x] 5.0 Milestone 2 (accepted handoff remainder; owner decisions of 2026-10-05)
  - [x] 5.1 Monitoring view: service, schedule, collection, failures, sources, data state. *Validation:* offscreen render; store tests.
  - [x] 5.2 Service start/stop: stop is disable + bootout and stays stopped at login; start is enable + bootstrap. *Validation:* black-box test with a stateful fake launchctl (exact call sequence); Swift store test.
  - [x] 5.3 Pause/resume through `poll_paused`. The daemon keeps running, `once` still works, and resume waits a full interval. *Validation:* `schedule_decision` unit tests; black-box daemon tests (paused at start, paused mid-run, resume); report tests (capability gating).
  - [x] 5.4 Settings: interval, providers and prices through the atomic, validated `configure`. *Validation:* black-box `configure` test (keys kept, mode kept, rejects leave the file byte-identical); live sandbox Swift test.
  - [x] 5.5 Per-provider progress through `once --progress`. *Validation:* black-box progress test; store progress test.
  - [x] 5.6 Notifications for limits and failures, once each. *Validation:* NotificationPlanner tests. Delivery is still a manual check.
  - [x] 5.7 Open at login (`SMAppService.mainApp`). *Validation:* manual check only.
  - [ ] 5.8 Accepted native visual baselines in `docs/design/app/references/` (needs human acceptance).
- [x] 6.0 Test isolation and latency (owner request 2026-10-05)
  - [x] 6.1 Fail-closed `AI_USAGE_LAUNCHCTL`, a module guard, regression tests, incident record (PR #4).
  - [x] 6.2 Report scan 6.3 s → 2.35 s with byte-identical output.
  - [x] 6.3 Coalesced refreshes, change-detected rescans, `becomes_stale_at` re-evaluation. *Validation:* store tests; `--measure` on real data.
- [x] 7.0 Review findings on #4 and #5 (2026-10-05)
  - [x] 7.1 Relative launcher override resolved to an absolute path (#4). *Validation:* PATH-trap regression, mutation-checked.
  - [x] 7.2 Paused scheduler never spins. *Validation:* virtual-clock `run_daemon` regression, mutation-checked.
  - [x] 7.3 Calendar rollover in the report and the cached snapshot. *Validation:* Python and Swift tests across midnight.
  - [x] 7.4 Service observation freshness order. *Validation:* Swift tests (both directions, out-of-order probes).
  - [x] 7.5 Explicit start order, a rollback fix, and a real-launchd integration check. *Validation:* `make launchd-integration-check` passes on this Mac.
