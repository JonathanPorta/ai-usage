## Session State: ux-pass
Last updated: 2026-11-15

**Status: phase 5 is in progress.** Requirements come from the owner's work package (2026-11-13).
Execution was directed by the owner on 2026-11-15. Slice 1 (state only) is implemented and independently reviewed, in worktree
`ux-state-slice`, based on `origin/main` at `39958af`. Its PR is not yet opened.

### Current Position
- **Working on:** Task 1.0 review: open and review the slice 1 PR.
- **Status:** slice 1 implemented; PR not yet opened; not merged, installed or released.
  Branch `jp/f/ux-policy-order-countdown`. No view change and no accepted baseline change.
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
1. Open and review the slice 1 PR.
2. Slice 2 (overview, header, cards), which wires these APIs into views and changes renders and
   needs the owner's visual acceptance. Not started.

### Validation results
- Slice 1 (2026-11-15, combined tree): implemented and independently reviewed (one round, findings
  fixed). PR not yet opened; not merged, installed or released.
  - `make check`: Ran 375 tests, OK (skipped=1).
  - `make fixture-check`: committed fixtures match the generator (fixture changes are additive keys).
  - `make app-test`: Executed 111 tests, 0 failures.
  - Renders: slice 1 output is byte-identical to `origin/main` (39958af) for all 106 fixture renders.
  - `make app-snapshot-check` fails for 42 renders on `origin/main` itself, before this work. That
    drift from `docs/design/app/references` is unresolved, was not caused by slice 1, and baselines
    were not touched.
- Next: open and review the slice 1 PR; then slice 2 (overview, header, cards), which wires these
  APIs into views and changes renders, needing the owner's visual acceptance.
- Slice 2: not started.
- Slice 3: not started.
- Slice 4: not started.

### Blockers / Open Questions
- Theme file user path: to choose after checking config ownership and installation (slice 4).
- Location of "Arrange mode" (overview or Settings).
- "Remove saved data..." is included only if the product already supports it.
- Whether providers with no usage events have a `last_activity_at` value (checked in slice 1).
- Issue #9 overlaps the reset-label spacing work (reconciled in slice 2).
