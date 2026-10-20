---
status: approved
owner: Jonathan
updated: 2026-10-05
source-template: tools/blessed/scripts/spec-scaffold.sh (desktop-app)
reviewed-by: Jonathan (2026-10-05, via approval of the design import and first implementation milestone)
---

# MVP — native macOS menu-bar app

The detailed requirements, acceptance criteria and validation are in
[`tasks/prd-macos-menu-bar-app.md`](../tasks/prd-macos-menu-bar-app.md).
This file is the summary contract.

## Problem

Collector data is accurate but unreadable at a glance. I need one calm place
that shows today's usage, the last 7 days, every quota window with its own
freshness, and whether the collector is healthy. It must not trigger collection
just because I looked.

## Users and journeys (MVP scope)

- **Primary journey:** open the menu-bar popover. See cached state immediately. Scan the provider cards.
- **Drill in:** open a provider's detail view to see the chart, its quota windows, today's figures, models, costs and sources.
- **Explicit refresh:** press Collect now. Watch it check. See the result. The popover and any open History window both update.
- **History:** open the History window, pick a provider and Tokens or Quota, and change the range.

## In scope for the first milestone

- A read-only reporting layer, `ai_usage_report.py`, that outputs JSON (`ai-usage/report/v1`). It works over the existing CSV, state, log and `launchctl`.
- A SwiftUI `MenuBarExtra` popover with:
  - a status row and notice;
  - overview cards (quota windows, today, 7-day chart);
  - provider detail.
- A separate History window, with Tokens over 7, 30 or 90 days and Quota with a window picker.
- One shared `@Observable` store feeding every view.
- Collect now through the installed collector's `once` command.

## Non-goals and out of scope (milestone 1)

These are planned as later tasks:

- the Monitoring view;
- Settings (interval, providers, prices);
- service start and stop;
- pause and resume, which needs a new collector capability;
- notifications;
- open at login.

These are not planned:

- changes to how providers are collected;
- sending data off the machine;
- Windows or Linux UI.

## Acceptance criteria

This is the summary. The authoritative list is AC-1 through AC-22 in the PRD.

- [ ] The report command outputs valid `ai-usage/report/v1` JSON from real collector files without running any provider binary or collection.
- [ ] Aggregation follows `record_kind`, and quota snapshots are never summed.
- [ ] Freshness is evaluated for each quota window. A fresh limit warning survives when another window is stale. A passed reset means `measuredAt < resetsAt <= now`.
- [ ] Failed or disabled sources keep their cached measurements and timestamps, and do not appear fresh.
- [ ] Missing data, measured zero, incomplete today and the period before collection began are distinguished.
- [ ] The popover (420 pt) shows cached data on open without starting a collection.
- [ ] Collect now runs the existing one-time path, then refreshes the shared store, including an already-open History window.
- [ ] Provider detail defaults to Tokens over 7 days, and History defaults to 30 days.
- [ ] Quitting the app leaves the collector running.

## Success signal

The owner checks usage from the menu bar instead of the CSV, and the numbers
match the CSV aggregation when spot-checked.

## Risks and unknowns

- **CSV size.** The live file is about 60 MB and growing, so report latency matters. Mitigations: show cached data first and refresh in the background.
- **Real data differs from the fixtures.** Codex limit IDs come and go, Grok's window is weekly, and Codex has no input/output split.
- **Interpreter resolution** for a GUI app with a minimal `PATH`.
