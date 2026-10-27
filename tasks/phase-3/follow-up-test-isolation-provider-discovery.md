# Follow-up: Synthetic Tests Must Not Reach Real Provider Tools

**Status: open. Recorded 2026-10-25; not fixed in the phase-3 closeout.** This is separate from
phase 3's acceptance, AC-13 and any release.

## Observed
The sandbox rehearsal for the personal-Mac reinstall used the CI harness from
`tests/test_ac10_procedure.py` (`ProcedureHome`). That harness provides:
- an isolated `HOME`;
- a fake `claude` first on `PATH`;
- `PATH` set to the harness's `bin`, then `/usr/local/bin:/usr/bin:/bin`.

Its `once` still ran the **real** `/opt/homebrew/bin/codex`:
- **Why:** provider discovery (`ai_usage_plugins/support.py`, `discovery_path()`) appends
  `COMMON_EXECUTABLE_DIRS` to `PATH`, and that list includes `/opt/homebrew/bin` and folders under
  `~`.
- **What it did:** Codex ran its account methods and answered `-32600: codex account authentication
  required…`. The collector recorded two Codex error rows.
- **Risk:** no account was used in that run. That was a consequence of where this Mac's Codex keeps
  its login, not a guarantee of the harness.

**An empty or isolated `HOME` does not isolate a test from real tools.** A real executable can
still be found through absolute fallback folders, and it may read credentials that `HOME` doesn't
control, such as the Keychain.

Other tests that collect through the same discovery path on a Mac with real tools installed may
behave the same way. This hasn't been audited.

## Requirement
- **No real tools:** a synthetic test, a rehearsal harness or the fixture generator can't discover
  or execute any real provider tool, whether through `PATH`, `COMMON_EXECUTABLE_DIRS`, the
  `~`-relative fallback folders, globbed folders (`~/.nvm/...`, `~/Library/pnpm`) or any other
  search path.
- **Fakes only:** only the fakes the test puts in place may be found.
- **Independent of the machine:** this holds whatever is installed on the developer's machine,
  not only on a clean CI runner.

## Validation to define with the fix
- **A failing check:** a test fails when discovery, under any harness, resolves a provider
  executable outside that harness's own folders.
- **The same check for every harness:** it covers `IsolatedHome`, `ProcedureHome`, the parity
  driver and `ai_usage_fixtures`.
- **Proof before and after:** the check fails today on a Mac with a real provider tool in a
  fallback folder, and passes after the fix.

## Out of scope here
The fix itself, any change to production discovery behavior, and anything in phase 3's closeout.
