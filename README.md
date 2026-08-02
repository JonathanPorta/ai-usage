# ai-usage

`ai-usage` is a zero-inference usage collector for AI coding tools on macOS. It
records token activity, quota snapshots, and known costs in
`~/.ai-usage/usage.csv`, without submitting prompts or creating model turns.

It installs as a per-user `launchd` service, polls once an hour by default, and
reloads its JSON config between polls.

## What it collects

| Provider | Source | Provider process started? | Capacity / cost caveats |
| --- | --- | --- | --- |
| OpenAI Codex | `account/usage/read` and `account/rateLimits/read` through `codex app-server` | Yes, only when `codex` exists | Quota is a weighted percentage, not an unused-token count. Subscription price is configured manually. |
| Claude Code | Read-only `~/.claude/stats-cache.json` | No | The cache is an undocumented local format. Claude Max subscription capacity is not available through a supported public API, so the collector does not scrape the TUI or call a private endpoint. |
| Antigravity CLI | Official status-line callback | No polling command | The callback writes sanitized token deltas and quota snapshots as Antigravity runs. Raw conversation IDs are hashed; email, paths, prompts, and transcripts are not retained. |
| Grok Build | Local session JSONL and fresh local billing log; billing-only `x.ai/billing` fallback | Only for a stale/missing billing snapshot, and only when `grok` and its auth cache exist | The fallback fetches billing metadata and never samples a model. Consumer credit percentages and cent values are not the recurring SuperGrok price. |
| Gemini CLI | Existing local OpenTelemetry file | No | Automatic telemetry setup is **off by default** because raw Gemini telemetry includes account identifiers. See [Optional Gemini telemetry](#optional-gemini-telemetry). |

Missing or disabled providers are skipped. `doctor` performs filesystem-only
discovery and never executes a provider binary.

## Requirements

- macOS
- Python 3.9 or newer
- Any supported provider CLIs you actually use; none are required

There are no third-party Python dependencies.

## Install

```bash
git clone https://github.com/JonathanPorta/ai-usage.git
cd ai-usage
python3 ai_usage_service.py install
```

The installer:

1. discovers installed providers without executing them;
2. copies itself to `~/.ai-usage/collector.py`;
3. creates `~/.ai-usage/config.json`, `usage.csv`, `collector.log`, and internal
   state/cache files;
4. attaches the Antigravity status-line callback only when `agy` exists and no
   custom `statusLine` is already configured; and
5. loads `~/Library/LaunchAgents/codes.porta.ai-usage.plist`.

It does not require `sudo`. If an older collector used `~/.ai-usage` as a file,
the installer preserves it beside the new directory with a `.legacy-*` suffix.
An incompatible existing CSV is also moved aside rather than appended to with
the wrong schema.

To write the files without starting `launchd`:

```bash
python3 ai_usage_service.py install --no-start
```

## Everyday commands

```bash
# Confirm which providers and local sources exist; executes no provider commands
~/.ai-usage/collector.py doctor

# Collect one snapshot now
~/.ai-usage/collector.py once

# Check the LaunchAgent
~/.ai-usage/collector.py status
launchctl print "gui/$(id -u)/codes.porta.ai-usage"

# Follow the rotating service log
tail -f ~/.ai-usage/collector.log

# Inspect the CSV
column -s, -t < ~/.ai-usage/usage.csv | less -S
```

## Configuration

Edit `~/.ai-usage/config.json`. The service reloads it between polls, so changing
the interval, output paths, provider enablement, or monthly prices does not
require a service restart.

```json
{
  "poll_interval_seconds": 3600,
  "poll_on_start": true,
  "paths": {
    "usage_csv": "~/.ai-usage/usage.csv",
    "log_file": "~/.ai-usage/collector.log",
    "state_file": "~/.ai-usage/state.json",
    "cache_dir": "~/.ai-usage/cache"
  },
  "providers": {
    "codex": {
      "enabled": true,
      "monthly_subscription_usd": 20
    },
    "claude": {
      "enabled": true,
      "monthly_subscription_usd": 100
    },
    "antigravity": {
      "enabled": true,
      "monthly_subscription_usd": null
    },
    "grok": {
      "enabled": true,
      "monthly_subscription_usd": null
    },
    "gemini_cli": {
      "enabled": true,
      "configure_telemetry": false,
      "monthly_subscription_usd": null
    }
  }
}
```

The installed config contains all advanced source paths, timeouts, and local
scan budgets. Set a provider's `executable` to an absolute path if normal `PATH`
discovery finds the wrong installation.

`monthly_subscription_usd` is intentionally manual: consumer billing metadata
generally does not expose the price on your receipt. It is recorded once per
calendar month, and again only if you change the configured value.

## Optional Gemini telemetry

Gemini CLI has no documented machine-readable consumer quota endpoint. The
collector can parse its official `gemini_cli.api_response` telemetry events, but
Gemini's raw telemetry includes `session.id`, `installation.id`, and
`user.email`, even when prompt logging is disabled. For that reason, automatic
setup is opt-in.

To enable it:

1. set `providers.gemini_cli.configure_telemetry` to `true`;
2. rerun `~/.ai-usage/collector.py install`; and
3. inspect `~/.gemini/settings.json`.

The installer only creates a telemetry block when none exists, uses local file
output, sets `logPrompts` and traces to `false`, and preserves any existing
telemetry configuration. The CSV stores only model names and documented token
counts, but the raw telemetry file remains sensitive and is protected by the
private `~/.ai-usage` directory.

## CSV semantics

The CSV is long-form. Important columns include `provider`, `category`,
`metric`, `record_kind`, `scope`, `period_start`, `period_end`, `value`, `unit`,
`resets_at`, `source`, and `status`.

Use `record_kind` when aggregating:

- `delta` and `event_total`: sum within the desired time range.
- `period_total`: take the latest row for each provider/metric/scope/period,
  then sum distinct periods. A later row can revise an earlier daily or weekly
  total.
- `snapshot`: do not sum; plot or take the latest observation.
- `monthly_rate`: take the latest value for the month; do not sum snapshots.
- `interval_total`: an operational count for one collector interval.

For used-versus-unused capacity plots, use quota rows whose metric is
`used_percent` or `remaining_percent`. Not every provider exposes consumer
capacity. A percentage-based allowance cannot honestly be converted into
unused tokens or wasted dollars without a provider-supplied absolute
denominator.

## Why polling does not consume model usage

- Codex receives only initialization plus the two account metadata methods; no
  thread or turn method is sent.
- Claude Code, Gemini CLI, Antigravity, and Grok token history are local file or
  callback reads.
- Grok's fallback calls only the first-party `x.ai/billing` ACP extension, with
  filesystem and terminal capabilities disabled.
- Provider binaries are checked for existence and executability before any
  subprocess is started.

Relevant primary documentation:

- [Codex app-server](https://developers.openai.com/codex/app-server/)
- [Antigravity status-line payloads](https://antigravity.google/docs/cli/statusline)
- [Gemini CLI telemetry](https://geminicli.com/docs/cli/telemetry/)
- [Grok Build CLI](https://docs.x.ai/build/overview) and the first-party
  [`x.ai/billing` implementation](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-shell/src/extensions/billing.rs)

## Uninstall

```bash
~/.ai-usage/collector.py uninstall
```

This unloads and removes the LaunchAgent, removes only provider settings that
exactly match integrations managed by this collector, and preserves config,
CSV data, logs, and raw caches. If a provider settings file cannot be inspected
safely, hook files are preserved rather than leaving a broken command behind.

## Tests

```bash
python3 -m unittest -v
```

The fixture suite uses fake provider binaries and asserts that only the Codex
account methods and Grok billing method are sent. It does not contact any AI
provider.
