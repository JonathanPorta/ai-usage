# Fix: a kept Claude quota reading was reported as current

Approved by the owner on 2026-10-22, as a focused correction before AC-10 closes.

The intermittent 30-second `/usage` timeout is out of scope. It is recorded below as a known limitation. This fix does not change timeouts or redesign the refresh.

## Defect

The collector reads each Claude account's limits from Claude Code's cache. When the cache is too old, it runs `/usage` once to refresh it. If the refresh fails (it times out, exits with an error, or Claude Code answers from its cache), the collector does two things:
- it writes an error row (`subscription_usage_query`);
- it keeps the old reading as quota rows marked `stale`.

The report then:
- counted the kept rows as a successful read. It set the source `ok`, cleared the error, and left the failure out of `collection.failures`, so a collection could report `success`;
- dated the reading by its collection time, because the rows carried no measurement time. A cache fetched two hours earlier showed as `current`, "measured" at the collection.

This behavior predates the plugin work: the `provider_errors` parity golden file, captured from the pre-refactor baseline, records it. It surfaced in AC-10 step 8, where the default account's refresh timed out and the report showed its kept reading as current.

## Fix

| Part | Change |
| --- | --- |
| Collector (`ai_usage_plugins/claude.py`) | Each transaction's Claude `/usage` rows are preceded by a `freshness` row, `usage_source_age_seconds`. Its value is the collection time minus Claude Code's `fetchedAtMs`. It has the same source and status as the readings it dates. |
| Report: failure (`ai_usage_report.py`) | For a quota source, a read that returns an error and only `stale` fallback rows is a failure. Other sources, such as the daily stats cache, keep their rule. The source is `failed` with the error, `last_read_at` is unchanged, and the collection lists the failure (`partial`). The values stay visible; their windows are `stale` with cause `failed`. |
| Report: measurement time | A reading is dated by its fetch time. Ages are kept per (provider id, transaction), so two accounts collected in one transaction never share them. The latest reading is chosen by collection order, not by measurement time. |
| Older rows | Rows written before the age row existed keep their collection time. The exception is a kept fallback (`claude-usage-cache`, `stale`), whose measurement time is unknown: its value is shown with `measured_at: null`, and it is left out of `readings`. No time is invented for it. Such a window is never current, and its reset counts as passed once due. |
| Report: kept readings | A Claude reading kept after a failed refresh stays a fallback, dated or not. Until a good read replaces it, its window is never current: `failed` while that failure is the latest attempt, usually `source_old` after an attempt that read no limits (which is not a new failure), or `reset_passed`/`not_collected` when those apply. |
| Report: bounds | A measurement time is never after the report's `now`, or more than two minutes after its collection, so a skewed `fetchedAtMs` can't keep a reading current. |

The report schema is unchanged: no new keys, and `measured_at` was already nullable in the app's model.

## Intentional output changes

The parity golden files are unchanged; their SHA-256 test still passes. `tests/parity/driver.py` declares these differences in `INTENTIONAL_CHANGES` and applies them to the golden files:
- one age row per transaction with Claude `/usage` rows, and the matching "Appended N rows" and `csv_rows` counts;
- Claude quota `measured_at` and reading times moved to the fetch time (60 s earlier in `all_providers`, and two hours earlier in `provider_errors`). Where the fetch time now ages a reading out first, `becomes_stale_*` follows it;
- in `provider_errors`, Claude's quota is `stale`/`failed`, its source is `failed` with the error, and the collection lists the failure (one provider moves from updated to partly).

The carry-forward runs, where an older collector's rows are read by this one, keep the older readings at their collection time beside the newly dated ones.

## Validation

`tests/test_stale_quota_reporting.py` checks the reported outcome end to end, using a fake `claude` in an isolated sandbox. Each test failed on `main` (`1c89e53`) and passes on this branch.

| Test | On `main` |
| --- | --- |
| Old cache plus a refresh timeout: stale/failed, dated two hours earlier, error kept, collection `partial` | `current`, measured at the collection, no failure |
| Fresh refresh: current, dated by its fetch (30 s before the collection) | dated by the collection time |
| Two accounts in one collection, only the default's refresh fails: the default is stale/failed, `claude-az` is current, one failure | both `current` |
| A kept fallback written by an older collector: value shown, `measured_at: null`, not plotted, failure listed | `current`, measured at the collection |
| An undated kept reading after a later attempt that read no limits: stale (`source_old`), never current | `current` |
| A dated kept reading (fetched 11:10, kept at 11:30) after an 11:45 attempt with subscription usage off: stays stale (`source_old`), source `stale`, no new failure. A good 11:50 read, through `/usage` or a newer cache read, then makes it current and clears the error. | `current`, `stale_cause: null` (the window disagreed with its stale source) |
| An undated kept reading whose reset is due: `reset_passed` | not passed |
| An old daily stats cache with a read error stays `stale`, and the collection `success` (a guard) | passes; fails with the first draft's rule for every source |

Mutation checks:
- keying ages by transaction alone, so that the two accounts share them, fails the two-account test;
- applying the fallback rule to every source fails the stats-cache guard.

Independent review: no blocking issues. It raised five findings, all fixed above: the rule's scope, undated readings shown as current, `reset_passed` for undated readings, unbounded ages, and the parity transform reading errors from the whole CSV. A second review at `24ed38f` found that a *dated* kept reading became current after a later attempt that read no limits. The report had dropped the rows' own stale status. Fixed, with the regression above.

A sixth finding from the first review predates this work and is left as it is: `last_success_at` counts an attempt whose only Claude quota rows are a fallback. The provider is still `partly`, and the failure is listed.

App: the app was built and rendered offscreen from an isolated sandbox that reproduces the timeout, using its existing states. The renders are in the session scratchpad and are not committed. They show:
- the overview's "Claude Code quota check failed" notice with the timeout message;
- each window's "Check failed · last reading <fetch time>";
- "Stale" in the detail view;
- `claude-az`, collected in the same run, current and measured at its own fetch time.

## AC-10 step 8, recorded precisely

- **Isolation (passed):** after the restart, the installed service collected both accounts in one collection.
- **The default account's quota:** its `/usage` refresh in that collection "did not finish within 30s". The collector kept Claude Code's reading from 07:09:02Z, marked stale. The 2.3.0 report showed it as current, which is the defect fixed here.
- **Known limitation, recorded separately:** the 2.2.0 collector hit the same timeout at 06:07 and 07:08 UTC before the reinstall, and refreshed successfully on other hours. The timeout is intermittent, predates 2.3.0, and is not an account-isolation failure.

AC-10 stays pending until:
1. this correction is approved and installed (done: merged as `ebe2b71`, installed 2026-10-22);
2. the owner compares each account's `/usage` with a report generated at about the same time;
3. the owner confirms both accounts' cards in the app overview.

## Follow-up: Claude Code's one-hour reuse (2026-10-23)

Every "Collect now" since the reinstall reported both accounts' plan limits as failed ("claude /usage did not fetch fresh usage"), while the hourly collections refreshed. Each click came 33 to 57 minutes after the last fetch; the hourly runs come about 60½ minutes apart. Claude Code 2.1.309's own code answers `/usage` from `cachedUsageUtilization` while it is under an hour old (`fetchedAtMs`), without fetching, so no run within that hour can be fresher.

Fix: a reading Claude Code returns unfetched, but fetched within the hour before the run, is kept as an `ok` reading from `claude-usage-cache`, dated by its fetch. Older than that, it is still the failed refresh above. `tests/test_stale_quota_reporting.py` covers both sides; the within-the-hour test failed before the fix. The parity golden (`provider_errors`, two hours old) is unchanged.
