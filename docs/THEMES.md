# Themes

The AI Usage app reads an optional theme file. Without it the app uses its built-in colors
from `macos/design/tokens.json`.

## Where

`~/Library/Application Support/AI Usage/themes.json`: the app's state directory, the same
folder as its report cache. It belongs to the app, not the collector. Replacing the app or
reinstalling the collector does not touch it. If `AI_USAGE_STATE_DIR` is set, the file is
read from that directory instead, which keeps sandboxes and tests isolated.

Settings → Theme → **Open theme file** creates a starter file if none exists, then opens
it. The starter has example schemes and assigns none of them. **Reload themes** applies
your edits right away; you don't need to restart or rebuild. The app also reads the file
at launch.

## Format (version 1)

```json
{
  "version": 1,
  "global": "slate",
  "providers": { "claude": "warm", "claude-az": "violet" },
  "schemes": {
    "slate":  { "light": { "accent": "#1769AA", "background": "#F3F5F7" },
                "dark":  { "accent": "#80B7F7", "background": "#25272B" } },
    "warm":   { "light": { "accent": "#9A4A24" }, "dark": { "accent": "#EAAF86" } },
    "violet": { "light": { "accent": "#7245B8" } }
  }
}
```

- `version`: required, and must be `1`.
- `global`: a scheme name, or `null`. It applies to everything: the popover, Settings,
  Monitoring and History.
- `providers`: maps a provider **instance id** to a scheme name. Settings shows each id;
  added accounts have their own ids, so two Claude accounts can use different schemes. An
  id that isn't in the current report is kept and applies again when that instance
  returns.
- `schemes`: each name maps to `light` and/or `dark` objects, which map roles to `#RRGGBB`
  colors.
- A key that starts with `_` or `$` is a note and is ignored, so you can use `"_comment"`.

## Roles

These are the semantic token names: `background`, `panel`, `text`, `muted`, `line`,
`lineStrong`, `hover`, `track`, `accent`, `accentSoft`, `accentBackground`, `success`,
`warning`, `warningBackground`, `danger`, `dangerBackground`, `focus`, `series1` to
`series4`. `surface` is accepted as another name for `panel`. Component colors follow their role. For example, card backgrounds follow
`panel`, meter fills and input bars follow `accent`, output bars follow `accentSoft`, and
All-view account series follow `series1` to `series4`.

## Resolution

Each role and each appearance is resolved separately: **built-in → global scheme →
provider scheme**.

- A role that a scheme leaves out keeps the color from the layer below.
- Light and dark are resolved separately. If a scheme has no `dark` object, or leaves a
  role out of it, dark mode takes that role from the layer below (the global scheme,
  then built-in). Light values are never copied into dark mode.
- A provider's scheme colors that provider's overview card, its detail page and its
  History page, including the chart series.
- The All view groups accounts by tool and keeps its legend identities. Its account
  series always use the global `series1` to `series4`, so a per-account scheme never
  relabels or merges accounts.

## Validation and safety

- The file is read locally as JSON data and nothing else. It is capped at 64 KB. Nothing
  in it is executed or fetched.
- Only a regular file is read. A symlink is followed only if it points to a regular
  file. A directory, FIFO or other special file at that path is treated as invalid.
- If the file is invalid (wrong version, bad JSON, a color that isn't `#RRGGBB`, an
  unknown role or key, or a reference to a scheme that isn't defined), the app keeps the
  last valid theme. If no valid theme was ever loaded, it uses the built-in colors.
  Settings shows a single line naming the problem and where it is, for example
  `themes.json: schemes.slate.light.panel: "#F3F5F" is not a "#RRGGBB" color`.
- After each successful load, the app saves a copy of the file next to it as
  `themes.last-valid.json` (written atomically, readable only by you). If the app starts
  and `themes.json` is invalid, it uses that copy and says it is keeping the last valid
  theme.
- Deleting `themes.json` is a deliberate removal, not an invalid edit. After Reload, or
  at the next launch, the app uses the built-in colors.
- Status always keeps its meaning. Warnings, failures, success and focus are always shown
  with a symbol and text, so they never depend on color alone.
- The app warns about colors but never blocks them. Settings shows a warning when a
  scheme you set causes any of these:
  - `text` or `muted` on `background` or `panel` below 3:1
  - `success`, `warning`, `danger`, `focus` or `accent` on `panel` or `background` below
    3:1
  - `text` on `accentBackground`, `warningBackground` or `dangerBackground` below 3:1
  - `success`, `warning` and `danger` so close in color that they look alike

  Your colors are still applied exactly as written.
