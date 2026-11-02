# Tasks: The All View (Phase 4)

> Generated from [PRD: The All View](prd-all-view.md). The analysis, D1–D7 and AC-1 to AC-12
> were approved by the owner on 2026-11-01 with corrections C1–C4 and a D4 clarification
> (#28, merged as `b569d31`). Branch `jp/f/phase-4-all-view`, cut from `main`.

## Acceptance Criteria Traceability

| AC | Short description | Tasks |
| --- | --- | --- |
| AC-1 | Groups | 1.0 |
| AC-2 | Day rules, null vs zero, parts' qualifications | 1.0 |
| AC-3 | Nothing mixed | 1.0 |
| AC-4 | Compatibility, collector untouched | 1.0, 2.0 |
| AC-5 | Figures F1–F4 | 2.0 |
| AC-6 | The page | 2.0, 3.0 |
| AC-7 | Chart marks | 2.0, 3.0 |
| AC-8 | Series and overflow | 2.0, 3.0 |
| AC-9 | Accessibility and token contrast | 1.0, 2.0, 3.0 |
| AC-10 | Visual acceptance by the owner (accepted 2026-11-02) | 4.0 |
| AC-11 | Live check by the owner, after an approved install (passed 2026-11-02) | — |
| AC-12 | Validation and isolation | 5.0 |

## Relevant Files
- `ai_usage_report.py`: `build_groups`, `group_day`, and `groups` in the report.
- `ai_usage_fixtures.py`: the `all-tools` and `overflow` scenarios and their 30-day fixtures.
- `tests/test_all_view.py` (new); `tests/parity/{driver,test_parity}.py` (`report.groups`, with an
  independent derivation).
- `macos/design/tokens.json`, the generated `DesignTokens.generated.swift`, and
  `macos/design/components.manifest.json`.
- `macos/Sources/AIUsageCore/{Report,AllTools}.swift`;
  `macos/Sources/AIUsageDesign/GroupCharts.swift`;
  `macos/Sources/AIUsageApp/{HistoryView,AllToolsView,SnapshotRenderer}.swift`.
- `macos/Tests/AIUsageCoreTests/AllToolsTests.swift` (new).
- `docs/CLI.md`, `DESIGN.md`, `DESIGN_SYSTEM.md`, `docs/design/app/design.md`.

### Notes
- Synthetic data only: fixture sandboxes with their own HOME and absent provider executables.
  No test reads the owner's files or starts a provider tool.
- Builds and renders run in this worktree's own `macos/build`, never the main checkout's, which
  holds the owner's running app.

## Tasks
- [x] 1.0 Report groups (AC-1 to AC-4, AC-9 tokens)
  - [x] 1.1 Validation first: the pair table for every two-state combination, the three-account
        cases, null vs zero, parts' times and incomplete events, the key sets, and the report
        end to end (two accounts, a local plugin, 7/30/90 days, a disabled account, a group
        with no members).
  - [x] 1.2 `build_groups` and `group_day` in the reporter; `groups` after `providers`.
  - [x] 1.3 Parity: `report.groups` declared, with `expected_groups` derived independently in
        the driver; the golden files unchanged.
  - [x] 1.4 Negative control: 10 weakened rules, each caught.
  - [x] 1.5 Series tokens with the contrast and lightness tests.
- [x] 2.0 Presentation model (AC-4 to AC-9)
  - [x] 2.1 Validation first: `AllToolsTests` (22 XCTests, including the zero day with incomplete
        turns).
  - [x] 2.2 `ToolGroup` and `GroupDay` decoding; `AllToolsPage`, figures F1–F4, series and
        overflow, bars, breakdown rows, readouts, and `HistorySelection`.
  - [x] 2.3 Negative control: 10 weakened rules, each caught.
- [x] 3.0 Views (AC-6 to AC-9)
  - [x] 3.1 `GroupDayChart` and `GroupChartLegend`.
  - [x] 3.2 `AllToolsView`; History's All tools row and selection; breakdown names open each
        account's page.
- [x] 4.0 Renders (AC-10)
  - [x] 4.1 Eight scenes, each in light and dark: two accounts (wide, narrow, 90 days), an
        incomplete day with an account off, the overflow (wide, and narrow with today selected,
        so the readout runs past three lines), one-account tools only, and no usage.
  - [x] 4.2 Compared with `main` rendered on the same Mac: 64 identical; the 26 History
        renders differ only in the sidebar.
  - [x] 4.3 The owner accepted the 16-render packet from `082c5f9` on 2026-11-02; the renders
        and their checksums are in `docs/design/app/references`.
- [x] 5.0 Docs and validation (AC-12)
  - [x] 5.1 `docs/CLI.md`, `DESIGN.md`, `DESIGN_SYSTEM.md`, the components manifest, and the
        verification log.
  - [x] 5.2 `make check`, `make app-test`, `make fixture-check`, `make design-build-check`, and
        `spec-check`/`design-check --strict`.
  - [x] 5.3 CI green on the final head `cd6ed0f`, with a fresh independent review; merged as
        `3a2473a`, and main CI green there.
