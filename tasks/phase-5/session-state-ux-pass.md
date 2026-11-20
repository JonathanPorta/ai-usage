## Session State: ux-pass
Last updated: 2026-11-19 (evening)

**Status: phase 5 is implemented and merged to `main`: #41 to #45 (#45 as merge commit ceee949). Visual baselines are accepted. Interaction validation is partial; the four follow-up fixes are verified on screen; the fix branch is not yet merged.**
Requirements come from the owner's work package (2026-11-13). Slices 1 to 4 are merged (#41, #42, #43, #44; merge commits
62af14a, 995357d, 9e9ccc5, e93da98). Slice follow-up #45 is merged as ceee949. Nothing is installed or released. The
2026-11-15 status is historical.

### Current Position
- **Working on:** the owner's remaining human-only checks, and merge of the follow-up fixes on `jp/f/ux-closeout-fixes`
  (base `main` ceee949, head 6a366bd). Details: "Interaction validation and follow-up fixes — 2026-11-19 (evening)".
- **Status:** as of 2026-11-19 evening, #41 to #45 are merged to `main`. The post-merge `main` run passed. The fix branch
  has four commits (90c08e9, dc6ec9c, 1c456ac, 6a366bd); its automated checks pass and the fixes are verified on screen.
- **Visual acceptance:** accepted and merged. The owner accepted the proposed baselines (96 added, 56 replaced, 0 removed,
  including 10 corrected screens and the 2 px height reductions) after a same-head re-review. Merged as ceee949.
- **Blocked:** No.

### Key Decisions
- The owner's work package is the requirements source; its proposed defaults are adopted
  (owner, 2026-11-15).
- All code is written by Opus low-effort subagents. The coordinator does not write code
  (owner, 2026-11-15).
- Accepted baselines in `docs/design/app/references` stayed unchanged until the owner decided on PR #45's proposed
  baselines (owner, 2026-11-15). The owner accepted them on 2026-11-19; #45 merged as ceee949 with those baselines.
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
1. Runtime verification of 90c08e9 and dc6ec9c in the running app (pending; see the evening section).
2. Owner's remaining human-only checks: seven items in the evening section.
3. Installing the new app and collector over the live installation is a separate owner action, not authorized by this work.

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
- Known limits: the "cost isn't reported" empty state now uses `costs.reports_cost` (completion pass), with note-text
  fallback for older collectors. The status look-alike threshold (sRGB distance 48) is the implementer's choice.
- Issue #9: reset annotation shipped inside the chart card in slice 3. Its baselines were accepted and merged with #45 (ceee949). The exact-time reveal legibility fix (90c08e9) awaits runtime verification.

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
- Agreed sequence, one PR at a time (used for #41–#44; complete): retarget to main first; merge main in; push;
  exactly one run; re-review; merge.
- No evidence reuse: there is no mechanism, and every head moves.
- Put fix commits on #44's branch before retargeting it.
- Merge only when `proofglass supervisor status` shows 0 of 1 slots in use, because pushes to main
  have no wait policy.
- On queue_timeout, use "Re-run all jobs" once, after the Mac is free. (Superseded 2026-11-19 ~20:22Z: for a flaky
  hosted job, "Re-run failed jobs" is enough.)

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

Superseded. The current next actions are in Current Position and in "Closeout — 2026-11-19" below.

## Closeout — 2026-11-19

> Superseded (2026-11-19 evening): #45 is merged as ceee949 and visual acceptance is recorded. Statements below that #45 is
> pending, or that visual acceptance is not recorded, are history. The walkthrough and next actions below are superseded by the
> evening section.

**Owner feedback (recorded as given):** the owner reviewed as much of the visual package as they could, and it looks
good. This is a positive review of the material inspected. It is not a record that every proposed baseline was
inspected, and it does not cover untested interactions.

**Stage "visually accepted": not recorded for the baseline set as a whole.** It is pending the owner's decision on
follow-up PR #45. Merging #45 is what would make the proposed baselines the accepted ones. The PRRQ review of #45
requires an explicit owner decision record before that.

### Follow-up PR #45

Branch `jp/f/ux-baselines`, base `main`. It carries three fix commits, PROPOSED reference baselines in
`docs/design/app/references`, and these records. The accepted references on `main` are unchanged.

Fixes:
- `64f6284`: compact quota captions wrap to two lines and carry the full text as a tooltip. They were truncated
  ("Reset time unavailable · mea…").
- `4088639`: three snapshot scenarios corrected to match the states they demonstrate.
  - Collection details: the failure was attached to a source that does not exist, so the summary said "1 failed"
    above two listed failures. It now says "2 failed". The summary counts providers and the list shows failed checks;
    app aggregation is unchanged. One provider with two failed checks would still read "1 failed".
  - No reading today: today's chart day is marked missing and renders as a dashed outline.
  - Success at limit: the at-limit card is ordered first so the state is in view.
- `25741df`: sandbox runs (`AI_USAGE_STATE_DIR` set) keep preferences in `<state dir>/preferences.plist` instead of the
  installed app's defaults, and show a "Sandbox" marker (menu-bar label and tooltip, popover header, History window
  title). The sandbox app shares the installed app's bundle id, so card order, acknowledged notices and notification
  settings would otherwise have been written to live preferences. No live preference was changed during validation;
  the preferences file was unchanged before and after. This commit changes no render.

Verified, no change needed:
- Theme Open and Reload buttons are dimmed only in offscreen renders. The render store has no theme file location; the
  running app supplies one.
- Truncated account names already expose the full name via tooltip and accessibility label.

### Proposed baselines (PR #45)

- Rendered at `4088639`. Against the accepted references at `e93da98`: 96 add, 56 replace, 0 remove, 34 unchanged;
  186 total.
- 176 of the 186 are byte-identical to the merged-main `e93da98` renders the owner looked at. The 10 that differ are the
  corrected screens: detail-no-reading-today, overview-collection-details, popover-overview-result-warning,
  popover-overview-reset-past-unknown, popover-overview-success-at-limit (light and dark).
- Candidate `5509771` and merged `main` `e93da98` renders are byte-identical (186/186). `25741df` changes no render
  (snapshot check passes, 186 OK).
- Of the 56 replacements, 42 files had already drifted between the accepted references and old main `a939f3a`: 26
  `history-*`, 14 `detail-*`, 2 `popover-monitoring-*`. For `history-*`, old main gained an "All tools" sidebar row
  (confirmed on one pair, assumed for the rest). For `detail-*` and `popover-monitoring-*` the cause is not established.
  The other 14 changed only in this work.
- Open visual decision: 30 screens are 2 px shorter than their accepted references. 28 at 840x1564 become 840x1562, and
  popover-overview-short light and dark go from 840x1212 to 840x1210. This came with the UX pass, not from old main.
  Whether it is intended is unconfirmed.

### Validation

- `make check` exit 0; `make fixture-check` clean; `make app-test` 182 tests, 0 failures; `make app-snapshot-check`
  passes on the branch (186 OK).

### Interaction validation (sandbox, synthetic data)

- Passed, History window only: opening it, provider switching, Tokens/Quota toggle, range selection, the reset control
  exposing the exact time via value and help, and accessibility labels, values and help on those controls.
- Not exercised: everything in the popover and Settings (timestamp reveal, trophy details, card ordering and
  persistence, tracking On/Off/Auto, theme Open/Reload/fallback/restart, header actions, manual collection feedback),
  and spoken VoiceOver. Automation could not open the menu-bar popover: the accessibility press had no effect, and
  scripted clicking lacks assistive access.
- Failed: none.
- A 15-step hands-on walkthrough is in the review package at
  `/Users/portaj/devel/ai-scratch/ai-usage-ux-pass/visual-package/interaction/RESULTS-closeout.md`.

### Running app

- A GUI process started 2026-11-17 15:56:53 by launchd, right after a reboot (apps reopened at login). It runs from the
  `ux-themes-slice` task worktree build with no sandbox variables, so it uses the live config `~/.ai-usage/config.json`
  and the default state directory `~/Library/Application Support/AI Usage`.
- Evidence of what it wrote: only its report cache `last-report.json` there. The preferences file was last modified
  2026-11-02 and `config.json` 2026-11-10, both before it started. The binary on disk was rebuilt on 2026-11-19, so the
  process runs an older image than the files now there.
- The collector is a separate launchd agent (`codes.porta.ai-usage`, `~/.ai-usage/collector.py daemon`) and does not
  depend on that GUI. No app bundle is installed in `/Applications`.
- Nothing was stopped or replaced. The `ux-themes-slice` worktree is kept while that process runs. Quitting the GUI
  removes the menu-bar app until another build is launched; it does not affect collection.
- Caution: `make app-run-sandbox` runs `pkill -x AIUsage`, which would also kill that GUI.

### Worktrees

- Slice heads preserved with keep refs and bundles: ux-state-slice 04c992e, ux-overview-slice f83e34e,
  ux-detail-slice 30acd63, ux-themes-slice 23196c8.
- Removed through the lifecycle check: ux-overview-slice, ux-detail-slice, ux-baseline-check, ux-visual-main.
- Kept: ux-themes-slice (running process), ux-baselines (PR #45).

### CI coordination (ProofGlass, 2026-11-19 ~20:22Z)

- One lane, `ai-usage-macos`, on the single Mac builder. Pull requests wait up to an hour when it is busy. There is no
  hosted macOS fallback.
- Plan: one push when the batch is complete; merge only when approved and the slot is free at merge time.
- For a flaky hosted job on a PR, "Re-run failed jobs" is now enough.

### Stage summary

- implemented: yes
- code-reviewed and merged: yes for #41–#44; #45 pending
- automated validation: yes
- visually accepted (proposed baseline set): pending the owner's decision on #45
- interaction validation: History window passed; popover, Settings and VoiceOver not exercised
- installed: no
- released: no

### Next actions

1. Owner decision on #45's proposed baselines, including the corrected screens and the 2 px height question. Merging #45
   requires an explicit owner decision record.
2. Owner hands-on walkthrough.
3. Installation and release are separate and not authorized by this work.

## Interaction validation and follow-up fixes — 2026-11-19 (evening)

Full detail and the per-check evidence: `/Users/portaj/devel/ai-scratch/ai-usage-ux-pass/visual-package/interaction/RESULTS-closeout.md`
(sections "Hands-on results — third session, keyboard input" and "Runtime verification of the fixes"). Session 3 ran on the
main ceee949 build, sandbox PID 35321, 18:18 to 18:46 MST. Runtime verification ran 18:49 to 19:03 MST on the sandbox app
rebuilt from `jp/f/ux-closeout-fixes`.

| Check | Result |
|---|---|
| Popover opens by real click; "Sandbox" header label | Passed |
| Escape from provider detail; focus returns to the card with a visible ring | Passed |
| Arrow and Tab focus on overview cards, scrolled into view | Passed |
| Exact reset time on keyboard focus | Failed, fixed (defect 1, 90c08e9); passed on screen |
| Exact reset time on activation (Space) | Passed |
| Exact time on hover | Not exercised (no pointer hover) |
| Trophy statistics by keyboard; Escape dismisses | Passed |
| Trophy focus statistics panel | Failed, fixed (defect 4, 6a366bd); passed on screen |
| Mini-chart keyboard caption | Failed, fixed (defect 2, dc6ec9c); passed on screen |
| Manual collection (Cmd-R): completion feedback | Passed; in-progress feedback not observed |
| Settings opens (Cmd-comma), shows sandbox config path | Passed |
| Settings provider rows | Failed, fixed (defect 3, 1c456ac); passed on screen |
| Themes: global and per-provider application | Passed (after restarting only the sandbox app) |
| Themes: invalid-file fallback after restart | Passed (warm/violet kept; last-valid file intact) |
| Themes: Open and Reload buttons, invalid-file notice, light mode | Not exercised |
| Tracking Auto/On/Off, ordering controls | Not exercised (not reachable by Tab; pointer clicks unavailable) |
| Card ordering and persistence; recent-activity stability | Not exercised |
| Popover accessibility attributes; spoken VoiceOver | Not exercised |

Observed, not established as defects: a provider past its reset time showed "Reset due · awaiting update" with the stale
marker; header keyboard focus reaches the monitoring indicator with a visible focus ring. On three occasions the first
click on the sandbox menu-bar icon right after launch showed no popover (possibly an artifact of synthetic clicks; needs a
person). Synthetic pointer clicks land about 3 % left of and above the aimed position on this display, so pointer-only
controls in the popover were not exercised.

Defects and fix commits (branch `jp/f/ux-closeout-fixes`, base main ceee949):
1. Exact-time focus reveal drawn behind figure and chart: `90c08e9`. Drawn at the window root above following content; hidden while the activation popover shows.
2. Focused mini-chart caption truncated: `dc6ec9c`. May wrap to two lines; full text in a tooltip.
3. Settings duplicate "Not found": `1c456ac`. Not repeated under the setup line when the tool is not detected; unit test added.
4. Trophy focus statistics panel drawn under the card's own rows: `6a366bd`. Drawn through the same window-level reveal host; verified opaque and legible, right edge aligned to the trophy.

Automated checks on head 6a366bd and docs: `make app-test` exit 0 (183 tests, 0 failures); `make check` exit 0;
`make app-snapshot-check` 186 OK; accepted baselines unchanged.

Remaining human-only checks:
1. Hover tooltip on a reset label.
2. Tracking Auto/On/Off in Settings and the detail switch; an Off provider's card leaves the overview.
3. Ordering: up/down buttons and drag in Settings; persists after quitting only the sandbox app; recent-activity order does not reshuffle while the popover is open.
4. Theme Open and Reload buttons, the invalid-file notice, light mode.
5. Header warning badge and monitoring indicator actions; in-progress feedback during manual collection.
6. VoiceOver speech for a card, a reset label and the tracking switch.
7. First click after launch opens the popover (check by hand: launch, click the icon once).
8. Reset-time chip in provider detail and History, also when scrolled.

Pending: the reset-time chip in provider detail and History, and near the bottom edge of the list (check 8 above).

### Stage summary (2026-11-19 evening)
- implemented: yes
- code-reviewed and merged: yes for #41 to #45 (#45 as ceee949); post-merge main run passed
- automated validation: yes; fix branch checks pass at 6a366bd
- visually accepted: yes; proposed baseline set accepted by the owner and merged (ceee949)
- interaction validation: partial; four defects found, fixed on `jp/f/ux-closeout-fixes` and verified on screen; pointer-only controls and VoiceOver not exercised
- installed: no
- released: no
