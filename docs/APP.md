---
status: approved
owner: Jonathan
updated: 2026-10-05
---

# APP — ai-usage macOS menu-bar app

A native SwiftUI menu-bar agent (`LSUIElement`) that presents the collector's
data. It is a view and control surface only: the Python collector remains the
only writer of usage data, and it keeps running when the app quits.

- **Package:** `macos/` (SwiftPM). There are three modules:
  - `AIUsageCore`: report models, the clients that run the report and the collector, and the store.
  - `AIUsageDesign`: tokens and primitives.
  - `AIUsageApp`: scenes and screens.
- **Data:** the `ai-usage/report/v1` JSON produced by `ai_usage_report.py` (see [`CLI.md`](CLI.md)).
- **Design:** [`DESIGN.md`](../DESIGN.md), [`DESIGN_SYSTEM.md`](../DESIGN_SYSTEM.md) and the accepted handoff in [`design/app/`](design/app/).

## Scenes

| Scene | SwiftUI | Notes |
| --- | --- | --- |
| Menu-bar item | `MenuBarExtra(…) { PopoverRoot() }`, `.menuBarExtraStyle(.window)` | A template glyph (`waveform.path.ecg`) with a state badge |
| Popover | `PopoverRoot` | 420 pt wide. Height is `min(780, screen − 96)`. |
| History | `Window("AI Usage History", id: "history")` | Resizable, minimum 640×460. Opened with `openWindow(id:value:)`, optionally preselecting a provider. |

A single `AppStore` (`@Observable`) is created in `AIUsageApp.init` and injected
into both scenes with `.environment(store)`.

## Screen inventory

| Screen | Status | Implementation |
| --- | --- | --- |
| Overview: header, status row, at most one notice, provider cards, footer | Milestone 1 | `OverviewView.swift` |
| Provider detail: chart (Tokens 7/30 d, or Quota with a window picker and ranges), quota windows, today, models, Cost and accounting, Sources and diagnostics, Open in History | Milestone 1 | `ProviderDetailView.swift` |
| History: provider list, Tokens or Quota, window picker, range (7/30/90 d, default 30), summaries, chart, table, accounting | Milestone 1 | `HistoryView.swift` |
| Monitoring: service start/stop, schedule pause/resume, collection attempts and failures, provider sources | **Later task** | — |
| Settings: interval, providers and prices, notifications, open at login | **Later task** | — |

## Interaction and state inventory

| State | Where it comes from | Presentation |
| --- | --- | --- |
| Loading (first launch, no cached report) | `store.phase == .loading` | "Reading collector data…" with a spinner. No numbers. |
| Report unavailable (interpreter or config missing, unsupported schema) | `store.phase == .failed(…)` | A red notice with the exact reason and the command to fix it. Cached data stays visible if any exists. |
| Healthy | Report statuses are all current | One quiet status line: "✓ Monitoring · Checked 2 min ago · next in 58 min" |
| Collecting (Collect now in flight) | `store.collect == .running` | Status row "Checking providers…", a *Checking* tag on enabled cards, and Collect now disabled. Per-provider progress is a proposed capability and is not shown. |
| Collect result | `store.lastCollectOutcome` | "Check complete", "Check partly complete" or "Check failed", with counts and skipped providers, until the next refresh |
| Partial failure | Report `failures[]` and source status `failed` | The card keeps its cached values. A red source notice offers "Details" (opens provider detail with Sources expanded). |
| Stale usage or quota window | Window and usage `status: stale` | Amber clock symbol plus the reading's age ("No reading since 08:38") |
| Mixed quota freshness | `quota.status: mixed` | Card caption "Quota freshness mixed"; each cell carries its own state |
| Limit reached or low | Window `limit` (current windows only) | Amber gauge symbol, "Weekly limit reached" |
| Quota unavailable or unsupported | `quota.status` | "— Quota not available", never 0% |
| Not set up, disabled | `setup` | Muted card with one line of explanation |
| First run (no attempts) | `collection.last_attempt_at == null` | "Waiting for the first check" with no charts |
| Service stopped or not installed | `service.state` | Red status row: "Collector stopped · last check 4 h ago". Cached data stays shown. |
| Cancellation | — | Collect now cannot be cancelled once started. The collector's own timeouts bound it. |
| Confirmation | — | No destructive actions in milestone 1. Stop and uninstall are later tasks, and will be confirmed. |
| Recovery | — | Every failure notice names its action: retry Collect now, open the log, or run a command. |

## Keyboard

| Keys | Action |
| --- | --- |
| `⌘R` | Collect now |
| `Esc` | Back from detail; at the overview it closes the popover (system behavior) |
| `↑`/`↓` | Move between the status row and the cards (focus) |
| `←`/`→`/`Home`/`End` on a focused chart | Move the readout |
| `⌘,` | Settings, a later task. Until then it is unbound. |

## Data refresh policy

1. **On launch:** decode the last report cached in `~/Library/Application Support/AI Usage/last-report.json`, then run the report in the background.
2. **When the popover opens:** render the store at once. If the report is more than 60 s old, refresh it in the background (read-only; it never collects).
3. **While running:** refresh every 5 minutes.
4. **Collect now:** run `once` (serialized by the collector's lock), then refresh. Every view, including an already-open History window, updates from the same store.

## Environment overrides (development and testing)

| Variable | Effect |
| --- | --- |
| `AI_USAGE_CONFIG` | Config path passed to the report and to `once`. Default: the plist's `--config`, or else `~/.ai-usage/config.json`. |
| `AI_USAGE_PYTHON` | Interpreter. Default: the plist's `ProgramArguments[0]`, or else `/usr/bin/python3`. |
| `AI_USAGE_COLLECTOR` | Collector script for Collect now. Default: the plist's `ProgramArguments[1]`. |
| `AI_USAGE_REPORT_SCRIPT` | Reporting module. Default: the bundled `Resources/collector/ai_usage_report.py`, next to the bundled `ai_usage_service.py` it imports. |
| `AI_USAGE_SERVICE` | `skip` passes `--service skip` (sandbox runs). |
| `AI_USAGE_STATE_DIR` | Where the last report is cached. Default: Application Support. |

`make app-run-sandbox` sets all of these to a temporary sandbox, so development
never touches `~/.ai-usage`.

## Offscreen verification

`AIUsage --snapshot DIR [--live]` (also `make app-snapshot` and
`make app-snapshot-live`) hosts the real SwiftUI views in offscreen windows. It
writes PNGs of these screens in light and dark, then exits:

- the overview at 420 pt, and in a 700 pt-tall screen;
- every provider detail, in Tokens and Quota;
- History in Tokens and Quota.

It needs no screen-recording permission. `--live` renders the live report
(read-only). Those images contain personal usage data, so they go under the
git-ignored `.sandbox/`.
