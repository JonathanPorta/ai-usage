# App design bucket — ai-usage macOS menu-bar app

This directory holds the accepted design source and provenance for the native
macOS app. It contains no runtime code: tokens, components and tests live under
`macos/`.

| File | Purpose |
| --- | --- |
| [`brief.md`](brief.md) | Design request. **Reconstructed.** The original prompt is not in the archive. |
| [`design.md`](design.md) | Handoff index: what is accepted, how the pieces fit, where implementation starts, and the verification record. |
| [`handoff.yml`](handoff.yml) | Machine-readable handoff (`blessed/design-handoff/v1`), checked by `make design-check`. |
| [`assets.yml`](assets.yml) | Asset inventory (`blessed/design-assets/v1`). |
| `source/ai-usage-design-0.1.1.zip` | The accepted archive, unchanged. SHA-256 `a3a0f5cf…48899fb3e`. |
| `source/ai-usage-design-0.1.1/` | The extracted archive contents, unchanged, including `_handoff/MANIFEST.json` and `CHECKSUMS.sha256`. |
| `references/` | Native visual baselines. **Pending**, waived in `handoff.yml`. |

## Provenance

- **Tool:** Open Design. The project is "ai-usage - Design", in namespace `release-stable`.
- **Accepted handoff:** `ai-usage-design-0.1.1.zip`, generated 2026-09-30T08:26:43Z. It corrects documentation only and follows handoff 0.1.0.
- **Design baseline:** accepted checkpoint `ai-usage-design-checkpoint-0.1.3.zip`. The prototype is unchanged from it.
- **Import (2026-10-05):**
  - Archive SHA-256 matched the expected value.
  - All 13 payload files and both handoff metadata files verified against `_handoff/CHECKSUMS.sha256`.
  - The archive and its extracted contents are stored unchanged.
- **Superseded and earlier archives** (0.1.0, and checkpoints 0.1.2 and 0.1.3) are not imported. Their checksums are recorded in `design.md` so they can be traced.

To re-verify:

```bash
shasum -a 256 docs/design/app/source/ai-usage-design-0.1.1.zip
(cd docs/design/app/source/ai-usage-design-0.1.1 && shasum -a 256 -c _handoff/CHECKSUMS.sha256)
make design-check ARGS=--strict
```

## What the archive's files are

- **Design references:** prototype HTML (`popover.html`, `history-window.html`, the boards, `index.html`), JavaScript (`app/components.js`, `app/icons.js`), CSS (`app/styles.css`) and fixtures (`app/fixtures.js`). None of them are production code.
- **Illustrative data:** every value in `app/fixtures.js` is sample data on a fixed clock (Tue 29 Sep 2026 10:40).
- **Accepted guidance:** `IMPLEMENTATION.md` and `design-notes.md`.

To preview the prototype, serve the extracted folder over HTTP (`python3 -m http.server`) and open `index.html`.
