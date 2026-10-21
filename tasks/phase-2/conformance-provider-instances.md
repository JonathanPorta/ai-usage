# Conformance: Provider Instances (Phase 2)

Which rules and declared standards govern phase 2, and where each is implemented and checked.
Nothing in this phase starts a standards change.

| Requirement | Status | Implemented by | Verified by |
| --- | --- | --- | --- |
| Phased plan, PRD with yes/no ACs, tasks, validation first, session state | Repo rule, mandatory (rules 00, 02–06) | `tasks/phase-2/{prd,tasks,session-state}-provider-instances.md` | AC approval by the owner, 2026-10-20; phase-gate audit (rule 13) before the PR description |
| Makefile is the command surface | Repo rule (rule 07) | New tests run under `make check` and `make app-test`; fixtures under `make fixture`/`fixture-check` | CI `check` jobs and the macOS lane |
| Branch, PR and commit conventions; no LLM attribution | Repo rules 09, 10 | `jp/f/provider-instances`, a regular ready-for-review PR | Commit and PR text review |
| Spec pack | Opted in (`blessed.yml`) | `docs/CLI.md`, `docs/PLUGINS.md`, `docs/APP.md`, `docs/RELEASE.md`, `docs/DECISIONS.md` | `make spec-check ARGS=--strict` |
| Design handoff and rules 30–38 | Opted in; domain rules | Add-account states inventoried in `docs/APP.md`, rendered by the snapshot renderer | `make design-check ARGS=--strict`; owner visual acceptance of the 24 renders (2026-10-20, recorded by #12) |
| macOS CI on Proofglass first | Owner decisions OD-2, OD-2a | Unchanged routing | CI evidence names the executor that ran |
| No release, no change to the installed collector, no credentials | Owner, 2026-10-20 | `VERSION` 2.3.0 in source only | Review |
