## Session State: provider-instances
Last updated: 2026-10-22

### Current Position
| Part | State | Evidence |
| --- | --- | --- |
| Implementation | **Merged.** PR #11, squash-merged at head `dc5371d` as `315c43e`. | Main CI run 39540177768 green; Proofglass job `job_01M5E8XYMRH8F9403R60S8209J` ("Passed on a disposable macOS VM"). |
| Visuals | **Approved and merged.** The 24 reviewed renders are in #12 (`4542429`), and the single-account `popover-settings`, approved separately, is in #14 (`c044725`). | Decision records on #12 and #14. |
| Corrective work | **Merged.** #13 (`1c89e53`): refresh collisions, folder spelling, the add-account result, and the procedure corrections. | Decision record on #13. |
| Reinstall | **Done** (owner approved, 2026-10-21). 2.3.0 is installed and `claude-az` was added. Backup: `~/.ai-usage/pre-2.3.0-20261021011658`. | Steps 1–5, 7 and 8 per `procedure-reinstall-ac10.md`. |
| Stale-quota correction | **Open PR** (`jp/b/stale-quota-reporting`). A kept Claude reading after a failed refresh was reported as current. | `fix-stale-quota-reporting.md`. |
| AC-10 | **Pending.** Needs the correction approved and installed, the owner's `/usage` comparisons against a report from about the same time, and the overview check. | Step 8 is recorded precisely in `fix-stale-quota-reporting.md`. |

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
1. The stale-quota correction PR: review gates, then the owner's merge decision.
2. The owner's approval to install it. Keep the backup and config until then.
3. The owner's two `/usage` comparisons and the overview check, then record AC-10.
4. Not yet: a release, or phase 3.

### Known limits
- An added account's source descriptions still name `~/.claude/projects`, because they're copied from the plugin's catalogue entry.
- Its daily-stats source stays "waiting" when the account has no stats cache.
- `make launchd-integration-check` (real launchd) was not run.
- On macOS 27.0.1, `make app-snapshot-check` fails on the earlier baselines, which were rendered on 26.6.1. Re-baselining them needs a separate review.
- **Intermittent `/usage` timeout:** the default account's `/usage` refresh sometimes exceeds 30 s. The 2.2.0 collector did the same at 06:07 and 07:08 UTC on 2026-10-21, before the reinstall. It is documented, not fixed; this work does not authorize changing timeouts.
