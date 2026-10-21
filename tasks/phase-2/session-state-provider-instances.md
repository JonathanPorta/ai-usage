## Session State: provider-instances
Last updated: 2026-10-20

### Current Position
- **Current phase:** merged. PR #11 was squash-merged at head `dc5371d` as `315c43e`. Main CI run 39540177768 is green, and Proofglass job `job_01M5E8XYMRH8F9403R60S8209J` reports "Passed on a disposable macOS VM". The owner accepted the 24 renders on 2026-10-20; they're recorded in `docs/design/app/references/`. AC-10 is pending until the owner reinstalls.
- **Branch:** `jp/f/provider-instances`, cut from main `1b60b5f` (#10's squash commit; main CI run 39529333928 green; Proofglass job `job_01M5E28PRY63TZXWP7V2QX06HS`).
- **Validation review mode:** auto-proceed (owner, 2026-10-20).
- **Blocked:** no.

### Key Decisions
- Phase-2 analysis accepted, and AC-1 to AC-11 approved, with Q1–Q8 decided: Claude only, built-ins stay under `providers`, type = plugin id, user slugs, removal deferred, Settings add gated, 2.3.0, explicit `CLAUDE_CONFIG_DIR`. Owner, 2026-10-20. Don't ask again.
- The phase-1 golden files are kept. Phase 2 declares its exact intentional differences. The carry-forward runs from `1b60b5f`.
- Visual acceptance was given on 2026-10-20. AC-10 is pending until the owner reinstalls.
- No release, no change to the installed collector, no credentials.

### What's Next
1. Owner: an approved reinstall, then AC-10 on the owner's Mac.

### Known limits
- An added account's source descriptions still name `~/.claude/projects`, because they're copied from the plugin's catalogue entry.
- Its daily-stats source stays "waiting" when the account has no stats cache.
- `make launchd-integration-check` (real launchd) was not run.
- On macOS 27.0.1, `make app-snapshot-check` fails on the earlier 50 baselines, which were rendered on 26.6.1. They need re-baselining in a separate review. The single-account `popover-settings` now shows "Add Claude account…" and was not part of this acceptance.
