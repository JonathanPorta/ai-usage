# PRD: Overview, Detail, Monitoring and Themes UX Pass (Phase 5)

Status: **requirements adopted, execution directed by the owner on 2026-11-15.** The owner's work
package (`/Users/portaj/devel/ai-scratch/ai-usage-ux-pass/work-package.md`, dated 2026-11-13) is the
requirements source. Its proposed defaults are adopted as written. Visual acceptance of changed
renders by the owner remains a gate before any accepted baseline in `docs/design/app/references`
is replaced. This PRD is the phase gate for the plan in `tasks/project-plan.md`, phase 5.

Related issues: [#8, Auto / On / Off tracking](https://github.com/JonathanPorta/ai-usage/issues/8)
(build on it, do not add a second policy) and [#9, reset annotation spacing](https://github.com/JonathanPorta/ai-usage/issues/9)
(reconcile with the countdown work).

Out of scope:
- a collector rewrite;
- billing features, estimated cost or new accounting;
- CI redesign;
- release work, installing over the live app or collector, and deleting history (the owner's
  work package forbids all four without a separate approval);
- the "sad puppy" artwork, which was an exploratory idea only.

## Summary

Make the macOS overview useful at a glance. It shows the providers the owner uses, puts quotas
first, and makes reset times easy to read. Repeated status text is removed. Cards can be ordered
by recent activity or by hand, and a file-defined theme can color the whole app or one provider.
The same hierarchy and controls then carry through provider detail, Monitoring, Settings and
History.

## Codebase Analysis

Read at `39958af` (main, the commit the work package inspected).

### Explored
- `macos/Sources/AIUsageApp/OverviewView.swift`: overview and card composition.
- `macos/Sources/AIUsageApp/PopoverRoot.swift`: shared shell and action banners
  (`CollectResultBanner`).
- `macos/Sources/AIUsageCore/Presentation.swift`: `Presentation.status()` (the persistent
  Monitoring row), freshness and date formatting.
- `ai_usage_report.py`: `evaluate_attempt()` and the collection summary. `evaluate_attempt()` can
  return `updated` when no source had a hard failure, even if a source is stale or waiting.
- `macos/Sources/AIUsageDesign/QuotaViews.swift`, `MonitoringView.swift`,
  `ProviderDetailView.swift`, `HistoryView.swift`, `macos/Sources/AIUsageDesign/Charts.swift`,
  `SettingsView.swift`, `AIUsageCore/AppStore.swift`, `Control.swift`.
- `macos/design/tokens.json`, `macos/scripts/generate_design_tokens.py` and
  `macos/Sources/AIUsageDesign/TokenColor.swift`: the existing token and theme foundation.

### Relevant Patterns
- The app is report-driven: it renders the providers the report lists and does not know about
  individual providers.
- Configuration changes go through the validated writer (`configure` / `update_config`).
- `PanelShownObserver` and `store.popoverOpened()` are the existing popover lifecycle. The popover
  stays mounted, so `.onAppear` does not run on every opening.
- Design tokens are generated. Views format what the report states and contain no literal hex
  values.

### Constraints Discovered
- A missing usage file is not evidence that a CLI is missing, and an old file is not evidence that
  it is installed. `Report.Provider` has no separate binary-presence field.
- Several built-ins report detection from either an executable or local data.
- The collection-result denominator counts attempted instances only, so it excludes disabled and
  zero-attempt providers. `Presentation.status()` treats a partial collection as healthy.
- Accepted renders in `docs/design/app/references` are owner-accepted evidence and must not be
  replaced only to make a visual test pass.

### Assumptions (need human confirmation)
- The work package's proposed defaults are adopted (owner direction, 2026-11-15). The open items
  below stay open until the owner decides them.
- Provider ordering ties resolve by name, then instance id (deterministic).

## Background

The overview today shows a subtitle ("Your AI tools, at a glance."), a persistent Monitoring row
and a full-width collection banner. Disabled providers keep their cards, with "cached values are
kept" copy. Quota and usage compete on each card. Each miniature chart has a long peak sentence.
Provider detail puts the chart and cost above quota. Monitoring puts the service first, and it
repeats source timestamps on every row. Colors come only from built-in tokens.

## Goals

- Off providers leave the overview immediately after a successful save, with no data loss.
- Each provider instance (for example `claude` and `claude-az`) has its own tracking policy:
  Auto, On or Off.
- Overview cards are ordered by recent activity by default, or by a custom order that survives
  restart.
- Reset labels use one formatter everywhere, with an exact local timestamp on focus or hover.
- The header separates Monitoring state, the latest collection result and local reread.
- Quota is the strongest element on each card and on provider detail.
- Statistics, cost, empty states and History navigation follow one visual language.
- A valid theme file applies globally or per provider. An invalid file keeps the last valid
  appearance.

## Non-Goals

- Collector rewrite, billing features, estimated spend, CI redesign and release work.
- Changing the collector's standard-library-only constraint or private-plugin isolation.
- Replacing accepted render baselines before the owner's visual review.
- A purge subsystem. "Remove saved data" is offered only if the existing product already supports
  a provider-scoped removal.
- A GitHub issue or any external report for each detection event.

## Architecture & Approach

The work is cut into four slices, detailed in [tasks-ux-pass.md](tasks-ux-pass.md):

1. **State (no view or baseline change).** Tracking policy in config, detection evidence, optional
   report additions, card-order model, reset-countdown formatter, header and collection-result
   models. Branch `jp/f/ux-policy-order-countdown`.
2. **Overview.** Header indicators, cards, statistics popover and icon toolbar.
3. **Detail and Monitoring.** Provider detail, Monitoring, empty states, History navigation and
   detection notices.
4. **Themes.** File-defined themes, resolved as built-in defaults, then the chosen global scheme,
   then the provider-instance override.

Report additions are optional and additive within `ai-usage/report/v1`, so older readers keep
working. The contract is in the slice-1 contract (`/Users/portaj/devel/ai-scratch/ai-usage-ux-pass/slice1-contract.md`).

Key behavior, from the work package:

- **Tracking.** Off collects nothing and hides the card. Auto with an installed CLI is active. Auto
  with no CLI and no saved data is inactive and hidden. Auto with no CLI but saved data is inactive,
  hidden and raises one notice with the actions Keep history (default) and Review provider settings.
  On is always visible and attempts collection. Unknown detection keeps the last known effective
  state. Migration maps `true` to On, `false` to Off and an absent policy to Auto.
- **Ordering.** Recent order uses actual provider activity (`last_activity_at`), never the read
  time. The order is snapshotted when the overview opens and held while it is open. Keyboard
  navigation uses the same filtered, sorted list as the cards.
- **Countdowns.** Whole units rounded down: days from 24 hours, hours to 24 hours, minutes to 60
  minutes, then "less than a minute". A passed reset reads "Reset due · awaiting update". Timer
  updates never trigger collection.
- **Collection result.** "N providers checked" with a fresh, stale and failed breakdown. "N of M
  active providers updated" only when all M are fresh. Zero active providers reads "No active
  providers", not "0 of 0".

## Acceptance Criteria

Each criterion maps one-to-one, in order, to an item of the work package's acceptance checklist.

- [ ] AC-1: Off providers disappear from the overview and remain recoverable through Settings and
  History. Disabling one Claude instance leaves the other alone.
- [ ] AC-2: Auto/On/Off migration preserves explicit choices, is repeatable, and keeps history and
  configuration safe.
- [ ] AC-3: Missing installation with no data, missing installation with saved data, missing
  telemetry, stale data, authentication failure and detection failure have distinct tested
  outcomes. Mismatch notices are deduplicated.
- [ ] AC-4: Custom ordering survives restart and disable/re-enable. Automatic ordering follows
  actual activity, with stable ties and no movement during an open session.
- [ ] AC-5: Countdown boundaries at 48h, 24h, 1h, 1m, zero and past reset behave consistently. Exact
  timestamps expose local date, time and time zone. A stale capacity never becomes fresh from
  elapsed time alone.
- [ ] AC-6: Every header indicator works by mouse and keyboard. Current service state and last
  collection outcome stay separately understandable, including paused-after-success and
  running-with-partial-failure.
- [ ] AC-7: Counts handle inactive, unsupported, missing, partially stale and multiple-account
  providers honestly. No eligible providers gets an empty state.
- [ ] AC-8: A normal success no longer occupies a full-width persistent banner. Recoverable and
  app-wide failures stay discoverable with the correct severity.
- [ ] AC-9: Cards preserve all quota windows, the used/remaining basis, the measured-zero versus
  missing distinction, and per-metric freshness.
- [ ] AC-10: Statistics details show meaningful coverage and peak values. Incomplete history is
  never labeled complete lifetime usage.
- [ ] AC-11: Toolbar icons have usable hit targets, focus, tooltips and busy feedback. Manual
  collection and saved-data reread remain different operations.
- [ ] AC-12: Quota leads provider detail. History opens in the right context. Graph keys collapse.
  Sources and diagnostics sit at the bottom.
- [ ] AC-13: Cost, today, graph-range and all-disabled empty states have accurate copy and valid
  settings actions. A subscription price is never substituted for reported cost.
- [ ] AC-14: Global and per-instance themes apply consistently and survive restart and reinstall.
  They support light and dark appearances and handle invalid files safely without losing the
  previous valid appearance.
- [ ] AC-15: Long names, many quotas, one visible provider, no providers, multiple accounts, narrow
  windows and keyboard and VoiceOver use remain usable. No essential state is shown only by color
  or hover.
- [ ] AC-16: Relevant automated checks and native renders are recorded. Outstanding native checks
  are reported as outstanding, never as passed. Changed renders wait for the owner's visual review
  before any accepted baseline is replaced.

## Open Questions

- The work package does not fix the theme file's user path. It must be chosen after checking config
  ownership and installation behavior (slice 4).
- Where "Arrange mode" lives (overview or Settings) is left open. The work package allows either.
- If "Remove saved data…" is not already supported by the product, the route is omitted, not
  built.
- Whether a provider with no usage events has a `last_activity_at` value (and so sorts last under
  recent order) depends on the source. This is checked during slice 1.
- Issue #9 overlaps the reset-label spacing work. Its status is reconciled in slice 2.
