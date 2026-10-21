## Session State: provider-instances
Last updated: 2026-10-20

### Current Position
- **Current phase:** implementation (tasks 1.0–8.0).
- **Branch:** `jp/f/provider-instances`, cut from main `1b60b5f` (#10's squash commit; main CI run 39529333928 green; Proofglass job `job_01M5E28PRY63TZXWP7V2QX06HS`).
- **Validation review mode:** auto-proceed (owner, 2026-10-20).
- **Blocked:** no.

### Key Decisions
- Phase-2 analysis accepted, and AC-1 to AC-11 approved, with Q1–Q8 decided: Claude only, built-ins stay under `providers`, type = plugin id, user slugs, removal deferred, Settings add gated, 2.3.0, explicit `CLAUDE_CONFIG_DIR`. Owner, 2026-10-20. Don't ask again.
- The phase-1 golden files are kept. Phase 2 declares its exact intentional differences. The carry-forward runs from `1b60b5f`.
- Visual acceptance and the PR merge are the next checkpoints. AC-10 is pending until the owner reinstalls.
- No release, no change to the installed collector, no credentials.

### What's Next
1. Tasks 1.0 to 8.0 in order, each test first.
