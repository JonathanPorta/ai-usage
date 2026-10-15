---
status: approved
owner: Jonathan
updated: 2026-10-05
provenance: reconstructed
---

# Design brief — ai-usage macOS menu-bar app (RECONSTRUCTED)

> **This brief is reconstructed. It is not the original request.** The prompt
> that commissioned the Open Design work was not included in the handoff
> archive, and no copy exists in this repository. This document rebuilds the
> request from statements in the accepted handoff (`IMPLEMENTATION.md`,
> `design-notes.md`) and from the repository as it existed. It records what the
> returned design shows was asked for. Where the wording is ours, it says so.
> Replace or annotate this file if the original prompt is recovered.
>
> The returned handoff is a separate artifact: see [`design.md`](design.md) and
> `source/ai-usage-design-0.1.1/`.

## Request (reconstructed)

Design a native macOS menu-bar app over the existing `ai-usage` Python collector
(`ai_usage_service.py`, installed as the LaunchAgent `codes.porta.ai-usage`). The
app shows AI coding-tool usage, quota and collector health at a glance.

Evidence in the handoff: "Target: SwiftUI app over the existing Python
collector".

## User and job

- **User:** a single developer using several AI coding tools (Codex, Claude Code, Antigravity, Grok Build, Gemini CLI) on one Mac.
- **Job:** know how much has been used today and over the last week, how much quota is left in each limiting window, and whether the numbers are trustworthy (fresh, complete, not failed). Take the one action that fixes a problem.

## Constraints evident in the result

- The app must not invent backend capabilities. Every capability is marked **Exists**, **Proposed** or **Decision**.
- It must aggregate the collector's long-form CSV by `record_kind` and never sum quota snapshots.
- Service health, schedule, collection attempts, source results and measurement freshness are separate concepts.
- Closing the popover or quitting the app never stops the collector.
- Opening the popover never waits for a check.
- Light and dark appearances; a popover 420 pt wide; a separate History window.

## Starting point

The design continues an earlier approved concept,
`ai-usage-popover-reference.html`, which is included in the archive.

## Design history (from the handoff)

- **Checkpoints 0.1.0 and 0.1.1:** published as `ai-usage-menu-bar-design-checkpoint-*`.
- **Checkpoint 0.1.2:** packaging only.
- **Checkpoint 0.1.3:** a correctness pass. It added source-by-source merging for Collect now, per-window quota freshness, History sharing the popover's state, and readability rules. **This is the accepted design baseline.**
- **Handoff 0.1.0:** the first handoff.
- **Handoff 0.1.1:** documentation corrections only. **This is the accepted handoff.**
