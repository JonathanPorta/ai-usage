# PRD: The All View (Phase 4)

Status: **draft for the owner's approval.** The codebase analysis, decisions D1–D7 and AC-1 to
AC-11 are proposed for one approval, as in phases 2 and 3. No phase-4 code is written until the
owner approves them.

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
"Claude (az)". Tools are never added to each other, and quota and cost are never combined. A day
on which one account has no reading is marked incomplete; it is never drawn as if that account
used nothing.

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

### Assumptions (need the owner's confirmation)
- History, not the popover, is the right home (D1).
- Two accounts per tool is the common case; more than four accounts of one tool is rare (D6).

## Decisions (proposed)

| # | Question | Options | Recommended |
| --- | --- | --- | --- |
| D1 | Where does it live? | **A.** An "All tools" entry at the top of the History sidebar. **B.** A "By tool" switch on the popover overview that merges same-type cards. **C.** Both. | **A.** Quota limits are per account, and the popover exists to act on them; merged cards would hide which account is near a limit. |
| D2 | What is combined? | **A.** Daily tokens only. **B.** Tokens, plus each account's current quota cells beside them (still never summed). | **A.** Quota and cost stay on each account's own page. |
| D3 | Which tools appear? | **A.** Every tool with at least one included account; a one-account tool shows plain bars. **B.** Only tools with two or more accounts. | **A.** "All" means every tool, so one page answers "what did I use this week". |
| D4 | What about a disabled account? | **A.** Leave it out of the combined figures and name it under its tool as off. **B.** Include it up to its last reading. | **A.** With **B**, every later day would show as incomplete. |
| D5 | What if one account has no reading for a day? | **A.** Mark the day incomplete: solid segments for the accounts read, a dashed segment for each missing one, and a total of "at least N". **B.** Draw the whole day as missing. | **A.** It keeps the readings that exist without passing them off as complete. |
| D6 | How are accounts told apart? | **A.** Four new series token pairs (light and dark), assigned in display order, with 1 pt gaps; the legend, the readout and the accessibility value name each account. A fifth or later account of one tool shares the fourth series as "Other accounts", and the readout still lists it by name. **B.** Reuse the input and output blues. | **A.** **B** changes what the blues mean. |
| D7 | Where is the combination computed? | **A.** In the bundled reporter, as `groups`. **B.** In the app, from `providers[].days`. | **A.** The authority map puts aggregation in the reporter, and the rules are then testable in Python. |

## Goals
- One page shows every tool's daily tokens, with each tool's accounts stacked.
- Each combined figure states when an account was missing a reading, off or not yet collected.
- Nothing is summed across tools, and quota and cost are never combined.
- Existing report fields, pages and renders outside History are unchanged.

## Non-Goals
- A combined popover card, menu-bar figure or notification.
- Combining quota, cost, subscription prices or models.
- Changing the collector, its version, or any installed file.
- Rebuilding or installing the owner's app. That is a separate decision after merge.

## Architecture & Approach

### Report: `groups` (additive to `ai-usage/report/v1`)

```jsonc
"groups": [
  {
    "type": "claude",
    "name": "Claude Code",                 // the built-in's name; a local plugin's own name
    "members": ["claude", "claude-az"],    // included accounts, in display order
    "excluded": [ { "id": "claude-work", "reason": "disabled" } ],
    "coverage_start": "2026-07-08",        // the earliest member's, or null
    "days": [                              // oldest → today, exactly --days entries
      { "date": "2026-10-04", "state": "incomplete", "total": 776000, "as_of": null,
        "parts": [ { "id": "claude", "state": "measured", "total": 776000 },
                   { "id": "claude-az", "state": "missing", "total": null } ] }
    ]
  }
]
```

1. **Groups.** There is one group per type in the report, in the order of each type's first
   provider.
   - The members are that type's providers that are enabled and whose setup isn't `disabled`.
   - The others are listed in `excluded` with reason `disabled`.
   - A group can have no members. The app then lists that tool only as not included.
2. **Parts.** A day's `parts` are the members' own Day records for that date, in member order,
   with `id`, `state` and `total`.
3. **Day state**, decided in this order:
   - every part is `not_collected`: `not_collected`, with a `total` of null;
   - no part has a value, and at least one is `missing`: `missing`, with a `total` of null;
   - at least one part is `missing` and another has a value: `incomplete`;
   - at least one part is `partial`: `partial`, and `as_of` is the earliest part's `as_of`;
   - otherwise `measured` when the total is above 0, and `zero` when it is 0.

   A `not_collected` part (an account added later) doesn't make a day incomplete. The readout
   names it as not collected that day.
4. **Total.** A group day's `total` adds the parts that have values, and nothing else. Each part's
   total already excludes cache tokens. No value crosses groups, and groups carry no quota, cost
   or model fields.
5. **Today.** The last day is the report's `today`; there is no separate today object.

### App
- **Sidebar:** an "All tools" row above the providers, with the caption "Accounts stacked by
  tool". It is shown only when the report has `groups`.
- **Page**, in the History layout:
  1. a header ("All tools" and the last check);
  2. the range control (7, 30 or 90 days; 30 by default; kept separately from the provider pages);
  3. one section for each group that has members:
     - a name row ("Claude Code · 2 accounts");
     - a figures line: the range total and today so far, each "at least" when an incomplete day
       is in the range;
     - the stacked chart and a legend whose account names open that account's History page;
     - "Not included: Claude (work), tracking off" when it has excluded accounts;
  4. one "Not included" line for tools with no included account;
  5. "About these numbers": each tool's accounts are added together, tools are never added to
     each other, and quota limits are per account.
- **Chart** (`GroupDayChart` in `AIUsageDesign`):
  - It uses the existing day marks: a zero tick, a missing box, a not-collected band, and a dashed
    today outline around the stack.
  - A measured or partial day stacks one segment per account, separated by 1 pt gaps.
  - An incomplete day stacks the accounts that were read, plus a dashed segment for each missing
    one.
  - One focusable element: ←/→/Home/End move the selection. The readout and accessibility value
    give the date, the day's state and total, and each account's value or state.
- **Unchanged:** the popover, Settings and every provider page.

### Tokens
- New semantic pairs `color.semantic.series1` to `series4` (light and dark), aliased as
  `color.component.chart.account1` to `account4`. The Swift values are generated by
  `make design-build`.
- Each one reaches at least 3:1 against the panel, the card and the selected-day background, in
  both appearances.
- Neighbouring series differ visibly in lightness. The 1 pt gap still separates them.

### Docs
- `docs/CLI.md`: `groups` and its rules.
- `DESIGN.md`: the All tools page, and an "incomplete day" row in the chart semantics table. The
  anti-patterns stay as they are.
- `DESIGN_SYSTEM.md` and the components manifest: the series tokens and `GroupDayChart`.
- `docs/design/app/design.md`: the verification record.

### Compatibility and parity
- The parity golden files are unchanged. The intentional changes gain exactly `report.groups`.
- The app decodes a report without `groups`, such as one from an older reporter (a test fixture
  with the key removed), and then hides All tools.
- The collector is unchanged: no file under `ai_usage_service.py` or `ai_usage_plugins/`
  changes, and `VERSION` stays `2.4.0`.

## Acceptance Criteria (proposed)

- **AC-1 Groups.** `report.groups` follows rules 1, 2 and 5 and is documented in `docs/CLI.md`.
  - **Verify:** `tests/test_all_view.py`, built on synthetic CSVs via `ai_usage_fixtures`:
    - two Claude Code accounts, a local plugin, and the other built-ins;
    - group order and member order;
    - every member of a group shares its type;
    - a disabled account in `excluded`, and a group with no members;
    - `--days` 7 and 90.
- **AC-2 Day rules.** Rules 3 and 4 hold for every combination of part states.
  - **Verify:** table-driven tests cover every pair of the five part states for two accounts,
    plus one three-account case.
  - On every group day, `total` equals the sum of the parts that have values.
  - Cache tokens are never included.
  - A recorded negative-control run weakens each rule in turn, and a test fails every time.
- **AC-3 Nothing mixed.** Groups carry exactly the documented keys, with no quota, cost or model
  field, and no group adds providers of different types.
  - **Verify:** a key-set test, and the type test from AC-1.
- **AC-4 Compatibility.** The existing report output is unchanged, and the collector is
  untouched.
  - **Verify:**
    - the parity golden files' hashes are unchanged, and the intentional-change list gains
      exactly `report.groups`;
    - a Swift test decodes a report without `groups` and finds All tools hidden;
    - `git diff --stat` shows no change to `ai_usage_service.py` or `ai_usage_plugins/`.
- **AC-5 The page.** The page meets the App section: the sidebar row and the page order, a
  section only for groups with members, the "Not included" lines, and the range control.
  - **Verify:** Swift tests of the page's presentation model (sections, names, figures, the
    "at least" wording, the not-included lines).
  - Selecting a legend name selects that provider in History.
- **AC-6 Chart marks.** Measured, zero, missing, not collected, partial and incomplete days draw
  as specified, and an account is never drawn as zero for a missing reading.
  - **Verify:** Swift tests of the chart's segment model for every state; the renders in AC-9.
- **AC-7 Accessibility.**
  - The chart is one focusable element with ←/→/Home/End.
  - Its accessibility value names the date, the day's state and total, and every account's value
    or state, for example: "4 Oct, incomplete, at least 776k tokens: Claude Code 776k, Claude
    (az) missing reading".
  - The series tokens meet the token contrast rule.
  - **Verify:** Swift tests of the readout and accessibility strings. A Python test computes each
    series token's contrast from `tokens.json` against the panel, card and selected-day colors in
    both appearances.
- **AC-8 Isolation.** Tests use synthetic data only, and start no real provider executable.
  Builds and renders run in a separate worktree, never the main checkout's `macos/build`.
  - **Verify:** the tests use `tests/isolation.py` and fixtures; the record in the PR.
- **AC-9 Visual acceptance by the owner** (the phase exit).
  - Renders, in light and dark:
    - All tools with two Claude Code accounts and the other tools;
    - the same at narrow width and over 90 days;
    - a range with an incomplete day;
    - an account that is off;
    - only one-account tools;
    - no usage at all.
  - The changed `history-*` renders differ only by the sidebar row. Every other existing render is
    byte-identical.
  - **Pending:** the owner accepts or corrects them.
- **AC-10 Live check by the owner**, after a separately approved app install on the personal Mac.
  - All tools shows Claude Code with both accounts stacked.
  - Today's part for each account matches that account's own History page.
  - **Pending:** the owner performs it.
- **AC-11 Validation.**
  - `make check` (with `AI_USAGE_REQUIRE_PARITY_HISTORY=1`), `make app-test`,
    `make fixture-check` and `make design-build-check` pass.
  - `make spec-check ARGS=--strict` and `make design-check ARGS=--strict` pass.
  - CI is green on the final head, with a fresh independent review.

## Open Questions
- D1–D7 above, with AC-1 to AC-11, for one approval.
