# Provider plugins

The collector reads each AI tool through a **provider plugin**. Phase 1 of the
provider-plugin project (`tasks/project-plan.md`) moved the five built-in
providers behind one interface and one registry. Their behavior did not change.
Phase 2 adds more than one instance of a provider (for example a second Claude
account). Phase 3 adds plugins that are not bundled. Neither exists yet.

## Layout

```text
ai_usage_plugins/
  __init__.py      the registry: BUNDLED, MODULES, load_plugins()
  base.py          the contract: ProviderPlugin, CollectContext, IntegrationSpec, ProviderDef, SourceDef
  support.py       helpers the collectors share (rows, paths, executable discovery, JSONL reading, process control)
  codex.py  claude.py  antigravity.py  gemini_cli.py  grok.py
```

`ai_usage_service.py` (the collector) and `ai_usage_report.py` (the report)
import the package. The package never imports either of them.

## The registry

`ai_usage_plugins.BUNDLED` is the only list of built-in providers:
`codex`, `claude`, `antigravity`, `gemini_cli`, `grok`.

Its order is the collection order. It is also the order of:
- CSV rows within a poll;
- `once --progress` events;
- `doctor` output;
- the providers in the default config.

`load_plugins()` imports each provider module on its own and returns two
dicts: the loaded plugins and the load errors, both keyed by id. The collector
calls it once at import time and keeps the results as `PLUGINS` and
`PLUGIN_LOAD_ERRORS`.

`MODULES` lists every file of the package. The installer and the app bundle
ship exactly these files.

## The interface

Each provider module defines `PLUGIN = ProviderPlugin(...)`.

| Field | Meaning |
| --- | --- |
| `id` | The provider id. It is the `providers.<id>` config key, the CSV `provider` value and the report id. |
| `default_settings` | This provider's entry in the default config. Its key order is kept, because install writes it as `config.json`. |
| `executable_names` | The names to look for when `executable` is not set. |
| `collect(context)` | Returns this provider's CSV rows. `CollectContext` carries `collected_at`, `settings`, `include_history`, the mutable `state`, and the collector `version`. |
| `doctor_extra(settings)` | Extra `doctor` fields. A key ending in `_readable`, or a `session_log_count`, counts as a detected source. |
| `path_roles` | `(setting, "file" \| "directory")` for each path setting. They are validated with the config, including alias and collision checks. |
| `integration` | Optional `IntegrationSpec`: a field the installer may add to the provider's own settings file (Antigravity's status line, Gemini CLI's telemetry). It can have a wrapper script, which the installer owns, that runs `collector.py <wrapper_command>`. |
| `status_line(settings, payload)` | Optional. Records one status-line payload and returns the text to show. It is run through the integration's wrapper command (`antigravity-statusline`). |
| `report`, `report_order` | The report's catalogue entry (`ProviderDef` with its `SourceDef`s) and its position in the report's provider list. |

**Rules for provider modules:**
- Importing a module only defines things. It starts no process, reads or writes no file, and collects nothing. A test checks this with an audit hook.
- A module imports only the standard library, `.base` and `.support`, never another provider.
- Everything a provider needs from the collector arrives in its arguments (`CollectContext`). Codex and Grok send `context.version` as their client version.

## Errors

| What fails | What happens |
| --- | --- |
| A provider module fails to import | The other providers load and collect as before. On every poll, the failed provider gets one `collection/unexpected_error` row whose message is `provider module failed to load: <error>`, unless it is disabled in the config. `doctor` shows `load_error` for it. The report leaves it out and lists the error in `collector.plugin_errors`; the key is present only when something failed. |
| `collect()` raises | Unchanged from before: one `collection/unexpected_error` row with the error message, and the other providers still run. |
| `install` from a source tree with a module that fails to load | Refused before anything is written. The installer does not install an incomplete module set. |

The tests break a provider module in a copy of the tree and compare the other
providers' rows with the parity golden files (`tests/test_plugin_architecture.py`).

## Packaging

| Path | What ships |
| --- | --- |
| Installed collector | `~/.ai-usage/collector.py` (0700) plus `~/.ai-usage/ai_usage_plugins/*.py` (each 0600; the directory is 0700). |
| App bundle | `Contents/Resources/collector/` holds `ai_usage_report.py`, `ai_usage_service.py` and `ai_usage_plugins/*.py`. `make app-build` checks the bundle (`scripts/check_app_bundle.py`). |
| Release candidate | The archived app, so the same bundle (`make release-build`). |

**Install:**
- Every module is a managed file, with the ownership key `plugin_module:<name>`.
- `collector.py` and all modules are written in the same transaction. If any write fails, every file is restored, so a failed upgrade never leaves a mix of versions. The failure test fails each write in turn.
- Upgrading from the single-file collector (2.2.0 and earlier) adds the package in that transaction. Config, state and CSV are not rewritten.
- Reinstalling the same version rewrites no program file.
- An owned module that a newer version no longer ships is removed only if it is unchanged.

**Uninstall:**
- Removes `collector.py`, the unchanged modules and the package directory once it is empty.
- Keeps config, usage, state, logs, backups, modified or unowned files, and settings it does not own.
- While an unowned hook may still run `collector.py` through the managed wrapper path, the collector and its modules are kept.

**Running outside the checkout:**
- The collector and the report add their own directory to `sys.path`, so they find the package even under `python -I`.
- They set `sys.dont_write_bytecode`, so no `__pycache__` appears beside the installed collector or inside the signed app.

## Compatibility guarantees (phase 1)

These are unchanged from the single-file collector (2.2.0):
- **Data formats:** provider ids, config keys and defaults, the CSV columns and rows, the `state.json` keys, and the report schema (`ai-usage/report/v1`).
- **Commands:** the CLI commands, including `antigravity-statusline`, which existing installed wrappers call.
- **Order:** rows, `doctor` output and the report's provider list.

`tests/parity/` shows this. It runs eight scenarios with time frozen and
compares the results with golden files captured from #5's merge commit
(`tests/parity/BASELINE`). Only sandbox paths, transaction ids and state inode
numbers are normalized.

## Provider rules that stay in the report

The report takes its provider set, order and catalogue from the registry. A
few presentation rules still name a provider, because they interpret that
provider's own CSV shapes:
- Gemini telemetry that is off by choice is shown as a setup note;
- Grok's per-model payload;
- quota window names;
- which quota metric each provider reports;
- Grok's billing and API-equivalent costs;
- the Claude stats-cache and Grok incomplete-usage notes.

`tests/test_plugin_architecture.py` (`REPORT_PROVIDER_RULES`) lists each one
with its reason, and fails if a new one appears without a reason. The
collector's shared code names no provider at all. The same test proves that
with an AST check, and a negative control confirms the check catches a
reintroduced provider branch.

## Adding a built-in provider

1. Add `ai_usage_plugins/<id>.py` with its collector code and a `PLUGIN`.
2. Add `<id>` to `BUNDLED`. `MODULES` follows.
3. Add tests and parity coverage for it.

Nothing else in the collector changes. The report shows the provider through its `ProviderDef`.

## External plugins: phase 3, not implemented

Plugins that are not bundled are not supported. That includes private
plugins in `~/.ai-usage/plugins/<name>/`, an executable with a JSON manifest,
and its stdout protocol. Nothing discovers, loads or installs third-party
code. Phase 3 defines that protocol and its isolation.
