## Session State: ux-pass
Last updated: 2026-11-15

**Status: phase 5 is in progress.** Requirements come from the owner's work package (2026-11-13).
Execution was directed by the owner on 2026-11-15. Slice 1 (state only) is PR [#41](https://github.com/JonathanPorta/ai-usage/pull/41), commit
`7b73412`, queued for review with all CI checks passing. Slice 2 (overview, header, cards) is
implemented and reviewed, stacked on slice 1, with its PR being opened. Neither is approved,
merged, installed or released.

### Current Position
- **Working on:** Slice 2 PR (branch `jp/f/ux-overview-header-cards`), then review and merge of #41.
- **Status:** slice 1 in PR #41, awaiting review. Slice 2 implemented, reviewed (one round, 15
  findings fixed), PR being opened. Owner visual acceptance of slice 2 renders is outstanding;
  accepted references in `docs/design/app/references` were not replaced.
- **Blocked:** No
- **Blocked:** No

### Key Decisions
- The owner's work package is the requirements source; its proposed defaults are adopted
  (owner, 2026-11-15).
- All code is written by Opus low-effort subagents. The coordinator does not write code
  (owner, 2026-11-15).
- Accepted baselines in `docs/design/app/references` are preserved until the owner's visual review
  (owner, 2026-11-15).
- Slice 1 is state only: no SwiftUI view and no render baseline changes (owner, 2026-11-15).
- Public release, installing over the live app or collector, and deleting history are out of
  scope (owner, 2026-11-15).
- Non-goals: collector rewrite, billing features, CI redesign and release work (owner,
  2026-11-15).
- Issues #8 and #9 are referenced. #8 defines the tracking policy this work builds on.

### Codebase Understanding
- `ai_usage_report.py` `evaluate_attempt()`: can return `updated` with a stale or waiting source,
  so "fully updated" is stronger than the evidence supports.
- `macos/Sources/AIUsageCore/Presentation.swift` `Presentation.status()`: builds the persistent
  Monitoring row and treats a partial collection as healthy.
- `macos/Sources/AIUsageApp/PopoverRoot.swift` `CollectResultBanner`: shows the retained result of
  a manual collection. Its "4 of 4" denominator excludes disabled and zero-attempt providers.
- `macos/Sources/AIUsageDesign/` and `macos/design/tokens.json`: the existing token vocabulary used
  for theme work.
- `PanelShownObserver` and `store.popoverOpened()`: the lifecycle used for order snapshots.

### What's Next
1. Open the slice 2 PR (`jp/f/ux-overview-header-cards`, stacked on slice 1), and queue it.
2. Review and merge PR #41 (slice 1), then the slice 2 PR. Merge only on the `/prrq` gate or the
   owner's direction.
3. Slice 3 (provider detail quota-first order, Monitoring page order, empty states, History
   navigation), then slice 4 (file-defined themes).
4. Owner visual acceptance of slice 2 renders before any reference baseline is replaced.
5. Native checks not yet run: keyboard focus reveal, drag-to-reorder, live VoiceOver, and the
   transient "check finished" state.

### Validation results
- Slice 1 (PR #41, commit `7b73412`; 2026-11-15): all CI checks pass; queued for review.
  Earlier combined-tree checks: `make check` Ran 375 tests, OK (skipped=1); `make fixture-check`
  matches the generator (additive keys); `make app-test` Executed 111 tests, 0 failures; 106
  fixture renders byte-identical to `origin/main` (39958af).
  - `make app-snapshot-check` fails for 42 renders on `origin/main` itself, before this work. That
    drift from `docs/design/app/references` is unresolved, was not caused by slice 1, and
    baselines were not touched.
- Slice 2 (2026-11-15): `make app-test` Executed 121 tests, 0 failures; `make design-check
  ARGS=--strict` 0 unwaived; `make design-build-check` pass; `make app-snapshot` wrote 136 images
  (19 new scenarios) to `~/devel/ai-scratch/ai-usage-ux-pass/renders/slice2`. Python unchanged.
  56 of the accepted references now differ (42 already differed on `main`). References were not
  replaced and await the owner's visual acceptance.
- Slice 3: not started.
- Slice 4: not started.

### Blockers / Open Questions
- Theme file user path: to choose after checking config ownership and installation (slice 4).
- "Remove saved data..." is included only if the product already supports it.
- Whether providers with no usage events have a `last_activity_at` value. Polled providers (Codex)
  have none and sort after providers with known activity; nothing is fabricated (slice 1).
- Issue #9 reconciliation is not recorded as done.
- Resolved: "Arrange mode" lives in Settings (owner direction and slice 2 decision).
