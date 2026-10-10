---
status: approved
owner: Jonathan
updated: 2026-10-05
---

# App design handoff index

**Accepted handoff:** 0.1.1. **Accepted design baseline:** checkpoint 0.1.3.
**Source:** `source/ai-usage-design-0.1.1.zip`, SHA-256
`a3a0f5cfff6162456e6147d73daccf936b3d0d73a599e3bcea5227e48899fb3e`.
**Source authority:** Open Design project "ai-usage - Design" (namespace
`release-stable`). The handoff archive is the authority for accepted design
intent. Runtime authority is mapped in [`handoff.yml`](handoff.yml) and
[`DESIGN_SYSTEM.md`](../../../DESIGN_SYSTEM.md#authority-map).
**Acceptance vs verification:** the design is **accepted**. Visual and
interactive verification is **outstanding**. These are separate facts.

## Where to start

1. [`DESIGN.md`](../../../DESIGN.md) gives the product character and language.
2. `source/ai-usage-design-0.1.1/IMPLEMENTATION.md` is the implementation guide:
   - views and navigation (§3);
   - the shared store (§4);
   - freshness (§5);
   - Collect now merge rules (§6);
   - accounting (§7);
   - chart semantics (§8);
   - existing vs proposed capabilities (§9);
   - open service decisions (§10);
   - SF Symbols (§12).
3. `source/ai-usage-design-0.1.1/design-notes.md` covers data semantics, scenarios and the reproductions to check.
4. [`docs/APP.md`](../../APP.md) holds the screen and state inventory and the implementation map.
5. [`docs/CLI.md`](../../CLI.md) defines the reporting contract that feeds the app.

## How the pieces fit

```text
~/.ai-usage/usage.csv, state.json, collector.log, launchctl
        │  (read-only)
        ▼
ai_usage_report.py  ──►  JSON report (ai-usage/report/v1)
        │                         │
        │                         ▼
        │              AppStore (@Observable, one instance)
        │               ├─► MenuBarExtra popover (overview, provider detail)
        │               └─► History Window
        ▼
collector.py once  ◄── Collect now (existing one-time path, flock-serialized)
```

## Proposed capabilities (not built; never shown as if they were)

These come from `IMPLEMENTATION.md` §9:

- Pause and resume of scheduled checks.
- Per-provider collection progress.
- Claude "Recent sessions" as a separate source. The repository now ingests Claude session transcripts (`claude-session-log`), which covers the same need. This is recorded in DECISIONS.
- Notifications.
- Open at login.

## Verification

### Performed by the design agent (from the handoff)

- Static code review of the merge, freshness and sync logic.
- A walkthrough of the two review reproductions.
- ZIP integrity check.

### Performed at import (2026-10-05)

- Archive SHA-256 matched the expected value.
- All 15 entries in `_handoff/CHECKSUMS.sha256` verified (13 payload files plus `README.md` and `MANIFEST.json`).
- The prototype was rendered once in headless Chrome to orient the implementation. This is **not** design verification.

### Outstanding (not yet verified)

- [ ] Popover at 420 pt and in a short (about 700 pt) viewport, with header, status and footer pinned.
- [ ] Light and dark appearances.
- [ ] Chart legibility (11 pt or larger), keyboard readouts, scrolling.
- [ ] Both mixed-freshness quota scenarios on screen.
- [ ] Collect now followed by Open history, with values and times matching.
- [ ] Price edits and collection results reaching an already-open History window. Price edits depend on Settings, which is a later task.
- [ ] Accepted native visual baselines in `references/` (waived in `handoff.yml`).

Native-app verification results are recorded below as they are performed.

### Native verification log

#### 2026-10-05: milestone 1 (agent; offscreen snapshots plus automated tests)

Performed:

- **`make app-snapshot`:** rendered all 24 fixture screens, in light and dark.
  - The popover is 420 pt wide. Its height is bounded at 770 pt on a 1000 pt screen, and at 594 pt in a 700 pt screen (`min(780, screen − 96)`).
  - In the short viewport the header, status, notice and footer stay pinned while the cards scroll.
  - Chart text is 11 pt or larger.
  - All five day-state marks render distinctly.
- **Mixed-freshness scenario on screen:** the Codex weekly window is current and at its limit, with "Limit reached" visible, while the 5-hour window is stale ("Not reported since 08:17").
- **Partial failure:** Grok's billing failed, so its quota shows as a stale "Check failed" with the cached 18% kept, while usage stays current.
- **Live real data** (`--live`, read-only, CSV row count unchanged): every provider renders. A real Codex account-usage timeout is surfaced, with its cached values kept.
- **Collect now plus a shared-store update:** an end-to-end test (`LiveSandboxTests`) runs the real `once` in an isolated sandbox. The store's revision advances, and every observer sees the new attempt.
- **Quitting the app:** the collector LaunchAgent keeps running (`launchctl print` shows the same daemon still `running`).

Defects found and fixed in the process:

- The quota-cell limit label wrapped awkwardly.
- The chart legend wrapped mid-word at 420 pt.
- Attempt detection missed checks that found no providers.

Still outstanding (needs a person at the Mac):

- the real menu-bar popover opened from the status item;
- keyboard focus order (`↑`/`↓`, chart `←`/`→`, `Esc`, `⌘R`);
- hover readouts;
- an already-open History window updating on screen after Collect now (proven by test, not yet observed);
- whether `popoverOpened()` fires on every open of the `.window`-style `MenuBarExtra`, or only on first appearance. The 60 s refresh-on-open policy depends on it; the 5-minute timer masks a miss;
- Reduce Motion;
- large text sizes;
- accepting native visual baselines into `references/`.

#### 2026-10-05: milestone 2 (agent; offscreen snapshots plus automated tests)

- Monitoring and Settings rendered offscreen in light and dark at 420 pt. The switch alignment was fixed after the first render.
- Collect now, Settings and Pause were exercised through the real collector commands in an isolated sandbox (`LiveSandboxTests`).
- Start/stop and pause were exercised against a fake launchctl and an isolated daemon. **The installed collector was not touched.**
- Latency with the real 334K-row CSV (`AIUsage --measure`):
  - cache-first render in 182 ms;
  - cold refresh 2.47 s;
  - longest main-thread stall during a refresh 16 ms;
  - 0 scans for 10 reopens;
  - 1 run for 5 overlapping requests.

#### 2026-10-05: review fixes (agent)

- Real launchd, using a disposable agent and `make launchd-integration-check`:
  - `--no-start`, Stop, Start, reinstall after Stop, and failed-install recovery all pass;
  - the real collector's pid was unchanged.
- Midnight rollover and service-observation order are covered by Swift tests. The rollover labels have not yet been seen on screen.

#### 2026-10-09: live acceptance on the owner's Mac (agent driving the real app through computer use; owner-authorized)

The app was built from `8945a54` and ran against the installed 2.2.0 collector. Screenshots stay local because they contain real usage.

Passed (observed in the running UI):
- Popover opened from the status item: header, status row and footer pinned, cards between them, "Data read … ago" footer.
- Current-day labels: today's column labelled as today, with a dashed "missing" box and "hasn't reported today's usage yet" for a provider without a reading.
- Provider detail: Tokens and Quota views, 7- and 30-day ranges, keyboard readouts (`←`/`→`, including "no data — missing reading, not zero"), `Esc` back with focus returned to the card.
- `⌘R` Collect now:
  - "Checking providers… · Reading Codex · 1 of 5", the Checking tag and the collecting badge;
  - then "Check complete · 4 of 4 providers fully updated", with totals updated;
  - the daemon log shows the one-shot run with 0 errors.
- An already-open History window updated its last check time without reopening and kept its selection.
- Pause: `poll_paused` written, the daemon logged the pause, the banner and menu-bar badge showed it. Resume: the daemon logged `resumed next_poll_seconds=3600`.
- The Stop confirmation copy presents correctly.

Failed, then fixed in this branch (each covered by a check; see the commit):
- Clicking **Stop collector** in the confirmation dialog closed the panel and did nothing (twice; the collector kept its pid and stayed enabled). A dialog takes key focus from the `.window` MenuBarExtra panel. Replaced with an inline confirmation inside the Service panel; `Esc` cancels it first.
- `↓` moved focus to cards below the visible area without scrolling them into view. The focused card now scrolls into view, without animation under Reduce Motion.
- The chart legend overlapped at popover width. It now wraps with a flow layout.
- The quota chart's x axis lacked its "now" label. Edge labels are now anchored inward.
- An early reset (the provider starting a new window before the reported reset time) wasn't marked. The report now detects it when the reset time jumps ahead by more than the elapsed time while usage didn't rise. An idle rolling window, whose reset time moves with the clock, is not counted. Reset times reported seconds apart count as one reset.
- Right after Resume, the schedule showed "Next check time not known yet", because the daemon logs the new schedule on its next tick (up to 15 s later). The app now re-reads once after that tick.

Blocked at the time; most completed in the second session below:
- Stop and Start from the fixed UI: the display went to a full-screen video, which hides the menu bar. The real collector was never stopped.
- Mouse-wheel scrolling and hover readouts (tool limits), footer mouse clicks, Settings and accounting, `⌘,`, appearance, Reduce Motion, large text, notifications, open at login, the overnight rollover, and an upside-down first row in an off-screen History capture.

Open design decision:
- In dark mode, the Claude chart's output series (`accentSoft`) is low-contrast against the panel (about 1.8:1).

Restored afterwards: config.json byte-identical to the original; appearance, Reduce Motion and login items unchanged; the collector running and enabled with its original pid; data and the upgrade backup untouched.

#### 2026-10-09 (evening): live acceptance, second session (agent; owner-authorized; desktop clear)

The app was rebuilt from the branch with the fixes above.

Passed (observed in the running UI, with system-side evidence):
- `↓` focus scrolls the focused card into view (Grok Build, below the fold).
- Stop through the inline confirmation, on the real collector:
  - `Esc` cancelled the confirmation and stayed on Monitoring.
  - **Stop collector** left launchd reporting the agent stopped and `disabled`, and the daemon logged its exit.
  - The UI showed "Collector stopped; it stays stopped at login" and "No scheduled checks", with Pause disabled, and the menu-bar badge changed.
- Start: launchd reported running and `enabled` with a new pid, and the daemon logged its start and its start-up check. The banner read "Collector started".
- The next check time was shown after Pause/Resume (the overview read "next in 52 min").
- `⌘,` opens Settings with the stored values.
- A price entered in Settings was written to config.json (`monthly_subscription_usd`) and shown in History as "Subscription price … entered by you".
- A mouse click on the footer's Open history opened History. The earlier failure was the tool.
- History's daily table renders its first row correctly; the earlier upside-down row was a capture artifact.
- Light appearance, switched live: the popover re-themed with readable cards and charts.

Failed, then fixed (live re-test passed):
- Right after Start, the schedule showed the *previous* daemon's next check. The report now ignores schedule lines from before a "collector daemon started" line (tested and mutation-checked). The app re-reads once after Start's start-up check.
- Reopening the popover didn't re-read changed collector files: a `.window` MenuBarExtra keeps its content alive, so `.onAppear` runs once. The re-read now runs each time the panel becomes key. Live, in both directions: a price set from the CLI while the popover was closed appeared after reopening, and so did clearing it. The re-read lands a few seconds after the panel opens.
- The inline **Stop collector** label wasn't red (`.tint` has no effect on a default macOS button); it now uses the danger color.

Not verified:
- Mouse-wheel scrolling and hover readouts: the automation tool can't scroll the panel or produce hover events.
- Reduce Motion: needs a System Settings change, and the effect (no slide transition) can't be judged from still captures.
- Large text: macOS has no system text size that applies to this app.
- Notifications: would need a real limit or a failed check.
- Open at login: the switch is below the fold and the panel can't be scrolled by the tool.
- The overnight rollover.
- Settings edits reaching an already-open History window: changing the price inside the popover was verified, and so was reopening picking up config changes, but not the two together while History was open.

Restored afterwards:
- config.json byte-identical to the original;
- Dark appearance; Reduce Motion and login items unchanged;
- the collector running and enabled (new pid after the Start test);
- data and the upgrade backup untouched.

#### 2026-10-09 (night): accessibility correction and third session (agent; owner-authorized)

Output-series contrast (failed, fixed at `3d88c7b`):
- `accentSoft` feeds the chart output segment, its legend swatch and the model-share meter. It measured 1.36–1.84:1 against the surfaces it touches.
- It is now `#5586c6` (light) and `#6c94c7` (dark), set in `macos/design/tokens.json` and regenerated by `make design-build`. Contrast (WCAG relative luminance), before → after:
  - light: panel 1.80 → 3.74; selected day (`hover`) 1.58 → 3.27; meter track 1.47 → 3.05; window background 1.69 → 3.50;
  - dark: panel 1.84 → 4.15; selected day 1.54 → 3.47; meter track 1.36 → 3.06; window background 2.17 → 4.89.
- Input against output is now 1.41:1 (light) and 1.38:1 (dark). Stacked segments are split by a 1 pt gap showing the background. The legend's Input and Output swatches are small stacked bars with only their own segment filled, so the series don't rely on color. Exact values stay in the readout and the History table.
- The accepted handoff archive is unchanged; its `--au-blue-soft` remains the historical value.

Notifications (failed, fixed at `3d88c7b`):
- Both switches default to on, but permission was requested only when a switch changed. With the defaults, the app never asked and never delivered anything.
- The app now asks the first time an alert is due.
- Checked with the isolated sandbox app (`make app-run-sandbox`: synthetic data, `AI_USAGE_SERVICE=skip`, separate state directory). Its current "weekly limit reached" window produced the system's "AI Usage Notifications" permission prompt.
- The prompt was left for the owner, so delivery itself is unverified. Permission can't be returned to "not asked" once answered.

Unverified, and why:
- An already-open History window updating after a Settings price change: History showed "Not entered" while open, but the check was stopped before the price was edited, because a video was playing under the window. The pieces are verified separately: Settings writes the price, History shows it, and the store reaches every observer (AC-20 store test).
- Open at login: not reached; it needs the owner's screen.
- Mouse-wheel scrolling and hover readouts: blocked by the tool. It can't address the panel's scroll view, and synthetic pointer moves deliver no hover events.
- Reduce Motion: not toggled. It's a system-wide setting, and its effect (no slide transition, a static checking symbol) can't be judged from still captures. The code reads `accessibilityReduceMotion` in the popover, overview and status row.

Passed:
- Overnight rollover. An app process launched on 5 Oct about 21:47 local was still running at 01:2x on 9 Oct, after three midnights. It showed "9 Oct · Today" and "hasn't reported today's usage yet", with no carried-over "Today" totals. Re-reading once per day is covered by Swift tests.

Unsupported (not a tool limitation):
- Larger text. Every font is a fixed `Font.system(size:)` (body 13 pt, chart text at least 11 pt). macOS offers no system text size that this app follows; Accessibility › Display › Text size applies only to the apps it lists.
- No accepted criterion requires it (AC-15 to AC-22; the handoff only raises it as open question 5). `DESIGN_SYSTEM.md` claimed that body text "follows system text-size settings" and that quota cells wrap under large Dynamic Type; both statements were corrected to match the implementation.
- Display scaling and Zoom still enlarge the whole app.

### Status of the manual checklist

Passed live: popover at 420 pt with pinned header and footer; keyboard (`↑`/`↓`, chart keys, `Esc`, `⌘R`, `⌘,`); reopen re-read; inline Stop/Start on the real collector; Pause/Resume; light and dark; overnight labels.
Passed separately but not together: History updating after a Settings price change.
Fixed, delivery unverified: notifications.
Blocked by the tool: wheel scrolling, hover readouts.
Not verified: Reduce Motion, open at login, staying stopped across logout/login.
Unsupported: larger text.
Pending owner approval: visual baselines (30 fixture renders at `3d88c7b`).

### Remaining manual checks (owner, about five minutes)

1. With History open on Codex, open the popover's Settings, enter a Codex price and press Return. History's Accounting shows it without reopening. Clear the field and press Return to restore it.
2. In Settings › This app, turn on "Open AI Usage at login" (approve it in System Settings › General › Login Items if asked), then turn it off again. Persistence needs a logout and login with it on.
3. Answer the "AI Usage Notifications" prompt. With Allow, a limit that is reached notifies once per reset period.
4. Scroll the popover list with the mouse wheel, and hover a chart bar to see its readout.
5. Optional: turn on Reduce Motion, open a provider detail (no slide) and use Collect now (static symbol), then turn it off.

## Related archives not imported

| Archive | SHA-256 | Status |
| --- | --- | --- |
| `ai-usage-design-0.1.0.zip` | `84b8a42e8f1bc6c716782c55cc55b19a3955651af044d8098262203812af3a60` | Superseded by 0.1.1 (documentation-only corrections) |
| `ai-usage-design-checkpoint-0.1.3.zip` | `316280d391d05e6739d3fd81f13aeb641167b90c721ecb2418683faee9a6f4be` | Accepted design baseline; its prototype is included unchanged in 0.1.1 |
| `ai-usage-design-checkpoint-0.1.2.zip` | `2d0fc82ac7334039cb7081e3cbe4b86a01a4e904af83d66e7e3af94b01965dd9` | Packaging-only checkpoint |
| `ai-usage-menu-bar-design-checkpoint-0.1.0.zip`, `-0.1.1.zip` | not recorded | Earlier name for the same series |
