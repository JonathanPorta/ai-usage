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
- **Attempt time:** each CSV transaction is one attempt. Its time is the `collected_at` of its `availability` rows.
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
