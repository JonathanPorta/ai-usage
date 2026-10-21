## Session State: provider-instances
Last updated: 2026-10-20

### Current Position
- **Current phase:** implemented. The PR is open and ready for review. Visual acceptance and the merge are the owner's next checkpoints. AC-10 is pending until the owner reinstalls.
- **Branch:** `jp/f/provider-instances`, cut from main `1b60b5f` (#10's squash commit; main CI run 39529333928 green; Proofglass job `job_01M5E28PRY63TZXWP7V2QX06HS`).
- **Validation review mode:** auto-proceed (owner, 2026-10-20).
- **Blocked:** no.

### Key Decisions
- Phase-2 analysis accepted, and AC-1 to AC-11 approved, with Q1–Q8 decided: Claude only, built-ins stay under `providers`, type = plugin id, user slugs, removal deferred, Settings add gated, 2.3.0, explicit `CLAUDE_CONFIG_DIR`. Owner, 2026-10-20. Don't ask again.
- The phase-1 golden files are kept. Phase 2 declares its exact intentional differences. The carry-forward runs from `1b60b5f`.
- Visual acceptance and the PR merge are the next checkpoints. AC-10 is pending until the owner reinstalls.
- No release, no change to the installed collector, no credentials.

### What's Next
1. Owner: visual review of the add-account renders, and the PR merge decision.
2. After the merge: an owner-approved reinstall, then AC-10 on the owner's Mac.

### Known limits
- An added account's source descriptions still name `~/.claude/projects`, because they're copied from the plugin's catalogue entry.
- Its daily-stats source stays "waiting" when the account has no stats cache.
- `make launchd-integration-check` (real launchd) was not run.
- `make app-snapshot-check` mismatches on this Mac, as it did in phase 1.
