# ai-usage — native macOS menu-bar app: implementation guide

**Project:** ai-usage - Design · **Open Design namespace:** `release-stable` (recorded separately from project identity)
**Design baseline:** accepted checkpoint `ai-usage-design-checkpoint-0.1.3.zip`. The prototype is unchanged from it. This handoff (0.1.1) corrects documentation only; it follows handoff `ai-usage-design-0.1.0.zip`.
**Acceptance vs verification:** the design is accepted. Visual and interactive verification is still outstanding (see §11). These are separate facts.
**Target:** SwiftUI app over the existing Python collector (`ai_usage_service.py`, installed as the LaunchAgent `codes.porta.ai-usage`).
**Sample data:** all values in the prototype are illustrative (`app/fixtures.js`, fixed clock Tue 29 Sep 2026, 10:40).

This guide uses three kinds of statement. Keep them apart when planning work:

- **Exists** — the repository does this today.
- **Proposed** — the design needs it, but it isn't built yet.
- **Decision** — a provisional choice for the implementing team to confirm. It is not a backend capability.

---

## 1. Files in this handoff

| Path | Role |
| --- | --- |
| `index.html` | Review entry point: scenario picker, appearance, "Collect now" outcome, links to every view. |
| `popover.html` | **Primary design.** Interactive popover: overview, provider detail, Monitoring, Settings. |
| `history-window.html` | Secondary, resizable History window. |
| `provider-details.html`, `state-gallery.html` | Static boards of every provider detail and every scenario, in light and dark. |
| `menu-bar-icons.html` | Menu-bar glyph states. |
| `app/fixtures.js` | Sample measurements, scenarios, the derivation (`finalize`) and the collection merge (`applyCollectResult`). **This is the reference for the data model.** |
| `app/components.js` | Pure render functions, chart components, the shared-state channel, and the popover controller. |
| `app/styles.css` | Layout primitives, then design tokens (light/dark pairs), then components. |
| `app/icons.js` | One outline icon family and the menu-bar glyph. The proposed SF Symbols mapping is in §12. |
| `design-notes.md` | Behavior, data semantics, scenario list, verification record, open questions. |
| `ai-usage-popover-reference.html` | The originally approved concept (loads icons from a CDN). |

## 2. Previewing the prototype locally

The prototype is plain HTML, CSS and JS with no build step. Serve the folder over HTTP:

```bash
cd <unzipped handoff>
python3 -m http.server 8000
# open http://localhost:8000/index.html
```

- **Why serve over HTTP:** all pages then share one origin, which the History sync relies on (`localStorage`, `storage` events and `BroadcastChannel`). Open "Open history" from the popover in the same browser.
- **Opening files directly through `file://`:** cross-window sync is **not verified**. Some browsers give each `file://` page its own origin or restrict storage events. History then falls back to reading the state from the window that opened it (`window.opener`), and it won't receive live updates.
- **Nothing has been rendered.** No part of the prototype has been rendered or interacted with by the design agent. See §11.

## 3. Views and navigation

```
Menu-bar item (glyph reflects overall status)
└─ Popover (MenuBarExtra, .window style, 420 pt wide, height ≤ min(780 pt, screen − 96 pt))
   ├─ Overview      header · status row · ≤1 notice · provider cards (scroll) · footer [Collect now · Open history · Settings]
   ├─ Provider detail   Back · heading + status · primary chart · quota windows · today · models · ▸Cost & accounting · ▸Sources & diagnostics · Open in History
   ├─ Monitoring    Service (start/stop) · Schedule (pause/resume) · Collection (attempts, Collect now, failures) · Provider sources
   └─ Settings      Collector: interval, providers + prices · Notifications · This app: open at login
History (separate Window scene, resizable, min ≈ 640×460)
   provider list · Tokens | Quota · window picker · range · summaries · chart · table · accounting
```

**Interaction defaults (keep these unless the team decides otherwise):**

- **Opening the popover:** the cached state renders immediately and never waits for a check.
- **Drilling in:** detail views replace the overview inside the popover. **Back** (or `Esc`) returns to the overview, restores its scroll position, and puts focus back on the card that opened the detail.
- **Pinned regions:** the header, status row and footer stay fixed; only the provider list (or detail body) scrolls.
- **Keyboard:**
  - `↑`/`↓` move between the status row and the cards.
  - `⌘R` runs Collect now.
  - `⌘,` opens Settings.
  - `Esc` goes back, then closes the popover.
  - A focused chart takes `←`/`→`/`Home`/`End`, and a live readout gives the exact date and value.
- **Closing:** closing the popover or quitting the menu app **never** stops the collector.
- **Notices:** the healthy status is a single quiet line. At most one prominent notice is shown, for conditions someone can act on; the rest collapse into "+N more in Monitoring".
- **History entry points:** it opens from the overview footer or from a provider detail, preselecting that provider. It remembers its provider, metric, window and range across updates.
- **Defaults, as implemented in the prototype:**
  - Provider detail opens on Tokens, 7 days. Its quota view opens on each window's first range; the 5-hour window opens at 24 hours.
  - History opens on Tokens, 30 days. Its quota view opens at 7 days.
  - The popover is the only place settings change.

## 4. One shared observable state

The popover and History must render from the **same** state object. They must never rebuild from separate sources.

- **SwiftUI:** a single `@Observable` app store owned by the `App`, injected into both the `MenuBarExtra` content and the History `Window` (`.environment(store)`). History is a read-only view of it. Settings and collection write to it.
- **The store holds the prototype's "Snapshot":** `service`, `schedule`, `collection` (`lastAttemptAt`, `lastAttemptResult`, `lastSuccessAt`, `nextScheduledAt`, `failures[]`, `summary`) and `providers[]`. Derived values are recomputed whenever measurements change (see `finalize()` in `fixtures.js`).
- **Prototype only:** the prototype stands in for this with `AIU.shared` in `components.js`. After every change the popover publishes the whole snapshot to `localStorage['aiu:active-state:v1']`, a `BroadcastChannel('aiu-active-state')`, and `window.__AIU_ACTIVE__`. History applies only newer revisions. None of this mechanism is needed natively.

## 5. Health and freshness: keep these independent

| Concept | Where | Meaning |
| --- | --- | --- |
| Service health | `service.state` | Is the LaunchAgent loaded and running (`running` / `stopped`)? |
| Schedule | `schedule.state`, `intervalMinutes` | Are periodic checks active or paused? |
| Collection attempt | `collection.lastAttemptAt`, `lastAttemptResult` | Did the latest check succeed, partly succeed, or fail? |
| Source result | `provider.sources[]` | Per-source status: `ok`, `failed`, `stale`, `auth`, `off`, `waiting`, plus `lastReadAt` (last good read) and `error`. |
| Usage freshness | `provider.usage.status`, `measuredAt`, `collectedAt`, `staleCause` | Measurement time (from the provider) is separate from collection time (when we read it). |
| Quota-window freshness | `quota.windows[].status`, `measuredAt`, `staleCause`, `resetPassed` | **Evaluated per window.** `quota.status` is only a summary: `current`, `stale` or `mixed`. |

**Stale causes:**
- `auth` — sign-in required;
- `failed` — the latest attempt for this source failed;
- `not_collected` — no successful check within the staleness limit;
- `source_old` — the provider's own reading is old, or the window was omitted;
- `reset_passed` — the window's reset deadline has elapsed since the reading was measured: `measuredAt < resetsAt <= now`. A reset still in the future never makes a reading stale.

**Rules:**
- A recent successful check does **not** make every measurement current.
- Limit warnings come only from **current** windows. A current, exhausted weekly window is always flagged, even if the 5-hour window is stale.
- Normal idle time between checks is healthy.
- Antigravity readings arrive only while it runs, so time since its last session is not stale on its own.

**Decision — staleness thresholds (provisional):**
- A reading is stale after 2× the check interval without a successful read.
- Grok billing gets 3 h.
- A quota window is stale once its reset time passes after the reading.

These values are the prototype's choices, not backend facts.

## 6. Collect now and cache preservation

The merge rules from `applyCollectResult()`:

1. **Only enabled, set-up providers are attempted.** Disabled providers keep their cached data and collection times, and are not marked as collected. They go stale normally.
2. **A successful source** replaces its measurements and sets its collection time.
3. **A failed source** keeps its previous measurements *and* measurement times. It records the failed attempt: a failure entry, the source `error`, and an unchanged `lastReadAt`.
4. **Usage and quota are separate sources.** A quota-only failure still updates usage.
5. **Result counts cover attempted providers only** (fully updated, partly updated, failed) and list skipped providers by name.
6. **A collector-level failure** (for example, results couldn't be saved) changes no measurements.
7. **Collect now while the service is stopped** runs a one-time check and doesn't start the service. *Proposed:* this relies on `collector.py once` being safe alongside the service's lock.

**Progress shown to the user:** "Checking providers… Reading Codex · 1 of 4", with a per-card *Checking* tag. The result appears as *Check complete*, *Check partly complete* or *Check failed*.

## 7. Accounting: never mix these

| Quantity | Source | Presentation |
| --- | --- | --- |
| Input and output tokens | Provider usage (`delta`, `event_total`, `period_total`) | Each provider's own accounting. Never summed or compared across providers. |
| Cache reads and writes | Where reported | Shown separately and never added to input + output. "Not reported" when absent. |
| Quota percentage | Per window, `used_percent` / `remaining_percent` snapshots | Always labelled *used* or *remaining* plus the window name. Never converted to tokens or dollars. |
| Reported usage cost | Grok billing (cents) | Shown with its billing period and as-of time. |
| Subscription price | User-entered `monthly_subscription_usd` (`monthly_rate`) | "per month · entered by you". Never compared with quota or cost. |

## 8. Chart semantics

| Case | Mark | Readout |
| --- | --- | --- |
| Incomplete day (today) | Dashed outline over a light fill, labelled "Today" | "Today so far (as of hh:mm)" |
| Missing reading | Dashed box, not a bar | "no data — missing reading, not zero" |
| Measured zero | Solid 2 px baseline tick | "0 tokens — measured, no usage" |
| Before collection began | Shaded "Not collected" band, dotted baseline on the card chart | "not collected (before … data began)" |
| Quota reset | Dashed vertical marker; the line breaks at the reset; only the latest reset is labelled when there is room | "… · after a reset" |
| Gap between quota readings | Line break | "… · after a gap in readings" |
| Stale window | Last value extended to "now" as a dashed amber line | "No reading since …" |

**Axis rules:**
- The axis always spans the selected range.
- Points are labelled with real observation dates, never "Today" for an old reading.
- Chart text is at least 11 pt.
- The card mini chart labels only the first day and "Today"; its exact values are in the readout.

## 9. Repository capabilities: existing vs proposed

| Capability | Status |
| --- | --- |
| LaunchAgent service, hourly polling, config reload, `once`, `status`, `doctor` | **Exists** |
| Long-form `usage.csv` with `record_kind` (`delta`, `event_total`, `period_total`, `snapshot`, `monthly_rate`, `interval_total`) | **Exists** |
| Codex rate limits (5-hour and weekly, % remaining) and account usage | **Exists** |
| Claude Code local stats cache (daily totals by model) | **Exists** |
| Claude "Recent sessions" source (keeps today current when the cache stops) | **Proposed** |
| Antigravity status-line deltas and quota snapshots | **Exists** (the window name "Model quota" is an assumption) |
| Grok session log, billing log and `x.ai/billing` fallback, reported cost | **Exists** |
| Gemini CLI opt-in telemetry | **Exists**, off by default |
| A read-only reporting interface that produces the Snapshot shape (from CSV, `state.json` and `launchctl`) | **Proposed** |
| Per-source attempt results and per-window measurement times exposed to the UI | **Proposed** (the collector logs them, but no API exposes them) |
| Pause/resume of scheduled checks: the collector keeps running and Collect now stays available (no config key today) | **Proposed** |
| One-time Collect now while the service is stopped | **Proposed** (`once` exists; lock behavior needs confirming) |
| Menu-app open at login (`SMAppService.mainApp`), separate from the LaunchAgent | **Proposed** (UI side) |
| Freshness thresholds, reading retention, notification rules | **Decision** |

## 10. Unresolved service behavior (decisions for the team)

1. Does "Stop collector" also stop the LaunchAgent from starting at next login? The current copy says it stays stopped.
2. How should the paused state be persisted (for example, a config key the running collector reads between polls)? Pause must not unload or stop the LaunchAgent. The collector stays running and Collect now stays available. Only **Stop** ends the service.
3. Can a rate-limit response legitimately omit a window? The prototype models this for both windows.
4. How long should quota readings be kept? The sample keeps 7 days (5-hour), 35 days (weekly) and 90 days (monthly).
5. Should a usage limit badge the menu-bar icon? The current design only sends a notification.

## 11. Verification status

**Performed:** static code review of the merge, freshness and sync logic; a walkthrough of both review reproductions against the code (see `design-notes.md`); and packaging validation by the handoff helper (ZIP integrity only).

**Not performed (outstanding):**
- rendering at 420 pt and in a short viewport;
- light and dark appearances;
- chart legibility, keyboard readouts, scrolling and pinned controls;
- both mixed-freshness quota scenarios, on screen;
- Collect now followed by Open history;
- price edits and collection results reaching an already-open History window;
- any `file://` sync behavior.

There are no screenshots in this handoff.

## 12. Proposed SF Symbols mapping

This mapping is **proposed**. Native symbol selection is implementation work: check each candidate against the SF Symbols app for availability on the minimum macOS version and for weight consistency. The prototype uses its own outline icons (`app/icons.js`).

| Prototype icon (`app/icons.js`) | Proposed SF Symbol |
| --- | --- |
| `activity` (app mark, menu-bar glyph) | `waveform.path.ecg` |
| `circle-check` | `checkmark.circle` |
| `circle-alert` | `exclamationmark.circle` |
| `circle-x` | `xmark.circle` |
| `circle-pause` | `pause.circle` |
| `circle-stop` | `stop.circle` |
| `clock` | `clock` |
| `clock-alert` (stale) | `clock.badge.exclamationmark` |
| `gauge` (limit reached / low) | `gauge.with.dots.needle.100percent` |
| `lock` (sign-in needed) | `lock` |
| `plug` (not set up) | `powerplug` |
| `hourglass` (waiting) | `hourglass` |
| `refresh` (Collect now) | `arrow.clockwise` |
| `loader` (checking) | `ProgressView` (a system spinner, not a symbol) |
| `chart` (History) | `chart.bar` |
| `settings` | `gearshape` |
| `power` (collector / quit) | `power` |
| `play`, `pause`, `square` (service and schedule controls) | `play.fill`, `pause.fill`, `stop.fill` |
| `file-text` (collector log) | `doc.text` |
| `info` | `info.circle` |
| `chevron-left`, `chevron-right` | `chevron.left`, `chevron.right` |
| `minus` (not available) | `minus` |

**Menu-bar glyph states:** a template image of `waveform.path.ecg` plus a badge. The badges are `arrow.triangle.2.circlepath` (collecting), `pause.circle.fill` (paused), `stop.circle.fill` (stopped, with the glyph dimmed) and `exclamationmark.circle.fill` (attention). Whether to draw a custom template image or compose symbols is an implementation decision. Candidates per state are also listed in `menu-bar-icons.html`.
