# PRD: Native macOS menu-bar app (milestone 1: data to UI)

Status: approved (scope approved by the owner on 2026-10-05: design import plus first implementation milestone)
Owner: Jonathan

## Summary

Intern the accepted Open Design handoff 0.1.1 according to Blessed standards.
Then deliver a native SwiftUI menu-bar app that shows real collector data,
through a new read-only JSON reporting layer over the existing Python collector.

## Codebase Analysis

### Explored
- `ai_usage_service.py` (5,080 lines). It is the single-file collector:
  - config (`DEFAULT_CONFIG` at line 89, `load_config`/`validate_configured_paths` at lines 196–325);
  - CSV schema (`CSV_FIELDS` at line 49);
  - transactions (`commit_collection_transaction` at line 815, `recover_pending_transaction` at line 794);
  - locking (`exclusive_file_lock` at line 393, `state_transaction_lock` at line 404);
  - collectors per provider (`collect_codex` at line 1447, `collect_claude` at line 2290, `collect_antigravity` at line 2704, `collect_gemini_cli` at line 3012, `collect_grok` at line 3643);
  - `collect_snapshot` (line 3864), `print_status` (line 4698), `run_once` (line 4848), `run_daemon` (line 4878) and the CLI (line 4954).
- `test_ai_usage_service.py`: 36 black-box tests built on `IsolatedHome` (line 39). They use fake provider binaries and a temporary `HOME`.
- `.github/workflows/tests.yml`: `make check` across a matrix of ubuntu and macos-14 with Python 3.9 and 3.14.
- `blessed.yml` (daemon, no standards). The `.ai-rules` subtree is v0.13.1.
- Real data in `~/.ai-usage`, inspected read-only: 329K rows, 60 MB, 1,366 transactions.
- The accepted handoff, at `docs/design/app/source/ai-usage-design-0.1.1/`.
- Blessed-cicd at `60dc5ed6`: `standards/design-system/handoff.md`, `standards/spec-pack/{core,profiles}.md`, `references/design-handoff-package.md`, `scripts/{design,spec}-check.sh`, and the templates.

### Relevant Patterns
- Rows are long-form. `record_kind` defines the aggregation (README "CSV semantics").
- Error rows are `category=collection, status=error`, and their `source` names the failing source. Disabled providers emit `availability/enabled=false, status=disabled`. Undetected providers emit `availability/detected=0, status=missing`.
- Many rows are deduplicated through `state_value_changed`. For example, Codex `daily_tokens` is emitted only when its value changes, so "latest per scope" is the correct read.
- Tests run the real script in a subprocess with an isolated `HOME`.

### Constraints Discovered
- `once` and `daemon` both hold an exclusive `flock` on `state.json.lock` for the whole cycle, so Collect now is serialized safely.
- Event rows store the **event time** in `collected_at`. The attempt time is the `collected_at` of the `availability` rows.
- Collector-level cycle failures write no CSV rows. They appear only in `collector.log`.
- The real data differs from the prototype fixtures:
  - Codex limit groups come and go (`codex`, `codex_bengalfox`, `base_model_inference`).
  - Grok's window is **weekly**, and two sources report it.
  - Codex daily usage has no input/output split.
  - Antigravity has no quota rows.
- The CSV is about 60 MB and growing, so the report should run in a few seconds and the app must not block on it.
- A GUI app gets a minimal `PATH`. The interpreter must come from the LaunchAgent plist.
- The prototype code (`resetsAt < now`) conflicts with the 0.1.1 documentation (`measuredAt < resetsAt <= now`). The documentation wins.

### Assumptions (confirmed by the approved prompt)
- Native SwiftUI, a waived Tailwind profile, and Collect now through the existing `once`.
- Monitoring, Settings and service control are later tasks.

## Background

See `docs/IDEA.md` and `docs/design/app/design.md`.

## Goals
1. Intern design handoff 0.1.1 to Blessed structure, with strict spec and design checks passing.
2. Add a read-only reporting layer that emits `ai-usage/report/v1`.
3. Deliver a working native app showing real data: the popover overview, provider detail, History, one shared store, and Collect now.

## Non-Goals
- Monitoring view, Settings, start/stop, pause/resume, notifications and open at login. They are listed as tasks 5.x.
- Any change to how providers are collected.
- Distribution and signing beyond ad-hoc local builds.

## Architecture & Approach

See `docs/DECISIONS.md` (2026-10-05 entries), `docs/CLI.md` and `docs/APP.md`.

## Acceptance Criteria

Design import:
- **AC-1:** The archive in `docs/design/app/source/` is byte-identical to the delivered archive (SHA-256 `a3a0f5cf…fb3e`), and all of the archive's checksums verify after extraction.
- **AC-2:** `make spec-check ARGS=--strict` and `make design-check ARGS=--strict` exit 0. The only waivers are `tailwind-v4-profile` and `implementation.visual_baselines`, each with an owner and a review date.
- **AC-3:** `make design-build-check` fails when `tokens.json` and the generated Swift disagree, and passes otherwise.
- **AC-4:** The design import is its own commit, separate from the implementation.

Reporting layer:
- **AC-5:** `ai_usage_report.py` emits schema `ai-usage/report/v1`. It never executes a provider binary and never writes to disk. Tested with a sentinel provider binary that fails the test if run, and with a before/after file snapshot.
- **AC-6:** Days are aggregated by `record_kind`:
  - `event_total` and `delta` are summed per local day;
  - `period_total` takes the latest row per scope;
  - `snapshot` is never summed.
- **AC-7:** Day states distinguish `measured`, `zero`, `missing`, `not_collected` and today `partial`.
- **AC-8:** Each quota window has its own status and cause. A current window that has reached its limit is flagged even when another window is stale.
- **AC-9:** `reset_passed` is true only when `measured_at < resets_at <= now`.
- **AC-10:** A failed source (from the latest attempt) keeps its previous readings and `last_read_at`, and is `failed`. Usage that succeeded in the same attempt is current, and quota is not.
- **AC-11:** A disabled provider keeps its cached data, its sources are `off`, and it ages normally. The summary lists it as skipped.
- **AC-12:** A collector-level failure in the log after the last transaction sets `last_attempt_result: failed`, without changing measurements.
- **AC-13:** Reported usage cost and the subscription price you entered are separate fields with separate labels.
- **AC-14:** On the real 60 MB CSV, the report finishes in under 10 s on the development Mac (target under 5 s).

App:
- **AC-15:** `make app-build` produces `AI Usage.app` (`LSUIElement`). Launching it shows a menu-bar item. The popover is 420 pt wide, with its height bounded by `min(780, screen − 96)`.
- **AC-16:** Opening the popover renders the store immediately and never starts a collection. No `once` process is spawned when the popover opens (verified in a store test with a spy collector).
- **AC-17:** The overview shows a status row, at most one notice, and a card per provider with every non-retired quota window, today's total and a 7-day chart that uses the five day-state marks.
- **AC-18:** Provider detail opens on Tokens over 7 days, offers Quota with a window picker and ranges, and includes today, models, Cost and accounting, Sources and diagnostics, and Open in History. Back returns to the overview.
- **AC-19:** History is a separate `Window` that opens on Tokens over 30 days (Quota at 7 days). It reads the same store, preselects the provider it was opened from, and keeps its selections across store updates.
- **AC-20:** Collect now runs `<python> <collector.py> once --config <cfg>`, prevents concurrent runs, shows checking progress, then refreshes the store. An already-open History window shows the new data (store test: one store and two observers both see revision N+1).
- **AC-21:** Quitting the app does not stop the collector (`launchctl print` still shows it running).
- **AC-22:** Previews and tests use the committed fixture report generated by Python. The normal app uses live report output.

## Validation approach

| ACs | Proof |
| --- | --- |
| AC-1–3 | Command output: `shasum`, `shasum -c`, the strict checkers, and the drift check with a negative control |
| AC-4 | `git log` |
| AC-5–13 | New `test_ai_usage_report.py`, using a synthetic CSV in a temporary `HOME` and the sentinel binary |
| AC-14 | Timed run against the real CSV (read-only) |
| AC-16, AC-19, AC-20 | Swift tests (`make app-test`) with fake report and collector clients |
| AC-15, 17, 18, 21 | Build plus a manual and accessibility-tree walkthrough of the running app, recorded in `docs/design/app/design.md` |
| AC-22 | A Python test that regenerates the fixture and compares it to the committed copy (`make fixture-check`) |

## Open Questions
- The service questions from handoff §10 (whether Stop also disables at-login start, and how pause is persisted). These are needed for task 5.x, not for this milestone.
