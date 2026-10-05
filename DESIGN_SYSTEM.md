# Design system engineering guide

Status: accepted (handoff 0.1.1). Implementation is in progress; see [Implementation status](#implementation-status).
Handoff: [`docs/design/app/handoff.yml`](docs/design/app/handoff.yml)

## Authority map

| Lane | Authority |
| --- | --- |
| Product/design language | [`DESIGN.md`](DESIGN.md) |
| Engineering usage | This document |
| Executable visual values | [`macos/design/tokens.json`](macos/design/tokens.json) (DTCG) |
| Generated runtime values | `macos/Sources/AIUsageDesign/DesignTokens.generated.swift`, generated from the token source and never hand-edited |
| Executable component behavior | [`macos/Sources/AIUsageDesign/`](macos/Sources/AIUsageDesign) (primitives) and `macos/Sources/AIUsageApp/` (screens) |
| Data semantics (aggregation, freshness, merge) | `ai_usage_report.py`; contract in [`docs/CLI.md`](docs/CLI.md) |
| Accepted source/provenance | [`docs/design/app/`](docs/design/app/), archive SHA-256 in `handoff.yml` |

When these disagree, stop and record a decision in `docs/DECISIONS.md`. Do not
blend sources. Prototype JavaScript (`components.js`, `fixtures.js`) is a
design reference. It is not production code. Where the prototype code and the
0.1.1 documentation disagree, the documentation wins. For example, a passed
reset is `measuredAt < resetsAt <= now`; see DECISIONS.

## Stack and styling profile

- **Framework:** native SwiftUI (`MenuBarExtra` with `.window` style, plus a `Window` scene for History), on macOS 14 or later.
- **Package manager:** Swift Package Manager (`macos/Package.swift`). `make app-build` assembles the `.app` bundle.
- **Styling profile:** `other`, with a waiver for the Tailwind-first default (`tailwind-v4-profile` in `handoff.yml`). There is no CSS runtime. The canonical DTCG tokens generate Swift constants.

## Commands

```text
make design-check          # vendored Blessed checker; ARGS=--strict in CI
make spec-check            # vendored Blessed spec-pack checker; ARGS=--strict in CI
make design-build          # tokens.json -> DesignTokens.generated.swift
make design-build-check    # fail when the generated Swift drifts from tokens.json
make test                  # collector and reporting tests (Python)
make app-test              # Swift unit tests (store, decoding, formatting)
make app-build             # build macos/build/AI Usage.app
make app-run               # build and launch against the installed collector
make app-run-sandbox       # launch against an isolated sandbox config and fixture data
```

The Blessed checkers are vendored under `tools/blessed/` at commit
`60dc5ed67089e55ff25468ae7626a3bb4d24eed8`. `tools/blessed/VENDOR-SHA256SUMS.txt`
records their checksums.

## Tokens and themes

- **Source:** `macos/design/tokens.json`, in Design Tokens Community Group JSON form. The values were transcribed from the accepted `app/styles.css` `:root` block.
- **Layers:**
  - `color.semantic.*` holds light/dark pairs. The light value is `$value`. The dark value is `$extensions["codes.porta.appearance"].dark`.
  - `color.component.*` aliases semantic tokens (`{color.semantic.accent}`).
  - Dimensions cover `font.size.*`, `space.*`, `radius.*`, `duration.*` and `layout.*`.
  - The handoff defines only paired semantic values and no separate primitive palette, so the primitive layer is collapsed into the semantic layer. Do not add primitives without a design reason.
- **Generation:** `macos/scripts/generate_design_tokens.py` writes `DesignTokens.generated.swift`. It is deterministic, uses only the standard library, and resolves references. `TokenColor` resolves light and dark against the effective `NSAppearance`, so views follow the system appearance.
- **Rule:** never write a hex value or a magic point size in a view. Add a token instead.

## Components and patterns

The manifest is [`macos/design/components.manifest.json`](macos/design/components.manifest.json).
Previews and tests use the committed fixture report
(`macos/Sources/AIUsageCore/Resources/fixtures/report-fixture.json`). The Python
reporting tests generate that file from a synthetic CSV, so the fixture and the
contract cannot drift. The normal app always consumes live report output.

Shared-state pattern: one `@Observable` `AppStore`, owned by the `App`, is
injected with `.environment(store)` into both the `MenuBarExtra` content and the
History `Window`. Views read from it only. Collect now and report refreshes
write to it. Views contain no aggregation or freshness logic; they format what
the report states.

## Responsive behavior

- **Popover:**
  - Fixed width of 420 pt.
  - Height is `min(780, visibleScreenHeight − 96)`.
  - The header, status row, notice and footer are pinned; only the card list or detail body scrolls.
  - On short screens (around 700 pt tall) the pinned regions stay visible and the list scrolls.
  - With large Dynamic Type, quota cells wrap to one column instead of truncating values.
- **History:**
  - Minimum size about 640×460, and the window is resizable.
  - The chart grows with the width. The provider list has a fixed width of about 190 pt.
  - The table scrolls independently.

## Accessibility

- Every status uses a symbol and text, never color alone.
- Each chart is one focusable element:
  - `←`/`→`/`Home`/`End` move the selection.
  - An accessibility value announces the date and the exact value, including "missing reading, not zero", "measured zero" and "today so far".
  - Charts also expose an accessibility summary.
- Keyboard:
  - `⌘R` runs Collect now.
  - `⌘,` opens Settings, once Settings is implemented.
  - `Esc` goes back from detail.
  - `↑`/`↓` move focus between the status row and the cards.
- Minimum chart text is 11 pt. Body text is 13 pt and follows system text-size settings where SwiftUI allows it.
- Reduce Motion replaces the checking spinner with a static symbol.
- Acceptance: covered by AC-UI-* in `tasks/prd-macos-menu-bar-app.md`. Manual verification is recorded in `docs/design/app/design.md#verification`.

## Assets and icons

- **Inventories:** [`docs/design/app/assets.yml`](docs/design/app/assets.yml) and [`docs/design/icon/`](docs/design/icon/).
- **Symbols:** the native app uses SF Symbols, following the proposed mapping in `IMPLEMENTATION.md` §12. Symbols are referenced by name in `macos/Sources/AIUsageDesign/Symbols.swift`; there are no bundled icon files.
- **Menu-bar glyph:** a `waveform.path.ecg` template symbol plus a state badge.

## Migration state

There is no prior UI, so this is a new surface. The Python collector is
unchanged. A read-only reporting layer (`ai_usage_report.py`) has been added
over the collector's CSV, its state and `launchctl`.

## Implementation status

| Area | Status |
| --- | --- |
| Token source, generator and drift check | Done |
| Reporting layer and JSON contract | Planned, milestone 1 |
| Popover overview, provider detail, History window, shared store, Collect now | Planned, milestone 1 |
| Monitoring view, Settings, service start/stop, pause/resume, notifications, open at login | Planned for later tasks; pause needs a new collector capability |
| Visual baselines | Pending (waived until verification) |
