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
  - [x] 1.9 Implementation done. PR review and merge still open: PR
        [#41](https://github.com/JonathanPorta/ai-usage/pull/41) (base main, branch
        `jp/f/ux-policy-order-countdown`, commit `7b73412`), CI green, queued for review. Open,
        not approved, not merged, not installed, not released.
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
  independently reviewed, one round, 15 findings fixed; PR [#42](https://github.com/JonathanPorta/ai-usage/pull/42),
  base #41's branch, branch `jp/f/ux-overview-header-cards`; open, not merged, installed or
  released) (AC-1, AC-4 to AC-11, AC-15, AC-16)
  - Note (2026-11-15): #42's "PR test gate" fails only because ProofGlass did not admit the macOS
    lane for the stacked PR. Linux and spec/design legs pass.
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
  - [x] 2.9 Issue #9 countdown layout work is in slice 3 (reset annotation inside the chart card;
        see 3.0). Its visual acceptance is open.
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
  - **Outstanding (2026-11-15):** owner visual acceptance of the slice 2 renders is open.

- [~] 3.0 Provider detail, Monitoring, empty states, History navigation (IMPLEMENTED; agent review
  one round, fixed; PR [#43](https://github.com/JonathanPorta/ai-usage/pull/43), base #42's branch;
  open, not merged, installed or released) (AC-3, AC-5, AC-12, AC-13, AC-15, AC-16)
  - [x] 3.1 Detail header with On/Off switch and "Open in history window". Tracking off reads as
        last known.
  - [x] 3.2 Detail order: quota, usage, today, cost, collapsed sources. Collapsed Chart key.
  - [x] 3.3 Detection notices: Keep history and Review provider settings kept, as in slice 2.
  - [x] 3.4 Monitoring ordered Collection, Schedule, Provider sources, Service, Saved data. The
        collection summary counts the page's active providers once each by worst source; this also
        drives the overview indicator.
  - [x] 3.5 Empty states implemented.
  - [x] 3.6 History opens with provider, metric and range, and keeps both actions for
        not-detected and not-configured providers.
  - [x] 3.7 Reset annotation inside the chart card (issue #9; visual acceptance pending).
  - **Validation (2026-11-15):** `make app-test` 147 tests, 0 failures; 168 renders.
  - **Known limits:** "cost isn't reported" is chosen by matching plugin note text (no report
    flag). Antigravity's "normal, not stale" note sits under a Stale label when tracking is off.
    The detail switch draws gray in offscreen renders, as the accepted Settings baselines do.
  - **Outstanding:** owner visual acceptance; PR review and merge.

- [~] 4.0 File-defined themes (IMPLEMENTED; agent review one round, six findings fixed; slice 4 PR
  opening from `jp/f/ux-file-defined-themes`, base #43's branch; not merged, installed or released)
  (AC-14, AC-15, AC-16)
  - [x] 4.1 Theme file: `<app state dir>/themes.json`, that is
        `~/Library/Application Support/AI Usage/themes.json`. `AI_USAGE_STATE_DIR` redirects it.
  - [x] 4.2 Format version 1: `global`, `providers` (instance id to scheme) and `schemes`
        (light/dark role to #RRGGBB). Roles are the semantic token names; `surface` is an alias of
        `panel`. Missing roles and appearances inherit.
  - [x] 4.3 Resolution: built-in, then global, then provider instance.
  - [x] 4.4 Invalid file keeps the last valid theme, including across restart via
        `themes.last-valid.json`. Deleting the file returns to built-in. Non-blocking contrast and
        status-similarity warnings. Data-only, 64 KB cap, regular files only.
  - [x] 4.5 Settings Theme section with "Open theme file" and "Reload themes". Docs in `docs/THEMES.md`.
  - [x] 4.6 The generator emits token names and roles. Colors resolve from the SwiftUI environment,
        with a per-provider scope on cards, detail and the History provider page. The All view stays
        on global series colors.
  - **Validation (2026-11-15):** `make app-test` 173 tests, 0 failures; `make design-build-check`,
    `design-check --strict` and `spec-check --strict` pass; 186 renders. With no theme file, 162
    of 168 pre-existing renders are byte-identical to slice 3. The other 6 are Settings renders
    showing the new Theme section.
  - **Known limits:** system controls keep system colors. The History sidebar row stays on the
    global scheme. The status look-alike threshold (sRGB distance 48) is the implementer's choice.
  - **Not exercised natively:** Open theme file and Reload in the running app.
  - **Outstanding:** owner visual acceptance; PR review and merge.

## Current Status

- **Closeout (2026-11-22):** the UX pass code is merged through PR #46; `main` is 48cda57 and its CI was green.
  - Interaction checks, 2026-11-19 to 2026-11-22 (keyboard-reachable checks and an accessibility-driven sandbox
    session): passed for popover navigation, exact times on Overview, Monitoring, History and provider detail,
    tracking Off hiding a card and writing config, custom card order applied and stored, the header badge popover
    and Open Monitoring link, manual collection, Settings, themes after restart, and the last valid theme kept.
  - Owner acceptance (2026-11-22): the owner reported checking the remaining human-only items (VoiceOver launch and
    speech, hover tooltips, drag reordering, first click after launch, History "Provider settings...", theme reload
    result) and accepted them ("They all look good... should be approved"). Owner acceptance, not itemised and not
    agent-observed.
  - Installed (2026-11-22 16:10 MST): the owner approved installing; a build of 48cda57 runs as the menu-bar app
    from a pinned clone. The collector is unchanged, so tracking "Auto" behaves as "On" until the collector is
    updated. No release is published.
  - Not exercised by anyone on record: tracking back On via the UI, the invalid-theme notice, light mode, the
    in-progress header state, scrolled detail. Slices 2.0, 3.0 and 4.0 stay `[~]` because their acceptance depends
    on those items (busy feedback, scrolled reset chip, invalid-theme and light-mode behavior); 1.0 stays unchecked
    because its own status text (pre-PR wording) was not rewritten here.
  - Known cosmetic follow-up: focus reveal panels use the global palette, not the provider's.
- **Defect fixes (2026-11-22):** owner-reported defects fixed in PR #48 (merged 9c14a40) and PR #49. Evidence is
  automated only (unit tests and offscreen renders). The changed reference images in #48 (98) and #49 are pending
  owner acceptance; the 2026-11-19 baseline and 2026-11-22 UX acceptances don't cover them. Neither fix is installed.
- Earlier status, 2026-11-15 (historical):
- Slice 1 (1.0): PR [#41](https://github.com/JonathanPorta/ai-usage/pull/41) (base main), CI
  green. Open; not approved, merged, installed or released.
- Slice 2 (2.0): PR [#42](https://github.com/JonathanPorta/ai-usage/pull/42) (base #41's branch).
  The "PR test gate" fails only because ProofGlass did not admit the macOS lane for the stacked PR;
  the Linux and spec/design legs pass. Open; not merged, installed or released.
- Slice 3 (3.0): PR [#43](https://github.com/JonathanPorta/ai-usage/pull/43) (base #42's branch).
  Open; not merged, installed or released.
- Slice 4 (4.0): PR being opened from `jp/f/ux-file-defined-themes` (base #43's branch). Not
  merged, installed or released.
- Merge order: #41, then #42, #43 and the slice 4 PR. Retarget each to `main` after its base
  merges, and re-run CI then.
- Outstanding overall:
  - owner review and approval of the four PRs;
  - owner visual acceptance of the new renders. After that, `docs/design/app/references` and
    `SHA256SUMS` can be regenerated in one docs/baseline PR. 56 references differed after slices
    2 and 3; 42 of them already differed on `main`;
  - native checks not run: keyboard-focus reveal, drag-to-reorder, VoiceOver, live theme open and
    reload, and the detail switch tint;
  - installing the new app and collector over the live installation (a separate owner action);
  - the collector's Python changes are only in #41.
- Renders: `~/devel/ai-scratch/ai-usage-ux-pass/renders/slice2`, `slice3`, `slice4`.
- Tracker: `Projects/AI Usage/Tracker` in the personal notes repo (notes-personal #92 merged; a
  further tracker PR follows).
