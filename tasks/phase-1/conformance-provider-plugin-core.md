# Conformance: Provider Plugin Core (Phase 1)

Which rules and declared standards govern phase 1, and where each is implemented and checked.
Status words follow blessed-cicd `standards/lifecycle.md`. Nothing in this phase starts a
standards change.

| Requirement | Status | Implemented by | Verified by |
| --- | --- | --- | --- |
| Phased plan, PRD with yes/no ACs, tasks, validation first, session state | Repo rule, mandatory (`.ai-rules/AGENTS.md`; rules 00, 02–06) | `tasks/project-plan.md`; `tasks/phase-1/{prd,tasks,session-state}-provider-plugin-core.md` | AC approval by the owner on 2026-10-20; phase-gate audit (rule 13) before the PR description |
| Makefile is the command surface | Repo rule (rule 07) | New checks go behind existing targets (`check`, `fixture-check`, `app-build`) | `make check` in CI |
| Branch, PR and commit conventions; squash merge | Repo rule, enabled (rule 10) | `jp/f/provider-plugin-core` cut from main after #5 merges | PR title check |
| Git and publication boundaries; no LLM attribution | Repo rule (rule 09) | Feature-branch pushes and the PR (owner authorization, 2026-10-20). Merge, publication and visual acceptance stay gated | Commit and PR text review |
| Spec pack (`spec-pack.core@1`, `.profiles@1`) | Opted in (`blessed.yml:8-9`); a proposal in blessed-cicd's registry | `docs/CLI.md`, `docs/RELEASE.md`, `docs/SPEC.yml` updated wherever they describe the single-file collector | `make spec-check ARGS=--strict` (CI `tests.yml:200`) |
| Design handoff (`design-system.handoff@1`) | Opted in (`blessed.yml:10`); a proposal | No UI change in phase 1 (non-goal) | `make design-check ARGS=--strict` (CI `tests.yml:202`); fixture renders unchanged |
| Release pipeline, candidate and ed25519 signing | Opted-in pilots (`blessed.yml:11-14`, owner, 2026-10-10) | `build_app.sh` bundle path only. No signing or release mechanism changes | `make release-check` (ephemeral key) if the bundle layout changes the candidate |
| macOS CI on Proofglass first; hosted fallback for capacity only | Owner decisions OD-2 and OD-2a | Unchanged (`tests.yml` route step). No new or broadened fallback | CI evidence names the executor that actually ran (AC-8) |
| No private blessed-cicd tooling in ai-usage | Owner decision, 2026-10-15 | Phase 1 adds none. The tree carries only the signed export, after #5 | Review |
