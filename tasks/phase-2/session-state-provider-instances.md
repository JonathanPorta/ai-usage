## Session State: provider-instances
Last updated: 2026-10-20

### Current Position
| Part | State | Evidence |
| --- | --- | --- |
| Implementation | **Merged.** PR #11, squash-merged at head `dc5371d` as `315c43e`. | Main CI run 39540177768 green; Proofglass job `job_01M5E8XYMRH8F9403R60S8209J` ("Passed on a disposable macOS VM"). |
| Visuals | **Approved and merged.** The 24 reviewed renders are in #12 (`4542429`), and the single-account `popover-settings`, approved separately, is in #14 (`c044725`). | Decision records on #12 and #14. |
| Corrective work | **Open:** #13 (`jp/b/fix-account-isolation-findings`) fixes the post-merge review findings below, plus the second review's cache-alias gap and procedure corrections. | Ready for review; merging it is the owner's checkpoint. |
| AC-10 | **Pending.** Needs #13 merged and an owner-approved reinstall. | Procedure: `procedure-reinstall-ac10.md`, prepared and not run. |

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
1. #13: reviewer approval, then the owner's merge decision.
2. Green main CI on the merge commit.
3. The owner's reinstall approval, then AC-10 per `procedure-reinstall-ac10.md`.

### Known limits
- An added account's source descriptions still name `~/.claude/projects`, because they're copied from the plugin's catalogue entry.
- Its daily-stats source stays "waiting" when the account has no stats cache.
- `make launchd-integration-check` (real launchd) was not run.
- On macOS 27.0.1, `make app-snapshot-check` fails on the earlier baselines, which were rendered on 26.6.1. Re-baselining them needs a separate review.
