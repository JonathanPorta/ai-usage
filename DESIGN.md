# Design

Status: accepted
Handoff version: 0.1.1 (based on accepted design checkpoint 0.1.3)
Handoff: [`docs/design/app/handoff.yml`](docs/design/app/handoff.yml)
Verification: **outstanding**. The design is accepted, but nothing has been rendered or tested interactively yet. See [`docs/design/app/design.md`](docs/design/app/design.md#verification).

This file holds the accepted product character and design language for the
ai-usage macOS menu-bar app. Open Design and later design work continue from
it. Engineering usage lives in [`DESIGN_SYSTEM.md`](DESIGN_SYSTEM.md). Accepted
source material and provenance live under [`docs/design/app/`](docs/design/app/).

## Product character

ai-usage is a quiet, trustworthy instrument. It answers "how much have I used,
how much is left, and can I trust these numbers?" for several AI coding tools at
a glance, without asking for attention when nothing is wrong.

- **Calm when healthy.** The healthy state is one quiet line, for example "✓ Monitoring · Checked 2 min ago · next in 58 min".
- **Loud only when someone can act.** At most one prominent notice appears at a time. The rest collapse into "+N more in Monitoring".
- **Honest about data.** Every number states what it is (used vs remaining, measured vs collected, reported vs entered) and how old it is. Missing data is never drawn as zero. Different providers' accounting is never mixed.
- **Fast.** Opening the popover shows cached state immediately. It never waits for a check.

## Color

Semantic intent only. Executable values are in `macos/design/tokens.json`.

| Intent | Use |
| --- | --- |
| Background / panel / line | Neutral greys; cards are panels with a hairline border |
| Text / muted | Primary values and labels; secondary captions and timestamps |
| Accent (blue) | Input bars, meters, links, selected state, today highlight |
| Accent soft | Output bars and secondary series |
| Success (green) | The healthy status glyph only |
| Warning (amber) | Usage limits (gauge symbol) **and** stale readings (clock symbol), always with the reading's age |
| Danger (red) | Collector or source failures (✕ or lock symbol), always with an action |

Every semantic color has a light and a dark value. Limits, staleness and
failures must stay distinguishable without color, through symbol and text.

## Typography

System font (SF Pro). Sizes: caption 12, body 13, lead 15, title 20, figure 24.
Chart text is never smaller than 11 pt. Large figures carry the value, and a
muted caption beside or below them gives the unit and basis ("38% remaining",
"today so far · input + output").

## Spacing and grid

4-pt base scale (4, 8, 12, 16, 20, 24, 32). The popover is 420 pt wide. Its
height is bounded by `min(780 pt, screen height − 96 pt)`. Card radius is 10,
popover radius 15.

## Layout and composition

- **Popover:** a pinned header (title, app mark), a pinned status row, and at most one notice. Provider cards scroll. A pinned footer holds Collect now, Open history and Settings.
- **Provider card:** a name row. Then one compact cell per quota window (label, % used or remaining, meter, reset time). Then today's total beside a 7-day bar chart, and finally freshness captions.
- **Provider detail:** replaces the overview inside the popover. Order: Back, then heading and status, then the primary chart (Tokens 7 or 30 days, or Quota with a window picker), quota windows, today, models, collapsible Cost and accounting, and collapsible Sources and diagnostics (opens automatically when a source has a problem). Last comes Open in History.
- **History:** a separate, resizable window (minimum about 640×460). A provider list sits beside a Tokens | Quota choice, the range, summary tiles, the chart, the table and accounting.

## Components

Status row, notice, provider card, quota cell and meter, 7-day mini bar chart,
token bar chart, quota line chart, stat tile, segmented control, disclosure
section, footer action. All of them read from one shared observable state.

## Motion and interaction

Short, functional motion (150 ms fast, 240 ms medium, ease-out). It is disabled
under Reduce Motion, where the checking spinner becomes static. Keyboard:

- `↑`/`↓` move between the status row and the cards.
- `⌘R` runs Collect now.
- `⌘,` opens Settings.
- `Esc` goes back, then closes.
- A focused chart takes `←`/`→`/`Home`/`End`, with a live readout.

## Voice and brand

Plain, specific and calm. Say "Check partly complete", not "Error!". Name the
provider, the source and the time, for example "Codex quota · No reading since
08:38". Label quantities precisely: "% remaining", "per month · entered by
you", "reported usage cost".

## Chart semantics

| Case | Mark |
| --- | --- |
| Today (incomplete) | Dashed outline over a light fill, labelled "Today" |
| Missing reading | Dashed box, never a bar of zero height |
| Measured zero | Solid 2 pt baseline tick |
| Before collection began | Shaded "Not collected" band / dotted baseline |
| Quota reset | Dashed vertical marker; the line breaks at the reset |
| Gap in readings | Line break |
| Stale window | Last value extended to now as a dashed amber line |

## Anti-patterns

- Summing or comparing tokens across providers; summing quota snapshots.
- Converting quota percentages into tokens or dollars.
- Treating a recent successful check as proof that every reading is current.
- Hiding a fresh limit warning because another window of the same provider is stale.
- Drawing missing data as zero, or labelling an old reading "Today".
- Mixing reported usage cost with the subscription price you entered.
- Stopping the collector when the popover closes or the app quits.
- Showing proposed capabilities (pause, per-provider progress, Claude "Recent sessions") as if they exist.
