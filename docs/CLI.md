---
status: approved
owner: Jonathan
updated: 2026-10-05
---

# CLI and reporting contract

## Collector commands (existing)

```text
python3 ai_usage_service.py install [--config PATH] [--no-start]
~/.ai-usage/collector.py once      [--config PATH]   # one collection, flock-serialized with the daemon
~/.ai-usage/collector.py daemon    [--config PATH]   # polling loop (launchd)
~/.ai-usage/collector.py status                      # LaunchAgent status
~/.ai-usage/collector.py service-status              # JSON: installed, state, pid, disabled   (2.2.0)
~/.ai-usage/collector.py start                       # enable + bootstrap the LaunchAgent        (2.2.0)
~/.ai-usage/collector.py stop                        # disable + bootout; stays stopped at login (2.2.0)
~/.ai-usage/collector.py configure --config PATH --set KEY=JSON [...]                          (2.2.0)
~/.ai-usage/collector.py once --progress             # also prints one JSON line per provider   (2.2.0)
~/.ai-usage/collector.py doctor    [--config PATH] [--json]
~/.ai-usage/collector.py uninstall [--config PATH]
~/.ai-usage/collector.py version
```

`once` exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Every row was ok |
| 2 | The transaction was committed, but at least one row has `status=error` (partial) |
| 1 | Config, IO or state error; nothing was committed |

`configure` accepts:

- `poll_paused=true|false`;
- `poll_interval_seconds=N` (60 to 604800);
- `providers.<id>.enabled=true|false`;
- `providers.<id>.monthly_subscription_usd=N|null`.

It is locked, validated against the merged config, atomic and mode-preserving,
and leaves every other key untouched. It exits 1 and changes nothing on any
invalid value.

`once --progress` lines look like
`{"event":"provider","provider":"codex","index":1,"total":4}`.

With `poll_paused: true` the daemon keeps running and skips scheduled checks,
re-reading config every 15 s. Clearing it schedules the next check one interval
later. `once` and event-driven collection are never paused.

`once` and `daemon` take an exclusive `flock` on `<state_file>.lock` for the
whole collect-and-commit cycle. Two collections never interleave. A `once`
started during a scheduled check waits for it to finish.

## Reporting command (new)

```text
python3 ai_usage_report.py [--config PATH] [--now ISO8601] [--days N]
                           [--service auto|skip] [--pretty]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--config` | `~/.ai-usage/config.json` | Collector config. Paths for the CSV, state and log come from it. |
| `--now` | current time | Evaluation clock, used by tests and fixtures. |
| `--days` | 90 | Daily history length (7–400). |
| `--service` | `auto` | `auto` runs `launchctl print gui/<uid>/codes.porta.ai-usage`; `skip` reports `service.state: "unknown"`. |
| `--pretty` | off | Indented JSON. |

**Guarantees:** the command is read-only. It never executes a provider binary,
never runs a collection, never takes the collector's lock, and writes nothing to
disk. It reads the CSV, the last 256 KiB of `collector.log`, the config and,
optionally, `launchctl`. It reads `state.json` only for `history_date`.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | A report was written to stdout. A missing CSV gives an empty report with `collection.last_attempt_at: null`. |
| 1 | The config or paths are invalid. The error goes to stderr as `ai-usage-report: <message>` and stdout is empty. |

`make report` runs it against `AI_USAGE_CONFIG` (default
`~/.ai-usage/config.json`). `make report-sandbox` runs it against the test
fixture.

## Report schema `ai-usage/report/v1`

All timestamps are ISO-8601 UTC with a `Z` suffix. Dates (`YYYY-MM-DD`) are
local calendar dates in `timezone`. A field that is `null` means *unknown or not
reported*. It never means zero.

```jsonc
{
  "schema": "ai-usage/report/v1",
  "generated_at": "2026-10-05T08:45:00Z",
  "timezone": "America/Denver",
  "today": "2026-10-05",
  "collector": {
    "version": "2.1.0",
    "config_path": "/Users/me/.ai-usage/config.json",
    "usage_csv": "/Users/me/.ai-usage/usage.csv",
    "log_file": "/Users/me/.ai-usage/collector.log",
    "interval_seconds": 3600,
    "csv_rows": 329277,
    "installed_version": "2.1.0",      // VERSION read from the LaunchAgent's script; null if unknown
    "capabilities": { "pause": false, "progress": false, "service_control": true, "settings": true }
  },
  "thresholds": { "stale_after_seconds": 7200, "grok_billing_stale_after_seconds": 10800 },
  "service": {
    "state": "running",          // running | loaded | stopped | not_installed | unknown
    "pid": 63638,
    "disabled": false,           // launchd disable flag (true after Stop: stays stopped at login)
    "detail": "launchctl: state = running"
  },
  "schedule": {
    "state": "active",           // active | paused | unknown
    "interval_minutes": 60,
    "pause_supported": false,    // installed collector honors poll_paused (2.2.0+)
    "pause_requested": false,    // poll_paused is set in config.json
    "next_scheduled_at": "2026-10-05T09:41:09Z"   // null when not running or unknown
  },
  "collection": {
    "last_attempt_at": "2026-10-05T08:40:56Z",
    "last_attempt_result": "success",       // success | partial | failed | null
    "last_success_at": "2026-10-05T08:40:56Z",
    "failures": [                          // from the latest attempt only
      { "provider": "codex", "source": "rate-limits", "kind": "quota",
        "at": "…", "message": "…", "auth": false }
    ],
    "summary": {
      "providers_attempted": 4, "providers_updated": 4, "providers_partly": 0,
      "providers_failed": 0, "skipped": ["Gemini CLI"]
    }
  },
  "providers": [ /* Provider, in display order: codex, claude, antigravity, grok, gemini_cli */ ]
}
```

### Provider

```jsonc
{
  "id": "codex", "name": "Codex", "product": "OpenAI Codex",
  "enabled": true,
  "setup": "ready",            // ready | not_detected | disabled | not_configured | waiting
  "setup_note": null,
  "usage": {
    "status": "current",       // current | stale | none
    "stale_cause": null,       // failed | not_collected | source_old | null
    "becomes_stale_at": "…",   // when a current reading turns stale without new data (or null)
    "becomes_stale_cause": "not_collected",
    "measured_at": "…",        // newest measurement time (event time or poll time)
    "collected_at": "…",       // last successful read of the usage source
    "record_kind": "period_total",
    "mode": "polled",          // polled | event
    "split": "total_only"      // input_output | total_only
  },
  "coverage_start": "2026-07-08",          // first local date with data, or null
  "today": { "date": "2026-10-05", "input": null, "output": null, "total": 776000,
             "cache_read": null, "cache_write": null, "as_of": "…", "partial": true } ,
  "days": [ /* Day, oldest → today, exactly --days entries */ ],
  "models_today": [ { "name": "claude-opus-5-5", "tokens": 512000 } ],  // input+output; [] if not reported
  "quota": {
    "status": "current",       // current | stale | mixed | unavailable | unsupported | none
    "stale_cause": null,
    "measured_at": "…", "collected_at": "…",
    "note": null,
    "windows": [ /* Window */ ]
  },
  "costs": {
    "reported": { "amount_usd": 3.18, "basis": "On-demand usage reported by xAI billing",
                  "period_start": "…", "period_end": "…", "measured_at": "…" },   // or null
    "reported_note": "Codex does not report a usage cost.",
    "api_equivalent": { "amount_usd": 12.4, "basis": "API-equivalent cost reported by Grok sessions",
                        "range_days": 30 },                                       // or null
    "subscription": { "monthly_usd": 20, "source": "config", "entered_by_user": true }  // or null
  },
  "sources": [ /* Source */ ],
  "notes": [ "…" ]
}
```

### Day

```jsonc
{ "date": "2026-10-04",
  "state": "measured",       // measured | zero | missing | not_collected | partial (today)
  "input": 684000, "output": 92000, "total": 776000,
  "cache_read": 3120000, "cache_write": null,
  "incomplete_events": 0 }
```

- `measured`: at least one usage record for the day.
- `zero`: the source was read successfully and covers the day, but no usage was recorded. This is a real zero.
- `missing`: no record for a day the source should cover. This is a gap, not zero.
- `not_collected`: the day is before `coverage_start`.
- `partial`: today, which is not over yet. The figures are a running total "as of" `today.as_of`.

`total` is input + output when the provider splits them. Otherwise it is the
provider's own total. Cache tokens are never included in `total`.

### Window

```jsonc
{
  "id": "codex:primary", "label": "Weekly limit", "short": "Weekly",
  "basis": "remaining",        // remaining | used — which percentage `percent` is
  "derived": true,             // true when converted (100 − used)
  "percent": 84,               // null when no reading
  "window_seconds": 604800,
  "measured_at": "…", "collected_at": "…",
  "resets_at": "…",
  "reset_passed": false,       // measured_at < resets_at <= now
  "status": "current",         // current | stale | none
  "stale_cause": null,         // auth | failed | not_collected | source_old | reset_passed
  "becomes_stale_at": "…",     // earliest of: last read + stale_after, reading + threshold, resets_at
  "becomes_stale_cause": "reset_passed",
  "omitted": false,            // absent from the latest successful quota read
  "retired": false,            // not reported for > 2× its length (min 24 h)
  "limit": null,               // reached | low | null — only for current windows
  "readings": [ { "t": "…", "percent": 84, "resets_at": "…" } ],
  "resets": [ "…" ],           // inferred reset instants (a reading's resets_at that has passed)
  "ranges": [ "24h", "7d" ]    // chart ranges offered for this window
}
```

Limits: `reached` when remaining is 0 or below (used 100 or above). `low` when
remaining is 10 or below. A limit is only flagged on a **current** window. Each
window's freshness is evaluated on its own.

### Source

```jsonc
{
  "id": "rate-limits", "role": "quota",    // usage | quota | history | both
  "label": "Rate limits",
  "kind": "Codex account API, read-only (account/rateLimits/read)",
  "status": "ok",          // ok | failed | stale | auth | off | waiting | not_detected
  "last_attempt_at": "…",  // latest attempt that included this provider
  "last_read_at": "…",     // latest successful read (unchanged by failures)
  "measured_at": "…",
  "error": null
}
```

## Merge and freshness rules the report guarantees

1. Aggregation by `record_kind`:
   - `delta` and `event_total` are summed within the day.
   - `period_total` takes the latest row per (metric, scope) and then sums distinct periods.
   - `snapshot` values are never summed; each one is a reading.
   - `monthly_rate` takes the latest value in the month.
   - `interval_total` is diagnostics only.
2. A failed source keeps its previous measurements and `last_read_at`, and is marked `failed` with the attempt's error. Because failures come from the latest attempt, a successful usage read never refreshes a failed quota source.
3. A disabled provider (`availability/enabled = false`) keeps its cached data. Its sources become `off`, and its usage and quota age normally.
4. Collector-level failures (`collection cycle failed` in the log after the latest transaction) set `last_attempt_result: failed` and change no measurement.
   - **What counts as an attempt:** a CSV transaction with at least one check-time row (any row except event usage rows, which carry their event time). A check that finds nothing writes no transaction; its `collection completed rows=0` log line still counts as the attempt.
5. Every quota window is evaluated independently. In priority order, a window is stale because of:
   - `auth`;
   - `failed`;
   - `not_collected`, when there has been no successful quota read within `stale_after`;
   - `source_old`, when the reading is older than its threshold or the window was omitted;
   - `reset_passed`.

## Acceptance commands

```bash
make test                                  # includes report contract tests
make report-sandbox | python3 -m json.tool # fixture report
make report | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["schema"], len(r["providers"]))'
```
