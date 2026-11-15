## Session State: ux-pass
Last updated: 2026-11-15

**Status: phase 5 is in progress.** Requirements come from the owner's work package (2026-11-13).
Execution was directed by the owner on 2026-11-15. As of 2026-11-15, all four slices are implemented and each has an open PR, stacked in order:
#41 (slice 1, base main, CI green), #42 (slice 2, base #41's branch), #43 (slice 3, base #42's
branch), and the slice 4 PR (being opened from `jp/f/ux-file-defined-themes`, base #43's branch).
None is approved, merged, installed or released.

### Current Position
- **Working on:** review and merge of the stack, in order. Each PR is retargeted to `main` after
  its base merges, and CI is re-run then.
- **Status:** all four PRs open, none approved. The #42 "PR test gate" fails only because ProofGlass
  did not admit the macOS lane for the stacked PR; its Linux and spec/design legs pass.
- **Blocked:** No, except that the owner's review and approval are needed to merge.

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
1. Check review state of #41 (`prrq status`, `gh pr view 41`). When approved and green, merge it.
2. Retarget #42 to `main`, re-run CI, then continue down the stack (#43, then the slice 4 PR).
   Merge only on the `/prrq` gate or the owner's direction.
3. Collect the owner's visual acceptance of the new renders. Then regenerate
   `docs/design/app/references` and `SHA256SUMS` in one docs/baseline PR.
4. Native checks not yet run: keyboard-focus reveal, drag-to-reorder, VoiceOver, live theme open
   and reload, and the detail switch tint. Also the transient "check finished" state.
5. Installing the new app and collector over the live installation is a separate owner action.

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
- Slice 3 (PR #43, 2026-11-15): `make app-test` 147 tests, 0 failures; 168 renders. Agent review
  one round, fixed.
- Slice 4 (2026-11-15): `make app-test` 173 tests, 0 failures; `make design-build-check`,
  `design-check --strict` and `spec-check --strict` pass; 186 renders. With no theme file, 162 of
  168 pre-existing renders are byte-identical to slice 3; the other 6 are Settings renders showing
  the new Theme section. Agent review one round, six findings fixed. Open and Reload were not
  exercised in the running app.

### Blockers / Open Questions
- Resolved: theme file path is `~/Library/Application Support/AI Usage/themes.json` (redirected by
  `AI_USAGE_STATE_DIR`).
- Resolved: "Arrange mode" lives in Settings.
- Resolved: no "Remove saved data..." route was built. None exists in the product, and it is not
  required.
- Whether providers with no usage events have a `last_activity_at` value. Polled providers (Codex)
  have none and sort after providers with known activity; nothing is fabricated (slice 1).
- Known limits, to decide later: "cost isn't reported" is matched on plugin note text; the status
  look-alike threshold (sRGB distance 48) is the implementer's choice.
- Issue #9: reset annotation shipped inside the chart card in slice 3, pending visual acceptance.
