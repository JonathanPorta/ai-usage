# PRD: Private Plugins From a Local Folder (Phase 3)

Status: **approved by the owner, 2026-10-24**, with five corrections made in this revision
(see Decisions). The codebase analysis, D1–D4 as recommended and AC-1 to AC-13 are approved in
one decision, at the owner's request, with no separate analysis-only round. AC-13 stays pending
until the owner performs it.

Scope comes from `tasks/project-plan.md`, phase 3, and the settled decisions:
- **2A:** a bundled provider is a Python module; an external plugin is an executable plus a
  JSON manifest.
- **6A:** private plugins are found under `~/.ai-usage/plugins/<name>/`.
- **Phase 2:** private plugins are instances too, with the same id, type and label model.

Out of scope: the All view (phase 4), the Proof Glass "Alexandria" label cleanup, changing the
owner's installed collector, and publishing a release.

## Summary

Let the owner drop a private collector into `~/.ai-usage/plugins/<name>/` on any Mac, turn it
on, and see it in the report and the app like a built-in provider. It needs no change to this
repository and no rebuild. The collector runs the plugin's executable once per poll and reads
one JSON response from its stdout. A hung, crashing, chatty or malformed plugin is stopped,
reported as that provider's failure, and can't stall the poll or change another provider's
data. The owner's work plugin, its credentials, configuration and logs stay on the work
laptop. This repository holds only the contract and synthetic test fixtures.

## Trust model

**A private plugin is trusted local code.** It runs as the owner's user, with the owner's
permissions, the same as the collector itself. It can read and write anything the owner can.

What the collector does guards against **mistakes and faults**, not hostile code:
- the timeout, the output limits and the process-group cleanup keep a faulty plugin from
  stalling the poll or leaving processes behind;
- response validation keeps a buggy plugin from writing malformed rows or another provider's
  data;
- the owner-only permission checks catch a plugin folder that another user could read or
  change.

**None of this is a security sandbox.** A plugin is not confined by file-system, network or
process restrictions. It can read files, open connections, spawn processes that escape its
process group (for example with `setsid`), or change `~/.ai-usage` directly. Install only
plugins you wrote or trust as much as the collector. The docs and the `plugin` CLI help say
this in those words.

## Codebase Analysis

Read at `357bcee` (main, 2026-10-23).

### Explored
- `ai_usage_plugins/__init__.py`: `BUNDLED` is a fixed tuple of the five built-ins.
  `load_plugins()` imports each one on its own and returns `(plugins, errors)`. `MODULES` is
  what install and the app bundle ship.
- `ai_usage_plugins/base.py`:
  - `ProviderPlugin` is a frozen dataclass of Python callables (`collect`, `doctor_extra`,
    `status_line`, `instance_settings`, `refresh_dir`, `refresh_cache`) plus a `ProviderDef`.
  - `ProviderDef` and `SourceDef` are plain data: name, product, mode, split, record kind,
    quota mode, notes, and sources mapped to CSV `source` values and error metrics.
  - An external plugin can describe all of `ProviderDef` in JSON. It can't provide the callables.
- `ai_usage_service.py`:
  - `PLUGINS` and `PLUGIN_LOAD_ERRORS` are computed once, at import. The daemon never rescans.
  - `accounts()` lists the built-ins (`provider_names()`, BUNDLED order), then added instances
    in config order.
  - `collect_snapshot()` runs each account in sequence inside one `state_transaction_lock`.
    It writes a disabled marker row for a disabled account, stamps an added instance's id on its
    rows, and turns any exception into one `collection/unexpected_error` row.
  - `commit_collection_transaction()` commits the poll's rows and state together, so a failed
    provider's state change can be discarded.
  - `validate_instances()` accepts only `plugin ∈ BUNDLED`. `parse_config_assignment()` accepts
    `providers.<id>` only for bundled ids.
  - `detect_providers()` (`doctor`) runs no provider command; it finds executables and readable
    sources.
  - `install_service()` owns `collector.py`, `ai_usage_plugins/*.py`, wrappers and the
    LaunchAgent, all recorded in `install-ownership.json`. Uninstall removes only owned,
    unchanged files. Nothing touches `~/.ai-usage/plugins/` today.
  - `VERSION` is `2.3.0`. `INSTANCES_MIN_VERSION` gates instance mutations by reading the
    installed script's `VERSION` line without running it.
- `ai_usage_plugins/support.py`:
  - `terminate_provider_process()` stops a process group with SIGTERM, then SIGKILL, and
    tolerates Darwin's EPERM from `killpg`. It's used by Codex, Claude and Grok, each started
    with `start_new_session=True`.
  - `metric_row()` defines the CSV row shape. `discovery_path()` is the PATH that providers use.
- `ai_usage_plugins/claude.py:1019`: Claude's `/usage` uses `communicate()`, which doesn't cap
  output. Codex and Grok read stdout line by line with a deadline but no size cap. **No existing
  helper bounds output size.**
- Built-in timeouts are `timeout_seconds` settings clamped to 5–120 s (`codex.py:37`,
  `claude.py:1005`, `grok.py:37`).
- `ai_usage_report.py`:
  - `PROVIDERS` is built from `PLUGINS` at import. `catalogue(config)` appends added instances
    via `ProviderDef.for_instance`.
  - The setup state for an older installed collector is applied positionally to
    `definitions[len(PROVIDERS):]`.
  - `capabilities` is computed from the installed collector's version.
  - `collector.plugin_errors` lists bundled load errors and appears only when there are some.
  - Presentation rules select on `definition.type`; data lookups use `definition.id`.
- The macOS app (`macos/Sources/AIUsageCore`) renders whatever providers the report lists:
  - Settings writes `providers.<id>.enabled` and `monthly_subscription_usd` through the bundled
    module's `configure` (`Control.swift:100`), and tells an added instance from a built-in by
    `type != id`.
  - `Accounts.swift` hardcodes the built-in ids and Claude, but only for "Add account…".
- Tests: `tests/test_plugin_architecture.py` (the module-import audit hook, load-error
  isolation, the `REPORT_PROVIDER_RULES` AST check), `tests/test_provider_instances.py`,
  `tests/test_install_lifecycle.py`, and `tests/parity/` (golden files plus declared
  `INTENTIONAL_CHANGES`). `ai_usage_fixtures.py` builds synthetic sandboxes. `make test`,
  `make check` and `make app-test` are the canonical targets.

### Relevant patterns
- Validate before writing: config mutations go through `update_config` (lock, validate merged,
  atomic write, reload).
- Failure isolation: one provider's failure becomes that provider's error row, and the others
  are unchanged.
- Additive report fields, with version capabilities so a newer app can face an older collector.
- Parity by declared differences against frozen golden files.

### Constraints discovered
- **Discovery is import-time.** The daemon's `PLUGINS` never changes while it runs. Per-poll
  discovery is new code in the collector, not a registry change.
- **The poll is serial and holds the state lock.** A plugin's timeout adds directly to the
  poll, and `once --progress` from the app waits behind it.
- **No output cap exists** in today's process helpers. Phase 3 needs a bounded reader.
- **The app and the collector ship separately.** A newer app's bundled report can discover a
  plugin that the installed 2.3.0 collector would never run.
- **The report positions added instances by index** (`definitions[len(PROVIDERS):]`).
  Appending external providers needs that code to select by kind instead.
- **`configure` and `validate_instances` hardcode `BUNDLED`.**
- **The reporter only renders the shapes it knows.** These were found in the owner's review
  and confirmed in the code:
  - Usage counts only the token metrics in `TOKEN_METRICS` (`ai_usage_report.py:47`); a
    `tokens` metric is dropped.
  - A `period_total` is one day's total, keyed by `period_start[:10]` and replaced by the
    latest reading (`aggregate_days`).
  - Event rows are summed by the local date of their `collected_at`.
  - `build_windows` picks the quota metric and the basis by built-in type. Any other type
    takes both metrics and the basis `remaining`, so a `used_percent` reading would show as
    percent remaining. External plugins therefore need a declared basis and a fixed row
    table.
- **`~/.ai-usage` is created 0700 by install**, but nothing checks the mode of anything a user
  adds under it.

### Assumptions (confirmed by this packet's approval)
- One provider per external plugin, its default instance. Added instances of an external
  plugin are out of scope (decision D4).
- The plugin folder is fixed at `~/.ai-usage/plugins/` (`$HOME`-relative, so tests use an
  isolated HOME), not a config path.
- External plugins get no status-line integration, no install-time integration, and no
  `path_roles` checks.

## Background

`docs/PLUGINS.md` § "External plugins: phase 3, not implemented" says nothing discovers, loads
or installs third-party code. The project plan's phase-3 exit asks for three things:
- a sample external plugin, in the tests only, that is collected and shown;
- a hung plugin that is killed and reported;
- a reinstall that leaves the folder alone.

## Goals
- A valid plugin folder appears in `doctor`, `plugin list` and the report with no repository
  change, rebuild or daemon restart. It is collected once the owner enables it.
- A faulty plugin is reported as its own provider's failure, and the poll completes within its
  bounds: a hang, a crash, a non-zero exit, too much output, a malformed response, or leftover
  child processes.
- Phase 2 behavior is unchanged. With no `~/.ai-usage/plugins/`, or with one that holds only
  disabled plugins, every built-in and added-instance output matches the phase-2 golden files,
  apart from the declared version and capability differences.
- Install, reinstall and uninstall never create, change or remove anything under
  `~/.ai-usage/plugins/`.

## Non-Goals
- A sandbox or any confinement of plugin code (see the trust model).
- Shipping, signing, downloading, updating or sharing plugins. There is no plugin registry and
  no plugin inside the app bundle or installer.
- Python-module plugins loaded from the folder. External plugins are executables only (2A).
- Added instances of an external plugin. Plugin-defined report presentation rules.
- An "Add plugin…" or plugin-management screen in the app.
- The All view (phase 4). The Proof Glass "Alexandria" label.
- Changing the owner's installed collector, or publishing a release.

## Architecture & Approach

### Folder and discovery
```text
~/.ai-usage/plugins/
  example/            # <name> = the plugin id
    plugin.json       # the manifest
    collect           # the executable the manifest names (any language, with a shebang)
```

- **When:** the collector scans the folder at the start of every poll, and `doctor`,
  `plugin list` and the report scan it whenever they run. So a new or changed plugin needs no
  daemon restart.
- **Order:** entries are scanned in byte order of their names. Entries starting with `.` are
  ignored.
- **What is never done:** discovery parses manifests and stats files. It never runs a plugin.
  The executable runs only in collection, and only for an enabled plugin.
- **Checks on each folder, before anything is trusted.** A failing check makes the plugin
  *invalid* with a named reason. It never makes the whole scan fail.
  - **Owner-only permissions, as the project plan requires (0700/0600):**
    - `~/.ai-usage/plugins/` and each plugin folder must be directories owned by the
      collector's uid, with no group or other permission bits (`mode & 0o077 == 0`, for
      example 0700).
    - `plugin.json` and the executable must be regular files owned by that uid, with no group
      or other bits (for example 0600 and 0700).
  - **Symlinks:** a symlink is allowed for the plugin folder, `plugin.json` or the executable.
    The link itself (`lstat`) must be owned by the uid, and its resolved target must pass the
    same owner-only check. The resolved executable must lie inside the resolved plugin folder.
  - **No repairs:** the collector refuses unsafe permissions; it never runs `chmod` or `chown`
    or repairs anything in a private folder. `doctor` and `plugin list` name the path and the
    mode it needs, so the owner fixes it.
  - **Folder name:** it must match `^[a-z][a-z0-9_-]{0,31}$`, equal the manifest's `id`, and not
    be a bundled id or an added instance's id.
  - **Manifest:** at most 64 KiB of UTF-8 JSON, valid against the schema below.

### Manifest (`plugin.json`, `manifest_version: 1`)
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

**Fields:**
- **`executable`:** a relative path inside the plugin folder. `..` and absolute paths are
  refused.
- **`timeout_seconds`:** an integer from 1 to 120, default 30. The owner's config may lower it
  with `providers.<id>.timeout_seconds`, never raise it.
- **`default_settings`:** a flat object of JSON scalars. It is sent to the plugin under the
  owner's `providers.<id>` values. It may not contain `enabled`, `timeout_seconds` or
  `monthly_subscription_usd`, so a manifest can never turn execution on (see Enablement).
- **`report`:** the collector derives the plugin's `ProviderDef` from it. The manifest picks
  one of the profiles the reporter already renders, never the reporter's internal
  fields, so every accepted response is displayable as intended.
  - **`usage`:** one of three values.
    - `daily_total`: per-day totals. This becomes `mode: polled`, `split: total_only` and
      `record_kind: period_total`, the same shape as Codex's daily usage.
    - `events`: individual input/output events. This becomes `mode: event`,
      `split: input_output` and `record_kind: event_total`, the same shape as Claude's
      transcripts.
    - `none`.
  - **`quota`:**
    - `used`: windows are reported as percent used. Claude Code and Grok do this.
    - `remaining`: windows are reported as percent remaining. Antigravity does this.
    - `none`: quota is shown as unsupported.
  - **`sources`:** one to four. Each has `id` (`^[a-z][a-z0-9_]{0,31}$`), `role` (`usage` or `quota`), `label`, `kind` and
    a unique `csv_source` matching `^[a-z][a-z0-9-]{0,63}$`. A `usage` other than `none` needs
    one `usage` source, and a `quota` other than `none` needs one `quota` source.
  - `quota_note` is optional display text.
- **Unknown keys are refused at every level.** A newer manifest fails clearly instead of being
  half-read.

### Protocol 1: one request, one response
- **Command line:** the collector runs `<plugin dir>/<executable> collect`.
- **Working directory:** the plugin folder.
- **Process group:** a new session, so the plugin gets its own process group.
- **Standard input:** one JSON object, then EOF:
  ```json
  {"protocol": 1, "collector_version": "2.4.0", "provider": "example",
   "collected_at": "2026-10-23T12:00:00Z", "include_history": false,
   "settings": {"region": "eu", "timeout_seconds": 30}, "state": {}}
  ```
- **Environment:** the plugin gets only these variables, never the collector's whole
  environment, so its other variables (for example `CLAUDE_CONFIG_DIR` or a token) don't leak
  in:
  - `PATH`, fixed from `discovery_path()`;
  - `AI_USAGE_PLUGIN_DIR` (the resolved plugin folder) and `AI_USAGE_PROTOCOL=1`, both fixed;
  - `HOME`, `USER`, `LOGNAME`, `TMPDIR` and `LANG`, copied from the collector's environment
    when set there. **These can differ between a shell run (`once`) and the launchd service**,
    which has its own `TMPDIR` and usually no `LANG`. A plugin must not depend on any other
    variable, and should treat a missing `LANG` or `TMPDIR` as normal.

  A plugin reads its own credentials from wherever it keeps them. The collector never passes
  credentials to it.
- **Standard output:** exactly one JSON object, optionally followed by whitespace:
  ```json
  {"protocol": 1,
   "rows": [
     {"category": "usage", "metric": "daily_tokens", "period_start": "2026-10-23",
      "value": 12345, "source": "example-usage"},
     {"category": "quota", "metric": "used_percent", "scope": "weekly", "value": 42.5,
      "window_seconds": 604800, "resets_at": "2026-10-27T07:00:00Z", "source": "example-limits"}
   ],
   "state": {"cursor": "abc"}}
  ```
- **Rows:** the collector writes `provider`, `collected_at`, the transaction columns, and
  `record_kind`, `unit` and `status` where the table fixes them. A row naming any of the
  stamped columns is refused. A row may contain only the keys its kind lists below.

  | Kind | Keys | Rules | Written as |
  | --- | --- | --- | --- |
  | Daily total (`usage: daily_total`) | `category: "usage"`, `metric: "daily_tokens"`, `period_start`, `value`, `source` | `period_start` is the Mac's **local** calendar date (`YYYY-MM-DD`) that the total covers, at most one day ahead of the collector's local date. `value` is a finite number ≥ 0. A later row for the same date replaces an earlier one; it is not added to it. | `record_kind: period_total`, `scope` = the date, `unit: tokens`, `status: ok`, `collected_at` = the poll time |
  | Event (`usage: events`) | `category: "usage"`, `metric` (`input_tokens`, `output_tokens`, `cache_read_input_tokens` or `cache_creation_input_tokens`), `at`, `value`, `source`, optional `scope` | `at` is the event's UTC time (ISO 8601 with `Z`), no later than the poll time plus 120 s. `value` is a finite number ≥ 0. Event rows are **summed**, so a plugin must report each event once, using its `state` as the checkpoint. A `scope` of `<event key>:<model>` adds the model to "models today". | `record_kind: event_total`, `collected_at` = `at`, `period_start` = the date of `at`, `unit: tokens`, `status: ok` |
  | Quota window (`quota: used` or `remaining`) | `category: "quota"`, `metric`, `scope`, `value`, `source`, optional `window_seconds` and `resets_at` | `metric` must be `used_percent` for `quota: used` and `remaining_percent` for `quota: remaining`. The other one is refused, so a percent used can never display as a percent remaining. `scope` (`^[a-z0-9][a-z0-9_-]{0,63}$`) names the window. `value` is 0–100. `window_seconds` is a positive integer (18000 shows as "5-hour", 604800 as "Weekly"). `resets_at` is a UTC time. The reading counts as measured at the poll time. | `record_kind: snapshot`, `unit: percent`, `status: ok`, `collected_at` = the poll time |
  | Not found | `category: "availability"`, `metric: "detected"`, `value: false`, `message` | The plugin found nothing to read on this Mac. | `status: missing` |
  | Source error | `category: "collection"`, `metric: "plugin_error"`, `source`, `message` | The plugin's own failure for one source. The report shows that source, and only that source, as failed, or as needing sign-in when the message says so. | `metric: <source id>_error` (each source's own error metric, so one source's error doesn't fail the others), `status: error` |

  - `source` must be a declared `csv_source` whose `role` matches the row: `usage` rows to the
    usage source, `quota` rows to the quota source.
  - `message` is at most 500 characters and has no control characters.
  - At most 2,000 rows.
- **`state`:** optional. A JSON object of at most 256 KiB once serialized. It is stored as the
  plugin's checkpoint in `state["instances"][<id>]`, the namespace phase 2 gives every
  non-bundled provider id, and sent back on the next poll. If the key is absent, the state is
  kept as it was.

### What is stored, and what isn't (privacy)
- **Stored:**
  - the rows of a valid response go to `usage.csv`, every string field included (`message`,
    `scope`), and the report and app show them;
  - a valid `state` goes to `state.json`.

  These are plain local files, readable by the owner and backed up with them. **A plugin must
  keep credentials, tokens and other secrets out of its rows, messages, scopes and state.** The
  collector does not inspect them for secrets.
- **Never stored:**
  - **stderr:** it is drained and discarded as it arrives. The collector counts the bytes and
    keeps none of them, so it never reaches the CSV, the report, the app, state or
    `collector.log`.
  - **A rejected response:** none of its rows, state or values are stored or echoed.
- **Diagnostics name things, never values.** Failure messages in the CSV, the report,
  `collector.log`, `doctor` and `plugin list` use fixed reason texts:
  - a field path (`rows[4].status`) or a manifest key (`report.usage`);
  - a JSON position;
  - an exit status, a signal number, or an `errno` name (`ENOENT`).

  They never include a rejected value, raw output, or a raw exception message, because any of
  those could hold whatever the plugin printed.
- `collector.log` records only the plugin id, the reason class, the exit status or signal,
  the duration, and the stderr byte count. To debug, the owner runs the plugin by hand, or the
  plugin writes its own log file.

### Bounds, cleanup and failure (all-or-nothing per plugin)
- **Output limits:**
  - **stdout** is read with a 1 MiB cap. Passing it is a failure: the collector stops
    reading, kills the process group, and doesn't buffer more.
  - **stderr** is read in fixed chunks and thrown away, so a chatty plugin can't block on a
    full pipe and memory stays fixed however much it writes. stderr never fails a run by
    itself. A plugin that writes stderr without end, and never exits, hits the deadline.
- **Timeout:** one deadline covers the whole run, from start to the pipes closing, including
  writing stdin and reading output. At the deadline the collector kills the process group and
  closes its pipe ends.
- **Cleanup:**
  - **After the plugin exits:** the collector waits at most 1 s for its stdout and stderr to
    close. A child that inherited them and keeps them open can't hold the poll. Then the
    collector calls `terminate_provider_process` on the process group (SIGTERM, then SIGKILL
    after 1 s, with Darwin's EPERM handled) and closes its own pipe ends.
  - **Always:** this happens after every run, whatever its result, so a background child left
    in the group is gone before the next provider starts.
  - **Not covered:** a process that leaves the group (`setsid`, a launchd job) can survive.
    Because the collector closes its pipe ends, it can't hold the poll. The trust model says so.
- **Success:** a run succeeds only if all of these hold:
  - the plugin's own exit status is 0;
  - it stayed within the time and output limits;
  - stdout is exactly one valid protocol-1 response.

  Killing leftover children during cleanup doesn't turn a success into a failure.
- **Anything else is a failure, and none of the response is used:**
  - **Rows:** that poll records exactly one `collection/unexpected_error` row for the provider,
    with `source: "plugin"` and a fixed reason text:
    - `timed out after 30s`;
    - `exited with status 3`;
    - `was stopped by signal 9`;
    - `wrote more than 1 MiB to stdout`;
    - `returned invalid JSON at line 1 column 5`;
    - `returned an invalid response at rows[4].status`;
    - `could not be started (ENOENT)`.
  - **State:** the plugin's state stays as it was, so its checkpoints don't advance.
  - **Other providers:** their rows and state are unchanged.

### Enablement, identity and lifecycle
- **Identity:** a plugin's id is its folder name, which is also its type. A built-in is the
  same. The report and `doctor --json` mark it with the additive field `"external": true`.
  Built-in and added-instance output gains nothing.
- **Explicit enablement (decision D1), on every path:**
  - **Rule:** an external plugin runs only when `config.json` has
    `providers.<id>.enabled: true`. Nothing else turns it on: not discovery, not a manifest
    default, and not a missing key. For external ids, `enabled` defaults to false.
  - **Every mutation that sets it true checks the same two things**, refusing and changing
    nothing otherwise. These paths are:
    - `collector.py plugin enable <id>`;
    - `configure --set providers.<id>.enabled=true`;
    - the app's Settings toggle, which runs that `configure`.

    The checks:
    - the plugin is discovered and valid now;
    - the LaunchAgent's collector is 2.4.0 or newer, read from its `VERSION` line without
      running it.
  - **Disabling is never blocked.** `plugin disable <id>` and
    `configure --set providers.<id>.enabled=false` work for any discovered id, and for any id
    already in `providers`, even when its folder is missing or invalid. No version check
    applies.
  - **`configure` scope:** it accepts `providers.<id>.{enabled,monthly_subscription_usd,timeout_seconds}`
    for an external id. `timeout_seconds` is CLI only and can't exceed the manifest's.
- **Rows each poll, the same as for built-ins:**

  | Config for the id | Folder | Rows each poll |
  | --- | --- | --- |
  | No `providers.<id>` entry | Discovered (valid or invalid) | None. The report lists a valid one as `disabled`, "Not turned on", with the enable command. |
  | `providers.<id>` present, `enabled` not true | Valid, invalid or missing | The disabled marker row (`availability/enabled`, `status: disabled`), as for a disabled built-in. |
  | `enabled: true` | Valid | Its collected rows, or one failure row. |
  | `enabled: true` | Invalid | One `unexpected_error` row: `plugin is invalid: <reason>`. |
  | `enabled: true` | Missing | One `unexpected_error` row: `plugin folder not found`. |
- **Order:** collection, CSV, `doctor` and `--progress` take the built-ins, then added
  instances (config order), then external ids (byte order). The report uses the same order
  after the built-ins' `report_order`. Appending them last keeps every phase-2 position.
- **Invalid plugins:** an invalid folder is listed, with its reason, in:
  - `doctor`;
  - `plugin list`;
  - the report's `collector.plugin_errors`, keyed `plugins/<folder name>`.

  It is never run.
- **Lifecycle:**
  - **Removed folder:** see the table. Its config, state and history are kept, and restoring
    the folder resumes it. Removing a plugin's data isn't supported, as in phase 2.
  - **Upgrade:** replace the files. The id, and therefore the state and history, carry over.
  - **Rename:** a new id, so a new provider.
- **Install and uninstall:** they never create, chmod, move or delete `~/.ai-usage/plugins/` or
  anything in it. It isn't in `install-ownership.json`. An invalid external plugin never blocks
  install. The app bundle and the installer carry no plugin. `scripts/check_app_bundle.py`
  fails if a `plugins/` folder appears in the bundle.

### Version compatibility (decision D3)
- `VERSION` becomes `2.4.0`, with the report capability `external_plugins`.
- Enabling is version-checked on every path (see above).
- With an older installed collector, the app's bundled report still lists discovered plugins,
  as `not_configured` with a "reinstall the collector to collect it" note. The app hides their
  toggle until the capability is true.
- Hand-editing `config.json` is the owner's own act and isn't blocked. An older collector
  ignores external ids, and the report says so with the note above.

### Files
| File | Change |
| --- | --- |
| `ai_usage_plugins/external.py` (new) | Discovery, the permission checks, manifest validation, the bounded runner, response validation and row stamping. Standard library only; imports `.base` and `.support`. |
| `ai_usage_plugins/base.py` | `ProviderDef` gains an optional `quota_basis` (`None` for the built-ins, whose rules are unchanged). |
| `ai_usage_plugins/__init__.py` | `MODULES` gains `external`. `BUNDLED` is unchanged. |
| `ai_usage_service.py` | Per-poll discovery into `accounts()`; the `plugin list/enable/disable` CLI; `configure` and validation for external ids, with the enable checks; `VERSION` 2.4.0; `doctor`. |
| `ai_usage_report.py` | A catalogue entry per discovered or configured external id, `external: true`, the capability, the quota basis for external types, select-by-kind setup notes, `plugin_errors` entries. |
| `macos/Sources/AIUsageCore` | Decode `external`; hide its toggle without the capability. No new screen. |
| `tests/test_external_plugins.py` (new) | Synthetic plugins generated into an isolated HOME at test time, with the shebang set to `sys.executable`: `example` (daily totals and used-percent quota), `events` (input/output events and remaining-percent quota), `hang`, `crash`, `chatty`, `stderr-flood`, `garbage`, `forker`, `pipe-holder`, `stateful`, `secret-printer`, `badmanifest`, `loose-perms`. |
| `docs/PLUGINS.md`, `docs/CLI.md`, `docs/SPEC.yml` | The contract, the trust model, the privacy rules, the CLI. |

## Acceptance Criteria

All of them are shown with synthetic fixtures in an isolated HOME. No test reads the owner's
real `~/.ai-usage`, any real plugin, credential or log. App tests and renders run against a
sandbox and never touch the owner's running app or its config.

- **AC-1 Discovery and manifest.**
  - A valid `~/.ai-usage/plugins/example/` appears in `doctor --json`, `plugin list` and the
    report, with `id = type = "example"` and `external: true`, and its name and sources from the
    manifest. That takes no daemon restart: a running `run_daemon` sandbox picks it up on its next
    poll.
  - Discovery runs no plugin. A test with a canary executable proves that `doctor`,
    `plugin list` and the report never start it.
  - Each of these makes that plugin invalid, with its reason in `plugin_errors` and `doctor`.
    The other plugins and the built-ins are unaffected.
    - invalid JSON;
    - an unknown key, at the top level or nested;
    - `manifest_version` ≠ 1 or `protocol` ≠ 1;
    - an id that doesn't match its folder;
    - a bundled id or an added instance's id;
    - an `executable` outside the folder, including through `..` or a symlink;
    - a missing or non-executable file;
    - a manifest over 64 KiB;
    - `timeout_seconds` outside 1–120;
    - an unknown `usage` or `quota` profile;
    - a missing source for a declared profile;
    - a duplicate `csv_source`;
    - `enabled`, `timeout_seconds` or `monthly_subscription_usd` in `default_settings`.
  - Each reason names the field, never its value.
- **AC-2 Owner-only permissions.**
  - A plugin is invalid, with a reason naming the path and the mode it needs, when:
    - `plugins/` or the plugin folder has any group or other permission bit (0750, 0705 and
      0755 each tested);
    - `plugin.json` or the executable has one (0640, 0604 and 0755 each tested).
  - A symlinked plugin folder, manifest or executable is accepted when the link and its
    resolved target both pass. It is refused when the target is group- or world-accessible.
  - A refused plugin's files keep their exact modes and contents: the collector repairs
    nothing (checked by a before-and-after `lstat` and hash of every path).
  - The foreign-owner check, on the link and on the target, is unit-tested with an injected
    `stat`, since tests can't `chown`.
- **AC-3 Enablement and identity.**
  - A discovered plugin with no config entry writes no rows, is reported `disabled` ("Not
    turned on"), and is never run.
  - A manifest can't enable it: `enabled` in `default_settings` makes the manifest invalid.
  - **Enabling:** `plugin enable example` writes `providers.example.enabled: true` under the
    config lock, atomically, keeping other keys and the file's mode, and the next poll
    collects it.
  - **Enable refusals:** `plugin enable` and `configure --set providers.<id>.enabled=true`
    (which the app runs) each refuse, changing nothing (file bytes compared):
    - an unknown id;
    - an invalid plugin;
    - a missing folder;
    - an installed collector older than 2.4.0.
  - **Disabling:** `plugin disable` and `configure --set providers.<id>.enabled=false`
    succeed for a valid, an invalid and a missing plugin that has a `providers.<id>` entry,
    with an older installed collector too. Each case then writes the disabled marker row on
    every poll, and keeps the state and history. Re-enabling resumes from the saved state.
  - **The rows table:** each row of the table in "Rows each poll" is tested.
  - **`configure` scope:** it accepts the three listed keys for an external id. It refuses a
    `timeout_seconds` above the manifest's, and any other key.
- **AC-4 Protocol, collection and report values (end to end).**
  - `example` receives exactly the documented request:
    - argv `collect`, with the plugin folder as its working directory;
    - the fixed `PATH` and `AI_USAGE_*` variables, plus only the copied variables that are
      set.

    A test sets `CLAUDE_CONFIG_DIR` and a dummy secret variable in the collector's environment
    and shows neither reaches the plugin.
  - Its rows are written with `provider = "example"`, the stamped columns from the row table,
    and the poll's transaction id.
  - Its returned `state` is stored in `state["instances"]["example"]` and sent back on the next
    poll.
  - **Daily total, end to end:** after a real `once` in the sandbox, the report's
    `providers[example]` matches the plugin's values:
    - for a total of 12,345 for today, `today.total` is 12345, and that day's record in `days`
      has `total` 12345 and state `partial`;
    - for 9,000 for yesterday, yesterday's record has `total` 9000;
    - a later poll reporting 13,000 for today replaces the earlier total; it isn't added to it.
  - **Used-percent quota, end to end:** a reading of `used_percent` 42.5 on a 604800-second
    window gives a window labelled "Weekly limit", with `basis: "used"`, `percent` 42.5,
    `resets_at` as sent, and `status` `current`. A reading of 95 gives `limit: "low"`.
  - **Events and remaining-percent quota, end to end:**
    - `events` reports input 100 / output 50 and then input 10 / output 5, across two polls
      with its state checkpoint;
    - `today.input` is 110 and `today.output` 55, with no event counted twice;
    - a `remaining_percent` reading of 30 gives a window with `basis: "remaining"` and
      `percent` 30.
  - **No cross-basis display:** a `quota: used` plugin returning `remaining_percent`, or the
    reverse, is refused as invalid. No window for it appears in the report.
  - **Order and cost:** the order follows the order rule. Monthly subscription cost works as
    for a built-in.
- **AC-5 Timeouts.**
  - `hang` (sleeps forever) with `timeout_seconds: 2` is killed. The poll finishes within 2 s
    plus 3 s of cleanup slack.
  - Its provider gets exactly one `unexpected_error` row, `timed out after 2s`.
  - The rows from the providers before and after it equal a run without it.
  - A plugin that closes stdout but keeps running also hits the deadline.
  - `stderr-flood` writes stderr without end and never exits. It hits the deadline the same
    way.
  - `providers.<id>.timeout_seconds` lowers the deadline and can't raise it.
- **AC-6 Output limits and privacy.**
  - **stdout cap:** `chatty` writes 5 MiB to stdout. The collector stops reading at 1 MiB,
    kills it, and records `wrote more than 1 MiB to stdout`. A test asserts the buffered stdout
    never exceeds the cap plus one read chunk.
  - **stderr is discarded:**
    - a plugin writing 1 MiB to stderr and a valid response succeeds, and its rows are kept;
    - `collector.log` records the stderr byte count (1048576);
    - during `stderr-flood`, the collector's retained stderr is never more than one read chunk.
  - **No echo of secrets:** `secret-printer` puts a credential-shaped marker
    (`AIU-TEST-SECRET-<random>`) in four places. Each is run as its own case, all failing:
    - stderr;
    - a rejected row value;
    - a rejected manifest value;
    - a start failure's exception text.

    In no case does the marker appear in `collector.log`, the CSV, `state.json`, the report,
    `doctor` or `plugin list` (byte search).
  - **Valid data is stored:** a valid row's `message` and a valid `state` are stored, as the
    privacy section says.
  - **Size refusals:** a response over 2,000 rows, or with a `state` over 256 KiB, is refused
    as invalid.
- **AC-7 Process cleanup.**
  - `forker` starts a background child in its process group that would outlive it, then
    returns a valid response.
  - After the poll no process from that group remains. Its rows are kept, because cleanup
    doesn't turn success into failure.
  - `pipe-holder` writes a valid response and exits 0, leaving a child that holds the
    inherited stdout and stderr open and sleeps for 60 s. The poll completes within 1 s of the
    parent's exit plus 3 s of cleanup slack, the response is used, and the child is gone.
  - The same holds after a timeout and after an output-limit kill.
  - Darwin's EPERM from `killpg` is covered by the existing `terminate_provider_process` tests.
- **AC-8 Malformed responses and crashes.**
  - **Cases:**
    - empty stdout;
    - non-JSON output;
    - two JSON objects;
    - a JSON array;
    - trailing non-whitespace;
    - `protocol` ≠ 1;
    - `rows` not a list;
    - a row with any of:
      - an unknown key, or a stamped column (`provider`, `record_kind`);
      - an undeclared `source`, or a source of the wrong role;
      - a metric outside its kind's list;
      - a reserved or disallowed `status`;
      - an over-long or control-character `message`;
      - a non-finite or negative number, or an object `value`;
      - a `period_start` that isn't a date or is too far ahead;
      - an event without `at`, or with `at` in the future;
    - `state` not an object;
    - a non-zero exit with a valid response;
    - death by a signal;
    - a missing interpreter (start failure).
  - **Each case:**
    - records exactly one `unexpected_error` row with its fixed reason text (a field path, not
      a value);
    - keeps none of the response's rows;
    - leaves the plugin's state unchanged (`stateful` shows the checkpoint doesn't advance);
    - leaves the other providers unaffected.
- **AC-9 Lifecycle and install.**
  - **Removed folder:** while it is enabled, it is reported as an error and its config, state
    and CSV history are kept. Restoring the folder resumes it.
  - **Upgrade:** replacing the executable keeps the state.
  - **Install, reinstall and uninstall:** run in the sandbox with a plugins folder that holds a
    valid and an invalid plugin. Afterwards the folder's tree, modes and contents are
    byte-identical, it isn't in `install-ownership.json`, and install isn't refused.
  - **App bundle:** `make app-build` fails if a `plugins/` folder is bundled.
- **AC-10 Phase-2 behavior preserved.**
  - The phase-1 golden files stay unchanged. Phase 3 adds only two declared
    `INTENTIONAL_CHANGES`:
    - `VERSION` becomes `2.4.0` (`collector.version` and `installed_version`);
    - `capabilities.external_plugins` is `true`.
  - Every parity scenario matches with that list in three set-ups:
    - no `plugins/` folder;
    - an empty one;
    - one holding only unconfigured and invalid plugins. Here every built-in and
      added-instance entry, row and state key matches. The plugins' own report entries and
      `plugin_errors` are asserted on their own.
  - A test fails if the list grows.
  - The phase-2 carry-forward runs from `357bcee`, then from this branch, in one sandbox.
  - All of `tests/test_provider_instances.py` passes unchanged.
  - The built-ins' quota windows are unchanged: `quota_basis` is `None` for every built-in,
    and the parity reports match.
- **AC-11 Version compatibility and app.**
  - With an installed 2.3.0 collector, a discovered plugin is reported `not_configured` with the
    reinstall note. Enabling is refused on every path.
  - The app decodes today's fixtures, which have no `external`, unchanged. It shows an external
    provider like a built-in. It shows its toggle only when `capabilities.external_plugins` is
    true.
  - An XCTest and synthetic snapshot renders of an external provider: the overview card, its
    detail with daily usage and a used-percent window, and Settings with its toggle. They are
    rendered from a synthetic fixture by the offscreen renderer, which doesn't touch the
    owner's running app.
  - The owner gives visual acceptance at PR review.
- **AC-12 Docs, validation and privacy.**
  - `docs/PLUGINS.md` documents:
    - the manifest, the protocol and its row table, the bounds and the lifecycle;
    - the privacy section: what is stored, that plugins must keep secrets out of rows and
      state, that stderr is discarded, and that diagnostics never echo values;
    - the trust model's "not a security sandbox" statement, word for word.

    `docs/CLI.md` and the `plugin` help text say the same.
  - `make check` and `make app-test` pass, and the fresh required CI is green.
  - The phase-gate audit is run.
  - The diff holds no plugin other than the synthetic fixtures, and no real hostnames,
    credentials, work-tool names or logs.
- **AC-13 Owner's work laptop (operational, pending until performed).** After the merge and an
  owner-approved reinstall, the owner installs their private plugin on the work laptop, enables
  it, and sees it collected and shown. The owner runs this check and reports only pass or fail.
  Nothing from that laptop comes back to this repository or conversation. It isn't a
  precondition for the implementation PR.

## Decisions (owner, 2026-10-24)

The owner approved the recommended answers to D1–D4 and AC-1–AC-13, with five corrections.
This revision makes them:
1. **Reporter compatibility:**
   - the manifest picks a usage profile and a quota basis;
   - rows follow one table with fixed metrics, kinds and time semantics;
   - a percent used can't display as a percent remaining;
   - AC-4 checks the reported values end to end.
2. **Narrowed privacy promise:**
   - stderr is discarded;
   - diagnostics never echo values or raw exceptions;
   - valid rows and state are documented as stored, so plugins must keep credentials out of
     them.
3. **Owner-only permissions:** the plan's 0700/0600, including symlink targets; unsafe
   permissions are refused and never repaired.
4. **Explicit enablement on every mutation path:**
   - the enable checks run in `plugin enable`, `configure` and the app;
   - manifest defaults can't enable a plugin;
   - disabling works for missing and invalid plugins;
   - one disabled-marker rule covers every case.
5. **Environment and cleanup:**
   - the environment section no longer claims a shell run and launchd are identical;
   - a test covers a child holding the inherited pipes;
   - endless stderr is bounded by the deadline and a fixed buffer.

| # | Decision |
| --- | --- |
| D1 | **A.** A discovered plugin is off until it is explicitly enabled. |
| D2 | **A.** Unsafe permissions are refused, and never repaired automatically. |
| D3 | **A.** `VERSION` 2.4.0 with the `external_plugins` capability. That doesn't authorize a release or changing the installed collector. |
| D4 | **A.** Added instances of an external plugin are out of scope. |

Merging the implementation, reinstalling and releasing remain separate owner decisions.
Settled decisions 1A–7B and the phase-2 decisions aren't reopened.

## Open Questions
- None.
