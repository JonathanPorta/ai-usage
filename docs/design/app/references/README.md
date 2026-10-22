# Accepted native visual baselines

Accepted by the owner (Jonathan) on 2026-10-10 from the reviewed package
`ai-usage-visual-review-425699b.zip`
(sha256 `c99d93fb8ff3700a0d00666a5fc59f2f36d2c638517013b2dc7e5c09d4d7e4cb`;
review page https://claude.ai/artifact/KPTdFWGJPhwnj1YtkZd2Kj).

| Item | Value |
| --- | --- |
| Images | 74 PNGs: 37 views × light (`aqua`) and dark (`darkAqua`); 30 accepted 2026-10-10, History updated 2026-10-15, added accounts and single-account Settings 2026-10-20, chart readout spacing 2026-10-22 (below) |
| Checksums | `SHA256SUMS` (identical to the package's `SHA256SUMS.txt`) |
| Command | `make app-snapshot` (`AIUsage --snapshot .sandbox/snapshots/fixture`) |
| Data | Committed synthetic fixture `macos/Sources/AIUsageCore/Resources/fixtures/report-fixture.json`, evaluated at 2026-09-29T16:40:00Z, `TZ=America/Denver` |
| Scale | 2× backing: popover 420 pt → 840 px (screen height 1000 pt; the short overview uses the short viewport); History 920 × 1400 pt |
| Toolchain | macOS 26.6.1, Swift 6.4 |
| Rendered at | 28 images at `3d88c7b`; `detail-gemini_cli-tokens-{light,dark}.png` at `425699b` |
| Re-verified | A fresh render at `22a5c56` is byte-identical to all 30 |

## History update (accepted 2026-10-15)

Accepted by the owner (Jonathan) on 2026-10-15 ("accept D3") from the review checkpoint
`ai-usage-history-review-48b7ac0.zip`
(sha256 `4404fa0fbd422508bf8cd60fd7e7cb61fea093ddd13bc18a41bf45b688ddf041`;
review page https://claude.ai/artifact/LXT3hwaqpWU1cwb9SLWi9t).

| Item | Value |
| --- | --- |
| Replaced (6) | `history-tokens`, `history-tokens-claude`, `history-quota`, light and dark |
| Added (20) | `history-tokens-90d`, `history-quota-grok`, `history-tokens-narrow`, `history-tokens-claude-narrow`, `history-quota-narrow`, `history-empty-not-detected`, `history-empty-not-detected-narrow`, `history-empty-tracking-off`, `history-empty-no-usage`, `history-empty-none-in-range`, light and dark |
| Rendered at | `48b7ac0` (History fixes); byte-identical at `dbc1093` (focus ring) and `eff480f` |
| Toolchain | macOS 26.6.1, Swift 6.4 |
| Re-verified | A fresh `make app-snapshot-check` at `eff480f` matches all 50 |

Functional behaviour of the History fixes was verified separately in a live sandbox
(`../design.md#verification`); this acceptance covers the rendered states only.

## Claude plan limits (accepted 2026-10-17)

Accepted by the owner (Jonathan) on 2026-10-17 from the review checkpoint
`ai-usage-claude-limits-review-f6537ee.zip` (sha256 `458e760dea52dbbb26e046839141bbf9ffe6b4de481aec711156a636088450e9`), together with the
feature's acceptance criteria. The fixture now carries Claude Code plan limits (5-hour 20% used,
weekly 65% used, weekly Fable 4% used), shown as "% used" like Claude Code's `/usage`.

| Item | Value |
| --- | --- |
| Replaced (12) | `detail-claude-tokens`, `history-tokens-claude`, `history-tokens-claude-narrow`, `popover-monitoring`, `popover-overview`, `popover-overview-short`, light and dark |
| Rendered at | `f6537ee`; byte-identical after merging the focus-ring and notice fixes (`5874dc8`) |
| Re-verified | A fresh `make app-snapshot-check` matches all 50 |

## Added accounts (accepted 2026-10-20)

Accepted by the owner (Jonathan) on 2026-10-20 from the review page
https://claude.ai/artifact/WayXUcbCyjuNB8wgSrkMap (PR #11, phase 2). The decision, its scope and its
exclusions are recorded on PR #12:
https://github.com/JonathanPorta/ai-usage/pull/12#issuecomment-6308344030.

| Item | Value |
| --- | --- |
| Added (24) | `popover-settings-two-accounts`, `popover-overview-two-accounts`, `detail-claude-az-quota`, and `add-account-{unsupported,closed,empty,problems,duplicate,ready,saving,saved,failed}`, light and dark |
| Data | The second committed fixture, `report-fixture-two-accounts.json` (`claude-az`, "Claude (work)") |
| Rendered at | `dc5371d`; byte-identical at the merge commit `315c43e` |
| Toolchain | macOS 27.0.1, Swift 6.4 |

On macOS 27.0.1 the earlier baselines (rendered on 26.6.1) no longer match byte for byte,
so `make app-snapshot-check` fails on them there. They are not re-rendered here; re-baselining
them needs its own review.

## Single-account Settings (accepted 2026-10-20)

Accepted by the owner (Jonathan) on 2026-10-20 ("The comparison page for the single account
settings looks good. I approve.") from the review page
https://claude.ai/artifact/S4U3uXPvAtABiau8aAwpWS, which showed it beside the 2026-10-10 baseline.

| Item | Value |
| --- | --- |
| Replaced (2) | `popover-settings`, light and dark: "Add Claude account…" at the end of Providers |
| Data | The committed fixture `report-fixture.json` |
| Rendered at | `315c43e` (main after #11); byte-identical on the corrective branch at `47afaa3` |
| Toolchain | macOS 27.0.1, Swift 6.4 |

## Chart readout spacing (accepted 2026-10-22)

Accepted by the owner (Jonathan) on 2026-10-22 ("I approve the reserved caption space shown in
the review report and the corresponding eight visual-reference updates") from the review page
https://claude.ai/artifact/QAJ7WSaXSmAmyDvdR9Le2P (PR #16). Each day chart now reserves the
height of its longest readout, so hovering or selecting a day no longer changes the card's
height; where a day's readout wraps, the chart has one blank line under its caption at rest.

| Item | Value |
| --- | --- |
| Replaced (8) | `popover-overview`, `popover-overview-short`, `popover-overview-two-accounts`, `detail-grok-tokens`, light and dark |
| Data | The committed fixtures `report-fixture.json` and `report-fixture-two-accounts.json` |
| Rendered at | `11f6fcc` (the PR head, as reviewed); byte-identical at the merge commit `c8eac81` |
| Toolchain | macOS 27.0.1, Swift 6.4 |
| Unchanged | Every other render at `c8eac81` is byte-identical to `main` (`ebe2b71`) rendered on the same Mac |

Interaction, checked in an isolated fixture app at `11f6fcc` (not a render):

| Check | Result |
| --- | --- |
| Hover from rest onto a day whose readout wraps (Grok Build, 16 Oct) | Passed: the readout filled the reserved line; the lines below stayed at the same positions (accessibility frames and pixels) |
| Hover between a wrapping and a short readout, across bars | Not verified |
| Keyboard selection (←/→) | Not verified |
| "Open history" and "Open in history window" close the popover and bring History forward, including when it is already open; the provider is selected | Not verified |
| The popover opens normally afterwards | Not verified |

The automation available could not move the pointer or deliver keys to the popover. The owner
accepted merging with these checks unverified; they are not recorded as passed.

Verify: `cd docs/design/app/references && shasum -a 256 -c SHA256SUMS`

To compare a build: `make app-snapshot`, then
`(cd .sandbox/snapshots/fixture && shasum -a 256 -c ../../../docs/design/app/references/SHA256SUMS)`.
A mismatch means the rendering changed; inspect it and re-accept deliberately.

## Scope of the acceptance

These are renders of fixture data in captured states. They do not verify
interaction: mouse-wheel scrolling, hover, Reduce Motion, larger text
(unsupported in this release) and login persistence remain unverified; see
`../design.md#verification`. No real usage data is included.

## Known deferred polish

The reset annotation ("Resets …") is placed too close to its neighbours: it
crowds the range selector in Antigravity's quota view
(`detail-antigravity-quota-*.png`) and extends outside the chart card in History
(`history-quota-*.png`). Accepted as is; tracked as deferred polish, not a new
design round.
