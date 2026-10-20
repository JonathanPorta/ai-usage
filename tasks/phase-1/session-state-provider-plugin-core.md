## Session State: provider-plugin-core
Last updated: 2026-10-20

### Current Position
- **Current Phase:** planning complete; implementation blocked on the entry condition (#5 merged)
- **Validation Review Mode:** auto-proceed (owner, 2026-10-20: "No further permission is needed for each task or commit")
- **Working on:** nothing. Next is Task 1.0 once the branch exists.
- **Blocked:** Yes. #5 isn't merged yet; it waits on the signed blessed-cicd export (F3).

### Key Decisions
- 1A phased plan; 2A in-process built-ins / external = executable + manifest (phase 3); 3A instances list; 4B CLI + Settings; 5 keep ids; 6A private plugins dir; 7B #5 first. Confirmed by the owner, 2026-10-19.
- Phase-1 ACs AC-1–AC-9 approved by the owner, 2026-10-20. Don't ask for approval again.
- `ai_usage_plugins/` at the repo root; one interface, one deterministic registry, provider-specific modules. Owner, 2026-10-20.
- No canonical-type field in phase 1; phase 2 defines it. Owner, 2026-10-20.
- Plugin implementation stays out of #5; the branch is cut from main after #5 merges. Owner, 2026-10-20.
- Routine ai-usage actions on the feature branch (commits, pushes, PR) are authorized; merge, publication, visual acceptance and production stay gated. Owner, 2026-10-20.

### Codebase Understanding
- `ai_usage_service.py`: provider code has no cross-provider references; 30 shared helpers, none of which reference provider code (AST closure, 2026-10-20).
- `VERSION` (l.40) is used by codex (l.1425) and grok (l.3458). The reporter regex-reads it from the installed file's first 16 KB, so it stays in the service, and plugins get it via `CollectContext.version`.
- The installed collector runs as `__main__` (`~/.ai-usage/collector.py`), so plugins must never import the service.
- `build_app.sh:21` already bundles the report and the service; add the package beside them.
- Install goes through `prepare_managed_file` + `FilesystemTransaction` (l.4416, l.4773). Adding plugin files as managed files gives rollback for free.
- Test fixture `IsolatedHome` (`test_ai_usage_service.py:70`): fake executables, fake launchctl.
- Sandbox `once` (`ai_usage_fixtures.write_sandbox`) points at absent binaries, so it's too thin for parity. Use the dedicated parity scenarios.

### What's Next
1. After #5 merges: `git switch -c jp/f/provider-plugin-core origin/main`; cherry-pick this planning commit from `jp/d/provider-plugin-plan`.
2. Task 1.0: parity harness; capture the golden files at the #5 merge SHA.
3. Tasks 2.0–8.0 in order; one provider per commit.

### Blockers / Open Questions
- Entry condition: #5 merge (waits on the export: Bitwarden domains → activation approval → draft → publish decision → install in #5 → prrq approval).
