# Accepted native visual baselines

Accepted by the owner (Jonathan) on 2026-10-10 from the reviewed package
`ai-usage-visual-review-425699b.zip`
(sha256 `c99d93fb8ff3700a0d00666a5fc59f2f36d2c638517013b2dc7e5c09d4d7e4cb`;
review page https://claude.ai/artifact/KPTdFWGJPhwnj1YtkZd2Kj).

| Item | Value |
| --- | --- |
| Images | 30 PNGs: 15 views × light (`aqua`) and dark (`darkAqua`) |
| Checksums | `SHA256SUMS` (identical to the package's `SHA256SUMS.txt`) |
| Command | `make app-snapshot` (`AIUsage --snapshot .sandbox/snapshots/fixture`) |
| Data | Committed synthetic fixture `macos/Sources/AIUsageCore/Resources/fixtures/report-fixture.json`, evaluated at 2026-09-29T16:40:00Z, `TZ=America/Denver` |
| Scale | 2× backing: popover 420 pt → 840 px (screen height 1000 pt; the short overview uses the short viewport); History 920 × 1400 pt |
| Toolchain | macOS 26.6.1, Swift 6.4 |
| Rendered at | 28 images at `3d88c7b`; `detail-gemini_cli-tokens-{light,dark}.png` at `425699b` |
| Re-verified | A fresh render at `22a5c56` is byte-identical to all 30 |

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
