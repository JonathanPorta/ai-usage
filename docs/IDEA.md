---
status: approved
owner: Jonathan
updated: 2026-10-05
source-template: tools/blessed/scripts/spec-scaffold.sh (desktop-app)
---

# IDEA

## The spark

I use several AI coding tools at once: Codex, Claude Code, Antigravity, Grok
Build and Gemini CLI. Each one meters usage differently. Some have 5-hour or
weekly limits, some report credits, and some only keep local logs. I want to
know how much I have used and how much I have left without opening five
dashboards and without spending any model tokens to find out.

`ai-usage` started as a zero-inference collector. It is a per-user LaunchAgent
that writes a long-form CSV of token activity, quota snapshots and known costs
to `~/.ai-usage/usage.csv`. The data exists, but reading it means `column`,
`less` and mental arithmetic.

## What exists today

- **Collector** (`ai_usage_service.py`, installed as `~/.ai-usage/collector.py`). It runs as the LaunchAgent `codes.porta.ai-usage`, polls hourly, and reloads its config between polls. Commands: `install`, `once`, `daemon`, `status`, `doctor`, `uninstall`, `antigravity-statusline` and `version`.
- **Storage:** an append-only `usage.csv` with a `record_kind` aggregation contract, plus `state.json` (offsets and dedupe state) and a rotating `collector.log`.
- **Desktop app:** none. This repo now adds a native macOS menu-bar app (`macos/`) over the collector.

## Users

One developer on one Mac, the owner of the data. Nothing leaves the machine.

## Constraints and non-negotiables

- Zero inference: the app and the reporting layer never start a model turn and never execute a provider binary. Only the collector's existing, audited paths contact providers.
- The collector stays the only writer of usage data. The app reads, and asks the collector to collect.
- Closing the popover or quitting the app never stops the collector.
- Honest numbers: aggregate by `record_kind`; never sum quota snapshots; never draw missing data as zero; keep measured and collected times apart.
- Native, small and quiet: a menu-bar agent, not a Dock app.

## Inspirations and references

- Accepted Open Design handoff 0.1.1: `docs/design/app/`.
- The original approved concept: `docs/design/app/source/ai-usage-design-0.1.1/ai-usage-popover-reference.html`.
