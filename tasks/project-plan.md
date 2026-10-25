# Project Plan: Provider Plugins and Multiple Accounts

Status: brief and phases from the owner's decisions 1A–7B (2026-10-19). Phase 1 approved and
merged as `1b60b5f` (#10, 2026-10-20). Phase 2's PRD and acceptance criteria approved on
2026-10-20 ([PRD](phase-2/prd-provider-instances.md)); implemented, merged (#11 to #17) and installed.
Its operational acceptance (AC-10) was accepted by the owner on 2026-10-23
([session state](phase-2/session-state-provider-instances.md)).
Phase 3's analysis, PRD and acceptance criteria were approved by the owner on 2026-10-24, with
five corrections made in the PRD ([PRD](phase-3/prd-private-plugins.md)). Its implementation is in
review ([session state](phase-3/session-state-private-plugins.md)); merging, reinstalling and AC-13
remain the owner's. Phase 4 is not yet PRD'd. Each gets its own PRD and
acceptance-criteria gate, and this plan does not widen their scope.

## Project Brief

### Vision
Make collection and display plugin-based. Then one tool can run as several instances, for
example the owner's second Claude Code account in `~/.claude-az` via `CLAUDE_CONFIG_DIR`. The
owner can also add a private work-tool plugin that never leaves the work laptop, without forking
the repo.

### Success Metrics
- The owner's `~/.claude` and `~/.claude-az` accounts appear as two separate providers, each
  with its own usage and plan limits. They aren't merged together.
- A private plugin dropped into `~/.ai-usage/plugins/<name>/` shows up in the app. It needs no
  repo change and no rebuild, and survives `install`/reinstall.
- An "All" view stacks usage by canonical type, for example both Claude accounts under "Claude".
- Existing history (`usage.csv`, `state.json`) carries over. Nothing collected before the change
  is lost or double-counted.

### Constraints
- Python standard library only in the collector. It runs under the LaunchAgent's pinned Python.
- Existing ids (`codex`, `claude`, `antigravity`, `gemini_cli`, `grok`) stay, unless a new
  scheme is clearly cleaner. If so, the change ships as a migration with a backup (decision 5).
- The macOS app stays report-driven: it renders whatever providers the report lists.
- Private plugins are owner-only (0700/0600) and are never committed, bundled or uploaded.
- External plugins run as subprocesses with a timeout and process-group kill. A hung or
  crashing plugin can't stall the poll or other providers (decision 2A).

### Known Risks
- Single-file install: `install` copies only `ai_usage_service.py` into
  `~/.ai-usage/collector.py`, and the report does `import ai_usage_service`. Splitting the code
  into modules changes what gets installed and bundled. Mitigation: phase 1 owns this and proves
  install, reinstall and uninstall.
- Per-provider state keys (`claude_offsets`, `claude_recent_message_ids`, …) assume one instance
  per tool. Mitigation: phase 2 namespaces state per instance, with a tested migration.
- Claude's `/usage` refresh and `~/.claude.json` cache are per config dir. A second instance
  must never refresh or read the first one's cache. Mitigation: phase 2 acceptance criteria name
  this explicitly.

## Phases (decision 1A)

### Phase 1: Plugin core, built-ins moved unchanged
**Objective:** the five built-ins are plugin modules behind one registry. The output is
byte-identical to today's.
**Entry:** #5 merged; branch `jp/f/provider-plugin-core` cut from that main.
**Exit:** AC-1–AC-9 of [the phase-1 PRD](phase-1/prd-provider-plugin-core.md) met with evidence:
- parity with the merged pre-refactor revision;
- the install lifecycle proven, rollback included;
- the installed daemon and the bundled reporter run outside the checkout;
- fresh CI on Proofglass.

**Features:** one plugin interface plus a deterministic registry; the five built-ins moved behind
it; every distribution path carrying the module set.

### Phase 2: Multiple instances, plus claude-az
**Objective:** `instances` list in config.json: `{id, plugin, label, settings}` (3A). Add one
with `ai-usage instance add` or from Settings, "Add account…" (4B). Its PRD defines what the
canonical/parent type means and which values it takes. Phase 1 doesn't define it.
**Entry:** Phase 1 exit.
**Exit:** two Claude instances collected and shown separately on the owner's Mac; the
migration keeps history.
**Depends on:** the Phase 1 registry; state namespacing.

### Phase 3: Private plugins from a local folder
**Objective:** an executable plus a JSON manifest in `~/.ai-usage/plugins/<name>/`, speaking a
stdout JSON protocol, with timeout/process-group isolation (2A, 6A).
**Entry:** Phase 2 exit (private plugins are instances too).
**Exit:** a sample external plugin, in the tests only, is collected and shown. A hung plugin is
killed and reported. Reinstall leaves the folder alone.

### Phase 4: "All" view
**Objective:** stacked bars by canonical/parent type across instances.
**Entry:** Phase 2 exit (canonical type exists).
**Exit:** All view renders; visual acceptance by the owner.

## Dependency Map

    Registry + interface (P1) ──→ Instances (P2) ──→ External plugins (P3)
                                       └──────────→ All view (P4)  [canonical type]
    Multi-file install (P1) ───→ External plugins (P3)  [plugins dir ownership]
