## Session State: private-plugins
Last updated: 2026-10-25 (the separate five-defect review fixed)

**Status: phase 3 is implemented on `jp/f/private-plugins` and up for review. It is not merged,
not installed and not released; each of those is a separate owner decision. AC-13 is pending
until the owner performs it on the work laptop.**

### Current Position
| Part | State | Evidence |
| --- | --- | --- |
| PRD packet | **Approved** by the owner, 2026-10-24, with five corrections; merged as `1b37aa7` (#22). | Human decision record on #22; independent review approved at `f50c643`. |
| Task list | All tasks 1.0–8.0 done. | `tasks/phase-3/tasks-private-plugins.md` |
| Implementation | **In review** (this PR). Merge on hold by the owner until the five-defect review is resolved. | `make check` (with `AI_USAGE_REQUIRE_PARITY_HISTORY=1`): 273 tests OK (1 skipped: a non-macOS control). `make app-test`: 66 XCTests OK. `fixture-check`, `spec-check --strict` and `design-check --strict` pass. |
| Renders | 14 new synthetic renders (7 screens, light and dark), up for the owner's visual acceptance, including the whole overview list down to the Example card. The 76 existing renders are byte-identical to `1b37aa7`'s on this Mac. | `make app-snapshot`; the PR description. |
| AC-13 | **Pending** until the owner performs it on the work laptop, and reports only pass or fail. | — |

- **Validation review mode:** auto-proceed (owner, 2026-10-24).
- **Blocked:** no. Waiting on: independent review of the five fixes, then the owner's
  implementation merge, visual acceptance, reinstall decision and AC-13.

### Key Decisions
- D1–D4 as recommended, and AC-1 to AC-13, approved by the owner on 2026-10-24 with five
  corrections. Don't ask again.
- Merging the implementation, reinstalling and releasing are separate owner decisions.
- Test builds and renders stay isolated from the owner's running app. Private plugin code and
  data stay on the work laptop. Phase 4 and unrelated cleanup are out.
- The phase-1 golden files are kept. Phase 3 declares only the capability, plus `VERSION`.

### Engineering refinements made during implementation (within the approved contract)
1. **Detected row:** a successful run whose response has no availability row gets one
   `availability/detected` row ("the plugin ran"). Without it, the report showed a plugin that
   returned no rows as "not found".
2. **PATH:** the plugin's `PATH` is built in `external.plugin_path()`: the collector's discovery
   folders without the environment's own `PATH`. `support.discovery_path()` is unchanged; its
   verbatim-move test guards it.
3. **Unknown ids:** `configure providers.<id>.…` for an id that is neither discovered nor
   configured is refused with the pre-phase-3 message, so the `symlinked_config` parity scenario
   is unchanged. `plugin enable` gives its own clearer message.
4. **Test isolation:** in-process tests and the fixture generator would have scanned the
   developer's real `~/.ai-usage/plugins`. `tests/isolation.py` points in-process discovery at
   an empty folder for each module, and `ai_usage_fixtures.fixture_report` sets `HOME` to its
   sandbox while it builds.

### Independent review of #23 (2026-10-24, at `f82cd8f`): changes requested, both fixed
- **F1 (blocking):** a symlinked plugin under a folder others could write (non-sticky 0777)
  could be swapped between discovery and launch. Discovery now refuses any folder on the
  resolved paths of `plugin.json` and the executable that isn't root's or the uid's, or that
  is group- or other-writable without the sticky bit. Test: a swap under a 0777 parent never
  runs; a sticky 1777 parent is accepted.
- **F2 (important):** a file vanishing between discovery's checks raised out of collection.
  `discover` now turns any `OSError` into that plugin's invalid state. Test: the providers
  before and after it collect normally.

### Phase-gate audit (rule 13), 2026-10-24
- Rules: no LLM attribution (09); branch and PR conventions (10); Makefile surface (07); the
  PRD's ACs are unchanged and are referenced by number in the tests and the PR.
- Specs: `docs/PLUGINS.md`, `docs/CLI.md`, `docs/APP.md` and `docs/DECISIONS.md` match the code.
  The trust statement is word for word, as a test checks.
- Scope: no phase-4 code, no Alexandria work, no installed-collector change, no release.
- Privacy: the diff holds only synthetic plugins and fixtures. The scan found no real
  hostnames, credentials, work-tool names or logs.
- Drift found and fixed: in-process discovery of the real HOME (refinement 4).

### Separate five-defect review (owner, 2026-10-25, reproduced at `b45d338`): fixed
Each has a regression test in `ReviewedDefectTests` that fails on `b45d338`'s code and passes now.
1. **Lone surrogate (`\ud800`)** in a message or in `state` passed validation, broke the CSV
   write and left `state.json.pending`, so every later poll failed. Now any string that isn't
   valid Unicode (rows, state, keys; and the manifest) fails the run before anything is written.
2. **`10**400` in an unconfigured manifest's `default_settings`** raised `OverflowError` out of
   discovery. Numbers now accept integers only within ±2^53 (`is_number` never raises), and an
   unexpected error while checking one folder makes only that folder invalid.
3. **`plugins/claude`** was refused but its id still came back as a second `claude` account
   (path-role collision). `Discovery.ids()` now leaves out built-in and added-account ids; the
   folder is listed under `other_folders` and in `plugin_errors`.
4. **Events** summed correctly (110/55) but the source stayed `waiting` and usage
   `stale/not_collected`, because event rows never count as a check's rows. An events usage
   source is now `needs_rows=False`, as Claude's and Grok's are: a run with no error for it is a read.
5. **A failed first quota source** made a second source's fresh window `stale/failed`. Each
   window now follows the quota source whose CSV source wrote it; windows of the same scope from
   two sources get ids `<source id>-<scope>`. Built-ins have one quota source, so their output is
   unchanged (parity passes).
