---
status: resolved
owner: Jonathan
updated: 2026-10-05
---

# Test suite stopped the installed collector (2026-10-05)

## Summary

The black-box `uninstall` tests in `test_ai_usage_service.py` isolate `HOME`
but still resolved the absolute `/bin/launchctl`. The LaunchAgent label
`codes.porta.ai-usage` is global per user. Running `make check` or `make test`
on a Mac where the collector is installed therefore booted out the developer's
real collector.

## Confirmed facts

All times are America/Denver. Source: `~/.ai-usage/collector.log`, read-only.

| Time | Log line | Cause |
| --- | --- | --- |
| 2026-10-05 02:55:14 | `stop requested signal=15`, then `collector daemon stopped` | `make check`, run during the app work on `jp/f/macos-menu-bar-app` |
| 2026-10-05 03:01:47 | `collector daemon started … pid=27864` | Manual recovery: `launchctl bootstrap gui/501 ~/Library/LaunchAgents/codes.porta.ai-usage.plist`. The plist was unchanged since its 02:40 install. |
| 2026-10-05 03:02:54 | `collection completed rows=171 … errors=2` | `poll_on_start` check after the restart |

Effect on collection:

- **Downtime** was 6 min 33 s, with no scheduled check missed. The previous check ran at 02:41:09 and the next was not due until 03:41. The restart also triggered an extra check at 03:02.
- **Event-sourced data** (Claude transcripts, Grok sessions, Antigravity events) is read cumulatively, so none was lost.
- **No observable gap** in usage or quota history resulted from this interruption.

The fix was verified after recovery. Two full suite runs left the daemon's pid
unchanged (`launchctl print` before and after). The suite no longer reaches the
real launchd domain.

## Earlier event, 2026-09-30: cause assumed, not confirmed

| Time | Log line |
| --- | --- |
| 2026-09-30 02:24:25 | last scheduled `collection completed` |
| 2026-09-30 03:18:08 | `stop requested signal=15`, then `collector daemon stopped` |
| 2026-10-05 02:40:56 | `collector daemon started` (reinstall) |

- **Confirmed:** the collector did not run for about 4 days 23 hours. The quota snapshots that are polled (Codex rate limits, Grok billing) have **no readings** for 2026-09-30 03:18 to 2026-10-05 02:40. That gap is permanent. Event-sourced usage was read in when collection resumed.
- **Assumed:** the same test-suite defect caused this stop. The evidence is circumstantial:
  - The `jp/f/claude-session-log-backfill` branch has a commit (`c815cee`) at 2026-09-30 03:18:36, 28 s after the stop.
  - That branch's suite contained the same `uninstall` tests.
- No log line identifies the sender of SIGTERM.

## Other restarts on 2026-10-05 not attributed to this work

- **03:10:** four `one-shot collection` runs.
- **03:26:07 to 03:26:51:** a stop followed by a start (pid 64145), consistent with a reinstall.

Neither matches any command run during this work. Their cause is unknown.

## Fix

- `find_launchctl()` honors `AI_USAGE_LAUNCHCTL`. When the variable is set, whether empty, missing, a directory or not executable, it **fails closed** and never falls back to the system binary. `run_launchctl()` names the bad override in its error.
- `IsolatedHome.environment()` points every collector subprocess at a recording fake that reports the service as not loaded.
- `setUpModule()` points in-process code at a refusing fake, as defense in depth.
- Regression tests:
  - uninstall consults only the fake;
  - every subprocess environment carries the fake;
  - invalid launchers fail closed in-process;
  - `status`, `uninstall` and `install` fail without changing files when the launcher is invalid.
- The fail-closed test was mutation-checked: reintroducing the old fallback on an empty value makes it fail.

The installed collector was **not** reinstalled or restarted to validate the
hook. The hook only affects processes that set the variable.
