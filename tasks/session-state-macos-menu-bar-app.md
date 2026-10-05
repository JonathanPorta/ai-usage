## Session State: macos-menu-bar-app
Last updated: 2026-10-05T11:20:00Z

### Current Position
- **Current Phase:** Phase 3 complete for milestone 1. Phase 4 (human verification) is pending.
- **Validation Review Mode:** auto-proceed. The owner approved the design import and milestone 1 scope.
- **Working on:** milestone 1 is done; next are tasks 5.x.
- **Blocked:** No.

### Key Decisions
- Native SwiftUI; Tailwind profile waived; Collect now uses the existing `once`. Confirmed by the human (prompt, 2026-10-05).
- Design 0.1.1 accepted on top of checkpoint 0.1.3. Visual verification is outstanding. Confirmed by the human.

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
