# Provider plugins

The collector reads each AI tool through a **provider plugin**. Phase 1 of the
provider-plugin project (`tasks/project-plan.md`) moved the five built-in
providers behind one interface and one registry. Their behavior did not change.
Phase 2 added more than one instance of a provider: an added Claude account
(see [Added accounts](#added-accounts-phase-2)). Phase 3 added local plugins
that are not bundled: an executable plus a manifest in `~/.ai-usage/plugins/<id>/`
(see [Local plugins](#local-plugins-phase-3)).

## Layout

```text
ai_usage_plugins/
  __init__.py      the registry: BUNDLED, MODULES, load_plugins()
  base.py          the contract: ProviderPlugin, CollectContext, IntegrationSpec, ProviderDef, SourceDef
  support.py       helpers the collectors share (rows, paths, executable discovery, JSONL reading, process control)
  external.py      local plugins (phase 3): discovery, permission checks, the manifest, the runner, response checks
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
| `instance_settings(explicit)` | Optional (phase 2). Present only on a plugin that allows added accounts: returns an added instance's complete settings, or raises `ValueError`. A plugin with an `integration` never has it. |
| `instance_only_settings` | Settings only an added instance may set; the built-in rejects them (Claude: `config_dir`). |
| `refresh_dir(settings)` | Optional. The folder an account's own refresh writes to, as `(setting name or "(description)", path)`, or `None` when the account never refreshes. No two accounts may share it or nest one inside another, whatever their read paths say (Claude: `config_dir`, or `~/.claude` for the default account). |
| `refresh_cache(settings)` | Optional. The file an account's own refresh writes, whatever its read settings say, or `None` when it never refreshes (Claude: `<config_dir>/.claude.json`, or `~/.claude.json` for the default account). It may not resolve to another account's refreshed file or `usage_cache_file`. |

**Rules for provider modules:**
- Importing a module only defines things. It starts no process, reads or writes no file, and collects nothing. A test checks this with an audit hook.
- A module imports only the standard library, `.base` and `.support`, never another provider.
- Everything a provider needs from the collector arrives in its arguments (`CollectContext`). Codex and Grok send `context.version` as their client version.

## Errors

| What fails | What happens |
| --- | --- |
| A provider module fails to import | The other providers load and collect as before. On every poll, the failed provider gets one `collection/unexpected_error` row whose message is `provider module failed to load: <error>`, unless it is disabled in the config. `doctor` shows `load_error` for it. The report leaves it out and lists the error in `collector.plugin_errors`; the key is present only when something failed. If the report's own copy of the module fails, the report also counts the provider as failed: one `collection.failures` entry (`kind: "plugin"`, the provider id, the load error), `providers_failed` and `providers_attempted` each one higher, and a `partial` result (`failed` if no other provider updated). The app shows it as a failure notice named by the provider id. A provider that is turned off is not counted, and isn't listed in `skipped` either, because its display name is in the module that failed. |
| `collect()` raises | Unchanged from before: one `collection/unexpected_error` row with the error message, and the other providers still run. |
| `install` from a source tree with a module that fails to load | Refused before anything is written. The installer does not install an incomplete module set. |
| The Antigravity module fails to load (known limit) | Its `antigravity-statusline` command isn't registered, so the status-line hook gets a usage error instead of the plain name "Antigravity" that it shows for any other failure. If `base` or `support` fails, no command works. Install never writes an incomplete set, and it writes the modules before `collector.py`, so this needs a damaged install. |

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
- An interruption (Ctrl-C, `KeyboardInterrupt`) during install or uninstall is handled like a failure: the files are restored and a service stopped for the change is started again, then the interruption is raised. A `kill -9` or a power loss is not covered.
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

## Added accounts (phase 2)

`config.json` may list **added instances** of a bundled plugin, beyond each
plugin's built-in one. In 2.3.0 only Claude allows them.

```json
"instances": [
  {"id": "claude-az", "plugin": "claude", "label": "Claude (az)", "settings": {"config_dir": "~/.claude-az"}}
]
```

Add one with `collector.py instance add` or Settings › "Add Claude account…".
`instance list`, `enable` and `disable` manage them. Removing one isn't
supported yet: disable it instead (its config entry, state and CSV history are
kept, and re-enabling resumes without recounting).

### Three names
| Name | Example | Meaning | Selects |
| --- | --- | --- | --- |
| **id** | `claude-az` | The provider id everywhere: the CSV `provider` column, `state["instances"][<id>]`, the report's `id`, the progress events and the `doctor` key. A lowercase slug (`^[a-z][a-z0-9_-]{0,31}$`), unique, never a bundled id, and never changed after it is added. | That account's rows, state, quota, notes, costs and settings |
| **type** | `claude` | The **canonical type**: the plugin id. A built-in's type is its id; an added account's never is. It is on every provider in the report (`providers[].type`) and in `doctor --json`. Phase 4's All view groups by it. | Provider-specific behavior: the plugin's collector code and the report's presentation rules |
| **label** | `Claude (az)` | Display text only: the report's `name`. | Nothing |

### Validation (before anything is written)
- The entry keys are `id`, `plugin`, `label` and an optional `settings` object; no others.
- Claude requires a non-empty `config_dir`, either absolute or starting with `~`. A relative folder is rejected, because the daemon has no working directory to resolve it against. `providers.claude.config_dir` is also rejected: the built-in account keeps `~/.claude` and `~/.claude.json`.
- Path roles are checked across every account, as resolved paths, so two accounts can't read the same transcripts or usage cache, even through a symlink. Claude's `usage_cache_file` is a path role for every account, the default included.
- One account's folder can't sit inside another account's folder for the same role, for example inside `~/.claude/projects`, because transcripts are found recursively.
- No two accounts may refresh the same folder, or one inside another's: an added account's `config_dir`, or `~/.claude` for the default account. This holds even when explicit settings move every read path apart, because `/usage` would refresh one account's login while the other read a cache nothing refreshes. Identity is compared resolved, so a symlink alias counts as the same folder; `claude` is still run with the folder as named. An account that never runs `claude` (disabled, or with `subscription_usage: false`) isn't compared, so disabling one resolves a collision, and enabling it again is refused while the collision remains.
- The `.claude.json` each refresh writes (`<config_dir>/.claude.json`, or `~/.claude.json` for the default account) may not resolve to another account's refreshed file or `usage_cache_file`, for example through a symlink. This is compared on its own, because an account's `usage_cache_file` override moves what it reads but not what its refresh writes. The same file read under two names within one account is fine.
- The config file itself can't be any account's path. This is checked before the write, so a refused `instance add` leaves the file unchanged.
- `instance add` refuses a plugin whose module currently fails to load.
- `instance add` saves the folder itself: `~` and environment variables are expanded when the command runs, and symlinks are kept as named. A process without the caller's environment, such as launchd's, then reads the same folder. A folder naming an unset variable is refused.

### A Claude account's paths and `/usage`
- **Paths:** from its own `config_dir`, with `~` expanded but symlinks kept as named, because Claude Code keys an account's login to its folder name: `<dir>/projects`, `<dir>/stats-cache.json` and `<dir>/.claude.json`. Explicit `settings` apply after those. Nothing falls back to `~/.claude`.
- **`/usage` refresh:**
  - an added account runs `claude` with `CLAUDE_CONFIG_DIR=<absolute dir>`;
  - the default account runs it with `CLAUDE_CONFIG_DIR` removed, even when the collector itself runs where it is set.
  
  Each account reads only its own `.claude.json`.
- **No stats cache:** an added account without `stats-cache.json` is collected from its transcripts and `/usage`. Its daily-stats source shows as waiting, not failed.

### Collection, state and order
- **Order:** collection, CSV rows, `doctor` and `once --progress` take the built-ins in `BUNDLED` order, then added accounts in config order. The report takes the built-ins in `report_order`, then added accounts in config order.
- **State:** an added account keeps its checkpoints in `state["instances"][<id>]`. The built-ins keep their top-level keys, so nothing is migrated.
- **Rows:** the collector stamps each added account's rows with its id.
- **A disabled account** writes the usual disabled marker row and nothing else.

### Version compatibility
- Added accounts need collector 2.3.0. The report's `collector.capabilities.instances` says whether the installed collector has them.
- `instance add`, `instance enable` and `configure instances.<id>.enabled=true` refuse unless the LaunchAgent's collector is 2.3.0 or newer. They read its `VERSION` from the script text and never run it.
- An older installed collector ignores `instances`, so the report shows each added account as `not_configured`, with a note to reinstall.
- `install` and `uninstall` never change `instances` or an account's state.

### Parity
Phase 2 keeps phase 1's golden files. `tests/parity/driver.py` declares the
only intended differences (`INTENTIONAL_CHANGES`):
- `VERSION` 2.3.0;
- `capabilities.instances`;
- `type` on report providers and `doctor --json` entries.

It applies them to the golden side, and compares everything else exactly.
Data written by the phase-1 squash commit (`tests/parity/PHASE1`) carries
forward.

## Local plugins (phase 3)

A **local plugin** is a private collector the owner drops into
`~/.ai-usage/plugins/<id>/`: an executable plus a `plugin.json` manifest. It
needs no change to this repository and no rebuild. It is never bundled,
installed, uploaded or committed: the work laptop's plugin, its credentials,
configuration and logs stay on that laptop. This repository holds the contract
and synthetic test plugins only.

### Trust model

**A local plugin is trusted local code.** It runs as the owner's user, with the
owner's permissions, the same as the collector itself. It can read and write
anything the owner can.

What the collector does guards against **mistakes and faults**, not hostile code:
- the timeout, the output limits and the process-group cleanup keep a faulty plugin from
  stalling the poll or leaving processes behind;
- response validation keeps a buggy plugin from writing malformed rows or another provider's
  data;
- the owner-only permission checks catch a plugin folder that another user could read or
  change.

**None of this is a security sandbox.** A plugin is not confined by file-system, network or process restrictions. It can read files, open connections, spawn processes that escape its process group (for example with `setsid`), or change `~/.ai-usage` directly. Install only plugins you wrote or trust as much as the collector.

### Folder, permissions and discovery

```text
~/.ai-usage/plugins/          0700
  example/                    0700   the folder name is the plugin id (and its type)
    plugin.json               0600   the manifest
    collect                   0700   the executable the manifest names (any language, with a shebang)
```

- **Discovery** runs at the start of every poll, and whenever `doctor`, `plugin list` or the
  report runs, so a new or changed plugin needs no daemon restart. It reads manifests and
  stats files and **never runs a plugin**.
- **Owner-only:** `plugins/`, each plugin folder and any folder on the way to the executable
  must be the collector's uid's, with no group or other permission bits. `plugin.json` and the
  executable must be regular files that are the uid's, with none either.
- **Symlinks** are allowed for the plugin folder, `plugin.json` and the executable when the
  link itself is the uid's and its resolved target passes the same check. The resolved
  executable must lie inside the resolved plugin folder.
- **Nothing is repaired:** a folder that fails is *invalid*, with a reason naming the path and
  the mode it needs. The collector never runs `chmod`, `chown` or any repair.
- **Ids:** the folder name must match `^[a-z][a-z0-9_-]{0,31}$`, equal the manifest's `id`, and
  not be a built-in's or an added account's id.

### Manifest (`plugin.json`, at most 64 KiB)

```json
{
  "manifest_version": 1,
  "protocol": 1,
  "id": "example",
  "executable": "collect",
  "timeout_seconds": 30,
  "default_settings": {"region": "eu"},
  "report": {
    "name": "Example",
    "product": "Example Tool",
    "usage": "daily_total",
    "quota": "used",
    "quota_note": null,
    "sources": [
      {"id": "usage", "role": "usage", "label": "Daily usage", "kind": "Example API, read-only", "csv_source": "example-usage"},
      {"id": "limits", "role": "quota", "label": "Limits", "kind": "Example API, read-only", "csv_source": "example-limits"}
    ]
  }
}
```

| Field | Rule |
| --- | --- |
| `executable` | A relative path inside the folder; no `..`, no absolute path |
| `timeout_seconds` | 1–120, default 30. `providers.<id>.timeout_seconds` may lower it, never raise it |
| `default_settings` | A flat object of JSON scalars, sent under the owner's `providers.<id>` values. It may not contain `enabled`, `timeout_seconds` or `monthly_subscription_usd`, so a manifest can never turn execution on |
| `report.usage` | `daily_total` (per-day totals, shown like Codex's), `events` (input/output events, shown like Claude's), or `none` |
| `report.quota` | `used` (percent used, like Claude Code and Grok), `remaining` (percent remaining, like Antigravity), or `none` |
| `report.sources` | 1–4 sources: `id` (`^[a-z][a-z0-9_]{0,31}$`), `role` (`usage` or `quota`), `label`, `kind`, and a unique `csv_source`. A usage or quota profile other than `none` needs a source of that role |

Unknown keys are refused at every level, so a newer manifest fails clearly
instead of being half-read.

### Protocol 1

The collector runs `<folder>/<executable> collect` in its own process group,
with the plugin folder as its working directory, writes one JSON request to its
stdin and reads one JSON object from its stdout.

**Request:**
```json
{"protocol": 1, "collector_version": "2.4.0", "provider": "example",
 "collected_at": "2026-10-23T12:00:00Z", "include_history": false,
 "settings": {"region": "eu", "timeout_seconds": 30}, "state": {}}
```

**Environment:** only these variables, never the collector's whole environment:
- `PATH`: the collector's executable-discovery folders, without the environment's own `PATH`;
- `AI_USAGE_PLUGIN_DIR` (the resolved plugin folder) and `AI_USAGE_PROTOCOL=1`;
- `HOME`, `USER`, `LOGNAME`, `TMPDIR` and `LANG`, copied when set. **These can differ between a
  shell run (`once`) and the launchd service**, which has its own `TMPDIR` and usually no
  `LANG`. A plugin must not depend on any other variable.

A plugin reads its own credentials from wherever it keeps them. The collector
never passes credentials to it.

**Response:** exactly one JSON object, optionally followed by whitespace:
```json
{"protocol": 1,
 "rows": [
   {"category": "usage", "metric": "daily_tokens", "period_start": "2026-10-23", "value": 12345, "source": "example-usage"},
   {"category": "quota", "metric": "used_percent", "scope": "weekly", "value": 42.5,
    "window_seconds": 604800, "resets_at": "2026-10-27T07:00:00Z", "source": "example-limits"}
 ],
 "state": {"cursor": "abc"}}
```

| Row kind | Keys | Rules | Written as |
| --- | --- | --- | --- |
| Daily total (`usage: daily_total`) | `category: "usage"`, `metric: "daily_tokens"`, `period_start`, `value`, `source` | `period_start` is the Mac's local date the total covers, at most one day ahead. `value` ≥ 0. A later row for the same date replaces it | `period_total`, `scope` = the date, `unit: tokens`, `collected_at` = the poll time |
| Event (`usage: events`) | `category: "usage"`, `metric` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`), `at`, `value`, `source`, optional `scope` | `at` is the UTC event time (`…Z`), at most 120 s after the poll. Events are **summed**: report each once, using `state` as the checkpoint. A `scope` of `<key>:<model>` adds the model to "models today" | `event_total`, `collected_at` = `at` |
| Quota window (`quota: used` or `remaining`) | `category: "quota"`, `metric`, `scope`, `value`, `source`, optional `window_seconds`, `resets_at` | `metric` is `used_percent` for `used` and `remaining_percent` for `remaining`; the other one is refused, so a percent used can't show as remaining. `value` 0–100. 18000 s shows as "5-hour", 604800 s as "Weekly" | `snapshot`, `unit: percent`, measured at the poll time |
| Not found | `category: "availability"`, `metric: "detected"`, `value: false`, `message` | The plugin found nothing to read | `status: missing` |
| Source error | `category: "collection"`, `metric: "plugin_error"`, `source`, `message` | That source failed, or needs sign-in when the message says so | `metric: <source id>_error`, `status: error` |

- A row may contain only its kind's keys; `provider`, `collected_at`, `record_kind`, `unit`
  and `status` are the collector's. `source` must be a declared `csv_source` of the row's role.
- `message` is at most 500 characters, with no control characters. At most 2,000 rows.
- `state` is optional, a JSON object of at most 256 KiB. It is kept in
  `state["instances"][<id>]` and sent back on the next poll; without it the state is kept.
- A successful run with no availability row also gets one `availability/detected` row
  ("the plugin ran"), so a quiet poll isn't shown as "not found".

### Bounds, cleanup and failure

- **One deadline** covers the whole run. At the deadline the process group is killed.
- **stdout** is capped at 1 MiB; passing it kills the run. **stderr** is read in fixed chunks
  and discarded; it never fails a run, and endless stderr hits the deadline.
- **After the plugin exits** its pipes may stay open for at most 1 s (a child holding them
  can't hold the poll). Then, after **every** run, the process group gets SIGTERM, then SIGKILL,
  and the collector closes its pipe ends. A process that leaves the group (`setsid`) can
  survive, but can't hold the poll.
- **Success** needs exit status 0, the limits kept, and one valid protocol-1 response.
  Killing leftover children doesn't turn success into failure.
- **Anything else** writes exactly one `collection/unexpected_error` row (`source: "plugin"`)
  with a fixed reason, keeps none of the response, and leaves the plugin's state unchanged:
  `timed out after 30s`, `exited with status 3`, `was stopped by signal 9`,
  `wrote more than 1 MiB to stdout`, `returned invalid JSON at line 1 column 5`,
  `returned an invalid response at rows[4].status`, `could not be started (ENOENT)`.
  The other providers are unaffected.

### What is stored, and what isn't

- **Stored:** a valid response's rows go to `usage.csv` (every string field, `message` and
  `scope` included) and the report and app show them; a valid `state` goes to `state.json`.
  **A plugin must keep credentials, tokens and other secrets out of its rows, messages,
  scopes and state.** The collector doesn't inspect them for secrets.
- **Never stored:** stderr (counted, then discarded), and anything from a rejected response.
- **Diagnostics name things, never values:** a field path, a manifest key, a JSON position, an
  exit status, a signal or an `errno` name. `collector.log` records the plugin id, the reason,
  the exit, the duration and the stderr byte count, nothing more. To debug, run the plugin by
  hand, or have it write its own log.

### Enablement and lifecycle

A plugin runs only when `config.json` has `providers.<id>.enabled: true`.
Discovery, a manifest default or a missing key never turn it on.

| Config for the id | Folder | Rows each poll |
| --- | --- | --- |
| No `providers.<id>` entry | Discovered | None. The report lists a valid one as `disabled`, "Not turned on" |
| `providers.<id>` present, `enabled` not true | Valid, invalid or missing | The disabled marker row, as for a built-in |
| `enabled: true` | Valid | Its rows, or one failure row |
| `enabled: true` | Invalid | One `unexpected_error` row: `plugin is invalid: <reason>` |
| `enabled: true` | Missing | One `unexpected_error` row: `plugin folder not found` |

- **Turning it on** (`plugin enable`, `configure providers.<id>.enabled=true`, the app's
  switch) needs a valid folder now and a LaunchAgent collector of 2.4.0 or newer. **Turning
  it off** always works, even for a missing or invalid folder.
- **Order:** built-ins, then added accounts, then local plugins by id.
- **Removed folder:** its config, state and history are kept; restoring it resumes. Replacing
  the files keeps the state. Renaming the folder makes a new provider.
- **Invalid folders** are listed in `doctor`, `plugin list` and the report's
  `collector.plugin_errors` (`plugins/<name>`, or `plugins` for the folder itself).
- **Install and uninstall** never create, chmod, move or delete `~/.ai-usage/plugins/`, and the
  app bundle never carries a `plugins/` folder (`scripts/check_app_bundle.py`).
- **An older installed collector** (before 2.4.0) ignores local plugins. The report shows them
  as `not_configured` with a reinstall note, and the app shows no switch.

### Parity

Phase 3 declares only `VERSION` 2.4.0 and `capabilities.external_plugins`
(`tests/parity/driver.py`). Every scenario also runs with an empty plugins
folder and with unconfigured and invalid plugins in it, and phase 2's data
carries forward from `357bcee` (`tests/parity/PHASE2`).
