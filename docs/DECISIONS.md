---
status: approved
owner: Jonathan
updated: 2026-10-05
source-template: tools/blessed/scripts/spec-scaffold.sh (desktop-app)
---

# DECISIONS

Lightweight log. "Approved" means the human approved it. "Engineering" means a
routine choice made within the approved scope; change it freely with a new entry.

## 2026-10-05 — Native SwiftUI for the menu-bar app (approved)
**Choice:**
- SwiftUI with `MenuBarExtra` (`.window` style) and a `Window` scene for History.
- macOS 14 or later, for `@Observable`.
- Swift Package Manager, plus a bundle-assembly script that runs through Make.

**Rejected:**
- Web or Electron with Tailwind, because a menu-bar agent gains nothing from a web runtime.
- An Xcode project, because SwiftPM is reproducible from the CLI and in CI without a `.xcodeproj` to keep in sync.

**Reason:** the handoff targets SwiftUI (`IMPLEMENTATION.md` §4), and the human approved it.
**Revisit if:** the app needs sandboxing or App Store distribution, which would require an Xcode project or `xcodebuild` archive.

## 2026-10-05 — Waive the Tailwind-first styling profile (approved)
**Choice:**
- `stack.styling.profile: other` with canonical DTCG tokens (`macos/design/tokens.json`).
- Generated Swift constants, checked for drift by `make design-build-check`.
- A `tailwind-v4-profile` waiver in `docs/design/app/handoff.yml`: owner Jonathan, review by 2027-04-05.

**Reason:** there is no CSS runtime, so Tailwind would be a cosmetic retrofit, which `design-system.handoff` says not to force.

## 2026-10-05 — Vendor the Blessed checkers (engineering)
**Choice:**
- Copy `design-check.sh`, `spec-check.sh`, `lib-json-schema.sh`, `spec-scaffold.sh` and the handoff schema from blessed-cicd at `60dc5ed6` into `tools/blessed/`.
- Record their checksums in `VENDOR-SHA256SUMS.txt`, following the CommsCore pattern.

**Rejected:** a `BLESSED_CICD_ROOT` checkout. CI would need a second checkout, and the results would float.
**Revisit if:** blessed-cicd publishes a versioned checker package.

## 2026-10-05 — Reporting layer is a separate, read-only Python module emitting JSON (engineering)
**Choice:**
- `ai_usage_report.py` reuses the collector's config loader and path validation.
- It reads `usage.csv`, the `collector.log` tail and `launchctl print`, and writes `ai-usage/report/v1` JSON to stdout.
- It never writes and never executes a provider binary.
- The app bundles copies of `ai_usage_report.py` and `ai_usage_service.py` and runs them with the collector's interpreter.

**Rejected:**
- Parsing the CSV in Swift, which would duplicate the `record_kind` semantics in a second language.
- A `report` subcommand inside the collector, which would grow the installed single file and tie report changes to collector reinstalls.

**Reason:** there is one canonical place for aggregation and freshness, which the existing Python test harness can test.

## 2026-10-05 — Freshness is evaluated by the report; the app refreshes the report, not the data (engineering)
**Choice:**
- The report computes every status at `generated_at`.
- The app shows the cached report instantly, then re-runs the report (read-only, no collection):
  - on launch;
  - when the popover opens and the report is more than 60 s old;
  - every 5 minutes while running;
  - after Collect now.
- Relative times ("2 min ago") are formatted live from absolute timestamps.

**Consequence:** a status can be up to about 5 minutes behind wall-clock time. That is acceptable for hourly polling.

## 2026-10-05 — A passed reset follows the documented rule, not the prototype code (engineering, per accepted docs)
**Choice:** a window has `reset_passed` when `measured_at < resets_at <= now`.
**Rejected:** the prototype's `fixtures.js` `finalize()` uses `resetsAt < now`. Handoff 0.1.1 corrected the documentation but left the prototype unchanged.

## 2026-10-05 — Staleness thresholds (provisional, from the handoff)
**Choice:**
- Usage and polled quota become stale after 2 × the configured poll interval without a successful read, or when the reading itself is older than that.
- Grok billing allows 3 h.
- Antigravity readings are event-driven, so time since its last session alone is not stale.

**Revisit if:** the owner adjusts the thresholds (handoff review question 2).

## 2026-10-05 — Quota window identity and retirement (engineering)
**Choice:**
- A window is identified by `(provider, scope)`.
- The label comes from `window_seconds`: 18000 is "5-hour", 604800 is "Weekly", and 28 to 31 days is "Monthly". Grok uses its `USAGE_PERIOD_TYPE_*` scope, and Antigravity uses the bucket name.
- Codex limit groups other than `codex` keep their `limit_id` as a prefix in the label.
- A window that was missing from the provider's latest successful quota read is stale with cause `source_old` (omitted).
- A window not reported for longer than 2 × its own length (at least 24 h) is retired. It is hidden from cards and detail, kept in History, and listed in the report with `retired: true`.

**Reason:** real Codex data shows limit groups appearing and disappearing (`codex_bengalfox`, `base_model_inference`).

## 2026-10-05 — Codex "remaining" is derived; Codex daily usage is total-only (engineering)
**Choice:**
- Codex reports `used_percent` only. The design presents remaining, so the report emits `basis: remaining` with `derived: true` (100 − used), and the UI says "remaining".
- Codex `daily_tokens` has no input/output split. Its bars are single "total" bars with the caption "input/output split not reported". No split is fabricated.

## 2026-10-05 — Day boundaries and event sums (engineering)
**Choice:**
- Event rows (`delta`, `event_total`) are bucketed by the local calendar date of the event time. In these rows `collected_at` holds the event time.
- `period_total` rows keep the provider's own period date.
- Grok `incomplete` and `ok` session events are separate events, so both are summed, and the count of incomplete events is shown for each day.

## 2026-10-05 — Collection attempts come from CSV transactions and the log tail (engineering)
**Choice:**
- **Attempt time:** each CSV transaction that contains a check-time row is one attempt. Its time is the latest `collected_at` among the transaction's non-event rows.
  - The first approach keyed on `availability` rows. The sandbox end-to-end test showed that a check finding no providers writes only `monthly_rate` rows, so that approach was replaced on 2026-10-05.
  - A check that writes no rows at all is read from its `collection completed rows=0` log line.
- **Collector-level failures** write no transaction. They are read from `collection cycle failed` lines in the last 256 KiB of `collector.log`.
- **Next scheduled check:** the latest daemon `collection completed … next_poll_seconds=N` log line plus N, when the service is running. Otherwise it is null. A `once` run does not move it.

## 2026-10-05 — Collect now runs the installed collector's `once` (engineering, verified)
**Choice:**
- The app reads the LaunchAgent plist's `ProgramArguments` (interpreter and script) and `EnvironmentVariables`.
- It runs `<python> <collector.py> once --config <config>`.
- **Verified:** `run_once` and `run_daemon` both hold an exclusive `flock` on `state.json.lock` for the whole cycle. A concurrent Collect now waits for an in-flight scheduled check and does not interleave with it.
- The app also allows only one Collect now at a time.
- Collect now never starts or stops the LaunchAgent.

**Note:** `once` and the daemon may both write to `collector.log` through separate `RotatingFileHandler`s. At most a rotation boundary could interleave lines. Usage data is protected by the lock.

## 2026-10-05 — Claude "Recent sessions" is met by transcript ingestion (engineering)
**Choice:** the handoff proposed a Claude "Recent sessions" source. The collector already ingests `~/.claude/projects/**/*.jsonl` transcripts (`claude-session-log`, PR #3). That source is the live usage source. The stats cache (`claude-stats-cache`) is shown as a secondary "Daily stats" source with its own freshness.

## 2026-10-05 — Costs (engineering)
**Choice:**
- Reported usage cost is the Grok billing `on_demand_used`, with its billing period and as-of time.
- API-equivalent cost reported by Grok sessions (`api_cost`) is labelled separately.
- Claude stats-cache `estimated_api_cost` is not shown. It is undocumented and always 0 in observed data.
- The subscription price is the latest CSV `monthly_rate` row for the month, or else the configured `monthly_subscription_usd`. It is labelled "per month · entered by you".

## 2026-10-05 — Session state stays off main (process)
**Choice:** `tasks/session-state-*.md` lives on the feature branch only, and is deleted before the merge to main (`.ai-rules` rule 06).

## 2026-10-05 — Offscreen snapshots for visual verification (engineering)
**Choice:** the app binary has a `--snapshot DIR [--live]` mode. It renders the real views in offscreen `NSHostingView` windows (light and dark, 420 pt, a short viewport) to PNG, then exits.
**Reason:**
- This session had neither screen-recording permission nor a working computer-use daemon, so interactive capture of the menu-bar popover wasn't possible.
- Snapshots are reproducible and permission-free.
- They are *not* a substitute for the outstanding interactive checks: keyboard, focus, hover, and live History updates in a running app.

## 2026-10-05 — Test isolation from the real LaunchAgent (engineering, bug fix)
**Choice:** `find_launchctl()` honors `AI_USAGE_LAUNCHCTL`, and the black-box suite points it at a recording fake.
**Reason:** the black-box `uninstall` tests booted out the developer's installed collector on every `make test` run on a Mac. This was observed during this work, and the service was re-bootstrapped.

## 2026-10-05 — Service control, pause and resume (approved by the owner)
**Choice:**
- **Stop collector:** `launchctl disable gui/<uid>/codes.porta.ai-usage`, then `bootout`. It stays stopped across login. The plist, config and data are kept.
- **Start collector:** `enable`, then `bootstrap` (`kickstart` only if it is loaded but not running).
- **Pause:** a persisted `poll_paused` boolean in config.json, named after the existing `poll_interval_seconds` and `poll_on_start`.
  - The daemon keeps running, and an in-flight check finishes. Later scheduled checks are skipped.
  - `once` (Collect now) and event-driven collection (the Antigravity status line) are unaffected.
- **Resume:** clears `poll_paused`. The next check comes one configured interval after the daemon notices the change.
- **Quitting the app** changes nothing about the collector or its schedule. The app's open-at-login (SMAppService) is a separate preference.

## 2026-10-05 — How pause reaches a running daemon (engineering)
**Choice:**
- An idle daemon re-reads config.json every 15 s (`AI_USAGE_SCHEDULE_TICK_SECONDS` lowers this for tests only).
- The pause/resume decision is a pure function (`schedule_decision`) with unit tests.
- The daemon logs `scheduled checks paused…` and `scheduled checks resumed next_poll_seconds=N`. The report derives the next check time from those lines.

**Reason:** the collector already reloads config between polls. Ticking keeps changes noticed within seconds without signals or IPC.

## 2026-10-05 — Capabilities come from the installed collector (engineering)
**Choice:**
- The report reads `VERSION` from the script the LaunchAgent runs (its plist's `ProgramArguments[1]`), as text, never executing it.
- Pause and `once --progress` are offered only for 2.2.0 or newer. Otherwise the UI says which version is needed and how to reinstall.
- An older collector ignores an unknown `poll_paused` key, so offering Pause there would be a silent no-op.
- Service control and settings work with any installed version: they use `launchctl` and config keys that 2.1.0 already understands.

**Reason:** never present a control the running collector won't honor. The owner's installed collector is 2.1.0 and was deliberately not reinstalled.

## 2026-10-05 — Settings are written by the collector, not the app (engineering)
**Choice:** the app runs its bundled `ai_usage_service.py configure --set KEY=JSON`.
- Writes hold a lock beside config.json, are validated against the full merged config, and use temp file + rename. The file mode and every other key are kept.
- Only `poll_paused`, `poll_interval_seconds` and `providers.<id>.enabled|monthly_subscription_usd` may change.
- A rejected value leaves the file byte-identical.

## 2026-10-05 — Interaction never waits for the CSV scan (engineering)
**Choice:**
- The last good snapshot renders immediately: about 180 ms from the on-disk cache at launch, then from memory.
- One worker runs reports. Overlapping requests share one follow-up run, and older results can't replace newer ones.
- Popover reopen and the 5-minute timer scan only when the CSV, log or config size/mtime changed. A cheap `service-status` probe keeps service state current.
- The report publishes `becomes_stale_at` / `becomes_stale_cause`, and `Report.evaluated(at:)` applies them, so a cached snapshot can't show stale readings as current.
- **Measured on the real 334K-row CSV:**
  - cold refresh 2.47 s;
  - 16 ms longest main-thread stall during a refresh;
  - 0 scans for 10 reopens;
  - 1 run for 5 overlapping requests.
- The report itself was sped up from 6.3 s to 2.35 s, with byte-identical output.
- This supersedes the earlier "re-read when the popover opens and the report is more than 60 s old" policy.

## 2026-10-05 — Notifications (engineering, within the accepted design)
**Choice:**
- Notify when a **current** window reaches its limit, once per window and reset period.
- Notify on check failures or sign-in needed, once per check and only within 2 h.
- Two toggles in Settings, both on by default; nothing is delivered until macOS permission is granted.
- Limits don't badge the menu-bar icon (handoff §10 Q5, current design).
