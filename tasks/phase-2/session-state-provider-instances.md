## Session State: provider-instances
Last updated: 2026-10-23

**Status: phases 1 and 2 are implemented, merged and installed. Phase 2's operational acceptance (AC-10) is pending the owner's three manual observations below.**

### Current Position
| Part | State | Evidence |
| --- | --- | --- |
| Implementation | **Merged.** PR #11, squash-merged at head `dc5371d` as `315c43e`. | Main CI run 39540177768 green; Proofglass job `job_01M5E8XYMRH8F9403R60S8209J` ("Passed on a disposable macOS VM"). |
| Visuals | **Approved and merged.** The 24 reviewed renders are in #12 (`4542429`), and the single-account `popover-settings`, approved separately, is in #14 (`c044725`). | Decision records on #12 and #14. |
| Corrective work | **Merged.** #13 (`1c89e53`): refresh collisions, folder spelling, the add-account result, and the procedure corrections. | Decision record on #13. |
| Reinstall | **Done** (owner approved, 2026-10-21). 2.3.0 is installed and `claude-az` was added. Backup: `~/.ai-usage/pre-2.3.0-20261021011658`. | Steps 1–5, 7 and 8 per `procedure-reinstall-ac10.md`. |
| Stale-quota correction | **Merged and installed.** #15 (`ebe2b71`): a kept Claude reading after a failed refresh is reported stale, dated by its fetch. Collector reinstalled from `ebe2b71` on 2026-10-22 at 02:53 local (owner approved). Backup: `~/.ai-usage/pre-ebe2b71-20261022025302`. | Main CI run 39750544011 green; Proofglass overall `job_01M5HCRQT84VTRN2VKVQJCBH41`. `fix-stale-quota-reporting.md`. |
| Chart hover and History | **Merged and installed.** #16 (`c8eac81`): each day chart reserves its tallest readout, so hovering doesn't reflow the card; opening History closes the popover. The installed app was replaced with a separate build of `c8eac81` on 2026-10-22 at 09:14 local (owner approved); the running executable's sha256 `498113e6…` matches that build. The collector, config and history were left alone. Rollback: `~/.ai-usage/app-rollback-pre-c8eac81-20261022091439`. | Main CI run 39802132773 green; Proofglass `ai-usage-macos` and overall (`job_01M5J2HS60KHRZCYNJRSH5RYJ1`). |
| Chart spacing visuals | **Approved and merged.** The eight replaced renders are in #17 (`3989efa`). | Owner approval, 2026-10-22; decision record on #17; `docs/design/app/references/README.md`. |
| #16 interaction checks | **Waived by the owner** (2026-10-22), except one that passed: hovering from rest onto a day whose readout wraps kept the card's height. Not verified: hover between a wrapping and a short readout, keyboard selection, both History buttons closing the popover and bringing History forward (including when already open) with the provider selected, and the popover reopening. | `docs/design/app/references/README.md#chart-readout-spacing-accepted-2026-10-22`. |
| AC-10 | **Pending the owner's manual observations.** Steps 1–5, 7 and 8 were run at the reinstall; step 8's isolation passed (both accounts collected by the installed service in one collection). Nothing below is recorded as passed. | Step 8 is recorded precisely in `fix-stale-quota-reporting.md`. |

### AC-10: outstanding owner observations
Right after a "Collect now" in the app:
1. `env -u CLAUDE_CONFIG_DIR claude`, then `/usage`: matches the "Claude Code" detail screen. **Pending.**
2. `CLAUDE_CONFIG_DIR="$HOME/.claude-az" command claude`, then `/usage`: matches the "Claude (az)" detail screen. **Pending.**
3. The overview shows both the "Claude Code" and "Claude (az)" cards, each with its own limits. **Pending.**

- **Validation review mode:** auto-proceed (owner, 2026-10-20).
- **Blocked:** no.

### Key Decisions
- Phase-2 analysis accepted, and AC-1 to AC-11 approved, with Q1–Q8 decided: Claude only, built-ins stay under `providers`, type = plugin id, user slugs, removal deferred, Settings add gated, 2.3.0, explicit `CLAUDE_CONFIG_DIR`. Owner, 2026-10-20. Don't ask again.
- The phase-1 golden files are kept. Phase 2 declares its exact intentional differences. The carry-forward runs from `1b60b5f`.
- The owner approved the 24 reviewed renders and, separately, the single-account `popover-settings` (2026-10-20). The other earlier baselines are not regenerated to hide the macOS 27 mismatch.
- No release, no change to the installed collector, no credentials.

### Corrective findings (independent review after the merge)
1. **Refresh folder:** two accounts could refresh the same Claude folder when explicit read-path overrides kept their read paths apart. Fixed by comparing every refreshing account's resolved refresh folder, the default account's `~/.claude` included; Claude still runs with the folder as named.
2. **Folder spelling:** `instance add --config-dir '$VAR'` saved the expression, so launchd couldn't load the config. Fixed: the expanded absolute folder is saved, symlinks kept; unset variables are refused.
3. **App result:** the app's add-account result was read from the shared banner after the refresh. Fixed: an operation-local result, and the form stays busy for its whole submission.
4. **Refreshed cache** (second review): distinct folders whose `.claude.json` files are symlinked together passed validation when one account read a separate file. Fixed by comparing each refreshed `.claude.json` on its own. Reproduced with the fake `claude` only; real Claude Code's symlink write behavior was not tested.
5. **AC-10 procedure** (second review): step 4's parsing, a coherent locked backup, and step 8's false pass on an earlier foreground collection. Each snippet is now tested.

### What's Next
1. The owner's three AC-10 observations above; then record each result here (step 9 of `procedure-reinstall-ac10.md`). Only then is phase 2 operationally accepted.
2. Not yet: a release, phase 3 or phase 4.

### Incident: the live app rebuilt for test renders
While working on #15, a test build was made with `make app-build`, which writes to `macos/build/AI Usage.app`, the owner's running app. The rebuilt app's report code then read rows from the older installed collector, which recorded no fetch times, so the owner saw "last reading —". It cleared when the collector was reinstalled from `ebe2b71`, matching both halves. Since then:
- test and review apps are built separately, with `APP_OUT=<scratch folder> bash macos/scripts/build_app.sh`, and run against an isolated sandbox;
- `make app-build`, `make app-run` and `make app-run-sandbox` are not used for tests, because they write to or relaunch the live app;
- the installed app is replaced only with owner approval, from a separate build, keeping a rollback copy and checking the running executable afterwards (as for `c8eac81`).

### Known limits
- An added account's source descriptions still name `~/.claude/projects`, because they're copied from the plugin's catalogue entry.
- Its daily-stats source stays "waiting" when the account has no stats cache.
- `make launchd-integration-check` (real launchd) was not run.
- On macOS 27.0.1, `make app-snapshot-check` fails on the earlier baselines, which were rendered on 26.6.1. Re-baselining them needs a separate review. The #17 renders were therefore compared against `main` rendered on the same Mac.
- **Intermittent `/usage` timeout:** the default account's `/usage` refresh sometimes exceeds 30 s. The 2.2.0 collector did the same at 06:07 and 07:08 UTC on 2026-10-21, before the reinstall. It is documented, not fixed; this work does not authorize changing timeouts.
- **`last_success_at`:** counts an attempt whose only Claude quota rows are a kept fallback. The provider still shows `partly` and lists the failure. Predates #15; left as is (`fix-stale-quota-reporting.md`).
- **PATH in the LaunchAgent:** `install` saves the invoking shell's PATH into the plist, so a shell shim in front of `claude` (such as a terminal multiplexer's) is used by the service too. Pre-existing; not changed.
