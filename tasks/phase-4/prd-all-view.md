# PRD: The All View (Phase 4)

Status: **approved by the owner, 2026-11-01**, with four corrections and a D4 clarification made
in this revision ([decision record](https://github.com/JonathanPorta/ai-usage/pull/28#issuecomment-6569972670)).
The approval covers the codebase analysis, D1–D7 as recommended, and the acceptance criteria. The
corrections renumber them as AC-1 to AC-12 (see Decisions). No further plan approval is needed
unless a material scope or product decision changes. Merging the implementation, installing the
app and releasing remain separate decisions.

**Closeout (2026-11-02):** implemented and merged in #29 (`3a2473a`). The owner accepted AC-10
([record](https://github.com/JonathanPorta/ai-usage/pull/29#issuecomment-6574017977)), the app was installed from `3a2473a` with the owner's approval, and the
owner passed AC-11 ([record](https://github.com/JonathanPorta/ai-usage/pull/29#issuecomment-6575171653)). Release is not approved.

Scope comes from `tasks/project-plan.md`, phase 4: "stacked bars by canonical/parent type across
instances". Exit: "All view renders; visual acceptance by the owner". Entry: phase 2's exit,
accepted on 2026-10-23. Phase 4 doesn't depend on phase 3 (see the plan's dependency map), so
phase 3's pending AC-13 doesn't block it.

Out of scope:
- any change to the collector (`ai_usage_service.py`, `ai_usage_plugins/`) or its `VERSION`;
- installing anything on the owner's Macs, and any release;
- AC-13 and the phase-3 test-isolation follow-up;
- issues #6–#9 and #26, the Proof Glass "Alexandria" label, timeouts.

## Summary

Add an **All tools** page to the History window. It shows one bar chart per tool, for example
Codex, Claude Code and Grok Build. Where a tool has several accounts, each day's bar stacks them,
so both Claude Code accounts read as one Claude Code bar split into "Claude Code" and
"Claude (az)". The combined figures cover the accounts tracked now. Tools are never added to each
other, and quota and cost are never combined. Each figure states its own completeness:
- a total that is missing an account's reading reads "at least N";
- an account that was never read is named, never drawn or counted as zero;
- with no known reading at all, a figure is unknown, not 0.

The bundled reporter computes the combined days as a new, additive `groups` section of the
report, and the app only draws it. The reporter ships inside the app, so phase 4 needs an app
build but no collector change and no collector reinstall.

## UX brief

- **Primary user:** the owner, with two Claude Code accounts (`claude`, `claude-az`) and several
  other tools on the personal Mac, and a private plugin on the work laptop.
- **Job:** "How much Claude Code did I use this week across both accounts, and how was it split?"
  and "What did I use per tool, all on one page?"
- **Emotional goal:** trust the combined figure. It says when an account is missing a reading,
  turned off, or wasn't collected yet.
- **Key flow:** open History, then All tools. Read each tool's range total and today so far, move
  over a day (pointer or keyboard) to see each account's part, and click an account's name to open
  its own page.
- **Success:** the owner reads the combined Claude Code figure, and each account's part of it,
  for any day in 7, 30 or 90 days, without switching pages. Every incomplete figure says so.
- **Constraints and risks:**
  - DESIGN.md forbids summing tokens across providers and summing quota snapshots.
  - The app's two chart blues mean input and output.
  - Renders must never touch the owner's running app.

## Codebase Analysis

Read at `efc605d` (main, 2026-11-01).

### Explored
- `ai_usage_report.py`:
  - `build_report()` (line 842) returns `providers` in display order: the built-ins, then added
    accounts in config order, then local plugins.
  - Every provider carries `type` (phase 2): a built-in's type is its id, an added account's is
    its plugin, and a local plugin's is its folder name.
  - `build_days()` (line 466) gives each provider exactly `--days` Day records. Each has a state
    (measured, zero, missing, not_collected or partial) and a `total` (input + output when split,
    cache excluded).
  - A disabled provider has `setup: "disabled"` (lines 909–918). It keeps its history; its days
    after its last read become `missing`.
  - Presentation rules select on `type`; data lookups use `id`.
- `ai_usage_service.py:287`: `validate_instances()` accepts only bundled plugins, so only a
  built-in type can have more than one account. A local plugin is always a type of one.
- `docs/CLI.md`: the report contract, the Day states, and "Merge and freshness rules the report
  guarantees". New report fields are additive within `ai-usage/report/v1`.
- `macos/Sources/AIUsageCore/CollectorEnvironment.swift:70`: the app runs its bundled
  `collector/ai_usage_report.py`.
- `macos/Sources/AIUsageApp/HistoryView.swift`:
  - The sidebar (210 pt) lists `report.providers`, each with a 30-day caption
    (`Presentation.historySidebarCaption`).
  - The main column holds the header, Tokens | Quota, the 7/30/90-day range, four
    `SummaryCards`, `DayBarChart(.full)` with its legend, "About these numbers" and the daily
    table.
  - Selection is local state; `Navigation.historyRequest` selects a provider.
- `macos/Sources/AIUsageDesign/Charts.swift`: `DayBarChart` draws one provider's days.
  - A measured day stacks input and output (`chart.input` = accent, `chart.output` = accentSoft)
    with a 1 pt gap.
  - Missing is a dashed box, zero a 2 pt tick, not collected a shaded band, partial a dashed
    outline.
  - The chart is one focusable element with a ←/→ readout.
- `macos/Sources/AIUsageCore/Report.swift`: `Report` decodes `providers`; `Day` (line 168) has
  the five states. `Presentation.sum` (`Presentation.swift:395`) adds the days that have values.
- `macos/design/tokens.json`: the chart component tokens are input, output, today, stale and
  notCollected only.
- `macos/Sources/AIUsageApp/SnapshotRenderer.swift`: the scenes `make app-snapshot` renders,
  including two-account and local-plugin stores. The owner-accepted references are in
  `docs/design/app/references/`, checked by `make app-snapshot-check`.
- `DESIGN.md`: "Different providers' accounting is never mixed". Its anti-patterns include
  summing or comparing tokens across providers and summing quota snapshots. It also has the chart
  semantics table.
- `DESIGN_SYSTEM.md`: aggregation, freshness and merge semantics belong to `ai_usage_report.py`;
  views format what the report states and never contain a hex value.
- `tests/parity/test_parity.py`: the list of intentional changes is closed. A new report key
  needs a PRD change first; this PRD is that change.

### Relevant patterns
- Additive report fields, and an app that decodes older reports.
- Day states with fixed marks, and missing data never drawn as zero.
- A fixture report generated from synthetic CSV (`make fixture`, `make fixture-check`).
- Owner-accepted renders as references.

### Constraints discovered
- **Like with like.** Accounts of one type share one plugin, and so one split, record kind and
  mode: adding them adds the same accounting. Adding across types is the forbidden mix.
- **The chart blues have a meaning.** Reusing input and output colors for accounts would change
  what they mean (design rule 38). Accounts need their own series tokens.
- **The reporter ships in the app.** The change reaches the owner only through an app build and
  install, which is a separate approval. The collector and its version are unchanged.
- **The running app.** `macos/build/AI Usage.app` in the main checkout is the owner's running
  app. Renders and test builds happen in a separate worktree, which has its own `macos/build`.
- **Sidebar renders change.** An All tools row in the sidebar changes every existing `history-*`
  render. The popover, detail and Settings renders don't change.

### Assumptions (confirmed by the owner, 2026-11-01)
- History, not the popover, is the right home (D1).
- Two accounts per tool is the common case; more than four accounts of one tool is rare (D6).

## Decisions (owner, 2026-11-01)

D1–D7 are approved as recommended. The owner's corrections are made in the sections below:
- **C1, completeness per figure:** Figures, rules F1–F4, and AC-2 and AC-5.
- **C2, the parts keep their qualifications:** Report rules 2 and 4, rule F4, and AC-2 and AC-5.
- **C3, missing-account markers carry no amount:** Chart, and AC-7.
- **C4, the overflow rule:** Series and overflow, and AC-8 and AC-10.
- **D4, enabled accounts:** the combined figures say they cover the accounts tracked now (App,
  and AC-6).

| # | Question | Decision |
| --- | --- | --- |
| D1 | Where does it live? | **A.** An "All tools" entry at the top of the History sidebar. The popover stays per account: quota limits are per account, and the popover exists to act on them. |
| D2 | What is combined? | **A.** Daily tokens only. Quota and cost stay on each account's own page. |
| D3 | Which tools appear? | **A.** Every tool with at least one included account. A one-account tool shows plain bars. |
| D4 | What about a disabled account? | **A.** It is left out of the combined figures and named under its tool as off. The figures say they cover the accounts tracked now. |
| D5 | What if one account has no reading for a day? | **A.** The day is incomplete. The known stack is drawn at its true height, a marker that carries no amount names the missing accounts, and the total reads "at least N". |
| D6 | How are accounts told apart? | **A.** Four new series token pairs, with the overflow rule under Series and overflow. **B** (reusing the input and output blues) would change their meaning. |
| D7 | Where is the combination computed? | **A.** The bundled reporter computes the combined days, as `groups`. The app's presentation model computes range and today figures from them, as History already does for one provider (`Presentation.sum`). |

Acceptance criteria renumbered by the corrections: AC-5 (figures) and AC-8 (series and
overflow) are new, isolation joins validation as AC-12, and the others keep their order.

## Goals
- One page shows every tool's daily tokens, with each tool's accounts stacked.
- Every figure states its own completeness, and every account's part stays visible with its own
  qualification.
- Nothing is summed across tools, and quota and cost are never combined.
- Existing report fields, pages and renders outside History are unchanged.

## Non-Goals
- A combined popover card, menu-bar figure or notification.
- Combining quota, cost, subscription prices or models.
- Changing the collector, its version, or any installed file.
- Changing how a provider's own History page qualifies its totals.
- Building or installing the owner's app. That is a separate decision after merge.

## Architecture & Approach

### Report: `groups` (additive to `ai-usage/report/v1`)

```jsonc
"groups": [
  {
    "type": "claude",
    "name": "Claude Code",                 // the built-in's name; a local plugin's own name
    "members": ["claude", "claude-az"],    // the accounts tracked now, in display order
    "excluded": [ { "id": "claude-work", "reason": "disabled" } ],
    "coverage_start": "2026-07-08",        // the earliest member's, or null
    "days": [                              // oldest → today, exactly --days entries
      { "date": "2026-10-04", "state": "incomplete", "total": 776000, "incomplete_events": 0,
        "parts": [
          { "id": "claude", "state": "measured", "total": 776000, "as_of": null, "incomplete_events": 0 },
          { "id": "claude-az", "state": "missing", "total": null, "as_of": null, "incomplete_events": 0 } ] }
    ]
  }
]
```

1. **Groups.** There is one group per type in the report, in the order of each type's first
   provider.
   - The members are that type's providers that are enabled and whose setup isn't `disabled`.
   - The others are listed in `excluded` with reason `disabled`. Their history is left out too.
   - A group can have no members. The app then lists that tool only as not included.
2. **Parts.** A day's `parts` copy each member's own Day record for that date, in member order:
   `id`, `state`, `total`, `as_of` and `incomplete_events`.
3. **Day state**, decided in this order:
   - every part is `not_collected`: `not_collected`;
   - no part has a value, and at least one is `missing`: `missing`;
   - at least one part is `missing`, and another has a value: `incomplete`;
   - at least one part is `partial`: `partial`;
   - otherwise `measured` when the total is above 0, and `zero` when it is 0.

   A part "has a value" when its `total` isn't null. A `not_collected` part (an account added
   later) doesn't make a day incomplete; the breakdown says when that account's collection began.
4. **Total.** When at least one part has a value, a day's `total` adds the parts' values and
   nothing else. When no part has one, `total` is null, never 0. A known 0 counts as a value. The
   day's `incomplete_events` adds the parts' counts. Each part's total already excludes cache
   tokens.
5. **No single time.** A group day has no `as_of`: each part keeps its own, because accounts are
   read at different times.
6. **Nothing else is combined.** No value crosses groups, and groups carry no quota, cost or model
   fields.
7. **Today** is the last day; there is no separate today object.

### Figures (the app's presentation model, `AIUsageCore`)
A figure is **qualified** when it may be low. Rules F1 to F3 apply to each figure on its own.

- **F1. A day** (the chart readout, and a table row):
  - no value: "unknown", never 0. A `missing` day reads "missing reading, not zero"; a
    `not_collected` day reads "not collected".
  - `incomplete`: "at least N", and it names each missing account.
  - a past `partial` day: "at least N", with "day incomplete".
  - today, `partial`: "N so far".
  - any day with `incomplete_events` above 0: "at least N". It names each account whose turns
    reported incomplete usage.
- **F2. The range total** over the selected 7, 30 or 90 days:
  - No day in the range has a value: "—", captioned "No readings in N days", never 0.
  - Otherwise it is the sum of the days that have values. It reads "at least N" when any day in
    the range is `missing` or `incomplete`, is `partial` before today, or has `incomplete_events`
    above 0.
  - Today being `partial` doesn't qualify it. The caption says it includes today so far, as
    History does now.
  - Days before `coverage_start` (`not_collected`) don't qualify it. The breakdown says when each
    account's collection began.
- **F3. Today** uses today's day alone, by F1:
  - no value: "—", "No reading today";
  - incomplete, or with incomplete events: "at least N today";
  - otherwise: "N today so far".

  An earlier incomplete day never changes today's figure.
- **F4. Times.**
  - There is never one combined "as of" time.
  - Where a time matters (a partial day), the readout and the breakdown give each account's own
    time ("Claude Code as of 14:02 · Claude (az) as of 13:40").
  - A part with no time reads "time unknown".
  - Accounts read at the same minute are still listed separately.

### App
- **Sidebar:** an "All tools" row above the providers, with the caption "Accounts stacked by
  tool". It is shown only when the report has `groups`.
- **Page**, in the History layout:
  1. a header ("All tools" and the last check);
  2. the range control (7, 30 or 90 days; 30 by default; kept separately from the provider pages);
  3. one section for each group that has members:
     - a name row: "Claude Code · 2 accounts tracked now";
     - a figures line: the range total (F2) and today (F3);
     - the stacked chart and its legend;
     - the breakdown: one row per member, every account listed by name even inside
       "Other accounts". Each row has:
       - the account's name, which opens its History page;
       - its range total and today, by F2 and F3 over its own days;
       - its status: missing days, past partial days, incomplete events, the date collection
         began, or "Complete";
     - "Not included: Claude (work), tracking off" when it has excluded accounts;
  4. one "Not included" line for tools with no included account;
  5. "About these numbers":
     - each tool's accounts are added together, and tools are never added to each other;
     - the figures cover the accounts tracked now; a turned-off account's history is left out;
     - quota limits are per account.
- **Unchanged:** the popover, Settings and every provider page.

### Chart (`GroupDayChart` in `AIUsageDesign`)
- **Scale:** from known values only. A bar's height is its known total, exactly as one account's
  bar would be.
- **Day marks:**
  - A measured or partial day stacks one segment per series, with 1 pt gaps. Segment heights add
    up to the known total.
  - A zero day is the zero tick, a missing day the existing missing box, and a not-collected day
    the shaded band. Today has the dashed outline around its stack.
- **Missing accounts (C3):**
  - An incomplete day draws its known stack at its true height.
  - A marker in a strip below the baseline (`circle.dashed`, with no fill, height or segment)
    shows that something is missing. It never adds height to the bar.
  - The legend explains the marker, and the readout names the missing accounts.
- **Focus:** one focusable element; ←/→/Home/End move the selection. The readout and the
  accessibility value give the date, the day's figure by F1, and every account's part by name,
  with its state and, for a partial day, its time.

### Series and overflow (C4)
- **Tokens:**
  - New semantic pairs `color.semantic.series1` to `series4` (light and dark), aliased as
    `color.component.chart.account1` to `account4`. The Swift values are generated by
    `make design-build`.
  - Each reaches at least 3:1 against the panel, the card and the selected-day background in both
    appearances, and neighbouring series differ visibly in lightness.
- **Up to four members:** each account is its own series, in member order.
- **Five or more:** the first three are series 1 to 3, and accounts four onward are drawn
  together as one "Other accounts" segment in series 4.
  - Its height is the sum of their known values on that day.
  - The legend reads "Other accounts (N)".
  - The readout, the accessibility value and the breakdown still list each of those accounts by
    name, with its own value, state and navigation.

### Docs
- `docs/CLI.md`: `groups` and its rules.
- `DESIGN.md`:
  - the All tools page;
  - chart semantics rows for an incomplete day and the missing-account marker;
  - the figure qualification rule.

  The anti-patterns stay as they are.
- `DESIGN_SYSTEM.md` and the components manifest: the series tokens and `GroupDayChart`.
- `docs/design/app/design.md`: the verification record.

### Compatibility and parity
- The parity golden files are unchanged. The intentional changes gain exactly `report.groups`.
- The app decodes a report without `groups`, such as one from an older reporter (a test fixture
  with the key removed), and then hides All tools.
- The collector is unchanged: no file under `ai_usage_service.py` or `ai_usage_plugins/`
  changes, and `VERSION` stays `2.4.0`.

## Acceptance Criteria (approved with the corrections, 2026-11-01)

- **AC-1 Groups.** `report.groups` follows Report rules 1, 2, 6 and 7, and is documented in
  `docs/CLI.md`.
  - **Verify:** `tests/test_all_view.py`, built on synthetic CSVs via `ai_usage_fixtures`:
    - two Claude Code accounts, a local plugin, and the other built-ins;
    - group order and member order;
    - every member of a group shares its type;
    - a disabled account in `excluded`, with its history left out, and a group with no members;
    - `--days` 7 and 90.
- **AC-2 Day rules.** Rules 3 to 5 hold for every combination of part states.
  - **Verify:** table-driven tests cover every pair of the five part states for two accounts,
    plus a three-account case.
  - On every group day, `total` is null exactly when no part has a value. Otherwise it equals
    the sum of the parts' values; a day of known zeros totals 0.
  - Each part carries its own `as_of` and `incomplete_events`. The day's `incomplete_events` is
    their sum, and a group day has no `as_of`.
  - Cache tokens are never included.
  - A recorded negative-control run weakens each rule in turn, and a test fails every time.
- **AC-3 Nothing mixed.** Groups and group days carry exactly the documented keys, with no quota,
  cost or model field, and no group adds providers of different types.
  - **Verify:** a key-set test, and the type test from AC-1.
- **AC-4 Compatibility.** The existing report output is unchanged, and the collector is
  untouched.
  - **Verify:**
    - the parity golden files' hashes are unchanged, and the intentional-change list gains
      exactly `report.groups`;
    - a Swift test decodes a report without `groups` and finds All tools hidden;
    - `git diff --stat` shows no change to `ai_usage_service.py` or `ai_usage_plugins/`.
- **AC-5 Figures.** Rules F1 to F4 hold for every figure, each on its own.
  - **Verify:** Swift tests of the presentation model:
    - a range with a fully missing day, a past partial day, an incomplete day or incomplete
      events reads "at least";
    - a range whose only partial day is today doesn't;
    - an earlier incomplete day leaves today's figure unqualified;
    - a range or today with no known reading reads "—", never 0, while known zeros read 0;
    - two accounts with different or unknown times are listed with their own times, and no
      combined time appears;
    - each breakdown row follows F2 and F3 over its own account's days.
  - A recorded negative-control run weakens each rule in turn, and a test fails every time.
- **AC-6 The page.** The page meets the App section:
  - the sidebar row and the page order, and a section only for groups with members;
  - "accounts tracked now" in each name row and in "About these numbers";
  - the breakdown, with every account's figures, status and navigation;
  - the "Not included" lines, and the range control.
  - **Verify:** Swift tests of the page's presentation model (sections, names, figures, the
    breakdown, the not-included lines). Selecting a breakdown name selects that provider in
    History.
- **AC-7 Chart marks.** Every day state draws as specified, and nothing is drawn or counted as
  zero for a missing reading.
  - An incomplete day's bar is exactly as tall as its known total, and its marker adds no height.
  - **Verify:** Swift tests of the chart's segment model, for every state:
    - the segment heights add up to the scaled known total;
    - the scale ignores missing accounts;
    - the marker is present exactly on incomplete days.
- **AC-8 Series and overflow.** Up to four members are individual series. With five or more, the
  first three are individual and the rest are one "Other accounts" series.
  - **Verify:** Swift tests for three, four, five and seven members:
    - the series assignment;
    - "Other accounts (N)" in the legend;
    - the "Other accounts" height is the sum of its accounts' known values, and its missing
      accounts get the marker;
    - every account is still named in the readout, the accessibility value and the breakdown.
- **AC-9 Accessibility.**
  - The chart is one focusable element with ←/→/Home/End.
  - Its accessibility value names the date, the day's figure, and every account's value or state
    with its time where it has one. For example: "4 Oct, at least 776k tokens: Claude Code 776k,
    Claude (az) missing reading".
  - The series tokens meet the token contrast rule.
  - **Verify:** Swift tests of the readout and accessibility strings. A Python test computes each
    series token's contrast from `tokens.json` against the panel, card and selected-day colors in
    both appearances.
- **AC-10 Visual acceptance by the owner** (the phase exit).
  - Renders, in light and dark:
    - All tools with two Claude Code accounts and the other tools;
    - the same at narrow width and over 90 days;
    - a range with an incomplete day;
    - an account that is off;
    - five accounts of one tool (the overflow);
    - only one-account tools;
    - no usage at all.
  - The changed `history-*` renders differ only by the sidebar row. Every other existing render is
    byte-identical.
  - **Accepted** by the owner on 2026-11-02, for the 16-render packet from `082c5f9`.
- **AC-11 Live check by the owner**, after a separately approved app install on the personal Mac.
  - All tools shows Claude Code with both accounts stacked.
  - Today's part for each account matches that account's own History page.
  - **Passed** (owner, 2026-11-02) on the app installed from `3a2473a`: both Claude accounts
    appear in All tools, and each account's Today value matches its own History page.
- **AC-12 Validation and isolation.**
  - `make check` (with `AI_USAGE_REQUIRE_PARITY_HISTORY=1`), `make app-test`,
    `make fixture-check` and `make design-build-check` pass.
  - `make spec-check ARGS=--strict` and `make design-check ARGS=--strict` pass.
  - CI is green on the final head, with a fresh independent review.
  - Tests use synthetic data only and start no real provider executable (`tests/isolation.py`,
    fixtures).
  - Builds and renders run in a separate worktree, never the main checkout's `macos/build`.

## Resolved Questions
- D1–D7 and the acceptance criteria: approved on 2026-11-01, with the corrections above.

## Open Questions
- None.
