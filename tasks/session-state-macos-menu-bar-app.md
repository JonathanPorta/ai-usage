## Session State: macos-menu-bar-app
Last updated: 2026-10-05T17:45:00Z

### Current Position
- **Current Phase:** Phase 3, milestone 2: isolation fix follow-up, latency, then Monitoring, Settings, service and pause, progress, notifications, login item.
- **Validation Review Mode:** auto-proceed. The owner approved the design import and milestone 1 scope.
- **Working on:** milestone 1 is done; next are tasks 5.x.
- **Blocked:** No.

### Key Decisions
- Native SwiftUI; Tailwind profile waived; Collect now uses the existing `once`. Confirmed by the human (prompt, 2026-10-05).
- Design 0.1.1 accepted on top of checkpoint 0.1.3. Visual verification is outstanding. Confirmed by the human.
- Milestone 1 directionally accepted. Confirmed by the human (2026-10-05, second prompt).
- Stop collector disables and boots out the LaunchAgent, so it stays stopped across login. Installation, config and data are kept. Confirmed by the human.
- Start collector re-enables and starts the LaunchAgent. Confirmed by the human.
- Pause persists a boolean in config.json, named consistently with the schema. The daemon keeps running, an in-flight check may finish, and later scheduled checks are skipped. Collect now and event-driven collection stay independent of it. Confirmed by the human.
- Resume clears the pause, and the next check comes one interval later. Collect now remains the way to check immediately. Confirmed by the human.
- Quitting the app affects neither the collector nor its schedule. The app's open-at-login setting is separate. Confirmed by the human.
- Approved: a narrowly scoped test-isolation fix. An explicit launcher that is missing or invalid must fail closed. Never reinstall or restart the installed collector to validate it. Confirmed by the human.
- Authorized: commit, push non-protected feature branches, and open or update draft PRs. Not authorized: merging, or replacing the installed collector. Confirmed by the human.

### Codebase Understanding
- PR #4 (`jp/b/isolate-launchctl-in-tests`) is the base of `jp/f/macos-menu-bar-app` (design import first, then the app).
- `ai_usage_service.py` 2.2.0 adds `poll_paused`, `service-status`, `start`, `stop`, `configure` and `once --progress`. `schedule_decision` is pure.
- The report reads the installed collector's VERSION from the LaunchAgent plist's script (as text) to gate Pause and progress. The owner's install is 2.1.0.
- AppStore: `snapshot` is raw, and `report` is `snapshot.evaluated(at: now)` merged with the live service status. All refreshes go through one worker. Rescans happen only when the fingerprint changes.
- `AIUsage --snapshot DIR [--live]` and `AIUsage --measure` give permission-free evidence.

### What's Next
1. Push the app branch and open a draft PR on top of #4. Check CI on Linux and macOS.
2. Human: manual checklist in `docs/design/app/design.md`.
3. Before merge: delete this file (rule 06).
