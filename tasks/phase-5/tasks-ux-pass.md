# Tasks: Overview, Detail, Monitoring and Themes UX Pass (Phase 5)

> Generated from [PRD: Overview, Detail, Monitoring and Themes UX Pass](prd-ux-pass.md). The PRD
> adopts the owner's work package (2026-11-13) and its proposed defaults, as directed on
> 2026-11-15. Slice 1 is on branch `jp/f/ux-policy-order-countdown`, in worktree
> `ux-state-slice`, based on `origin/main` at `39958af`.

## Acceptance Criteria Traceability

| AC | Short description | Slices |
| --- | --- | --- |
| AC-1 | Off providers hidden, recoverable; instances independent | 1, 2 |
| AC-2 | Auto/On/Off migration, repeatable, history safe | 1 |
| AC-3 | Distinct detection outcomes; deduplicated notices | 1, 3 |
| AC-4 | Custom and recent ordering, persistence, no movement | 1, 2 |
| AC-5 | Countdown boundaries, exact timestamps, no fabricated capacity | 1, 2, 3 |
| AC-6 | Header indicators by mouse and keyboard; separate facts | 1, 2 |
| AC-7 | Honest counts; no-eligible empty state | 1, 2 |
| AC-8 | Success not a persistent banner; failures discoverable | 2 |
| AC-9 | Cards keep all quotas, bases and per-metric freshness | 2 |
| AC-10 | Statistics popover coverage and peaks | 2 |
| AC-11 | Toolbar hit targets, tooltips, busy feedback | 2 |
| AC-12 | Detail order, History context, collapsed keys | 3 |
| AC-13 | Empty-state copy and settings actions | 3 |
| AC-14 | File-defined themes, global and per instance | 4 |
| AC-15 | Long names, narrow widths, keyboard and VoiceOver | 2, 3, 4 |
| AC-16 | Checks and renders recorded; owner review before baselines | 1, 2, 3, 4 |

## Relevant Files

- `ai_usage_report.py`, `ai_usage_service.py`, `ai_usage_plugins/`: detection evidence, tracking
  resolution, report additions, collection summary (slice 1).
- `macos/Sources/AIUsageCore/` (`Presentation.swift`, `Report.swift`, `AppStore.swift`,
  `Control.swift`): tracking policy, card order, countdown formatter, header and collection-result
  models, detection notices (slices 1, 3).
- `macos/Sources/AIUsageApp/` (`OverviewView.swift`, `PopoverRoot.swift`, `MonitoringView.swift`,
  `ProviderDetailView.swift`, `HistoryView.swift`, `SettingsView.swift`, `SnapshotRenderer.swift`):
  views (slices 2 to 4).
- `macos/Sources/AIUsageDesign/` (`QuotaViews.swift`, `Charts.swift`, `TokenColor.swift`): cards,
  charts and themes (slices 2 to 4).
- `macos/design/tokens.json` and `macos/scripts/generate_design_tokens.py`: theme roles (slice 4).
- `macos/Tests/AIUsageCoreTests/`: model tests (every slice).
- `docs/design/app/references/`: accepted renders. Not replaced without the owner's review.
- `/Users/portaj/devel/ai-scratch/ai-usage-ux-pass/work-package.md` and `slice1-contract.md`.

### Notes

- Tests use synthetic data and sandboxed config and state directories only. No test reads the
  owner's `~/.ai-usage`, the installed collector or real history.
- Builds and renders run in the task worktree's own `macos/build`, never the main checkout's.
- Coordination: the coordinator does not write code. Implementation is done by Opus low-effort
  subagents, per the owner's direction of 2026-11-15.

## Tasks

- [ ] 1.0 State: tracking, evidence, report additions, ordering, countdowns, header models
  (IMPLEMENTED and independently reviewed, one round, findings fixed; PR not yet opened; not
  merged, not installed, not released; branch `jp/f/ux-policy-order-countdown`; no view or
  baseline change) (AC-1 to AC-7, AC-16)
  - [x] 1.1 Tracking policy: `tracking` auto|on|off is canonical in config. The writer mirrors
        legacy `enabled` (on = true, off = false, auto removes it) so an older installed collector
        honors Off. Old configs map enabled true to on, false to off, absent to auto. Local
        (external) plugins with no explicit choice stay Off.
  - [x] 1.2 Detection evidence per instance: the collector records observed evidence in its state
        file, and the report reads that record (never its own PATH probe). Fields: `installed`
        (true/false/null), `requirement` (executable/none), `saved_data`.
  - [x] 1.3 Effective state rules, with the last known state persisted for unknown detection.
  - [x] 1.4 Report additions: `tracking`, `tracking_effective`, `detection`, `last_activity_at`,
        `capabilities.tracking_policy`, and `collection.summary` keys (`providers_expected`,
        `providers_fresh`, `providers_stale`, `outcomes`). `evaluate_attempt()` no longer equates
        "no hard failure" with fully updated.
  - [x] 1.5 Card order model: recent and custom modes; `moveUp`/`moveDown`; hidden positions kept;
        injectable preferences store. The order is snapshotted on popover open.
  - [x] 1.6 Reset-countdown formatter with injectable time zone and locale, shared by overview,
        detail and History.
  - [x] 1.7 Header models: Monitoring states and collection-result states as new APIs
        (`AIUsageCore/Tracking.swift`). Existing `Presentation.status()` strings are unchanged.
  - [x] 1.8 Decoding: new fields optional and tolerant of unknown enum values; old reports fall
        back to `enabled`. `AppStore.cardProviders` is the single list for cards and keyboard.
  - [~] 1.9 PR review and merge: PR [#41](https://github.com/JonathanPorta/ai-usage/pull/41),
        branch `jp/f/ux-policy-order-countdown`, commit `7b73412`. All CI checks pass. Queued for
        review. Not approved, not merged, not installed, not released.
  - **Validation (2026-11-15, combined tree):**
    - `make check`: Ran 375 tests, OK (skipped=1).
    - `make fixture-check`: committed fixtures match the generator (fixture changes are additive
      keys).
    - `make app-test`: Executed 111 tests, 0 failures.
    - Renders: slice 1 output is byte-identical to `origin/main` (39958af) for all 106 fixture
      renders.
    - `make app-snapshot-check` fails for 42 renders on `origin/main` itself, before this work.
      That pre-existing drift from `docs/design/app/references` is unresolved and was not caused
      by slice 1. Baselines were not touched.
  - **Known limits:** polled providers (Codex) have no `last_activity_at`, so they sort after
    providers with known activity; nothing is fabricated. The recent order is taken when the
    popover opens, before the open-time refresh completes. Parity-harness expectations for the
    new keys partly mirror the implementation.
  - **Validates when:**
    - `make check` and the collector tests pass, including a table of legacy and new config
      values (true, false, absent, auto, on, off, invalid).
    - `make app-test` passes the model tests: ordering ties, countdown boundaries (47h to 1 day,
      23h to 23 hours, 59s, zero, past), and the header and collection-result states.
    - An old report with no new keys decodes and renders with the old fallbacks.
    - `git diff --stat` shows no change under `docs/design/app/references` and no SwiftUI view
      file changed.

- [~] 2.0 Overview: header indicators, cards, statistics popover, icon toolbar (IMPLEMENTED and
  independently reviewed, one round, 15 findings fixed; PR being opened; branch
  `jp/f/ux-overview-header-cards`, stacked on slice 1; not merged, installed or released)
  (AC-1, AC-4 to AC-11, AC-15, AC-16)
  - [x] 2.1 "My AI Usage" title.
  - [x] 2.2 Cards come from `AppStore.cardProviders`, with "Choose your providers" and
        awaiting-detection empty states.
  - [x] 2.3 ECG button opens Monitoring, with state badge and tooltip. Collection-result capsule
        shows a count and opens a details popover. Collection issues are shown separately from
        quota limits. Detection notices offer Keep history and Review provider settings.
  - [x] 2.4 Banner only when no report loads or a manual check fails. Success is not a persistent
        banner.
  - [x] 2.5 Quota-first cards with one countdown formatter and a dotted underline. Exact time shows
        on hover, focus or click. "Last known: resets in ..." under failed windows. "Reset due ·
        awaiting update" and "Reset time unavailable" states. One issue marker per affected card.
  - [x] 2.6 Trophy statistics popover: today, "Recorded total" with coverage, peak in 7 days,
        last activity, token basis.
  - [x] 2.7 Three icon-only toolbar buttons (collect, History, Settings).
  - [x] 2.8 Settings: Auto/On/Off control with detected state, a fallback switch for older
        collectors, and a Card order section with Move up/down, drag-to-reorder and "Use recent
        activity order". Ordering controls live in Settings (Arrange mode decision).
  - [ ] 2.9 Issue #9 reconciled against the countdown layout (not recorded as done).
  - **Validation (2026-11-15):**
    - `make app-test`: Executed 121 tests, 0 failures.
    - `make design-check ARGS=--strict`: 0 unwaived.
    - `make design-build-check`: pass.
    - `make app-snapshot`: wrote 136 images (19 new scenarios, including one-off,
      choose-providers, awaiting-detection, result-warning, success-at-limit, custom and recent
      order, long-name, reset-past-unknown, collection details, statistics, and three settings
      scenarios). Renders are kept at `~/devel/ai-scratch/ai-usage-ux-pass/renders/slice2`.
    - Python is unchanged in slice 2.
    - 56 of the accepted reference renders now differ (42 already differed on `main`). The
      references in `docs/design/app/references` were NOT replaced and await the owner's visual
      acceptance.
  - **Outstanding:**
    - Owner visual acceptance.
    - Keyboard-focus reveal of the exact reset time and statistics, and drag-to-reorder, are
      implemented but not exercised natively. Offscreen renders cannot show focus or drag.
    - Popover contents are rendered standalone.
    - No render of the transient "check finished" state.
    - Live VoiceOver not run.

- [ ] 3.0 Provider detail, Monitoring, empty states, History navigation (NOT STARTED)
  (AC-3, AC-5, AC-12, AC-13, AC-15, AC-16)
  - [ ] 3.1 Detail header: title, On/Off status and toggle (no change to Auto by opening the page),
        History action. Save failure restores the prior state with feedback.
  - [ ] 3.2 Detail order: quota, usage and chart, today, cost and accounting, then collapsed
        sources and diagnostics. "Chart key" disclosure under the graph.
  - [ ] 3.3 Detection notice: one per provider, "Saved usage found. We couldn't find this CLI.",
        Keep history (default) and Review provider settings. Dismissal persists by provider id
        and condition signature.
  - [ ] 3.4 Monitoring order: Collection, Schedule, Provider sources, Service, Saved data. Source
        groups with symbols; exact times on hover or focus; inactive sources in a collapsed group.
  - [ ] 3.5 Empty states per the work package table; subscription price shown faded and linked to
        the provider's Settings field.
  - [ ] 3.6 History opens with the provider selected and the metric and range kept where
        compatible.
  - **Validates when:**
    - Model tests cover each empty-state copy and action, and that a price never substitutes for
      reported cost.
    - A test shows that a repeated condition does not repost the notice, and that a changed
      condition does.
    - Snapshot renders of detail, Monitoring and the empty states are recorded as "pending owner
      review".

- [ ] 4.0 File-defined themes (NOT STARTED) (AC-14, AC-15, AC-16)
  - [ ] 4.1 Choose the user file path after checking config ownership and installation behavior.
  - [ ] 4.2 Versioned schema (`version: 1`) with semantic roles and light/dark variants; partial
        schemes inherit missing roles.
  - [ ] 4.3 Resolution: built-in defaults, then global scheme, then provider-instance override.
        Targets by stable instance id.
  - [ ] 4.4 Validation of schema, colors and references. An invalid edit keeps the last valid
        theme and shows a concise diagnostic.
  - [ ] 4.5 Settings shows the active assignment, with Open theme file and Reload.
  - [ ] 4.6 Contrast and status meaning checked for each role in both appearances.
  - **Validates when:**
    - Tests cover a missing file (defaults), a partial scheme, an invalid file (previous valid
      theme kept), and a per-instance override for one of two Claude accounts.
    - A contrast test passes for every role in both appearances.
    - Reinstall keeps user schemes and assignments (a sandboxed check).

## Current Status

- Slice 1 (1.0): PR [#41](https://github.com/JonathanPorta/ai-usage/pull/41), commit `7b73412`.
  All CI checks pass; queued for review. Not approved, merged, installed or released.
- Slice 2 (2.0): implemented and reviewed; PR being opened. Not merged, installed or released.
  Owner visual acceptance is outstanding; references not replaced.
- Slices 3.0 and 4.0: not started.
- Validation results: see 1.0 and 2.0 above.
