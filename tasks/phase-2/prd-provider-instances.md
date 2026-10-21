# PRD: Multiple Provider Instances, Plus claude-az (Phase 2)

Status: **draft for the owner's review (2026-10-20).** Both PRD gates are still open:
- the codebase analysis must be confirmed;
- the acceptance criteria must be approved.

Nothing in this phase is implemented. It depends on phase 1 (PR #10) merging; this branch is cut
from #10's head `b0c2b26`.

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
- Each instance is collected, stored and shown as its own provider, with its own usage and plan
  limits.
- The five built-ins keep their ids, config, state and history.
- Every provider in the report gains a `type`, so that phase 4 can group instances.

## Codebase Analysis

Read at #10's head `b0c2b26`.

### Explored

**Config (`ai_usage_service.py`):**
- Each built-in's settings live at `providers.<id>`. The defaults come from `plugin.default_settings`.
- `validate_config` checks only the poll interval, flags, `paths` and `providers` being an
  object. Unknown top-level keys are accepted and ignored, so the 2.2.0 collector would
  silently ignore an `instances` key.
- `configured_path_roles` validates each plugin's `path_roles`, and rejects aliases and
  collisions across them. Claude declares `stats_file` and `projects_dir`. Its
  `usage_cache_file` is not a path role, so two instances could point at the same
  `.claude.json` without being caught.

**Collection:**
- `collect_snapshot` iterates `PLUGINS` and passes each one the same mutable `state` dict.
- CSV rows carry the provider id in the `provider` column (`CSV_FIELDS`). The CSV has no
  instance or type column.

**State (`ai_usage_plugins/*`):** the keys are named per tool, not per instance:
- Claude: `claude_offsets`, `claude_recent_message_ids`, `claude_accounted_usage`, `claude_legacy_scan`.
- Others: `antigravity_offsets`, `antigravity_session_totals`, `gemini_offsets`, `grok_offsets`,
  `grok_recent_event_ids`.

A second Claude instance sharing `claude_recent_message_ids` would drop the other account's
messages as duplicates.

**Claude plugin (`ai_usage_plugins/claude.py`):**
- Default paths:
  - `stats_file` `~/.claude/stats-cache.json`;
  - `projects_dir` `~/.claude/projects`;
  - `usage_cache_file` `~/.claude.json`.
- The `/usage` refresh runs `claude -p /usage` with `claude_usage_environment()`. That copies
  `os.environ`, minus `ANTHROPIC_*`, `CLAUDE_CODE_*` and `CLAUDECODE*`. **`CLAUDE_CONFIG_DIR` is
  passed through.**
- Then it reads `usage_cache_file`.

**The owner's second account, file names only:**
- `~/.claude-az/` holds `.claude.json`, which has `cachedUsageUtilization`, and `projects/`. It
  has no `stats-cache.json`.
- With `CLAUDE_CONFIG_DIR` set, Claude Code keeps its `.claude.json` inside the config dir,
  not beside it.

**Report (`ai_usage_report.py`):**
- The catalogue is one `ProviderDef` per plugin, sorted by `report_order`. Rows are grouped by
  `row.provider`.
- The presentation rules listed in `REPORT_PROVIDER_RULES` compare `definition.id` with a
  literal: Claude's quota basis and per-model weekly windows, and the stats-cache note. An
  instance with any other id would lose them.

**App (`macos/`):**
- Report-driven: lists, Settings toggles, notices and history all iterate `report.providers`.
  Only the snapshot renderer names ids.
- Settings writes through the bundled module's `configure`. `parse_config_assignment` accepts
  only `providers.<bundled id>.{enabled,monthly_subscription_usd}`.
- The app already gates features on the installed collector's version, for example pause needs
  2.2.0 (`capabilities`).

**Install:**
- The ownership record covers program files and integrations (Antigravity's status line, Gemini
  CLI's telemetry), one per plugin.
- Config is never rewritten at install.

### Relevant patterns
- **Config writes:** locked, validated against the merged config, written to a temp file and
  renamed, mode kept, other keys preserved (`configure`).
- **Compatibility:** shown by `tests/parity/` golden files from a fixed BASELINE commit, plus
  carry-forward tests that run BASELINE and then the branch in one sandbox.
- **Feature gates:** keyed on the installed collector's `VERSION`.

### Constraints discovered
- **The collector and the app ship separately.** A config written by a newer app can be read by
  an older installed collector, which drops `instances` without an error.
- **Existing environment hazard:** when the collector runs from a shell where
  `CLAUDE_CONFIG_DIR` is set, the default `claude` instance's refresh targets that other
  account but reads `~/.claude.json`. This shell has it set to `~/.claude-az`; launchd and the
  app do not. Today it is latent. Phase 2 must fix it, because it is exactly the cross-account
  read the plan's risk list names.
- **Integrations are single-instance:** a wrapper path and one settings file per tool. Multiple
  Antigravity or Gemini CLI instances would need their own integration design.

## Clarifying Questions (recommended answers marked)

1. **Which plugins can have more than one instance in phase 2?**
   - **A) Claude only.** An instance of any other plugin is refused with a clear error. *(recommended)*
   - B) All five.
   - C) Claude and Codex.
   - D) Other.
2. **Config shape.**
   - **A) The built-ins stay in `providers.<id>` as their own default instances. `instances`
     holds only the added ones.** No config migration. *(recommended)*
   - B) Move everything into `instances`, with a backup.
   - C) Other.
3. **Canonical type.**
   - **A) `type` = the plugin id (`claude`, `codex`, …), on every report provider and in
     `doctor`.** Phase 3 plugins declare their own. *(recommended)*
   - B) A vendor family (`anthropic`, `openai`, `google`, `xai`).
   - C) Both.
   - D) Other.
4. **Instance ids.**
   - **A) Chosen by the user, matching `^[a-z][a-z0-9_-]{0,31}$`. Never a bundled id, and
     unique. The CLI suggests one, e.g. `claude-az`.** *(recommended)*
   - B) Generated.
   - C) Other.
5. **Removing an instance.**
   - **A) `instance remove <id>` drops its config entry. Its CSV history is kept, and its state
     key is removed once unused.** *(recommended)*
   - B) Out of scope: add plus the existing enable toggle only.
   - C) Also purge history.
6. **Settings "Add account…".**
   - **A) Claude only: a label plus a config-folder picker.** It checks the folder holds a
     `.claude.json` and runs `instance add` through the bundled module. It is offered only when
     the installed collector supports instances. *(recommended)*
   - B) CLI only in phase 2.
   - C) Other.
7. **Version.**
   - **A) `VERSION` 2.3.0, with an `instances` capability. The owner reinstalls to use it.**
     *(recommended)*
   - B) Other.
8. **The `CLAUDE_CONFIG_DIR` hazard.**
   - **A) The default `claude` instance always runs `/usage` with `CLAUDE_CONFIG_DIR` removed.
     An added instance always sets it to its own `config_dir`.** *(recommended)*
   - B) Other.

## Goals
- The owner's `~/.claude` and `~/.claude-az` accounts are two providers, each with its own
  usage and plan limits, never merged.
- No history is lost or double-counted. Existing config, state and CSV carry forward unchanged.
- Each provider has a documented canonical type that phase 4 can group by.

## Non-Goals
- External or private plugins, manifests, subprocess plugins (phase 3).
- The All view, or any cross-instance aggregation in the UI (phase 4).
- Instances of plugins other than those chosen in Q1. Multi-instance integrations.
- Changing the installed collector on the owner's Mac. That is a separate, owner-approved step
  after merge.

## Approach (assuming the recommended answers)

**Config:**
- `instances` is an optional list. Each entry is
  `{"id": "claude-az", "plugin": "claude", "label": "Claude (az)", "settings": {...}}`.
- Its settings are merged over the plugin's `default_settings`.
- Validation runs before any mutation: the id rules, a known plugin that allows instances, and
  path roles across every instance.

**Claude:**
- A new `config_dir` setting.
- When it is set:
  - the default paths come from it (`<dir>/projects`, `<dir>/stats-cache.json`, `<dir>/.claude.json`);
  - `/usage` runs with `CLAUDE_CONFIG_DIR=<dir>`;
  - `usage_cache_file` becomes a path role.
- When it is not set (the built-in), today's paths stay and `CLAUDE_CONFIG_DIR` is removed from
  the environment.

**State:**
- The built-in instance keeps its existing top-level keys, so there is no migration.
- An added instance gets its own dict at `state["instances"][<id>]`, passed to `collect` in
  place of the shared one.

**Order:** the built-ins in `BUNDLED` order, then the instances in config order. This applies
to collection, doctor, progress and the report.

**Report:**
- One `ProviderDef` per instance (`id` = instance id, `name` = label), plus `type` on every
  provider.
- The rules in `REPORT_PROVIDER_RULES` switch from `definition.id` to the type.
- The schema stays `ai-usage/report/v1`; `type` is added.

**CLI:** `instance add|list|remove`, all through `configure`'s locked, atomic write.

**App:**
- Settings lists every report provider, as it does today.
- `configure` accepts instance ids.
- "Add account…" follows Q6, with these states: not supported by the installed collector,
  invalid folder, duplicate, saving, saved, failed.

## Proposed Acceptance Criteria (awaiting approval)

- **AC-1 Config contract.**
  - `instances` entries are validated: the id pattern, unique, never a bundled id, a supported
    plugin, a non-empty label, settings as an object.
  - Path roles are checked across all instances, with alias and collision detection, including
    Claude's `usage_cache_file` when `config_dir` is set.
  - An invalid config is rejected before anything is written.
  - A config without `instances` produces byte-identical output: every phase-1 parity golden
    file still passes, unchanged.
- **AC-2 CLI.**
  - `instance add --plugin claude --id claude-az --label "…" --config-dir ~/.claude-az` writes
    config.json atomically, keeping other keys and its mode.
  - It refuses a duplicate id, an unsupported plugin, a collision or a missing folder, changing
    nothing.
  - `instance list` prints JSON. `instance remove` follows Q5.
  - Tests use an isolated HOME.
- **AC-3 Collection.**
  - Each instance collects with its own settings and writes rows with `provider` = instance id.
  - `doctor`, `once --progress` and the CSV follow the order above.
  - A failing instance yields its own `unexpected_error` row, and the others are unchanged.
- **AC-4 State and history compatibility.**
  - The built-in keeps its existing state keys.
  - Two Claude instances whose transcripts share message ids each count their own messages.
  - A `state.json` and CSV written by phase 1's merge commit carry forward with no double count
    (a carry-forward test in one sandbox).
  - Removing an instance keeps its CSV rows.
- **AC-5 Account isolation.**
  - With a fake `claude` that records its environment and the files it touches:
    - the built-in instance's refresh runs without `CLAUDE_CONFIG_DIR` (even when the
      collector's own environment sets it) and reads `~/.claude.json`;
    - `claude-az`'s refresh runs with `CLAUDE_CONFIG_DIR=~/.claude-az` and reads
      `~/.claude-az/.claude.json`.
  - No instance reads or refreshes another's cache.
- **AC-6 Report.**
  - Each instance is its own provider entry: id, label as name, its own quota windows and
    notes. The Claude rules apply to every `type: "claude"` provider.
  - Every provider carries `type`.
  - Additive only: the app decodes today's report fixture and a new two-account fixture.
  - `REPORT_PROVIDER_RULES` and the AST check are updated to key on type.
- **AC-7 Canonical type.** `docs/PLUGINS.md` defines `type`:
  - its values (the plugin id);
  - where it appears (report, doctor);
  - that phase 4 groups by it.

  No All view is built.
- **AC-8 App.**
  - Settings shows and toggles every instance.
  - "Add account…" covers each state from the approach, with XCTest coverage and snapshot
    renders of each.
  - It is offered only when the installed collector reports the `instances` capability.
    Otherwise it explains that a reinstall is needed.
  - Design rules 30–38 apply. The owner gives visual acceptance.
- **AC-9 Version compatibility.**
  - `VERSION` is 2.3.0, with an `instances` capability.
  - An older installed collector with an `instances` config is reported in the app, not
    silently ignored.
  - Install and uninstall leave `instances` and instance state alone.
- **AC-10 Owner's Mac (the exit criterion).** After the merge and an owner-approved reinstall,
  `claude` and `claude-az` appear separately, each with its own usage and plan limits. The owner
  runs this check, because changing the installed collector needs their approval.
- **AC-11 Validation and docs.**
  - The canonical Make targets all pass.
  - Fresh required CI, with the executor that actually ran recorded (Proofglass macOS).
  - `docs/PLUGINS.md`, `docs/CLI.md` and the specs are updated.
  - The phase-gate audit is run.

## Open Questions
- Q1–Q8 above. Each answer can change ACs 1, 2, 4, 6, 8 and 9.
- Does the owner want `claude-az`'s `monthly_subscription_usd` set at add time, or later in
  Settings? It is the existing per-provider setting either way.
