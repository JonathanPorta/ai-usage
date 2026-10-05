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
- `ai_usage_report.py`: attempts are transactions with check-time rows. Event rows carry event time in `collected_at`.
- `ai_usage_fixtures.py`: one synthetic scenario feeds the tests, `report-fixture.json` and the sandbox.
- `test_ai_usage_service.py`: `IsolatedHome` must keep `AI_USAGE_LAUNCHCTL` pointing at the fake, or the tests boot out the real collector.
- A release `swift build` once spent about 18 minutes waiting at 3% CPU. Later builds were fast.

### What's Next
1. A human verifies the outstanding items in `docs/design/app/design.md#verification`.
2. Tasks 5.1–5.8 (Monitoring, Settings, service control, pause, progress, notifications, login item, baselines).

### Blockers / Open Questions
- Handoff §10 Q1 and Q2 (Stop vs. login start; pause persistence) gate tasks 5.2 and 5.3.
- Delete this file before merging to main (rule 06).
