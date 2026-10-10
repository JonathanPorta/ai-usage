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

### Manual checklist (needs a person at the Mac; offscreen renders don't complete these)

- [ ] The real menu-bar popover opened from the status item: 420 pt wide, pinned header and footer, short-screen scrolling.
- [x] Keyboard (verified live 2026-10-09):
  - `↑`/`↓` between the status row and the cards;
  - chart `←`/`→`/`Home`/`End` readouts;
  - `Esc` back;
  - `⌘R` Collect now;
  - `⌘,` Settings.
- [ ] Hover readouts on the 7-day and full charts.
- [ ] An already-open History window updates after Collect now and after a price change.
- [x] `popoverOpened()` fires on every reopen of the `.window` MenuBarExtra. It didn't (`.onAppear` runs once); it now runs when the panel becomes key, verified live 2026-10-09.
- [ ] Reduce Motion: no slide transitions, and a static checking symbol.
- [x] The inline Stop confirmation in Monitoring stops the collector, and `Esc` cancels it. A system dialog failed here live on 2026-10-09: it dismissed the panel without acting.
- [ ] The interval picker and provider switches settle on the saved value. They show the stored value again until the post-save refresh lands (about 2–3 s).
- [ ] Large text sizes: quota cells and the footer stay readable.
- [ ] Notifications appear after permission is granted, once per limit period.
- [ ] Open at login registers (it may require approval in System Settings).
- [ ] Stop and Start on the real collector, including staying stopped across a logout and login. Stop and Start from the UI were verified live on 2026-10-09; the logout/login part is not yet verified. The launchd behavior itself is verified on a disposable agent; this check confirms the UI on the real one.
- [ ] Leave the popover cached overnight. The next morning it shows no "Today" totals from yesterday, and re-reads once.
- [x] Pause and Resume (verified live 2026-10-09). This needs the installed collector at 2.2.0 or newer (`python3 ai_usage_service.py install`).
- [ ] Accept native visual baselines into `references/`. This retires the waiver.

## Related archives not imported

| Archive | SHA-256 | Status |
| --- | --- | --- |
| `ai-usage-design-0.1.0.zip` | `84b8a42e8f1bc6c716782c55cc55b19a3955651af044d8098262203812af3a60` | Superseded by 0.1.1 (documentation-only corrections) |
| `ai-usage-design-checkpoint-0.1.3.zip` | `316280d391d05e6739d3fd81f13aeb641167b90c721ecb2418683faee9a6f4be` | Accepted design baseline; its prototype is included unchanged in 0.1.1 |
| `ai-usage-design-checkpoint-0.1.2.zip` | `2d0fc82ac7334039cb7081e3cbe4b86a01a4e904af83d66e7e3af94b01965dd9` | Packaging-only checkpoint |
| `ai-usage-menu-bar-design-checkpoint-0.1.0.zip`, `-0.1.1.zip` | not recorded | Earlier name for the same series |
