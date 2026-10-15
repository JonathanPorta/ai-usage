# CLI design bucket

The CLI surface has no visual design. It has two kinds of command:

- **Operator commands** of the collector: `install`, `once`, `status`, `doctor`, `uninstall` and `version`.
- **The reporting command** `ai_usage_report.py`, whose JSON output (`ai-usage/report/v1`) is the machine contract consumed by the macOS app.

The full specification (grammar, flags, exit codes, schema, examples and
acceptance commands) is [`docs/CLI.md`](../../CLI.md). Interaction design for
human-facing output follows the voice rules in [`DESIGN.md`](../../../DESIGN.md#voice-and-brand).
