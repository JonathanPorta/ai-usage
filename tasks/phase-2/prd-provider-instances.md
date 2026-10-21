# PRD: Multiple Provider Instances, Plus claude-az (Phase 2)

Status: **approved by the owner, 2026-10-20.** The codebase analysis is accepted, AC-1 to AC-11
are approved, and the decisions below are recorded. Phase 1 merged as `1b60b5f` (#10). Main CI on
that commit is green, and its macOS lane ran on Proofglass (job `job_01M5E28PRY63TZXWP7V2QX06HS`).

Scope comes from `tasks/project-plan.md`, phase 2:
- an `instances` list in config.json (decision 3A);
- `ai-usage instance add` and Settings "Add account…" (4B);
- existing ids kept (5);
- state and history compatibility;
- the canonical-type definition.

External plugins (phase 3) and the All view (phase 4) stay out.

## Summary

Let the owner add a second instance of a bundled provider: their second Claude Code account in
`~/.claude-az` (`CLAUDE_CONFIG_DIR`).
- Each added account is collected, stored and shown as its own provider, with its own usage,
  plan limits and costs.
- The five built-ins keep their ids, config, state and history.
- Every provider gains a canonical `type`, which phase 4 will group by.

## Decisions (owner, 2026-10-20)

| # | Decision |
| --- | --- |
| Q1 | **A.** Only Claude can have added instances in phase 2. The instance model is generic: a plugin opts in. |
| Q2 | **A.** The built-ins stay under `providers.<id>` and are their own default instances. `instances` holds only the added accounts. No config or state migration. |
| Q3 | **A.** The canonical type is the plugin id (`claude`), not the vendor. |
| Q4 | **A.** Ids are unique, stable slugs chosen by the user (`^[a-z][a-z0-9_-]{0,31}$`), never a bundled id. Labels are display text, not identity. The app proposes an id that can be edited. |
| Q5 | **B.** Add, list, enable and disable are in scope; removal is deferred. Disabling keeps the config entry, its metadata, its CSV history and its checkpoint state. Re-enabling resumes without recounting. |
| Q6 | **A.** "Add account…" supports Claude: a label, a config-folder picker and the id behavior above. It is offered only when the installed collector supports instances. |
| Q7 | **A.** `VERSION` becomes 2.3.0, with an `instances` capability. That does not authorize publishing a release or changing the owner's installed collector. |
| Q8 | **A.** The default account's `/usage` refresh explicitly removes `CLAUDE_CONFIG_DIR`. Each added account's refresh explicitly sets it. |
| Cost | An added account's monthly subscription cost is left unset at creation. It can be entered later in Settings. |

## Codebase Analysis (accepted 2026-10-20)

Read at #10's head `b0c2b26`, which is identical in content to `1b60b5f`.

### Explored

**Config (`ai_usage_service.py`):**
- Each built-in's settings live at `providers.<id>`, merged over `plugin.default_settings` by
  `load_config`.
- `validate_config` ignores unknown top-level keys, so the 2.2.0 collector would silently
  ignore an `instances` key.
- `configured_path_roles` checks each plugin's `path_roles` for aliases and collisions. Claude
  declares `stats_file` and `projects_dir`. `usage_cache_file` is not checked.

**Collection:**
- `collect_snapshot` iterates the registry and passes each plugin the same mutable `state`.
- CSV rows carry the provider id in `provider`.

**State:** the keys are named per tool:
- Claude: `claude_offsets`, `claude_recent_message_ids`, `claude_accounted_usage`, `claude_legacy_scan`.
- Shared helpers add keys such as `claude_daily_model_tokens` and `monthly_subscription_rates`.

A second Claude sharing them would drop the other account's messages as duplicates.

**Claude plugin:**
- Default paths: `~/.claude/stats-cache.json`, `~/.claude/projects` and `~/.claude.json`.
- `claude_usage_environment()` copies `os.environ`, minus `ANTHROPIC_*`, `CLAUDE_CODE_*` and
  `CLAUDECODE*`. **`CLAUDE_CONFIG_DIR` passes through.**
- Every row it emits names `"claude"` as its provider.

**The owner's second account (file names only):** `~/.claude-az/` holds `.claude.json` (which
has `cachedUsageUtilization`) and `projects/`. It has no `stats-cache.json`.

**Report (`ai_usage_report.py`):**
- The catalogue is one `ProviderDef` per plugin, ordered by `report_order`.
- Rules compare `definition.id` with literals, and some data lookups name the provider: the
  stats-cache note reads `latest.provider_rows("claude")`, and the Grok model and cost scans
  filter on `"grok"`.

**App:**
- Report-driven. Settings writes through the bundled module's `configure`, which accepts only
  `providers.<bundled id>.{enabled,monthly_subscription_usd}`.
- Features are gated on the installed collector's version (`capabilities`).

**Install:** integrations (Antigravity's status line, Gemini CLI's telemetry) are one per
plugin. Config is never rewritten at install.

### Constraints discovered
- **The collector and the app ship separately.** A config written by a newer app can be read by
  an older installed collector, which drops `instances` silently.
- **`CLAUDE_CONFIG_DIR` hazard:** the collector run from a shell where `CLAUDE_CONFIG_DIR` is set
  refreshes that other account but reads `~/.claude.json`. Launchd and the app do not set it.
- **Integrations are single-instance.** A plugin with an integration cannot have added instances.

## Goals
- The owner's `~/.claude` and `~/.claude-az` accounts are two providers, each with its own
  usage, plan limits and costs, never merged.
- No history is lost or double-counted. Existing config, state and CSV carry forward with no
  migration.
- Each provider has a documented canonical type for phase 4.

## Non-Goals
- External or private plugins (phase 3). The All view or any cross-account totals (phase 4).
- Added instances of plugins other than Claude. Multi-instance integrations.
- Removing an instance (deferred by Q5).
- Publishing a release, or changing the owner's installed collector. AC-10 is the owner's own
  check after the merge and an approved reinstall.

## Architecture & Approach

### Identity
| Name | Meaning | Selects |
| --- | --- | --- |
| **Instance id** (`claude-az`) | The provider id everywhere: the CSV `provider` column, the state namespace, the report `id`, the progress events, the `doctor` key | This account's rows, state, quota, notes, costs and settings |
| **Type** (`claude`) | The plugin id. A built-in's type equals its id; an added instance's never does, because ids can't be bundled ids | Provider-specific behavior: the collector code, the report's presentation rules |
| **Label** | Display text only (the report's `name`) | Nothing |

### Config
`instances` is optional. Entries look like
`{"id": "claude-az", "plugin": "claude", "label": "Claude (az)", "settings": {"config_dir": "~/.claude-az"}}`.
- `settings` is optional, defaulting to `{}`. It takes any of the plugin's settings, such as
  `enabled`, `monthly_subscription_usd` and path overrides. No other entry keys are allowed.
- The entry is validated in `validate_config`, before any mutation:
  - the id rules, uniqueness, and no bundled id;
  - a bundled plugin that allows added instances;
  - a non-empty label, settings as an object;
  - the plugin's own instance rules.
- Path roles are checked across the built-ins and every instance together, as resolved paths,
  so aliases and collisions are caught.
- `DEFAULT_CONFIG` gains no key. Install still writes the same default config.

### Plugin contract (additive)
- `instance_settings(explicit) -> settings`: present only on plugins that allow added
  instances. It returns the instance's complete settings, or raises `ValueError`.
  - **Claude:** requires a non-empty `config_dir`, and expands it to an absolute path.
  - It derives `projects_dir = <dir>/projects`, `stats_file = <dir>/stats-cache.json` and
    `usage_cache_file = <dir>/.claude.json`.
  - Then it applies the plugin defaults for everything else, and the instance's explicit
    overrides last. Derived paths never fall back to `~/.claude`.
- `instance_only_settings`: settings a built-in may not set. Claude's is `config_dir`, so
  `providers.claude.config_dir` is rejected rather than half-applied.
- Claude's `path_roles` gains `usage_cache_file`, which is validated for the default account
  too.
- **Rows:** the collector stamps the instance id on every row an instance's plugin returns.
- **State:** the instance gets `state["instances"][<id>]` in place of the shared dict. A built-in
  keeps its existing top-level keys.
- **`/usage`:** `claude_usage_environment(config_dir)` always removes `CLAUDE_CONFIG_DIR`, and
  sets it to the expanded absolute `config_dir` when there is one.

### Order
| Surface | Order |
| --- | --- |
| Collection, CSV rows, `doctor`, `once --progress` | The built-ins in `BUNDLED` order (codex, claude, antigravity, gemini_cli, grok), then instances in config order |
| Report providers | The built-ins in `report_order` (codex, claude, antigravity, grok, gemini_cli), then instances in config order |

### Report (`ai-usage/report/v1`, additive)
- One `ProviderDef` per instance: a copy of the plugin's, with the instance id, the label as
  `name`, and `type`.
- Every provider in the report carries `type`.
- `REPORT_PROVIDER_RULES` select behavior by `definition.type`. Every lookup of rows, quota,
  notes, costs and settings uses `definition.id`.
- `collector.capabilities.instances` is true when the installed collector is 2.3.0 or newer.
- When the installed collector is older, an added instance is reported with setup
  `not_configured` and a note saying it is not collected until the collector is reinstalled.

### CLI
- `instance add --plugin claude --id claude-az --label "Claude (az)" --config-dir ~/.claude-az`
- `instance list`
- `instance enable <id>` and `instance disable <id>`

Every mutation goes through `configure`'s locked path: lock beside the resolved config, apply,
validate the merged config, atomic write that keeps the mode, then reload. `configure` also
accepts `instances.<id>.enabled` and `instances.<id>.monthly_subscription_usd`.

**Mutation boundary:** `instance add` and `instance enable` refuse unless the LaunchAgent's
collector reports `VERSION` 2.3.0 or newer, read from the script text and never executed. The
error says to reinstall first.

### App
- Settings lists every report provider, as today.
- An instance's toggle and price write `instances.<id>.…`. The app tells an instance from a
  built-in by `type != id`, which validation guarantees.
- **"Add account…"** (Claude only) asks for a label and a config folder. It proposes an id from
  the label, which can be edited, and runs `instance add` through the bundled module.
- Its states:
  - **unsupported:** the installed collector is older; reinstall is explained;
  - **editing**, with validation messages for an empty label, an invalid id, a duplicate id,
    and a folder without `.claude.json`;
  - **saving**;
  - **saved**;
  - **failed:** the collector's message is shown.
- Every state has an XCTest and a snapshot render.

### Compatibility and parity (corrected requirement)
- **The phase-1 golden files stay exactly as they are.** Phase 2 compares every scenario with
  them after applying one declared list of intentional differences, and nothing else:
  - `report.collector.version` and `report.collector.installed_version` become `"2.3.0"`;
  - `report.collector.capabilities` gains `"instances": true`;
  - each `report.providers[*]` gains `"type"` equal to its `id`;
  - each `doctor --json` entry gains `"type"` equal to its key.
- A test fails if that list grows. The new fields are asserted by value.
- **Carry-forward:** run the phase-1 squash commit `1b60b5f` for the first poll, then this
  branch in the same sandbox. The result must equal the golden files with the declared
  differences. A second run adds an account after phase-1 data exists.
- **App:** decodes today's fixtures, without `type` or `instances`, unchanged.

## Acceptance Criteria (approved 2026-10-20, updated with the decisions)

- **AC-1 Config contract.**
  - `instances` entries are validated:
    - the id pattern, unique, never a bundled id;
    - a plugin that allows instances (Claude);
    - a non-empty label;
    - settings as an object;
    - no unknown entry keys;
    - Claude requires a non-empty `config_dir`.
  - `providers.claude.config_dir` is rejected.
  - Path roles are checked across all accounts as resolved paths, with alias and collision
    detection. That includes every Claude account's `usage_cache_file`, the default's included.
  - An invalid config is rejected before anything is written.
  - A config without `instances` behaves exactly as phase 1, apart from the declared parity
    differences.
- **AC-2 CLI.**
  - `instance add --plugin claude --id claude-az --label "…" --config-dir ~/.claude-az` writes
    config.json under the config lock, atomically, keeping other keys, the mode and a symlinked
    config's link.
  - It refuses, changing nothing:
    - a duplicate or invalid id;
    - an unsupported plugin;
    - a path collision;
    - a missing config folder;
    - an installed collector older than 2.3.0.
  - `instance list` prints JSON.
  - `instance enable` and `disable` toggle `settings.enabled`; enable has the same version check.
  - The monthly cost is left unset.
  - Tests use an isolated HOME.
- **AC-3 Collection.**
  - Each account collects with its own settings, and every one of its rows has
    `provider` = its id.
  - Collection, CSV, `doctor` and `--progress` follow the order table.
  - A disabled account writes the existing disabled marker row and nothing else.
  - A failing account yields its own `unexpected_error` row, and the others are unchanged.
- **AC-4 State and history compatibility.**
  - The built-in keeps its top-level state keys, and an added account uses
    `state["instances"][<id>]`.
  - Two Claude accounts whose transcripts share message ids each count their own messages.
  - The carry-forward from `1b60b5f` matches, as described above.
  - Disabling and then re-enabling an account keeps its config, state and CSV rows, and resumes
    from its checkpoints with no recount.
- **AC-5 Account isolation.** Shown with isolated fixtures and a fake `claude` that records its
  environment and refreshes the cache that real Claude Code would use:
  - **The default account:** its refresh runs with no `CLAUDE_CONFIG_DIR`, even when the
    collector's own environment sets it, and reads `~/.claude.json`.
  - **An added account:** its refresh runs with `CLAUDE_CONFIG_DIR` set to the expanded absolute
    folder, and reads `<dir>/.claude.json`.
  - No account reads or refreshes another's cache.
  - Derived paths never fall back to `~/.claude`.
  - An account folder without `stats-cache.json` can be added, and its transcripts and quota are
    collected.
  - The owner's real accounts are never used in development or tests.
- **AC-6 Report.**
  - Each account is its own provider, with its own quota windows, notes and costs; the label is
    its name.
  - The Claude rules apply to every `type: "claude"` provider, and its data lookups use its own
    id.
  - Every provider carries `type`, and the report stays `ai-usage/report/v1`, additive.
  - `REPORT_PROVIDER_RULES` and the AST check cover the type-based rules and id-based lookups.
- **AC-7 Canonical type.** `docs/PLUGINS.md` defines `type`:
  - its values (the plugin id);
  - where it appears (the report, `doctor --json`);
  - how it differs from the id and the label;
  - that phase 4 groups by it.

  No All view is built.
- **AC-8 App.**
  - Settings shows every account, with its toggle and price.
  - "Add account…" covers each state listed in the approach, with XCTest coverage and a
    snapshot render of each.
  - It is offered only when `collector.capabilities.instances` is true; otherwise the app
    explains that a reinstall is needed.
  - Design rules 30–38 apply. The owner gives visual acceptance at PR review.
- **AC-9 Version compatibility.**
  - `VERSION` is 2.3.0, with the `instances` capability.
  - An older installed collector with a config that has `instances` shows in the report and the
    app as `not_configured`, with a note. It is never silently ignored.
  - The app decodes reports without `type` or `instances`.
  - Install and uninstall leave `instances` and the instance state alone.
- **AC-10 Owner's Mac (operational acceptance, pending until performed).** After the merge and
  an owner-approved reinstall, `claude` and `claude-az` appear separately, each with its own
  usage and plan limits. The owner runs this check. It is not a precondition for the
  implementation PR, and phase 2 isn't called operationally accepted until it passes.
- **AC-11 Validation and docs.**
  - The canonical Make targets pass.
  - Fresh required CI, recording the executor that actually ran (Proofglass macOS).
  - `docs/PLUGINS.md`, `docs/CLI.md` and the specs are updated.
  - The phase-gate audit is run.

## Resolved Questions
- Q1 to Q8 and the monthly cost: see Decisions.

## Open Questions
- None.
