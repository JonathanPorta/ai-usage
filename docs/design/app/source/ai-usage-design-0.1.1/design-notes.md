# ai-usage menu-bar app — design notes (handoff of accepted checkpoint 0.1.3)

**Project identity:** the Open Design project is now named **ai-usage - Design** (namespace `release-stable`, recorded separately). Checkpoints 0.1.0 and 0.1.1 were published under the earlier name as `ai-usage-menu-bar-design-checkpoint-0.1.0.zip` and `…-0.1.1.zip`. Both archives are kept. Checkpoint 0.1.2 continues the same series under the new file prefix `ai-usage-design`.
**0.1.3 changes:** see the section below. **0.1.2 changes:** packaging only. The design is identical to 0.1.1, apart from these notes and the launcher's intro text, which no longer names a version.

**Status:** final design handoff (0.1.1 is a documentation-only correction of handoff 0.1.0). The accepted baseline is checkpoint `ai-usage-design-checkpoint-0.1.3.zip`, and the design is unchanged from it. For the implementation guide and the local preview method, see `IMPLEMENTATION.md`.
**Outstanding before implementation sign-off:** nothing here has been rendered or interactively tested. That covers the popover at 420 pt and in a short viewport; light and dark appearances; chart labels, readouts, scrolling and pinned controls; both mixed-freshness quota scenarios; Collect now followed by Open history; and price or collection updates reaching an already-open History window. Cross-window sync over `file://` is also unverified. There are no screenshots.
**Sample data:** every value is illustrative and comes from `app/fixtures.js`. The reference clock is fixed at Tue 29 Sep 2026, 10:40.
**Direction:** continues the approved `ai-usage-popover-reference.html` concept: its palette, compact cards, status row, notices and drill-in navigation. The refinements below were accepted as the design baseline in checkpoint 0.1.3. Acceptance is separate from verification: visual and interactive verification is still outstanding (see the verification record).

## What changed in 0.1.3 (correctness pass)

### Collect now merges into the active state, source by source
`applyCollectResult()` no longer rebuilds providers from the baseline fixtures:

- **Only enabled, set-up providers are attempted.** A provider that is turned off keeps its cached days, quota readings and timestamps, and is not marked as collected, so it stays stale.
- **A successful source** replaces its own measurements and sets its collection time to the check time.
- **A failed source** keeps its previous measurements and measurement times, and records the failed attempt: the failure entry, the source's error text, and "Last good read" in the diagnostics.
- **Usage and quota are independent.** When only Codex's quota source fails, usage still updates and quota keeps its old reading. Use "Partial — Codex quota only fails" in the review controls to see this.
- **Counts come from what was attempted.** The result banner reports fully updated, partly updated and failed providers out of those attempted, and names any skipped (disabled) providers.
- **Persistent source conditions survive a successful check:** stale billing, a stopped stats cache, a needed sign-in, missing windows and limits.

Reproductions from the review, as the fixtures now behave:

| Steps | Result |
| --- | --- |
| Partial scenario → Collect now (Partial) | Codex fails again and keeps **563K** tokens and **55%** remaining, measured 09:38, now marked failed. Others update. |
| Paused scenario → Settings: turn Codex off → Collect now (Success) | Codex keeps **22K**, still stale (not collected since 07:06). Result: "3 of 3 providers read… Codex is off and kept its cached values." |

### Freshness is per quota window
Each window has its own `measuredAt`, `status` (`current` / `stale` / `none`), `staleCause` (`auth`, `failed`, `not_collected`, `source_old`, `reset_passed`) and reset state. Meters, value colour, stale captions, chart stale extensions, warnings and notices all read the window, not the provider.

The provider-level `quota.status` is a summary only: `current`, `stale` or `mixed`. A card shows "Quota freshness mixed", but each cell still carries its own state.

Limit warnings come from **current** windows only, so a current, exhausted weekly window is always flagged. New scenarios:

- **Weekly reading stale, 5-hour current:** recent rate-limit responses omitted the weekly window; its last reading was yesterday at 19:38.
- **Weekly limit reached, 5-hour stale:** the weekly window is current at 0% remaining. The 5-hour window was last reported at 08:38.

### History shares the popover's active state
The popover is the only writer. On every change it publishes the whole snapshot through three channels:

1. `localStorage` key `aiu:active-state:v1`, read when History opens. Its `storage` events reach windows that are already open.
2. `BroadcastChannel('aiu-active-state')`, for live updates where supported.
3. `window.__AIU_ACTIVE__`, read through `window.opener` if storage is blocked.

"Open history" opens `history-window.html?linked=1&provider=…`. History applies only payloads with a newer revision. On each update it keeps the selected provider, metric, window, range and focus. The review bar shows "Linked … updated hh:mm:ss". Picking a scenario in History detaches it, and "Link to popover" re-attaches it.

This is prototype scope. The SwiftUI app would share one observable store between the `MenuBarExtra` and the History `Window`.

### Readability
Chart text is at least 11 px:

- **Card mini chart:** the axis is HTML at 11 px and labels only the first day and "Today". The exact date and value appear in the 12 px readout on focus or hover.
- **Quota charts:** only the latest reset is labelled, and only when there is room. Other resets are dashed markers explained by the legend and the readout ("after a reset", "after a gap in readings").
- **SVG chart text:** set to 11.5 viewBox units, which is about 11 px or more at the 420 pt popover width.

## What changed in 0.1.1

- **7-day chart on each card.**
  - A small input + output bar chart sits beside today’s total. Bar marks:

    | Mark | Meaning |
    | --- | --- |
    | Dashed outline | Today, incomplete |
    | Dashed box | Missing reading — not zero |
    | Solid 2 px line | Measured zero |
    | Dotted baseline | Before collection began |
  - The chart is its own keyboard stop. `←`/`→`, `Home` and `End` announce the exact date and value; pointer hover does the same.
  - The rest of the card opens the detail view.
  - Providers with fewer than two measured days show a one-line empty state.
- **Every quota window.**
  - Cards show one compact cell per window, each with its label, % *remaining* or *used*, a meter and its own reset time. Codex shows both 5-hour and Weekly.
  - Flags and notices check every window, not only the first. So “Weekly limit reached” appears even when the 5-hour window has room (*Weekly limit reached* scenario).
  - Three different conditions stay distinct:
    - Usage limits: amber, gauge symbol.
    - Stale readings: amber, clock symbol, with the reading’s age.
    - Collector or source failures: red, ✕ or lock symbol, with an action.
- **Provider detail puts the chart first.**
  - The chart comes right after the heading and status: Tokens for 7 or 30 days, or Quota with a window picker.
  - Each window has its own ranges: 5-hour gets 24 h and 7 d; Weekly gets 7 d and 5 weeks; Monthly credits get 30 d and 90 d.
  - Resets are dashed vertical lines, and the line breaks at each reset.
  - A stale reading is extended to “now” as a dashed amber line labelled with the reading’s time.
  - Quota windows, today’s split and models follow. Cost and accounting, and sources and diagnostics, are collapsible sections; the sources section opens automatically when a source has a problem.
- **History window.**
  - The Tokens or Quota choice drives the whole view: heading, explanatory text, summary tiles, chart, legend, table and chart description.
  - The axis always spans the selected range. Days before collection began are shaded “Not collected” and called out in text (for example, Antigravity’s 90-day view shows 34 days).
  - Axis ends are labelled “now” with the real date. Readings are labelled with their actual measurement time, so an old last reading is never called “Today”.
  - Quota has a window picker, reset markers and a readings table that marks “After a reset”, “Latest reading · stale” and “Limit reached”.
  - The subscription price (monthly rate, entered by the user) and the reported usage cost (billing period and as-of time) are labelled separately.
- **The popover is height-bounded** to `min(780 px, screen height − 96 px)`. The header, status row and notice stay pinned at the top and the footer actions at the bottom; the provider list scrolls between them. Back restores the overview’s scroll position and returns focus to the card that opened the detail.
- **Quieter healthy status.** When everything is fine, the status is a one-line “✓ Monitoring · Checked 2 min ago · next in 58 min”. The larger status treatment and notices are reserved for conditions someone can act on.

## Consistent sample data (derivation model)

Each provider has one set of measurements, and everything the UI shows is calculated from it:

- `days[]`: one record per calendar day, with `input`, `output`, `cacheRead` and `cacheWrite`, and a state of `measured` or `missing`. The last record is today, marked `partial`.
- `quota.windows[].readings[]`: timestamped percentages, each carrying the reset time known at that reading, plus the window’s `resets[]`.

`finalize()` then calculates:
- today’s totals and the card’s total (the same record the chart ends on);
- the model split (it sums exactly to today’s input + output);
- each window’s current value, measurement time and reset time (the latest reading, so chart endpoints always match the cards);
- the Grok reported cost (carried on the billing reading);
- freshness and stale causes.

Scenarios don’t set display values. They transform the measurements:
- cut everything at the last successful check (collecting, paused, stopped);
- fail a source (partial);
- stop a source (stale billing, stale stats cache);
- remove quota (unavailable);
- rebuild a window with a different end value (weekly limit);
- clear everything (first run).

“Collect now” rebuilds from the same measurements. Persistent source problems remain after it; timing-only problems clear.

This resolves the contradictions found in 0.1.0:
- Codex now shows 38% remaining on the card and in the chart.
- Grok now shows 46% used on the card and in the chart, and the reported cost comes from the same reading ($3.18).
- Codex today (776K) is the same record as the chart’s last bar.

Where scopes or timestamps legitimately differ, the UI says so:
- *Quota measured 31 min ago · usage measured 6 min ago* on the Grok card.
- The *so far, as of 10:38* label on today.
- *Measured* and *collected* times shown separately in detail views.

**Staleness rules used by the fixtures** (a proposal; the repository doesn’t define them):
- A reading is stale when no successful check has run within 2× the check interval.
- A polled quota source is also stale when its reading is older than 2× the interval. Grok billing allows 3 h.
- A quota reading is stale when its window’s reset deadline has elapsed since the reading (`measuredAt < resetsAt <= now`). A reset still in the future doesn't make it stale.
- A source failure or a needed sign-in makes the reading stale.
- Antigravity readings are event-driven, so time since its last session alone is **not** stale.

### Future aggregation (repository `record_kind`)

| record_kind | Aggregation |
| --- | --- |
| `delta`, `event_total` | Sum within the range (Antigravity deltas, Grok and Gemini events). |
| `period_total` | Take the latest row per provider, metric, scope and period, then sum distinct periods (Codex and Claude daily totals). |
| `snapshot` | Never sum. Plot each reading (quota percentages). |
| `monthly_rate` | Take the latest value per month (configured subscription price). |
| `interval_total` | Operational counts for diagnostics only. |

## Provider coverage (from the repository)

| Provider | Quota windows | Usage | Cost |
| --- | --- | --- | --- |
| Codex | 5-hour and weekly, % remaining (rate-limits API scope `limit_id:window`) | Account usage (`period_total`) | Not reported; subscription price entered manually |
| Claude Code | Not available (no supported API) | Local stats cache; “Recent sessions” is a **proposed** source | Not reported |
| Antigravity CLI | Model quota, from status-line events | Token deltas | Not reported |
| Grok Build | Monthly credits, % used | Local session log | Reported usage cost, billing period to date |
| Gemini CLI | Not available | Opt-in telemetry, off by default | Not reported |

## Verification record (0.1.3)

**Not performed.** This environment doesn't allow rendering, previewing, screenshotting or running tests on the generated design. No browser rendering, screenshots or interactive walkthroughs were done, and this checkpoint contains no screenshots. None of the following has been verified visually or interactively:

- chart legibility at the real 420 pt width and in a short viewport;
- the pinned header and footer while content scrolls;
- chart keyboard and pointer readouts;
- metric, range and window controls;
- light and dark appearances;
- live History updates in a real browser, including whether `storage` and `BroadcastChannel` events fire for `file://` pages in your browser.

**Done instead, while writing the code:**

- Reviewed the source for each rule above.
- Designed agreement between views into the code: cards, charts, summaries, tables and History all read the same active snapshot, and every derived value is recalculated by `finalize()` after each merge.
- Walked through the two reproductions against the merge code, with the expected values recorded in the table above.

**Please check in a browser:**

1. Partial scenario → Collect now → Open history: Codex values and times match.
2. Change a price → the open History updates the Accounting panel.
3. Turn a provider off → collect → its History is unchanged.
4. Collect again while History is open.
5. Check readability at 420 pt and in a roughly 700 px-tall window, in both appearances.

## Remaining review questions

1. Should a usage limit badge the menu-bar icon, or only notify? (Current design: notify only.)
2. Are the proposed staleness thresholds right for each provider (see above)?
3. Should the Claude “Recent sessions” source be built, so today stays current when the stats cache stops?
4. Is a 7-day mini chart enough on the card, or should it follow the detail view’s last-used range?
5. Should cards with two quota windows show only the tighter window when space is short (for example, at large system text sizes)?
6. Should “Stop collector” also disable the collector starting at login?
7. How long should quota readings be kept (this sample keeps 7 days for 5-hour windows, 35 for weekly and 90 for monthly)?
8. When one quota window is stale, should the popover offer a targeted re-check of just that provider?
