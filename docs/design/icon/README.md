# Icon design bucket

The ai-usage app has no custom icon artwork yet. It uses system SF Symbols.

| Use | Symbol | Status |
| --- | --- | --- |
| Menu-bar glyph (app mark) | `waveform.path.ecg`, template rendering | Proposed in the handoff (`IMPLEMENTATION.md` §12) and used in milestone 1 |
| Badge: collecting | `arrow.triangle.2.circlepath` | Proposed |
| Badge: paused | `pause.circle.fill` | Proposed; depends on a pause capability that does not exist yet |
| Badge: stopped | `stop.circle.fill`, with the glyph dimmed | Proposed |
| Badge: attention | `exclamationmark.circle.fill` | Proposed |

- **Shared sources:** the accepted glyph state candidates are in [`../app/source/ai-usage-design-0.1.1/menu-bar-icons.html`](../app/source/ai-usage-design-0.1.1/menu-bar-icons.html). The prototype's outline icon family is in [`../app/source/ai-usage-design-0.1.1/app/icons.js`](../app/source/ai-usage-design-0.1.1/app/icons.js). These are referenced here rather than copied.
- **Licensing:** SF Symbols are used under Apple's SF Symbols license, as system-provided symbols in an Apple-platform app. No symbol artwork is redistributed.
- **App icon (Dock/Finder):** none. The app is `LSUIElement` (menu-bar only). A Finder icon is a later task, recorded in `assets.yml` as optional.
