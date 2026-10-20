# PRD: Provider Plugin Core (Phase 1)

Status: **acceptance criteria approved by the owner on 2026-10-20.** Implementation starts once #5
merges, on `jp/f/provider-plugin-core` cut from that main. Part of the
[project plan](../project-plan.md).

## Summary
Move the five built-in providers out of hard-coded dispatch tables and into their own modules
behind one interface and one deterministic registry. Behavior, identifiers and on-disk formats
stay the same. Phases 2–4 build on this registry. Phase 1 has no user-visible feature.

## Codebase Analysis
(Confirmed by the owner on 2026-10-19. Re-read against the #5 branch, 08e8a50, on 2026-10-20.)

### Explored
- `ai_usage_service.py` (5,670 lines). The provider set is listed in parallel in:
  - `DEFAULT_CONFIG["providers"]` (l.114)
  - the `collect_snapshot` handlers (l.4125)
  - the `detect_providers` definitions plus per-id `extra` branches (l.4188)
  - `configured_path_roles` provider paths (l.~285)
  - `prepare_integration_plans`, for antigravity and gemini_cli (l.4608)
  - `parse_config_assignment` (l.5166)
- Provider code is self-contained (AST closure analysis, 2026-10-20):
  - No provider references another provider's code.
  - The collectors share 30 helpers (`metric_row`, `availability_row`, `expand_path`,
    `find_executable`, `read_new_json_lines`, `terminate_provider_process`, …).
  - None of those helpers references provider code.
  - Approximate sizes: claude ~1,000 lines, grok ~690, codex ~320, antigravity ~290,
    gemini ~140.
- `VERSION` (l.40) is used by codex (l.1425) and grok (l.3458) as client version. The reporter
  reads it from the installed script's text with a regex over the first 16 KB
  (`ai_usage_report.installed_collector_version`).
- `ai_usage_report.py`:
  - `SourceDef`/`ProviderDef`/`PROVIDERS` (l.55–173) give the display metadata and CSV-source
    mapping per provider.
  - It does `import ai_usage_service as collector`.
- `ai_usage_fixtures.py` imports the service. It writes the sandbox and the app fixture
  `report-fixture.json`, which is checked by `make fixture-check`.
- Distribution paths:
  - `install` writes `Path(__file__).read_bytes()` to `~/.ai-usage/collector.py` as a managed
    file in a `FilesystemTransaction` (snapshots, rollback, ownership record).
  - `macos/scripts/build_app.sh:21` copies `ai_usage_report.py` **and** `ai_usage_service.py`
    into `AI Usage.app/Contents/Resources/collector/`. `make app-build`, `release-build` and
    `archive-app.sh` all use it.
  - Every installed entry point runs `~/.ai-usage/collector.py`: the LaunchAgent, the app's
    "Collect now" (`Clients.swift:162`) and the antigravity status-line wrapper (l.4377).
- macOS app:
  - The app is report-driven (`Report.swift` `Provider`).
  - Settings writes `providers.<id>.*` through `configure` (`Control.swift:88`).
  - Ids appear in the app only in snapshot fixtures (`SnapshotRenderer.swift`).

### Relevant Patterns
- One provider failure can't suppress the others: an exception becomes an `unexpected_error`
  row (l.4155).
- Handler signature: `(collected_at, settings, include_history, state) -> list[row]`.
- Install, uninstall and recovery mutate files only through `prepare_managed_file` plus
  `FilesystemTransaction` and the ownership record.
- Tests:
  - `IsolatedHome` (`test_ai_usage_service.py:70`) supplies fake executables, a fake launchctl
    and a sandbox home.
  - `make check` is the canonical gate.

### Constraints Discovered
- The installed collector runs as `__main__` from `collector.py`. A plugin module that imported
  `ai_usage_service` would load a second copy, or fail when installed. So plugins must never
  import the service.
- The collector may use only the standard library, under the LaunchAgent's pinned Python.
- Running from `~/.ai-usage` can write `__pycache__/` there unless bytecode writing is off.
  The installer doesn't own those files.

## Background
Adding a provider today means editing about six parallel tables in two files. Running a second
Claude account is impossible, because the id is also the config key, the state-key prefix and
the report key. Phase 1 removes the parallel tables. Phase 2 can then change the identity model
in one place.

## Goals
- One registry is the only list of bundled providers. Collection, doctor, config defaults,
  path checks, integration plans and reporting read from it.
- Each built-in lives in its own module, and its behavior is shown to match the merged
  pre-refactor revision.
- Every distribution path (install, app bundle, release archive) carries the full module set
  consistently.

## Non-Goals
- Instances, `instance add`, "Add account…", claude-az (phase 2).
- External or private plugins, discovery or installation of third-party code, the phase-3
  protocol.
- A canonical/parent type field. Phase 2's PRD defines what it means and which values it takes.
- The All view (phase 4). Any visual change.
- Any change to the CSV, state, config or report format.

## Architecture & Approach

### Layout (decision: `ai_usage_plugins/` at the repository root)
    ai_usage_plugins/
      __init__.py      registry: BUNDLED = ("codex", "claude", "antigravity", "gemini_cli", "grok")
                       load_plugins() -> ordered results; each module imported on its own
      base.py          ProviderPlugin interface + CollectContext (data only, no side effects)
      support.py       the 30 shared collection helpers, moved out of the service
      codex.py  claude.py  antigravity.py  gemini_cli.py  grok.py

### The interface (one shared contract)
Each provider module exposes `PLUGIN`, an object with these fields:
- `id`
- `default_settings` (today's `DEFAULT_CONFIG` entry, byte for byte)
- `executable_names`
- `collect(context) -> rows`
- `doctor_extra(settings) -> dict`
- `path_roles(settings) -> list`
- `integration_plan(...)` (optional; only antigravity and gemini_cli have one)
- `report` (today's `ProviderDef` with its `SourceDef`s)

`CollectContext` carries `collected_at`, `settings`, `include_history`, `state` and `version`.
Today's handler arguments plus `version` replace the module-level `VERSION` that codex and grok
read.

### Layering, with no cycles
`support` ← `base` ← provider modules ← registry ← `ai_usage_service` ← `ai_usage_report` and
`ai_usage_fixtures`.

Importing any module in the package only defines things: no collection, no subprocess, no file
writes. `ProviderDef`/`SourceDef` move to `base.py`, and the report re-exports them under their
current names.

### Compatibility surface
- `ai_usage_service` re-exports the moved helpers, so `SERVICE.metric_row` and the like still
  resolve for tests and fixtures.
- `VERSION` stays a literal near the top of `ai_usage_service.py`, because the reporter parses
  it from the installed file.

### Failure isolation (AC-5)
- `load_plugins()` imports each bundled module inside its own `try`. If loading fails, the
  registry records `(id, error)`.
- `collect_snapshot` turns that into the same one `collection/unexpected_error` row used for a
  raising collector, and the other providers still run.
- `collect()` failures keep today's handling.
- The report skips a provider whose metadata failed to load and records the failure.
  Nothing crashes, and the other providers render unchanged.

### Packaging
- **Install:** `collector.py` (the service) plus each `ai_usage_plugins/*.py` become managed
  files in the existing transaction:
  - Directory `~/.ai-usage/ai_usage_plugins` 0700; files 0600; `collector.py` stays 0700.
  - Ownership keys: `plugin_module:<name>`.
- **Upgrade from the single-file install:** the old record has no plugin keys, so the module
  files are new owned files written in the same transaction as `collector.py`. If any write
  fails, the transaction restores every snapshot, so a failed upgrade never leaves mixed
  versions.
- **Reinstall:** same content means no write.
- **Files a version no longer ships:** an owned module file is removed only if its hash still
  matches the record.
- **Uninstall:**
  - removes the owned program files whose content is unchanged, then the package directory if
    it's empty;
  - keeps config, usage, state, backups, modified or unowned files, and integrations the
    installer doesn't own (today's rules).
- **Bytecode:** the collector sets `sys.dont_write_bytecode = True` before importing the
  package, so no unowned `__pycache__/` appears under `~/.ai-usage`.
- **App bundle:** `build_app.sh` also copies `ai_usage_plugins/*.py` into
  `Resources/collector/ai_usage_plugins/`. The app already sets `PYTHONDONTWRITEBYTECODE=1`.
- **Release:** `release-build` → `app-build` picks up the bundle change. `docs/RELEASE.md`'s
  collector row is updated.

### Validation approach (validation-first; details in the task file)
- **Parity (AC-3):**
  - `tests/parity/` holds a driver and scenarios, committed first on the branch. The driver
    runs at the merge commit of #5 (`BASELINE_SHA`) and writes the golden files from there.
  - Scenarios use `IsolatedHome`-style fake executables and fixed inputs. The driver fixes time
    by patching `utc_now`/`time.time` on every loaded `ai_usage*` module, and fixes TZ, HOME
    and PATH.
  - Normalized fields are listed in one place, `NORMALIZE`: `transaction_id`, plus fields that
    contain a sandbox path (replaced with `/sandbox`). Nothing else is normalized.
  - The golden files are never regenerated from the new code. A test asserts that their
    recorded `BASELINE_SHA` is an ancestor of HEAD.
- **Architecture (AC-2):** an AST test asserts:
  - the shared orchestration functions contain no provider-id literals and no per-id branches;
  - `ai_usage_plugins/*` never imports `ai_usage_service`/`ai_usage_report`;
  - importing the package starts no subprocess and writes no file (sentinel test).
- **Lifecycle (AC-6):** black-box tests with a fake launchctl and an isolated HOME. Failure is
  injected at the Nth managed write to test rollback.
- **Outside the checkout (AC-7):**
  - Install into an isolated HOME, then run `collector.py once` from a temporary cwd with
    `-I`-style isolation (no PYTHONPATH, no repo on `sys.path`).
  - Build the app and run the bundled `ai_usage_report.py` the same way.

## Acceptance Criteria (approved 2026-10-20)
- [ ] **AC-1 — Built-in extraction.** Each of the five existing providers has its own
  implementation module behind the shared interface. Existing behavior, identifiers, ordering,
  configuration defaults and capabilities are preserved.
- [ ] **AC-2 — Generic orchestration.** A focused architectural check replaces the
  repository-wide provider-ID grep. Shared collection, doctor and reporting code derive the
  provider set and applicable behavior from the registry and plugin contract, not from parallel
  hard-coded provider lists.
  - Provider ids may legitimately remain in fixtures, tests, documentation, compatibility
    records and provider-specific UI assets. Narrow exceptions are documented, and unrelated
    code isn't rewritten to satisfy a text search.
- [ ] **AC-3 — Reproducible parity.**
  - The baseline is captured from the final merged pre-refactor revision, including the pending
    app/collector fixes.
  - Time and external inputs are controlled.
  - Collected rows, `doctor --json` and report output are equivalent, including ordering, error
    classifications, disabled/missing-provider behavior and reporting fields.
  - Only genuinely variable fields are normalized, and they are listed explicitly. The
    comparison isn't weakened, and expected output is never regenerated from the new
    implementation.
- [ ] **AC-4 — Data compatibility.** Existing config, state and CSV files stay usable without
  migration or install-time rewriting. The symlinked-config fix is preserved. Normal collection
  still updates state and appends usage as before.
- [ ] **AC-5 — Failure isolation.** The existing `unexpected_error` behavior for a
  provider-local failure is preserved, and other providers still complete.
  - Tests cover provider-local loading failures and collection failures, wherever the
    extraction introduces those boundaries.
  - Importing one provider can't disable all collection.
- [ ] **AC-6 — Installation lifecycle.** Fresh install, upgrade from the single-file
  installation, reinstall, failed-upgrade rollback and uninstall are all verified.
  - The complete module set installs consistently, with no mixed versions after a failure.
  - Existing entry points, service-state recovery, ownership checks and uninstall protections
    are preserved.
  - "Nothing left behind" means obsolete owned program files only. It doesn't mean deleting
    configuration, usage history, backups, modified or unowned files, or integrations the
    installer doesn't own.
- [ ] **AC-7 — Distribution and app compatibility.** The app already bundles `ai_usage_report.py`
  and `ai_usage_service.py`.
  - Every affected distribution path includes its required modules.
  - The installed daemon and the bundled reporter are shown to work from outside the
    repository, without the checkout or a development PYTHONPATH.
  - Report and fixture-render output stay deterministic under matching rendering conditions.
    No visual redesign.
- [ ] **AC-8 — Required validation.** The applicable static, Python, Swift, fixture, packaging
  and design/spec checks run, and they include the newly extracted modules.
  - Lifecycle regression tests use isolated data and a fake launchctl. They never exercise the
    installed personal collector.
  - The final revision has fresh CI evidence. Proofglass stays the primary macOS executor under
    the owner-approved routing and spending policy, with no new or broadened hosted fallback.
    The report names the executor that actually ran.
- [ ] **AC-9 — Documentation.** `docs/PLUGINS.md` documents the actual built-in interface,
  registry, error behavior, packaging and compatibility guarantees, and clearly marks the
  external-plugin protocol as phase 3. The applicable specs and task records are updated
  through the existing repository workflow.

## Resolved Questions
- Package location: `ai_usage_plugins/` at the repo root (owner, 2026-10-20).
- Canonical type: no field in phase 1. The original brief asks for a parent/canonical type but
  doesn't define its values, so phase 2 defines them (owner, 2026-10-20).

## Open Questions
None blocking. Implementation-level choices within this PRD (helper placement, ownership-key
names) are made during implementation and recorded in the session state.
