## Session State: all-view
Last updated: 2026-11-01 (implemented on `jp/f/phase-4-all-view`; renders and merge await the owner)

**Status: phase 4's plan is approved and merged (#28, `b569d31`). The implementation is in its own
PR, with 14 renders for the owner's visual acceptance. Merging it, installing the app and
releasing are separate owner decisions.**

### Current Position
| Part | State | Evidence |
| --- | --- | --- |
| PRD packet | **Approved** by the owner, 2026-11-01: D1–D7 as recommended, with corrections C1–C4 and a D4 clarification. The acceptance criteria are renumbered AC-1 to AC-12. | [`prd-all-view.md`](prd-all-view.md); the [decision record](https://github.com/JonathanPorta/ai-usage/pull/28#issuecomment-6569972670) on #28. |
| Task list | Tasks 1.0 to 5.2 done; 4.3 (the owner's visual acceptance) and 5.3 (CI and review on the final head) open. | [`tasks-all-view.md`](tasks-all-view.md) |
| Implementation | **Done, in review** on `jp/f/phase-4-all-view`. There is no collector change (`ai_usage_service.py` and `ai_usage_plugins/` are untouched; still 2.4.0). | `tests/test_all_view.py`: 19 tests. `AllToolsTests`: 22 XCTests. 10 weakened report rules and 10 weakened figure rules are each caught. Parity declares only `report.groups`. |
| Renders (AC-10) | **Awaiting the owner.** 16 new renders (8 scenes, light and dark). Against `main` rendered on this Mac, 64 are identical, and the 26 History renders differ only by the sidebar row. | `docs/design/app/design.md`, the native verification log. |
| Live check (AC-11) | Pending a separately approved app install. | — |

- **Entry:** phase 2's exit (accepted 2026-10-23). Phase 4 doesn't depend on phase 3, so
  phase 3's pending AC-13 doesn't block it.
- **Blocked:** no. Waiting on: the owner's review of the implementation PR and its renders.

### Phase-gate audit (phase 3 → phase 4, 2026-11-01)
**Result: PASS WITH WARNINGS.**
- **Rules compliance:** planning only. The PRD has the codebase analysis, lettered decisions,
  and acceptance criteria for approval; it doesn't skip the gates.
- **Listed done but not done:** none found.
- **Required but not done:** phase 3's AC-13 (the owner, on the work laptop). It doesn't gate
  phase 4.
- **Done differently than documented:**
  - `tasks/project-plan.md` still said phase 3's implementation was in review. This PR corrects it.
  - The personal Mac's collector is no longer the `d9ea445` one recorded in phase 3. #27 (the
    Login Items launcher, outside this project) was installed there on 2026-11-01. Phase 3's
    session state now says so.
- **Verification performed:** read-only checks only. The installed `~/.ai-usage/collector.py`
  hashes the same as `ai_usage_service.py` at `efc605d` (`511636191b81…`). The launcher
  `~/.ai-usage/ai-usage-collector` is there, dated 2026-11-01 14:44.

### Key Decisions
- D1–D7 as recommended, with C1–C4 and the D4 clarification (owner, 2026-11-01). Don't ask
  again unless a material scope or product decision changes.
- Merging the implementation, installing the app and releasing are separate owner decisions.
- AC-13 (phase 3) stays pending and independent of phase 4. The #27 installed revision is kept
  as recorded, and the personal-Mac reinstall isn't repeated.
- Carried forward: no collector change, no install and no release without the owner's separate
  approval. Renders and test builds stay out of the owner's running app.

### Engineering refinements made during implementation (within the approved contract)
1. **The "so far" stack.** The accent dashed outline vanished against the first series' blue.
   The stack is now drawn lighter, inside a dashed outline in the text color. Its legend reads
   "So far (day not over)", because a past day cut short gets the same mark.
2. **One-account readouts.** A lone account's part is left out of a day's readout unless it adds
   its own time or incomplete turns ("Codex 805K tokens", not "…: Codex 805K").
3. **A tool with no readings in the range.** Its panel keeps its figures ("—") and the
   breakdown, and drops the empty chart.
4. **The dark series** were spread after the first overflow render (lavender and slate looked
   alike). The contrast test covers both appearances.
5. **The renderer's selected day.** The incomplete-day render shows a day selected so its
   readout can be reviewed. This uses `chartPreviewDate`, an environment value that the app
   never sets.

6. **The owner's review of the first packet (2026-11-02), first finding:** the readout no
   longer stops at three lines.
   - Every account's value, state and time stays visible, and the chart reserves the tallest
     readout so its height doesn't move.
   - Breakdown names and statuses wrap instead of truncating.
   - Two overflow-fixture accounts have long names, and a narrow overflow render with today
     selected shows a four-line readout.
7. **The same review, second finding:** a zero day with incomplete turns reads "at least 0
   tokens" with the warning. "Measured, no usage" is only for a complete zero. A regression
   covers it, and fails against the old wording.

### Discovered, not in scope
- A provider's own History page shows its range total without "at least" when the range has a
  missing day; only its "Days measured" card shows the gap. Phase 4's rule F2 applies only to
  All tools, as approved. Changing the provider pages would be a separate decision.
