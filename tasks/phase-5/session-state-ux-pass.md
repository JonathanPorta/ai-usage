## Session State: ux-pass
Last updated: 2026-11-15

**Status: phase 5 is in progress.** Requirements come from the owner's work package (2026-11-13).
Execution was directed by the owner on 2026-11-15. As of 2026-11-15, all four slices are implemented and each has an open PR, stacked in order:
#41 (slice 1, base main, CI green), #42 (slice 2, base #41's branch), #43 (slice 3, base #42's
branch), and the slice 4 PR (being opened from `jp/f/ux-file-defined-themes`, base #43's branch).
None is approved, merged, installed or released.

### Current Position
- **Working on:** visual acceptance follow-up and the owner's hands-on checks (see the 2026-11-19 sections).
- **Status:** as of 2026-11-19, #41, #42, #43 and #44 are all merged to `main` (squash; merge commits
  62af14a, 995357d, 9e9ccc5, e93da98). Each was merged under the PRRQ merge gate with a fresh approval
  at the final head and green checks. The 2026-11-15 status above is historical.
- **Blocked:** No. The owner's visual acceptance is recorded as given on a partial review; the native
  checks listed below remain open.

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
1. Done 2026-11-19: #41 merged as 62af14a.
2. Done 2026-11-19: #42, #43 and #44 merged to `main` in order (995357d, 9e9ccc5, e93da98).
3. Done 2026-11-19: owner visual acceptance recorded on a partial review; baseline change applied in
   this branch to `docs/design/app/references` (see the Baseline acceptance section).
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

## Completion pass fix batch

- Themes: `ThemeStore.init` now reads `themes.last-valid.json` through the same pre-read check as
  `themes.json` (`ThemeLoader.readBounded`: regular file only, at most 64 KB, stat before read), so
  an oversized sidecar is ignored and a FIFO there cannot block launch. Tests cover both (FIFO test
  has a 5 s timeout).
- Cost empty state: decision — no existing structured signal distinguished "provider never reports
  cost" from "no reading yet", so the report gained an additive `costs.reports_cost` boolean (true
  only for Grok, whose billing is the one reported-cost source). The app uses it when present and
  falls back to the old note-text match when absent (older collectors). Fixtures regenerated;
  `docs/CLI.md` documents the field. Supersedes the "matched on plugin note text" known limit.
- Antigravity copy: the quota note "… that is normal, not stale." is hidden on detail
  (`DetailPresentation.quotaNote`) whenever the quota or any displayed window reads Stale
  (including not collecting), so it never sits under a Stale label.

## Completion pass — 2026-11-19

Facts as of 2026-11-19 ~15:25Z. Stage words are exact: implemented, reviewed, merged, visually
accepted, installed, released. Nothing is installed or released.

### Main and merge order

- ai-usage `main` is 62af14a (squash of #41). Before it: a939f3a (#40 "Require ProofGlass For
  Native macOS CI", merged by the ProofGlass CI session; it removed the hosted macOS fallback).
- #41 (slice 1, state): merged as 62af14a. It was PRRQ-approved at 7b73412 and went BEHIND when
  #40 landed. Main was merged in (04c992e), one ProofGlass Mac run was green, PRRQ re-approved at
  04c992e, and it was squash-merged. Main's post-merge run 44552280668 succeeded. Stage: merged.
- #42 (slice 2, overview): PRRQ-approved at 6661d94, but its "PR test gate" failed because the
  stacked base was not admitted by ProofGlass. It was closed as a side effect of deleting #41's
  branch at merge. It was recovered the same hour: base branch restored, PR reopened, retargeted
  to main, temporary branch deleted. Main was merged in, head f83e34e. Local `make check` 376
  tests OK and `make app-test` 121 tests 0 failures. Pushed. Awaiting its Mac run and PRRQ
  re-review. Stage at that point: implemented. Later the same day: re-reviewed, approved at f83e34e,
  and squash-merged as 995357d. Stage: merged.
- #43 (slice 3, detail/Monitoring) head 090802c and #44 (slice 4, themes) head 4ace771 were
  PRRQ-approved at those heads. Their gate fails by design on non-main bases. Each waited its turn:
  retarget to main, merge main in, one run, re-review, merge.
- Later on 2026-11-19 (supersedes the status above): #43 was approved at 30acd63 and squash-merged as
  9e9ccc5. #44 was approved at 23196c8 and squash-merged as e93da98, the current main. Stage: merged.
  The earlier heads 090802c and 4ace771 are historical.
- Lesson: retarget a child PR to main BEFORE deleting its parent's branch.

### Fix batch (local commit 5509771 on #44's branch, not pushed)

- Theme sidecar `themes.last-valid.json` gets the same pre-read file-type and size validation as
  `themes.json`. Tests: an oversized sidecar is ignored; a FIFO does not block init.
- Cost empty state uses a new optional structured field `costs.reports_cost`, with fallback to
  note text for older collectors.
- Antigravity "not stale" note is hidden when the quota shows Stale.
- Validation: `make check` pass, `make fixture-check` pass, `make app-test` 177 tests 0 failures.
- Render effect: `detail-tracking-off` only.
- Stage: implemented. Not yet reviewed.

### ProofGlass CI guidance

Coordinated with the proofglass-c0 session (cmux thread 55fcfa55, reply fe6d6648, 2026-11-19):

- Policy: single lane `ai-usage-macos@1`; displacement on PRs and main; owner-only actor; required
  context "PR test gate".
- `pull_requests_offline=wait` has applied since 06:50Z (proofglass #214). A busy Mac queues PR
  jobs for up to 3600 s.
- NO hosted macOS fallback (ai-usage #40). Do not enable any fallback.
- PRs are admitted only when the base is main and the branch is from the same repo. Stacked-base
  failures on #42–#44 are expected and predate the ProofGlass changes.
- Agreed sequence, one PR at a time: retarget to main first; merge main in; push; exactly one run;
  re-review; merge.
- No evidence reuse: there is no mechanism, and every head moves.
- Put fix commits on #44's branch before retargeting it.
- Merge only when `proofglass supervisor status` shows 0 of 1 slots in use, because pushes to main
  have no wait policy.
- On queue_timeout, use "Re-run all jobs" once, after the Mac is free.

### Interaction validation (sandbox instance, synthetic data, candidate 5509771)

- Background automation (cmux-cua) could not open the menu-bar popover: AXPress was unverifiable,
  AXShowMenu was unsupported (-25206), and there was no menu item for Settings or Monitoring.
- Exercised: only the History window. Provider switching and the Tokens/Quota toggle work. The
  reset control exposes the label "Resets in 55 minutes", value = exact time, help "Shows the
  exact reset time". History accessibility labels are present.
- NOT exercised; needs a person at the keyboard: popover timestamp reveal by focus/activation;
  trophy popover; drag/keyboard ordering and persistence; detail On/Off/Auto and switch
  appearance; theme Open/Reload/per-instance/invalid/restart; header indicators; manual collection
  feedback; spoken VoiceOver.
- Evidence: `/Users/portaj/devel/ai-scratch/ai-usage-ux-pass/visual-package/interaction/RESULTS.md`.
- Observed: an AIUsage process (PID 1800, started 2026-11-17 15:56) runs from this worktree's build
  with no sandbox environment. It was left alone.

### Visual acceptance package (candidate 5509771 vs main a939f3a vs accepted references)

- Package: `/Users/portaj/devel/ai-scratch/ai-usage-ux-pass/visual-package/index.html`, with
  `comparison.tsv`, `proposed-baselines.md` and `REVIEW-NOTES.md`.
- Counts from file hashes, 186 candidate files: 96 new screens (80 not on main; 16 on main without
  a reference), 42 inherited-drift-and-changed, 34 unchanged, 14 intended change, 0 removed.
- References: 90 PNGs, identical on main.
- Inherited drift on main: 20 history-* files (main gained an "All tools" sidebar row that was
  never re-baselined) and 22 detail-*/popover-monitoring files (small text-row pixel differences,
  probably glyph rendering; cause not confirmed).
- Proposed baseline change: 96 add, 56 replace, 0 remove. Later on 2026-11-19: applied in this branch
  (see the Baseline acceptance section).
- Render review of 48 candidate screens: no blockers. Minor: truncated caption "Reset time
  unavailable · mea…" (popover-overview-reset-past-unknown-light); "1 failed" summary above two
  listed failures (overview-collection-details-dark); "today so far" bar drawn with "No reading for
  today yet" (detail-no-reading-today-light).
- Owner visual acceptance: NOT given as of the facts above. Later on 2026-11-19: given on a partial
  review (see the Baseline acceptance section).

### Next actions

1. Done 2026-11-19: #42 merged (995357d).
2. Done 2026-11-19: #43 merged (9e9ccc5), then #44 merged (e93da98).
3. Done 2026-11-19: owner visual acceptance on the baseline proposal, recorded on a partial review.
4. Owner hands-on popover checks (list above). Still open.

## Baseline acceptance — 2026-11-19

- Visual review: the owner reviewed the visual review package (a review page built from renders of the
  cumulative candidate). Renders of merged main e93da98 are byte-identical to that candidate. The owner
  said it looks good. The owner noted the review covered as much of the page as they could, so
  acceptance is recorded as given on a partial review.
- Baseline change applied in this branch to `docs/design/app/references/`: 96 PNGs added, 56 replaced,
  0 removed. `SHA256SUMS` regenerated. References now total 186 PNGs. `make app-snapshot-check` passes
  locally after the change. On main it failed with exactly those 56 mismatches.
- The 56 replaced files:
  - 42 had already drifted on main before this work. 20 are `history-*` screens: main gained an
    "All tools" sidebar row that was never re-baselined. 22 are `detail-*` and popover-monitoring
    screens with small text-row pixel differences; cause unconfirmed, probably glyph rendering.
  - 14 changed only because of this work.
  - Full-height screens changed from 840x1564 to 840x1562. Whether that is intentional is unconfirmed.
- Stage status:
  - implemented: yes
  - code-reviewed: yes
  - merged: yes
  - automated validation: yes (`make check` 377 tests; `make fixture-check` clean; `make app-test`
    180 tests; offscreen renders)
  - visually accepted: yes for rendered screens, on a partial owner review
  - interaction validation: only for the History window
  - installed: no
  - released: no
- Still open, needing a person at the keyboard:
  - popover timestamp reveal by focus and activation
  - trophy popover
  - drag and keyboard ordering and persistence
  - detail On/Off/Auto and switch appearance
  - theme Open/Reload/per-instance/invalid-file/restart behaviour
  - header indicators and manual collection feedback
  - spoken VoiceOver
- Three minor render observations, not yet decided:
  - truncated "Reset time unavailable · mea…" caption (popover-overview-reset-past-unknown-light)
  - "1 failed" above two listed failures (overview-collection-details-dark)
  - a "today so far" bar beside "No reading for today yet" (detail-no-reading-today-light)
