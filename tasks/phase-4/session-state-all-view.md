## Session State: all-view
Last updated: 2026-11-02 (phase 4 closed: merged, installed, AC-10 and AC-11 passed)

**Status: phase 4 is complete.** The plan is merged (#28, `b569d31`) and the implementation is
merged (#29, `3a2473a`). The owner accepted its 16 renders (AC-10), the app was installed from
`3a2473a`, and the owner passed the live check (AC-11), all on 2026-11-02. Release is not
approved.

### Current Position
| Part | State | Evidence |
| --- | --- | --- |
| PRD packet | **Approved** by the owner, 2026-11-01: D1–D7 as recommended, with corrections C1–C4 and a D4 clarification. The acceptance criteria are renumbered AC-1 to AC-12. | [`prd-all-view.md`](prd-all-view.md); the [decision record](https://github.com/JonathanPorta/ai-usage/pull/28#issuecomment-6569972670) on #28. |
| Task list | All tasks done, 1.0 to 5.3. | [`tasks-all-view.md`](tasks-all-view.md) |
| Implementation | **Merged** as `3a2473a` ([#29](https://github.com/JonathanPorta/ai-usage/pull/29), squash-merged at head `cd6ed0f` with the head-match guard; [decision record](https://github.com/JonathanPorta/ai-usage/pull/29#issuecomment-6574656688)). Main CI run 41427767134 is green on `3a2473a`. There is no collector change (`ai_usage_service.py` and `ai_usage_plugins/` are untouched; still 2.4.0). | `tests/test_all_view.py`: 19 tests. `AllToolsTests`: 22 XCTests. 10 weakened report rules and 10 weakened figure rules are each caught. Parity declares only `report.groups`. |
| Renders (AC-10) | **Accepted** by the owner, 2026-11-02, for the 16-render packet from `082c5f9` (8 scenes, light and dark). They are committed as references with their checksums. Against `main` rendered on this Mac, 64 are identical, and the 26 earlier History renders differ only by the sidebar row; those baselines aren't replaced. | The [decision record](https://github.com/JonathanPorta/ai-usage/pull/29#issuecomment-6574017977) on #29; `docs/design/app/references/README.md`. |
| App install | **Installed** on the personal Mac, 2026-11-02, owner-approved. Built in a clean clone pinned to `3a2473a` (`~/devel/portaj/ai-usage-pinned/3a2473a…`), checked with `check_app_bundle.py` and `codesign --verify --deep --strict` (ad-hoc, `codes.porta.ai-usage.app`), and deployed to the path it already ran from. The running app's CDHash matches the bundle (executable sha256 `d0acf053…`). The collector, its plugins, `config.json`, `install-ownership.json`, the LaunchAgent and history are unchanged, and the collector process wasn't restarted. | Rollback: `~/.ai-usage/app-rollback-pre-3a2473a-20261102032859` (the previous app, sha256 `984e02d2…`). |
| Live check (AC-11) | **Passed** by the owner, 2026-11-02: both Claude accounts appear in All tools, and each account's Today value matches its own History page. | The [decision record](https://github.com/JonathanPorta/ai-usage/pull/29#issuecomment-6575171653) on #29. |

- **Entry:** phase 2's exit (accepted 2026-10-23). Phase 4 doesn't depend on phase 3, so
  phase 3's pending AC-13 doesn't block it.
- **Blocked:** no. Phase 4 is closed; nothing waits on it. AC-13 (phase 3) stays pending on the
  work laptop, release isn't approved, and deferred work stays deferred.

### Phase-gate audit (phase 4 exit, 2026-11-02)
**Result: PASS.**
- **Exit criterion** (the project plan: the All view renders, and the owner accepts it visually):
  met. AC-10 was accepted, and AC-11 passed live.
- **Listed done but not done:** none found. Tasks 1.0 to 5.3 are checked against #29, its merge
  commit and the two decision records.
- **Required but not done:** none for phase 4. Phase 3's AC-13 stays pending on the work laptop,
  independent of this phase.
- **Done differently than documented:** none. The 26 earlier History baselines weren't
  replaced, as recorded in the references README.
- **Verification performed:** main CI on `3a2473a`; the install checks above. AC-11 is the
  owner's live check; no agent GUI automation was used.

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
