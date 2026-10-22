"""A Claude /usage refresh that fails keeps the last reading, and the report says so.

The fix for tasks/phase-2/fix-stale-quota-reporting.md: a reading is dated by
when Claude Code fetched it (`fetchedAtMs`), a kept reading after a failed
refresh is stale with the refresh's error, and accounts collected together keep
their own freshness. Every test uses an isolated sandbox and a fake `claude`.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any
import unittest

from ai_usage_service import CSV_FIELDS
from tests.parity import driver
from tests.test_provider_instances import AZ, OFF, REPO, installed_collector

FIXED = driver.FIXED_TS
TWO_HOURS_AGO = "2026-08-21T10:00:00Z"
COLLECTED = "2026-08-21T12:00:00Z"

# Refreshes its account's cache unless that folder holds a `hang` file; then it
# never answers, like the 30-second timeouts seen with the installed collector.
FAKE_CLAUDE = f"""import json, os, sys, time
config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.environ["HOME"]
if os.path.exists(os.path.join(config_dir, "hang")):
    time.sleep(60)
cache = os.path.join(config_dir, ".claude.json")
document = json.load(open(cache))
document["cachedUsageUtilization"]["fetchedAtMs"] = {(FIXED - 30) * 1000!r}
json.dump(document, open(cache, "w"))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "usage"}}))
"""


def cache(fetched_at: float, session_percent: int) -> str:
    document = json.loads(driver.claude_usage_cache(fetched_at * 1000))
    document["cachedUsageUtilization"]["utilization"]["limits"][0]["percent"] = session_percent
    return json.dumps(document)


def report_after_one_collection(*, default_hangs: bool, az: bool = False) -> dict[str, Any]:
    """Collect once with both caches two hours old, then report."""

    def plan(box: driver.Sandbox) -> list[dict[str, Any]]:
        box.write("home/.claude.json", cache(FIXED - 2 * driver.HOUR, 8))
        if default_hangs:
            box.write("home/hang", "")
        box.write("home/.claude/stats-cache.json", driver.CLAUDE_STATS, mtime=FIXED - driver.HOUR)
        claude: dict[str, Any] = {"executable": box.python_executable("claude", FAKE_CLAUDE), "timeout_seconds": 5}
        instances = []
        if az:
            box.write("home/claude-az/.claude.json", cache(FIXED - 2 * driver.HOUR, 50))
            instances = [{**AZ, "settings": {**AZ["settings"], "executable": claude["executable"],
                                             "timeout_seconds": 5}}]
        box.write_config({"codex": OFF, "claude": claude, "antigravity": OFF, "gemini_cli": OFF, "grok": OFF},
                         instances=instances)
        installed_collector(box, "2.3.0")
        return [box.once(), {"op": "report", "days": 7}]

    return driver.run_scenario(REPO, plan)["results"][-1]["report"]


def provider(report: dict[str, Any], provider_id: str) -> dict[str, Any]:
    return next(p for p in report["providers"] if p["id"] == provider_id)


def quota_freshness(entry: dict[str, Any]) -> dict[str, Any]:
    quota = entry["quota"]
    source = next(s for s in entry["sources"] if s["id"] == "subscription-usage")
    return {
        "status": quota["status"], "stale_cause": quota["stale_cause"], "measured_at": quota["measured_at"],
        "windows": sorted({(w["status"], w["stale_cause"], w["measured_at"]) for w in quota["windows"]}),
        "percents": {w["short"]: w["percent"] for w in quota["windows"]},
        "source": (source["status"], source["last_read_at"], source["measured_at"], source["error"]),
    }


class StaleQuotaReportingTests(unittest.TestCase):
    def test_an_old_cache_kept_after_a_refresh_timeout_is_stale_and_dated_by_its_fetch(self) -> None:
        report = report_after_one_collection(default_hangs=True)
        timeout = "claude /usage did not finish within 5s"
        self.assertEqual(quota_freshness(provider(report, "claude")), {
            "status": "stale", "stale_cause": "failed", "measured_at": TWO_HOURS_AGO,
            "windows": [("stale", "failed", TWO_HOURS_AGO)],
            "percents": {"5-hour": 8, "Weekly": 85, "Weekly · Fable": 4},
            "source": ("failed", None, TWO_HOURS_AGO, timeout),
        }, "the kept reading stays visible, stale, with when Claude Code fetched it and the refresh error")
        collection = report["collection"]
        self.assertEqual(collection["last_attempt_result"], "partial",
                         "transcripts were read, but the quota refresh failed")
        self.assertEqual([(f["provider"], f["source"], f["kind"], f["message"]) for f in collection["failures"]],
                         [("claude", "subscription-usage", "quota", timeout)])
        self.assertEqual((collection["summary"]["providers_updated"], collection["summary"]["providers_partly"]),
                         (0, 1))

    def test_a_refreshed_reading_is_current_and_dated_by_its_fetch(self) -> None:
        report = report_after_one_collection(default_hangs=False)
        self.assertEqual(quota_freshness(provider(report, "claude")), {
            "status": "current", "stale_cause": None, "measured_at": "2026-08-21T11:59:30Z",
            "windows": [("current", None, "2026-08-21T11:59:30Z")],
            "percents": {"5-hour": 8, "Weekly": 85, "Weekly · Fable": 4},
            "source": ("ok", COLLECTED, "2026-08-21T11:59:30Z", None),
        })
        self.assertEqual((report["collection"]["last_attempt_result"], report["collection"]["failures"]),
                         ("success", []))

    def test_only_the_account_whose_refresh_failed_is_stale(self) -> None:
        report = report_after_one_collection(default_hangs=True, az=True)
        self.assertEqual(quota_freshness(provider(report, "claude"))["windows"],
                         [("stale", "failed", TWO_HOURS_AGO)])
        az = quota_freshness(provider(report, "claude-az"))
        self.assertEqual((az["status"], az["windows"], az["percents"]["5-hour"], az["source"]),
                         ("current", [("current", None, "2026-08-21T11:59:30Z")], 50,
                          ("ok", COLLECTED, "2026-08-21T11:59:30Z", None)),
                         "the two accounts share a transaction but not their freshness")
        self.assertEqual([f["provider"] for f in report["collection"]["failures"]], ["claude"])
        self.assertEqual(report["collection"]["last_attempt_result"], "partial")

    def test_a_kept_reading_from_an_older_collector_has_no_invented_measurement_time(self) -> None:
        # 2.3.0 and earlier wrote the kept cache with no age row; when it was fetched is unknown.
        report = report_from_rows([[REFRESH_TIMED_OUT, KEPT_SESSION(8)]])
        freshness = quota_freshness(provider(report, "claude"))
        self.assertEqual((freshness["status"], freshness["measured_at"], freshness["windows"]),
                         ("stale", None, [("stale", "failed", None)]))
        self.assertEqual(freshness["percents"], {"5-hour": 8}, "the kept value is still shown")
        self.assertEqual(provider(report, "claude")["quota"]["windows"][0]["readings"], [],
                         "a reading with no known time is not plotted")
        self.assertEqual([(f["provider"], f["source"]) for f in report["collection"]["failures"]],
                         [("claude", "subscription-usage")])

    def test_an_undated_reading_is_never_current_once_the_failure_is_no_longer_latest(self) -> None:
        # A good read at 11:00, the failed one at 11:30, then one that read no limits
        # (subscription usage turned off) at 11:45.
        good = {**KEPT_SESSION(40), "collected_at": "2026-08-21T11:00:00Z", "status": "ok", "message": ""}
        failed = [{**r, "collected_at": "2026-08-21T11:30:00Z"} for r in (REFRESH_TIMED_OUT, KEPT_SESSION(45))]
        off = row("quota", "personal_subscription_quota", source="config", status="disabled",
                  message="subscription usage disabled in config; nothing was read or run",
                  collected_at="2026-08-21T11:45:00Z")
        report = report_from_rows([[good], failed, [off]])
        window = provider(report, "claude")["quota"]["windows"][0]
        self.assertEqual((window["status"], window["stale_cause"], window["measured_at"], window["percent"],
                          window["becomes_stale_at"], window["limit"]), ("stale", "source_old", None, 45, None, None))

    def test_a_dated_fallback_stays_stale_after_a_later_attempt_reads_no_limits_until_a_good_read(self) -> None:
        # 11:00 a good read; 11:30 the refresh fails and keeps the reading fetched at 11:10;
        # 11:45 subscription usage is turned off, so that collection reads no limits.
        good = dated(KEPT_SESSION(40), "2026-08-21T11:00:00Z", 0, status="ok", source="claude-usage-command")
        failed = [{**REFRESH_TIMED_OUT, "collected_at": "2026-08-21T11:30:00Z"},
                  *dated(KEPT_SESSION(45), "2026-08-21T11:30:00Z", 1200)]
        off = row("quota", "personal_subscription_quota", source="config", status="disabled",
                  message="subscription usage disabled in config; nothing was read or run",
                  collected_at="2026-08-21T11:45:00Z")
        report = report_from_rows([good, failed, [off]])
        claude = provider(report, "claude")
        window = claude["quota"]["windows"][0]
        source = next(s for s in claude["sources"] if s["id"] == "subscription-usage")
        self.assertEqual((window["status"], window["stale_cause"], window["measured_at"], window["percent"],
                          window["becomes_stale_at"], window["limit"]),
                         ("stale", "source_old", "2026-08-21T11:10:00Z", 45, None, None),
                         "the kept reading is still the one the failed refresh left")
        self.assertEqual((claude["quota"]["status"], source["status"]), ("stale", "stale"),
                         "the window and its source agree")
        self.assertEqual(report["collection"]["failures"], [], "turning collection off is not a new failure")

        # Turned back on, a good read clears it.
        recovered = dated(KEPT_SESSION(50), "2026-08-21T11:50:00Z", 0, status="ok", source="claude-usage-command")
        report = report_from_rows([good, failed, [off], recovered])
        claude = provider(report, "claude")
        window = claude["quota"]["windows"][0]
        source = next(s for s in claude["sources"] if s["id"] == "subscription-usage")
        self.assertEqual((window["status"], window["stale_cause"], window["measured_at"], window["percent"]),
                         ("current", None, "2026-08-21T11:50:00Z", 50))
        self.assertEqual((source["status"], source["error"]), ("ok", None))

    def test_an_undated_reading_whose_reset_is_due_has_passed_it(self) -> None:
        # Kept at 11:45 with a reset at 11:30: whenever it was measured, that was before the reset.
        kept = {**KEPT_SESSION(90), "collected_at": "2026-08-21T11:45:00Z", "resets_at": "2026-08-21T11:30:00Z"}
        report = report_from_rows([[{**REFRESH_TIMED_OUT, "collected_at": "2026-08-21T11:45:00Z"}, kept]])
        self.assertTrue(provider(report, "claude")["quota"]["windows"][0]["reset_passed"])

    def test_an_old_stats_cache_with_a_read_error_is_not_turned_into_a_failure(self) -> None:
        # Only quota sources treat stale-only rows as a failed read; the daily stats cache is unchanged.
        report = report_from_rows([[
            row("freshness", "source_age_seconds", value="259200", unit="seconds", source="claude-stats-cache",
                status="stale", message="Stats cache has not been rewritten; session transcripts are the live source"),
            row("collection", "stats_cache", source="claude-stats-cache", status="error", message="bad json"),
        ]])
        stats = next(s for s in provider(report, "claude")["sources"] if s["id"] == "stats-cache")
        self.assertEqual((stats["status"], stats["error"]), ("stale", None))
        self.assertEqual((report["collection"]["last_attempt_result"], report["collection"]["failures"]),
                         ("success", []))


def row(category: str, metric: str, **fields: str) -> dict[str, str]:
    return {"collected_at": COLLECTED, "provider": "claude", "category": category, "metric": metric,
            "record_kind": "snapshot", **fields}


REFRESH_TIMED_OUT = row("collection", "subscription_usage_query", source="claude-usage-command", status="error",
                        message="claude /usage did not finish within 30s")


def KEPT_SESSION(percent: int) -> dict[str, str]:
    return row("quota", "used_percent", scope="session", value=str(percent), unit="percent", window_seconds="18000",
               source="claude-usage-cache", status="stale", message="last reading Claude Code cached; the refresh failed")


def dated(reading: dict[str, str], collected_at: str, age: int, **fields: str) -> list[dict[str, str]]:
    """`reading` collected at `collected_at`, preceded by the age row this collector writes."""
    reading = {**reading, "collected_at": collected_at, **fields}
    if reading["status"] == "ok":
        reading["message"] = ""
    return [row("freshness", "usage_source_age_seconds", value=str(age), unit="seconds", source=reading["source"],
                status=reading["status"], collected_at=collected_at), reading]


def report_from_rows(transactions: list[list[dict[str, str]]]) -> dict[str, Any]:
    """The report for a CSV holding `transactions`, each a list of rows."""

    def plan(box: driver.Sandbox) -> list[dict[str, Any]]:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, restval="")
        writer.writeheader()
        for number, rows in enumerate(transactions):
            for index, fields in enumerate(rows):
                writer.writerow({"transaction_id": f"tx-{number}", "transaction_index": index, **fields})
        box.write("data/usage.csv", buffer.getvalue())
        box.write_config({"codex": OFF, "claude": {"stats_file": box.missing("claude.json")},
                          "antigravity": OFF, "gemini_cli": OFF, "grok": OFF})
        installed_collector(box, "2.3.0")
        return [{"op": "report", "days": 7}]

    return driver.run_scenario(REPO, plan)["results"][-1]["report"]

if __name__ == "__main__":
    unittest.main()
