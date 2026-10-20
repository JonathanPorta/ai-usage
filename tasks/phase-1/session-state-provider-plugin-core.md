## Session State: provider-plugin-core
Last updated: 2026-10-20

### Current Position
- **Current phase:** done. PR #10 is open and ready for review, CI is green with the macOS lane on ProofGlass, and the review findings are addressed (`c821a35`).
- **Branch:** `jp/f/provider-plugin-core`, cut from main `ddb923f` (#5's merge commit; main CI green on ProofGlass, job `job_01M5DNWQ5C2WVBQFBW7XKAP75B`).
- **Validation review mode:** auto-proceed (owner, 2026-10-20: "No further permission is needed for each task or commit").
- **Blocked:** no. The PR's merge is the owner's next approval checkpoint.

### Key Decisions
- 1A phased plan; 2A in-process built-ins, and external plugins as an executable plus a manifest (phase 3); 3A instances list; 4B CLI + Settings; 5 keep ids; 6A private plugins directory; 7B #5 first. Owner, 2026-10-19.
- Phase-1 ACs AC-1 to AC-9 approved by the owner, 2026-10-20. Don't ask for approval again.
- `ai_usage_plugins/` at the repo root: one interface, one deterministic registry, one module per provider. Owner, 2026-10-20.
- No canonical-type field in phase 1; phase 2 defines it. Owner, 2026-10-20.
- Routine branch work (commits, pushes, a ready-for-review PR) is authorized. Merge, publication, visual acceptance and production stay gated. PRs are never drafts. Owner, 2026-10-20.
- Engineering (recorded in `docs/DECISIONS.md`):
  - the report keeps its provider order (`report_order`) and the listed presentation rules;
  - install refuses an incomplete module set;
  - ownership keys are `plugin_module:<name>`;
  - `VERSION` stays 2.2.0.

### What Was Built
- `tests/parity/` (`a20cb56`, `da111fb`): eight frozen-time scenarios. The golden files come from BASELINE `ddb923f`, and there is a negative control.
- `ai_usage_plugins/` and the registry-driven service and report (`eb1c20e`), with `tests/test_plugin_architecture.py` covering:
  - the AST id check and its negative control;
  - an import-side-effect sentinel;
  - load and collect failure isolation;
  - the verbatim move against BASELINE.
- Installer fix: no leftover recovery directory (`65ddbfe`). `tests/test_install_lifecycle.py` (`60791b8`).
- App bundle carries the package; `make app-build` checks it (`0175057`).
- Docs: `docs/PLUGINS.md`, plus the CLI, RELEASE, APP, IDEA, DECISIONS and README updates.

### What's Next
1. Owner: the merge decision on PR #10.
2. After the merge: phase 2 needs its own PRD and AC approval.

### Blockers / Open Questions
- None blocking. `make launchd-integration-check` (real launchd, disposable agent) was updated but not run in phase 1.
