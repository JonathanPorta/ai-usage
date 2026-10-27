## Session State: private-plugins
Last updated: 2026-10-26 (the AC-13 rollback fixed for F1 and tested; AC-13 still pending)

**Status: phase 3 is merged (`d481cef`, #23) and installed on the owner's personal Mac from
`d9ea445`, which also has the Collect-result banner fix (#24). The owner accepted that reinstall on
2026-10-25. It is not released; a release is a separate, unapproved decision. AC-13 is pending
until the owner performs it on the work laptop.**

### Current Position
| Part | State | Evidence |
| --- | --- | --- |
| PRD packet | **Approved** by the owner, 2026-10-24, with five corrections; merged as `1b37aa7` (#22). | Human decision record on #22; independent review approved at `f50c643`. |
| Task list | All tasks 1.0–8.0 done. | `tasks/phase-3/tasks-private-plugins.md` |
| Implementation | **Merged** as `d481cef` (#23), on the owner's approval, from the approved head `191735a`. Main CI is green on `d481cef`, including Proofglass macOS. | `make check` (with `AI_USAGE_REQUIRE_PARITY_HISTORY=1`): 273 tests OK (1 skipped: a non-macOS control). `make app-test`: 66 XCTests OK. `fixture-check`, `spec-check --strict` and `design-check --strict` pass. |
| Banner fix | **Merged** as `d9ea445` (#24): a newer check replaces the Collect now result. Merged from `7373993`: main was merged into the branch, then freshly reviewed and approved at that head. Main CI is green on `d9ea445`. | #24's review at `7373993`; `make check` 273 OK, `make app-test` 68 OK on that head. |
| Renders | **Accepted** by the owner, 2026-10-25: 14 synthetic renders (7 screens, light and dark). The 76 existing renders are byte-identical to `1b37aa7`'s on this Mac. | The owner's decision record on #23. |
| Personal-Mac reinstall | **Done and accepted** by the owner, 2026-10-25. Collector 2.4.0 and the app are from `d9ea445`. | See "Personal-Mac reinstall" below. |
| Release | **Not approved.** | — |
| AC-13 | **Pending** until the owner performs it on the work laptop, and reports only pass or fail. The owner's corrected procedure (2026-10-26) covers fresh and upgrade installs, and its install steps stop on failure. Its checks need a new collection with the plugin's success marker, match the report contract per manifest profile, and keep the measurement comparison as a separate check. Its upgrade rollback uses the installers' own records (review F1 on #25). | [`procedure-ac13-work-laptop.md`](procedure-ac13-work-laptop.md); `tests/test_ac13_procedure.py`: 31 synthetic tests, no real provider tools (an audit hook checks what starts). 8 weakened checks and 10 weakened rollback steps are each caught. |

- **Validation review mode:** auto-proceed (owner, 2026-10-24).
- **Blocked:** no. Waiting on: AC-13 (the owner, on the work laptop). A release needs its own
  approval.
- **Follow-up:** test isolation for provider discovery,
  [`follow-up-test-isolation-provider-discovery.md`](follow-up-test-isolation-provider-discovery.md).

### Independent review of #25 (2026-10-26, at `304d751`): F1 fixed
- **F1 (important):** the upgrade rollback copied the old collector, package and LaunchAgent back under 2.4.0's `install-ownership.json`. A later `uninstall` kept the old files as modified and dropped the record, so the next install refused them as unowned.
- **Fix:** step 3 now also backs up the ownership record, and refuses an install that has none. The rollback uses the installers' own mechanisms:
  - 2.4.0's `uninstall` removes what its record lists, including `external.py` and the integration settings the upgrade added;
  - the previous collector's own `install`, run from the backup, writes its files and a matching record.

  The shutdown, the locked snapshot with journal recovery, and a kept copy of the newer data are unchanged. The data from before the upgrade is put back under the previous collector's locks.
- **Test:** `RollbackTests` runs the procedure's backup, install, rollback and previous blocks as written, in an isolated HOME, from the real 2.3.0 (`1b37aa7`) to the pin, then an uninstall and a reinstall.
  - It covers an integration managed before the upgrade (Gemini CLI) and one the upgrade added (Antigravity), and checks that the owner's other settings survive.
  - It checks that the files and the ownership record agree at each step.
  - It also covers a file changed after the upgrade, a changed integration setting, an integration setting nothing manages, and the old copy-back rollback.

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

### Personal-Mac reinstall (owner-approved and accepted, 2026-10-25)
- **Installed revision:** `d9ea44543861bd18eaba5f1c86867d4ef2079d63`.
  - The collector was installed from a fresh clone of the GitHub repository, pinned to that SHA and clean, at `~/devel/portaj/ai-usage-pinned/<sha>`.
  - The app was built and verified in that clone, then deployed to the path it already ran from, `macos/build/AI Usage.app` in the owner's main checkout, without a rebuild.
  - Before the reinstall: collector 2.3.0, and both the collector and the app's bundled collector were byte-identical to `1b37aa7`.
- **Backup:** `~/.ai-usage/pre-2.4.0-20261025153758`. It holds:
  - a locked snapshot of `config.json`, `state.json` and `usage.csv`;
  - the 2.3.0 collector and plugin package, the LaunchAgent, `install-ownership.json` and the previous app;
  - the completed procedure (`procedure.md`), with its run log and its sandbox rehearsal log.

  Earlier backups were left in place, and their SHA-256 manifest matched before and after. **Keep the backup, and keep the pinned rollback clone at `1b37aa7` (`~/devel/portaj/ai-usage-pinned/1b37aa78a4d7e1768c17d9c5db1984280059008d`).** The procedure's rollback uses both.
- **Preflight (nothing live changed):**
  - Main CI was green on the SHA, and GitHub verified the commit signature.
  - The app passes `make app-build` and `check_app_bundle.py`, and `codesign --verify --deep --strict` (ad-hoc, `codes.porta.ai-usage.app`). Its bundled report, service and 9 plugin modules are byte-identical to the clone, with no `plugins/`.
  - `make launchd-integration-check` passed on a disposable agent, and the real agent's pid was unchanged.
  - A sandbox rehearsal ran the data and file steps, rollback included. It used a fake `claude`, but also reached a real tool; see the follow-up.
- **Verified on the live install:**
  - **Collector:**
    - `collector.py` and `ai_usage_plugins/` are byte-identical to the pinned clone;
    - `config.json`, `state.json`, `usage.csv` and the LaunchAgent are byte-identical to the backup after the install, so history is preserved;
    - version 2.4.0, with `instances` and `external_plugins` both true and no `plugin_errors`;
    - `plugin list` is empty, as expected, and no `plugins/` folder was created.
  - **Collections:**
    - `once` exit code captured as 0: 76 rows, no error rows, both `claude` and `claude-az`;
    - a fresh collection by the service passed the phase-2 service check (both Claude accounts), with errors=0.
  - **App:**
    - it runs from the deployed path, and its code-signature hash equals the verified build's;
    - it wrote `last-report.json` after launch: collector 2.4.0, local plugins supported, the service running, both Claude accounts;
    - the bundled reporter returns quota `current` for both accounts and 0 failures;
    - the signature still verifies after launch.
- **Seen by the owner on screen (2026-10-26):** after a Collect now with no error rows, the overview reads Monitoring and lists every provider. Claude Code and Claude (az) have separate cards, each with its own limits and today's total.
- **Still unverified:** Settings, and the Collect result banner giving way to a later check. No GUI automation was used or authorized.
- **Re-install from the same SHA (2026-10-26):** the owner ran `ai_usage_service.py install` again from a second clean clone at `d9ea445`. Afterwards:
  - the collector and plugin package are still byte-identical to the pinned clone, and `config.json` is unchanged;
  - only the LaunchAgent changed: the installer records the installing shell's `PATH`, and `install-ownership.json` updated its hash to match.
- AC-10 and the accepted renders stay closed; this reinstall doesn't reopen them.
