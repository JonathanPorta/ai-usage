# PRD: Private Plugins From a Local Folder (Phase 3)

Status: **draft for the owner's single approval decision.** This one packet holds the codebase
analysis, the PRD, the acceptance criteria and the open decisions, at the owner's request
(2026-10-23), so there is no separate analysis-only round. Nothing is implemented until the
owner approves the whole packet.

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
- the ownership and permission checks catch a plugin folder that another user, or a world- or
  group-writable path, could change.

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
  - **Ownership and mode:** `~/.ai-usage/plugins/`, the plugin folder, `plugin.json` and the
    executable must be owned by the collector's uid. None may be group- or world-writable.
    The check uses the resolved paths, so a symlinked plugin folder is checked at its target.
  - **Executable location:** the executable must be a regular, executable file whose resolved
    path is inside the resolved plugin folder.
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
    "name": "Example", "product": "Example Tool",
    "mode": "polled", "split": "total_only", "record_kind": "period_total", "quota": "polled",
    "reported_note": null, "quota_note": null,
    "sources": [
      {"id": "usage", "role": "both", "label": "Usage", "kind": "Example API, read-only",
       "csv_sources": ["example-api"], "needs_rows": true}
    ]
  }
}
```

**Fields:**
- **`executable`:** a relative path inside the plugin folder; `..` and absolute paths are
  refused.
- **`timeout_seconds`:** an integer from 1 to 120, default 30. The owner's config may lower it
  with `providers.<id>.timeout_seconds`, never raise it.
- **`default_settings`:** a flat object of JSON scalars. It is merged under
  `providers.<id>` the same way as a built-in's settings.
- **`report`:** maps one to one onto `ProviderDef` and `SourceDef`, with the same allowed
  values as the built-ins.
  - `mode` is `polled` or `event`.
  - `split` is `total_only` or `input_output`.
  - `record_kind` is `period_total`, `interval_total`, `event_total` or `delta`.
  - `quota` is `polled`, `event` or `unsupported`.
  - A source's `role` is `usage`, `quota` or `both`.
  - `csv_sources` values match `^[a-z][a-z0-9_-]{0,63}$`, and no two sources share one.
  - `error_metrics` is optional per source. The collector always adds `unexpected_error`, so
    the failure rows below count against the provider.
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
   "settings": {"region": "eu", "enabled": true, "timeout_seconds": 30, "monthly_subscription_usd": null},
   "state": {}}
  ```
- **Environment:** a fixed minimal environment, not the collector's own:
  - `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `LANG`;
  - `PATH` from `discovery_path()`;
  - `AI_USAGE_PLUGIN_DIR` (the resolved plugin folder) and `AI_USAGE_PROTOCOL=1`.

  So a run from a shell and a run from launchd see the same thing, and the collector's own
  variables (for example `CLAUDE_CONFIG_DIR`) don't leak in. A plugin reads its own credentials
  from wherever it keeps them. The collector never passes, stores or logs credentials.
- **Standard output:** exactly one JSON object, optionally followed by whitespace:
  ```json
  {"protocol": 1,
   "rows": [{"category": "usage", "metric": "tokens", "record_kind": "period_total",
             "scope": "", "period_start": "2026-10-01", "period_end": "2026-10-31",
             "value": 12345, "unit": "tokens", "source": "example-api", "status": "ok", "message": ""}],
   "state": {"cursor": "abc"}}
  ```
- **Rows:**
  - Allowed keys: `category`, `metric`, `record_kind`, `scope`, `period_start`, `period_end`,
    `value`, `unit`, `window_seconds`, `resets_at`, `source`, `status` and `message`. Missing
    ones default as in `metric_row()`.
  - `provider`, `collected_at` and the transaction columns are refused. The collector stamps
    them.
  - `category` and `metric` match `^[a-z][a-z0-9_]{0,63}$`.
  - `source` must be one of the manifest's `csv_sources`.
  - `status` is one of `ok`, `error`, `missing`, `partial`, `stale`, `incomplete`,
    `unsupported_format`. `disabled` is reserved for the collector.
  - `value` is a finite number, a boolean, a string of at most 256 characters, or null.
  - The other strings are at most 256 characters; `message` is at most 1,000.
  - At most 2,000 rows.
- **`state`:** optional. A JSON object of at most 256 KiB once serialized. It is stored as the
  plugin's checkpoint in `state["instances"][<id>]`, the namespace phase 2 gives every
  non-bundled provider id, and sent back on the next poll. If the key is absent, the state is
  kept as it was.

### Bounds, cleanup and failure (all-or-nothing per plugin)
- **Output limits:**
  - **stdout** is read with a 1 MiB cap. Passing it is a failure: the collector stops
    reading, kills the process group, and doesn't buffer more.
  - **stderr** is drained and discarded as it arrives, so a chatty plugin can't block on a
    full pipe. Any amount of stderr is allowed and never fails a run. The collector keeps only
    a running byte count, never the bytes.
- **Timeout:** the deadline covers the whole run, from start to exit, including writing
  stdin and reading output. At the deadline the collector kills the process group.
- **Cleanup:** after every run, whatever its result, the collector calls
  `terminate_provider_process` on the plugin's process group. That sends SIGTERM, then SIGKILL
  after 1 s, and handles Darwin's EPERM. A background child left in the group is gone before the
  next provider starts. A process that leaves the group itself (`setsid`, a launchd job) is
  outside this guarantee. The trust model says so.
- **Success:** a run succeeds only if all of these hold:
  - exit status 0;
  - within the time and output limits;
  - stdout is exactly one valid response for `protocol: 1`.
- **Anything else is a failure, and none of the response is used:**
  - **Rows:** that poll records exactly one `collection/unexpected_error` row for the provider,
    with `source: "plugin"` and a message naming the reason class:
    - `timed out after 30s`;
    - `exited with status 3`;
    - `was stopped by signal 9`;
    - `wrote more than 1 MiB to stdout`;
    - `returned invalid JSON at line 1 column 5`;
    - `returned an invalid response: rows[4].status "great" is not allowed`;
    - `could not be started: <OSError>`.
  - **State:** the plugin's state stays as it was, so its checkpoints don't advance.
  - **Other providers:** their rows and state are unchanged.
- **Diagnostics:**
  - **No stderr content is stored anywhere:** not in the CSV, the report, the app, state or
    `collector.log`. That keeps the promise that the collector never stores or logs
    credentials, even when a plugin prints one by mistake.
  - `collector.log` records only the plugin id, the reason class, the exit status or signal,
    the duration, and the stderr byte count.
  - To debug, the owner runs the plugin by hand, or the plugin writes its own log file.
  - A response's own `status: "error"` rows are the plugin's chosen, user-facing messages.

### Enablement, identity and lifecycle
- **Identity:** a plugin's id is its folder name, which is also its type. A built-in is the
  same. The report and `doctor --json` mark it with the additive field `"external": true`.
  Built-in and added-instance output gains nothing.
- **Off until enabled (decision D1):**
  - A discovered plugin with no `providers.<id>` entry is reported with setup `disabled` and
    a note naming the enable command. It isn't run.
  - `collector.py plugin enable <id>` and `disable <id>`, and the app's existing Settings
    toggle, write `providers.<id>.enabled` through `configure`'s locked path.
  - `configure` accepts `providers.<id>.{enabled,monthly_subscription_usd,timeout_seconds}`
    for a discovered plugin id. `timeout_seconds` is CLI only and can't exceed the manifest's.
- **Order:** collection, CSV, `doctor` and `--progress` take the built-ins, then added
  instances (config order), then enabled external plugins (byte order of their ids). The
  report uses the same order after the built-ins' `report_order`. Appending them last keeps every
  phase-2 position.
- **Invalid plugins:**
  - An invalid folder is listed in `doctor`, `plugin list`, and the report's
    `collector.plugin_errors` (keyed `plugins/<folder name>`) with its reason. It is never run.
  - If it is enabled in the config, each poll also writes its `unexpected_error` row
    (`plugin is invalid: <reason>`), so a broken upgrade is visible on its provider.
  - A disabled one writes no rows.
- **Lifecycle:**
  - **Removed folder:** while it is enabled, it is reported as an error (`plugin folder not
    found`). Its config, state and history are kept, and restoring the folder resumes it.
    Removing a plugin's data isn't supported, as in phase 2.
  - **Upgrade:** replace the files. The id, and therefore the state and history, carry over.
  - **Rename:** a new id, so a new provider.
- **Install and uninstall:** they never create, chmod, move or delete `~/.ai-usage/plugins/` or
  anything in it. It isn't in `install-ownership.json`. An invalid external plugin never blocks
  install. The app bundle and the installer carry no plugin. `scripts/check_app_bundle.py`
  fails if a `plugins/` folder appears in the bundle.

### Version compatibility (decision D3)
- `VERSION` becomes `2.4.0`, with the report capability `external_plugins`.
- `plugin enable` refuses unless the LaunchAgent's collector is 2.4.0 or newer. It reads the
  version line without running the collector, the same as phase 2.
- With an older installed collector, the app's bundled report still lists discovered plugins,
  as `not_configured` with a "reinstall the collector to collect it" note. The app hides their
  toggle until the capability is true.

### Files
| File | Change |
| --- | --- |
| `ai_usage_plugins/external.py` (new) | Discovery, manifest validation, the bounded runner, response validation. Standard library only; imports `.base` and `.support`. |
| `ai_usage_plugins/support.py` | A bounded pipe reader. `terminate_provider_process` is reused unchanged. |
| `ai_usage_plugins/__init__.py` | `MODULES` gains `external`. `BUNDLED` is unchanged. |
| `ai_usage_service.py` | Per-poll discovery into `accounts()`, the `plugin list/enable/disable` CLI, `configure` and validation for external ids, `VERSION` 2.4.0, `doctor`. |
| `ai_usage_report.py` | A catalogue entry per discovered plugin, `external: true`, the capability, select-by-kind setup notes, `plugin_errors` entries. |
| `macos/Sources/AIUsageCore` | Decode `external`; hide its toggle without the capability. No new screen. |
| `tests/test_external_plugins.py`, `tests/fixtures/external_plugins/` (new) | Synthetic plugins: `example` (valid), `hang`, `crash`, `chatty`, `garbage`, `forker`, `stateful`, `badmanifest`, `loose-perms`. Each is generated into an isolated HOME at test time, with its shebang set to `sys.executable`. |
| `docs/PLUGINS.md`, `docs/CLI.md`, `docs/SPEC.yml` | The contract, the trust model, the CLI. |

## Acceptance Criteria

All of them are shown with synthetic fixtures in an isolated HOME. No test reads the owner's
real `~/.ai-usage`, any real plugin, credential or log.

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
    - an unknown report enum value;
    - a duplicate `csv_sources` value.
- **AC-2 Ownership and permissions.**
  - A plugin is invalid, named as a permission problem, when its folder, manifest or executable,
    or the `plugins/` folder, is group- or world-writable (each case tested).
  - A symlinked plugin folder is accepted when its resolved target passes the same checks.
  - The foreign-owner check is unit-tested with an injected `stat`, since tests can't
    `chown`.
- **AC-3 Enablement and identity.**
  - A discovered plugin with no config entry is reported `disabled` and is never run.
  - `plugin enable example` writes `providers.example.enabled: true` under the config lock,
    atomically, keeping other keys and the file's mode, and the next poll collects it.
  - `plugin enable` refuses, changing nothing:
    - an unknown or invalid plugin;
    - an installed collector older than 2.4.0.
  - `plugin disable` writes the disabled marker row on the next poll, and keeps the state and
    history. Re-enabling resumes from the saved state.
  - `configure` accepts the three listed keys for a discovered id. It refuses a
    `timeout_seconds` above the manifest's, and any other key.
- **AC-4 Protocol and collection.**
  - `example` receives exactly the documented request: argv `collect`, the plugin folder as its
    working directory, the documented environment keys and no others. A test sets
    `CLAUDE_CONFIG_DIR` and a dummy secret variable in the collector's environment and shows
    neither reaches the plugin.
  - Its rows are written with `provider = "example"` and the poll's `collected_at` and
    transaction id.
  - Its returned `state` is stored in `state["instances"]["example"]` and sent back on the next
    poll.
  - The order follows the order rule. Monthly subscription cost works as for a built-in.
- **AC-5 Timeouts.**
  - `hang` (sleeps forever) with `timeout_seconds: 2` is killed. The poll finishes within 2 s
    plus 3 s of cleanup slack.
  - Its provider gets exactly one `unexpected_error` row, `timed out after 2s`.
  - The rows from the providers before and after it equal a run without it.
  - A plugin that closes stdout but keeps running also hits the deadline.
  - `providers.<id>.timeout_seconds` lowers the deadline and can't raise it.
- **AC-6 Output limits.**
  - `chatty` writes 5 MiB to stdout. The collector stops reading at 1 MiB, kills it, and
    records `wrote more than 1 MiB to stdout`. Its peak memory stays bounded: a test asserts
    the buffered bytes never exceed the cap plus one read chunk.
  - A plugin writing 1 MiB to stderr and a valid response succeeds. Its rows are kept, and
    `collector.log` records its stderr byte count (1048576).
  - A plugin printing a credential-shaped marker (`AIU-TEST-SECRET-<random>`) to stderr is
    run in two cases: once with a valid response and once failing. In neither case does the
    marker appear in `collector.log`, the CSV, `state.json` or the report (byte search).
  - A response over 2,000 rows, or with a `state` over 256 KiB, is refused as invalid.
- **AC-7 Process cleanup.**
  - `forker` starts a background child in its process group that would outlive it, then
    returns a valid response.
  - After the poll no process from that group remains. Its rows are kept, because cleanup
    doesn't turn success into failure.
  - The same holds after a timeout and after an output-limit kill.
  - Darwin's EPERM from `killpg` is covered by the existing `terminate_provider_process` tests.
- **AC-8 Malformed responses and crashes.** Each of these records exactly one
  `unexpected_error` row with its reason class, keeps none of the response's rows, and leaves the
  plugin's state unchanged (`stateful` shows the checkpoint doesn't advance). The other
  providers are unaffected.
  - empty stdout;
  - non-JSON output;
  - two JSON objects;
  - a JSON array;
  - trailing non-whitespace;
  - `protocol` ≠ 1;
  - `rows` not a list;
  - a row with an unknown key, a `provider` key, an undeclared `source`, a disallowed or
    reserved `status`, an over-long string, a non-finite number, or an object `value`;
  - `state` not an object;
  - a non-zero exit with a valid response;
  - death by a signal;
  - a missing interpreter (start failure).
- **AC-9 Lifecycle and install.**
  - **Removed folder:** while it is enabled, it is reported as an error and its config, state
    and CSV history are kept. Restoring the folder resumes it.
  - **Upgrade:** replacing the executable keeps the state.
  - **Invalid plugin:** an enabled invalid one writes its error row each poll; a disabled one
    writes nothing.
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
    - one holding only disabled and invalid plugins. Here every built-in and added-instance
      entry, row and state key matches. The plugins' own report entries and `plugin_errors`
      are asserted on their own.
  - A test fails if the list grows.
  - The phase-2 carry-forward runs from `357bcee`, then from this branch, in one sandbox.
  - All of `tests/test_provider_instances.py` passes unchanged.
- **AC-11 Version compatibility and app.**
  - With an installed 2.3.0 collector, a discovered plugin is reported `not_configured` with the
    reinstall note. `plugin enable` is refused.
  - The app decodes today's fixtures, which have no `external`, unchanged. It shows an external
    provider like a built-in. It shows its toggle only when `capabilities.external_plugins` is
    true.
  - An XCTest and a snapshot render of an external provider, from a synthetic fixture.
  - The owner gives visual acceptance at PR review.
- **AC-12 Docs, validation and privacy.**
  - `docs/PLUGINS.md` documents the manifest, the protocol, the bounds, the lifecycle, and the
    trust model's "not a security sandbox" statement word for word. So do `docs/CLI.md` and the
    `plugin` help text.
  - `make check` and `make app-test` pass, and the fresh required CI is green.
  - The phase-gate audit is run.
  - The diff holds no plugin other than the synthetic fixtures, and no real hostnames,
    credentials, work-tool names or logs.
- **AC-13 Owner's work laptop (operational, pending until performed).** After the merge and an
  owner-approved reinstall, the owner installs their private plugin on the work laptop, enables
  it, and sees it collected and shown. The owner runs this check and reports only pass or fail.
  Nothing from that laptop comes back to this repository or conversation. It isn't a
  precondition for the implementation PR.

## Open Decisions (each with a recommendation)

| # | Decision | Recommended | Alternative |
| --- | --- | --- | --- |
| D1 | Is a discovered plugin collected right away? | **A. No: it's off until `plugin enable` or the Settings toggle.** It shows as disabled at once, so it still "shows up". Running code never depends only on a file appearing. | B. Collect every valid plugin on discovery. |
| D2 | What do bad permissions do? | **A. Refuse: the plugin is invalid until the owner fixes the permissions.** The fix is one `chmod`, and a writable plugin is code that someone else could change. | B. Warn in `doctor` and run it anyway. |
| D3 | Versioning | **A. `VERSION` 2.4.0 with the `external_plugins` capability. That doesn't authorize a release or changing the installed collector**, the same as phase 2's Q7. | B. Version-free: detect the feature by trying it. |
| D4 | Added instances of an external plugin (a second work account) | **A. Out of scope for phase 3.** One provider per plugin folder; a second account means a second folder. Revisit only if needed. | B. Let a manifest opt in to `instances`. |

No other decision is open. Settled decisions 1A–7B and the phase-2 decisions aren't reopened.
The packet's engineering choices are reviewable as part of the one approval:
- the protocol shape;
- the limits (1 MiB stdout, stderr discarded, a 64 KiB manifest, 2,000 rows, 256 KiB state, 1–120 s);
- the minimal environment;
- appending external providers last.
