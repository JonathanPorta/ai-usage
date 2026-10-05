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

### Manual checklist (needs a person at the Mac; offscreen renders don't complete these)

- [ ] The real menu-bar popover opened from the status item: 420 pt wide, pinned header and footer, short-screen scrolling.
- [ ] Keyboard:
  - `↑`/`↓` between the status row and the cards;
  - chart `←`/`→`/`Home`/`End` readouts;
  - `Esc` back;
  - `⌘R` Collect now;
  - `⌘,` Settings.
- [ ] Hover readouts on the 7-day and full charts.
- [ ] An already-open History window updates after Collect now and after a price change.
- [ ] `popoverOpened()` fires on every reopen of the `.window` MenuBarExtra.
- [ ] Reduce Motion: no slide transitions, and a static checking symbol.
- [ ] The Stop confirmation dialog presents correctly from the `.window` MenuBarExtra (sheet and dialog presentation there is known to be fragile).
- [ ] The interval picker and provider switches settle on the saved value. They show the stored value again until the post-save refresh lands (about 2–3 s).
- [ ] Large text sizes: quota cells and the footer stay readable.
- [ ] Notifications appear after permission is granted, once per limit period.
- [ ] Open at login registers (it may require approval in System Settings).
- [ ] Stop and Start on the real collector, which also confirms it stays stopped across a logout and login. Optional, at your discretion: these change your installed service.
- [ ] Pause and Resume. This needs the installed collector at 2.2.0 or newer (`python3 ai_usage_service.py install`).
- [ ] Accept native visual baselines into `references/`. This retires the waiver.

## Related archives not imported

| Archive | SHA-256 | Status |
| --- | --- | --- |
| `ai-usage-design-0.1.0.zip` | `84b8a42e8f1bc6c716782c55cc55b19a3955651af044d8098262203812af3a60` | Superseded by 0.1.1 (documentation-only corrections) |
| `ai-usage-design-checkpoint-0.1.3.zip` | `316280d391d05e6739d3fd81f13aeb641167b90c721ecb2418683faee9a6f4be` | Accepted design baseline; its prototype is included unchanged in 0.1.1 |
| `ai-usage-design-checkpoint-0.1.2.zip` | `2d0fc82ac7334039cb7081e3cbe4b86a01a4e904af83d66e7e3af94b01965dd9` | Packaging-only checkpoint |
| `ai-usage-menu-bar-design-checkpoint-0.1.0.zip`, `-0.1.1.zip` | not recorded | Earlier name for the same series |
