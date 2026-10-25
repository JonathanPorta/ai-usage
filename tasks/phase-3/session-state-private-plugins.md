## Session State: private-plugins
Last updated: 2026-10-24 (implementation PR opened)

**Status: phase 3 is implemented on `jp/f/private-plugins` and up for review. It is not merged,
not installed and not released; each of those is a separate owner decision. AC-13 is pending
until the owner performs it on the work laptop.**

### Current Position
| Part | State | Evidence |
| --- | --- | --- |
| PRD packet | **Approved** by the owner, 2026-10-24, with five corrections; merged as `1b37aa7` (#22). | Human decision record on #22; independent review approved at `f50c643`. |
| Task list | All tasks 1.0–8.0 done. | `tasks/phase-3/tasks-private-plugins.md` |
| Implementation | **In review** (this PR). | `make check`: 265 tests OK (1 skipped: a non-macOS control). `make app-test`: 66 XCTests OK. `fixture-check`, `spec-check --strict` and `design-check --strict` pass. |
| Renders | 12 new synthetic renders (6 screens, light and dark), up for the owner's visual acceptance. The 76 existing renders are byte-identical to `1b37aa7`'s on this Mac. | `make app-snapshot`; the PR description. |
| AC-13 | **Pending** until the owner performs it on the work laptop, and reports only pass or fail. | — |

- **Validation review mode:** auto-proceed (owner, 2026-10-24).
- **Blocked:** no. Waiting on the owner for: the implementation merge, visual acceptance, a
  reinstall decision and AC-13.

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

### Phase-gate audit (rule 13), 2026-10-24
- Rules: no LLM attribution (09); branch and PR conventions (10); Makefile surface (07); the
  PRD's ACs are unchanged and are referenced by number in the tests and the PR.
- Specs: `docs/PLUGINS.md`, `docs/CLI.md`, `docs/APP.md` and `docs/DECISIONS.md` match the code.
  The trust statement is word for word, as a test checks.
- Scope: no phase-4 code, no Alexandria work, no installed-collector change, no release.
- Privacy: the diff holds only synthetic plugins and fixtures. The scan found no real
  hostnames, credentials, work-tool names or logs.
- Drift found and fixed: in-process discovery of the real HOME (refinement 4).
