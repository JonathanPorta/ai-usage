#!/usr/bin/env python3
"""Contract tests for the read-only reporting layer (ai_usage_report.py)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Optional

import ai_usage_fixtures as fx
import ai_usage_report as report
import ai_usage_service as collector

ROOT = Path(__file__).resolve().parent
HOUR = 3600
DAY = 86400
NOW_ISO = "2026-09-29T16:40:00Z"  # 10:40 America/Denver
_PREVIOUS_TZ: Optional[str] = None


def setUpModule() -> None:
    global _PREVIOUS_TZ
    _PREVIOUS_TZ = os.environ.get("TZ")
    os.environ["TZ"] = "America/Denver"
    time.tzset()


def tearDownModule() -> None:
    if _PREVIOUS_TZ is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = _PREVIOUS_TZ
    time.tzset()


class ReportCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ai-usage-report-test-")
        self.root = Path(self._tmp.name)
        self.now = report.parse_ts(NOW_ISO)
        self.builder = fx.CsvBuilder()
        self.config_path = self.root / "config.json"
        self.settings: dict[str, Any] = {}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self, *, log: str = "", service: Optional[dict[str, Any]] = None,
              extra: Optional[dict[str, Any]] = None,
              installed: Optional[tuple[int, int, int]] = None) -> dict[str, Any]:
        data = self.root / "data"
        data.mkdir(exist_ok=True)
        providers = {name: {"enabled": True} for name in ("codex", "claude", "antigravity", "gemini_cli", "grok")}
        for name, values in self.settings.items():
            providers[name].update(values)
        config = {
            "poll_interval_seconds": 3600,
            "paths": {
                "usage_csv": str(data / "usage.csv"),
                "log_file": str(data / "collector.log"),
                "state_file": str(data / "state.json"),
                "cache_dir": str(data / "cache"),
            },
            "providers": providers,
        }
        config.update(extra or {})
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        self.builder.write(data / "usage.csv")
        (data / "collector.log").write_text(log, encoding="utf-8")
        loaded = collector.load_config(self.config_path)
        return report.build_report(loaded, config_path=self.config_path, now=self.now, days=30, service=service,
                                   installed_version=installed)

    def provider(self, built: dict[str, Any], pid: str) -> dict[str, Any]:
        return next(p for p in built["providers"] if p["id"] == pid)

    def day(self, provider: dict[str, Any], days_ago: int) -> dict[str, Any]:
        date = time.strftime("%Y-%m-%d", time.localtime(self.now - days_ago * DAY))
        return next(d for d in provider["days"] if d["date"] == date)

    def window(self, provider: dict[str, Any], label: str) -> dict[str, Any]:
        return next(w for w in provider["quota"]["windows"] if w["label"] == label)

    def check(self, ts: float, *rows: dict[str, Any], providers: tuple[str, ...] = ("codex",)) -> None:
        self.builder.transaction([fx.detected(ts, p) for p in providers] + list(rows))

    def events(self, *rows: dict[str, Any]) -> None:
        self.builder.transaction(list(rows))


class AggregationTests(ReportCase):
    def test_event_totals_sum_per_local_day_and_snapshots_never_sum(self) -> None:
        for back in (0, 1):
            ts = self.now - back * DAY - HOUR
            self.check(ts, fx.R(ts, "claude", "usage", "input_tokens", 999_999, scope="claude-x",
                                unit="tokens", source="claude-stats-cache"),  # lifetime snapshot
                       providers=("claude",))
        # 23:30 local yesterday and 00:30 local today belong to different days.
        late = self.now - 11 * HOUR - 10 * 60  # 23:30 yesterday local
        early = self.now - 10 * HOUR - 10 * 60  # 00:30 today local
        self.events(*fx.claude_event(late, "m1", 100, 10), *fx.claude_event(late + 60, "m1", 50, 5),
                    *fx.claude_event(early, "m2", 7, 3))
        claude = self.provider(self.build(), "claude")
        yesterday, today = self.day(claude, 1), self.day(claude, 0)
        self.assertEqual((yesterday["input"], yesterday["output"], yesterday["total"]), (150, 15, 165))
        self.assertEqual((today["input"], today["output"], today["total"]), (7, 3, 10))
        self.assertEqual(today["state"], "partial")
        self.assertEqual(claude["models_today"], [{"name": "m2", "tokens": 10}])

    def test_period_totals_take_latest_revision_per_period(self) -> None:
        date = time.strftime("%Y-%m-%d", time.localtime(self.now - DAY))
        first = self.now - 20 * HOUR
        self.check(first, fx.codex_usage_summary(first), fx.codex_daily(first, date, 1000))
        second = self.now - HOUR
        self.check(second, fx.codex_usage_summary(second), fx.codex_daily(second, date, 1500))
        codex = self.provider(self.build(), "codex")
        day = self.day(codex, 1)
        self.assertEqual(day["total"], 1500, "a revised period total must replace, not add")
        self.assertIsNone(day["input"], "Codex has no input/output split; none may be invented")
        self.assertEqual(codex["usage"]["split"], "total_only")

    def test_day_states_distinguish_zero_missing_partial_and_not_collected(self) -> None:
        start = self.now - 5 * DAY
        ts = start
        while ts < self.now - 60:
            self.check(ts, providers=("claude", "grok"))
            ts += 6 * HOUR
        self.events(*fx.claude_event(self.now - 4 * DAY, "m", 10, 1))
        self.events(*fx.grok_event(self.now - 4 * DAY, "g1", 10, 1))
        loss = self.now - 2 * DAY
        self.check(loss, fx.R(loss, "grok", "collection", "rotation_data_loss", 1, kind="interval_total",
                              source="grok-session-log", status="error"), providers=("grok",))
        built = self.build()
        claude, grok = self.provider(built, "claude"), self.provider(built, "grok")
        self.assertEqual(self.day(claude, 6)["state"], "not_collected")
        self.assertEqual(self.day(claude, 4)["state"], "measured")
        self.assertEqual(self.day(claude, 3)["state"], "zero", "a fully read quiet day is a measured zero")
        self.assertEqual(self.day(claude, 3)["total"], 0)
        self.assertEqual(self.day(grok, 2)["state"], "missing", "rotation loss is a gap, never zero")
        self.assertIsNone(self.day(grok, 2)["total"])
        self.assertEqual(self.day(claude, 0)["state"], "partial")

    def test_stopped_collector_across_midnight_marks_yesterday_partial_and_today_empty(self) -> None:
        # Last checks at 10:20 on the 29th; the collector then stops. Files never change.
        for back in (30, 20):
            self.check(self.now - back * 60, providers=("claude",))
        self.events(*fx.claude_event(self.now - 15 * 60, "m", 100, 10))
        self.now = self.now + 22 * HOUR  # 08:40 on the 30th
        claude = self.provider(self.build(), "claude")
        self.assertIsNone(claude["today"], "nothing has been read today")
        self.assertEqual(claude["models_today"], [])
        yesterday, today = self.day(claude, 1), self.day(claude, 0)
        self.assertEqual((yesterday["state"], yesterday["total"]), ("partial", 110),
                         "a day last read before it ended is partial, never complete")
        self.assertEqual(yesterday["as_of"], report.iso(self.now - 22 * HOUR - 20 * 60))
        self.assertEqual((today["state"], today["total"]), ("missing", None))
        self.assertEqual(claude["days"][-1]["date"], time.strftime("%Y-%m-%d", time.localtime(self.now)),
                         "the daily window ends on the new day")

    def test_deferred_session_files_make_empty_days_missing_not_zero(self) -> None:
        ts = self.now - 3 * DAY
        self.events(*fx.claude_event(ts, "m", 10, 1))
        last = self.now - HOUR
        self.check(last, fx.R(last, "claude", "collection", "session_files_deferred", 12, kind="interval_total",
                              source="claude-session-log", status="partial"), providers=("claude",))
        claude = self.provider(self.build(), "claude")
        self.assertEqual(self.day(claude, 1)["state"], "missing")
        self.assertTrue(any("queued" in note for note in claude["notes"]))

    def test_grok_incomplete_events_are_summed_and_counted(self) -> None:
        ts = self.now - DAY
        self.check(ts, providers=("grok",))
        self.events(*fx.grok_event(ts + 60, "a", 100, 10), *fx.grok_event(ts + 120, "b", 50, 5, incomplete=True))
        day = self.day(self.provider(self.build(), "grok"), 1)
        self.assertEqual(day["total"], 165)
        self.assertEqual(day["incomplete_events"], 1)


class QuotaFreshnessTests(ReportCase):
    def codex_check(self, ts: float, *, five: Optional[float], weekly: Optional[float],
                    five_reset: Optional[float] = None, week_reset: Optional[float] = None) -> None:
        rows = [fx.codex_usage_summary(ts)]
        if five is not None:
            rows.append(fx.codex_quota(ts, "codex:primary", five, 18000, five_reset or ts + 3 * HOUR))
        if weekly is not None:
            rows.append(fx.codex_quota(ts, "codex:secondary", weekly, 604800, week_reset or ts + 3 * DAY))
        self.check(ts, *rows)

    def test_fresh_weekly_limit_survives_a_stale_five_hour_window(self) -> None:
        self.codex_check(self.now - 2 * HOUR, five=40, weekly=90)
        self.codex_check(self.now - 20 * 60, five=None, weekly=100)  # 5-hour omitted
        codex = self.provider(self.build(), "codex")
        five, weekly = self.window(codex, "5-hour limit"), self.window(codex, "Weekly limit")
        self.assertEqual((five["status"], five["stale_cause"], five["omitted"]), ("stale", "source_old", True))
        self.assertEqual((weekly["status"], weekly["limit"], weekly["percent"]), ("current", "reached", 0))
        self.assertEqual(codex["quota"]["status"], "mixed")
        self.assertTrue(weekly["derived"])
        self.assertEqual(weekly["basis"], "remaining")

    def test_stale_weekly_with_current_five_hour(self) -> None:
        self.codex_check(self.now - 15 * HOUR, five=10, weekly=30)
        for back in (3, 2, 1):
            self.codex_check(self.now - back * HOUR + 600, five=20, weekly=None)
        codex = self.provider(self.build(), "codex")
        self.assertEqual(self.window(codex, "Weekly limit")["status"], "stale")
        self.assertEqual(self.window(codex, "5-hour limit")["status"], "current")
        self.assertIsNone(self.window(codex, "5-hour limit")["limit"])

    def test_reset_passed_boundary_is_measured_before_reset_at_or_before_now(self) -> None:
        cases = [
            (self.now - 30 * 60, self.now, True),                # resets exactly at now
            (self.now - 30 * 60, self.now + 1, False),           # reset still ahead
            (self.now - 30 * 60, self.now - 30 * 60, False),     # reset == measured: not after the reading
            (self.now - 30 * 60, self.now - 10 * 60, True),      # reset between reading and now
        ]
        for measured, resets, expected in cases:
            with self.subTest(resets=resets - self.now):
                self.builder = fx.CsvBuilder()
                self.codex_check(measured, five=50, weekly=50, five_reset=resets)
                five = self.window(self.provider(self.build(), "codex"), "5-hour limit")
                self.assertIs(five["reset_passed"], expected)
                self.assertEqual(five["stale_cause"] == "reset_passed", expected)

    def test_early_reset_is_detected_when_the_reset_time_jumps_ahead(self) -> None:
        week = 7 * DAY
        self.codex_check(self.now - 3 * HOUR, five=None, weekly=60, week_reset=self.now + 3 * DAY)
        self.codex_check(self.now - 2 * HOUR, five=None, weekly=61, week_reset=self.now + 3 * DAY)
        # The provider reset the week early: usage dropped and the reset moved a whole window ahead.
        self.codex_check(self.now - 1 * HOUR, five=None, weekly=2, week_reset=self.now - 1.5 * HOUR + week)
        weekly = self.window(self.provider(self.build(), "codex"), "Weekly limit")
        self.assertEqual(weekly["resets"], [report.iso(self.now - 1.5 * HOUR)],
                         "marked where the new window began, between the two readings")
        self.assertEqual(weekly["status"], "current")

    def test_a_reset_reported_seconds_apart_is_one_reset(self) -> None:
        reset = self.now - 2 * HOUR
        self.codex_check(self.now - 3 * HOUR, five=None, weekly=70, week_reset=reset)
        self.codex_check(self.now - 2.5 * HOUR, five=None, weekly=71, week_reset=reset + 1)
        self.codex_check(self.now - 1 * HOUR, five=None, weekly=1, week_reset=self.now + 6 * DAY)
        weekly = self.window(self.provider(self.build(), "codex"), "Weekly limit")
        self.assertEqual(weekly["resets"], [report.iso(reset)])

    def test_rolling_idle_window_is_not_an_early_reset(self) -> None:
        for back in (4, 3, 2, 1):
            ts = self.now - back * HOUR
            self.codex_check(ts, five=100, weekly=None, five_reset=ts + 5 * HOUR)
        five = self.window(self.provider(self.build(), "codex"), "5-hour limit")
        self.assertEqual(five["resets"], [], "a reset time that moves with the clock is not a reset")

    def test_current_readings_state_when_they_will_turn_stale(self) -> None:
        measured = self.now - 30 * 60
        reset = self.now + 2 * HOUR
        self.codex_check(measured, five=50, weekly=50, five_reset=reset, week_reset=self.now + 3 * DAY)
        codex = self.provider(self.build(), "codex")
        five = self.window(codex, "5-hour limit")
        # 2x interval after the last good read, earlier than the 2 h reset.
        self.assertEqual(five["becomes_stale_at"], report.iso(measured + 2 * HOUR))
        self.assertEqual(five["becomes_stale_cause"], "not_collected")
        self.assertEqual(codex["usage"]["becomes_stale_at"], report.iso(measured + 2 * HOUR))
        # The same reading evaluated at that instant is stale for that reason.
        self.now = measured + 2 * HOUR + 1
        later = self.window(self.provider(self.build(), "codex"), "5-hour limit")
        self.assertEqual((later["status"], later["stale_cause"]), ("stale", "not_collected"))

    def test_reset_before_other_deadlines_is_the_next_stale_cause(self) -> None:
        measured = self.now - 10 * 60
        self.codex_check(measured, five=50, weekly=50, five_reset=self.now + 20 * 60)
        five = self.window(self.provider(self.build(), "codex"), "5-hour limit")
        self.assertEqual((five["becomes_stale_at"], five["becomes_stale_cause"]),
                         (report.iso(self.now + 20 * 60), "reset_passed"))

    def test_window_absent_for_long_is_retired_not_current(self) -> None:
        ts = self.now - 4 * DAY
        self.check(ts, fx.codex_usage_summary(ts), fx.codex_quota(ts, "codex_x:primary", 10, 18000, ts + HOUR))
        self.codex_check(self.now - HOUR, five=10, weekly=10)
        windows = self.provider(self.build(), "codex")["quota"]["windows"]
        retired = next(w for w in windows if w["id"] == "codex_x:primary")
        self.assertTrue(retired["retired"])
        self.assertEqual(retired["label"], "codex_x · 5-hour limit")
        self.assertEqual(windows[-1]["id"], "codex_x:primary", "retired windows sort last")

    def test_quota_not_collected_after_twice_the_interval(self) -> None:
        self.codex_check(self.now - 3 * HOUR, five=50, weekly=50)
        five = self.window(self.provider(self.build(), "codex"), "5-hour limit")
        self.assertEqual(five["stale_cause"], "not_collected")
        self.assertIsNone(five["limit"], "stale windows never raise limit warnings")

    def test_antigravity_event_quota_is_not_stale_from_idle_time(self) -> None:
        self.check(self.now - HOUR, providers=("antigravity",))
        self.events(*fx.antigravity_delta(self.now - 2 * DAY, 10, 1),
                    *fx.antigravity_quota(self.now - 2 * DAY, 60, self.now + DAY))
        window = self.provider(self.build(), "antigravity")["quota"]["windows"][0]
        self.assertEqual(window["status"], "current")
        self.assertEqual(window["label"], "Model quota")


class SourceAndCacheTests(ReportCase):
    def test_failed_quota_keeps_cached_reading_while_usage_updates(self) -> None:
        first = self.now - 70 * 60
        self.check(first, fx.codex_usage_summary(first),
                   fx.codex_quota(first, "codex:primary", 45, 18000, self.now + 2 * HOUR))
        second = self.now - 10 * 60
        self.check(second, fx.codex_usage_summary(second),
                   fx.error(second, "codex", "rate_limits_query", "codex-app-server", "rate limits timed out"))
        built = self.build()
        codex = self.provider(built, "codex")
        five = self.window(codex, "5-hour limit")
        self.assertEqual(five["percent"], 55)
        self.assertEqual(five["measured_at"], report.iso(first), "a failure must not move the measurement time")
        self.assertEqual((five["status"], five["stale_cause"]), ("stale", "failed"))
        sources = {s["id"]: s for s in codex["sources"]}
        self.assertEqual(sources["rate-limits"]["status"], "failed")
        self.assertEqual(sources["rate-limits"]["last_read_at"], report.iso(first))
        self.assertEqual(sources["account-usage"]["last_read_at"], report.iso(second))
        self.assertEqual(codex["usage"]["status"], "current")
        self.assertEqual(built["collection"]["last_attempt_result"], "partial")
        self.assertEqual(built["collection"]["summary"]["providers_partly"], 1)
        self.assertEqual(built["collection"]["failures"][0]["kind"], "quota")

    def test_metadata_failure_fails_both_codex_sources(self) -> None:
        ts = self.now - 10 * 60
        self.check(ts, fx.error(ts, "codex", "metadata_query", "codex-app-server", "app-server exited"))
        built = self.build()
        codex = self.provider(built, "codex")
        self.assertEqual({s["status"] for s in codex["sources"]}, {"failed"})
        self.assertEqual(built["collection"]["last_attempt_result"], "failed")

    def test_auth_failure_is_reported_as_sign_in(self) -> None:
        first = self.now - 2 * HOUR
        self.check(first, *fx.grok_billing(first, 30, 100, first - DAY, first + 6 * DAY), providers=("grok",))
        ts = self.now - 10 * 60
        self.check(ts, fx.error(ts, "grok", "billing_query", "grok-x.ai-billing-acp", "401 Unauthorized: sign in again"),
                   providers=("grok",))
        grok = self.provider(self.build(), "grok")
        self.assertEqual(grok["quota"]["windows"][0]["stale_cause"], "auth")
        self.assertEqual(next(s for s in grok["sources"] if s["id"] == "billing")["status"], "auth")

    def test_disabled_provider_keeps_cached_values_and_is_skipped(self) -> None:
        first = self.now - 5 * HOUR
        self.check(first, fx.codex_usage_summary(first),
                   fx.codex_quota(first, "codex:primary", 30, 18000, self.now + HOUR))
        ts = self.now - 10 * 60
        self.builder.transaction([fx.disabled(ts, "codex"), fx.detected(ts, "claude")])
        self.settings = {"codex": {"enabled": False}}
        built = self.build()
        codex = self.provider(built, "codex")
        self.assertEqual(codex["setup"], "disabled")
        self.assertFalse(codex["enabled"])
        self.assertEqual(self.window(codex, "5-hour limit")["percent"], 70)
        self.assertEqual(self.window(codex, "5-hour limit")["status"], "stale")
        self.assertEqual({s["status"] for s in codex["sources"]}, {"off"})
        self.assertEqual(built["collection"]["summary"]["skipped"], ["Codex"])

    def test_collector_level_failure_changes_no_measurement(self) -> None:
        ts = self.now - 2 * HOUR
        self.check(ts, fx.codex_usage_summary(ts), fx.codex_quota(ts, "codex:primary", 20, 18000, self.now + HOUR))
        failed_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now - 30 * 60))
        built = self.build(log=f"{failed_at},123 ERROR collection cycle failed: disk full\n")
        self.assertEqual(built["collection"]["last_attempt_result"], "failed")
        self.assertEqual(built["collection"]["failures"][0]["kind"], "collector")
        self.assertEqual(built["collection"]["failures"][0]["message"], "disk full")
        self.assertEqual(self.window(self.provider(built, "codex"), "5-hour limit")["percent"], 80)

    def test_next_scheduled_check_comes_from_daemon_log_only_when_running(self) -> None:
        self.check(self.now - 20 * 60, fx.codex_usage_summary(self.now - 20 * 60))
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now - 20 * 60))
        log = (f"{stamp},000 INFO collection completed rows=9 detected_providers=1 errors=0 next_poll_seconds=3600\n"
               f"{stamp},500 INFO one-shot collection completed rows=9 errors=0\n")
        running = self.build(log=log, service={"state": "running", "pid": 1, "detail": ""})
        self.assertEqual(running["schedule"]["next_scheduled_at"], report.iso(self.now + 40 * 60))
        stopped = self.build(log=log, service={"state": "stopped", "pid": None, "detail": ""})
        self.assertIsNone(stopped["schedule"]["next_scheduled_at"])
        self.assertFalse(stopped["schedule"]["pause_supported"])

    def test_check_that_detects_nothing_still_counts_as_an_attempt(self) -> None:
        early = self.now - 3 * HOUR
        self.check(early, fx.codex_usage_summary(early))
        ts = self.now - 5 * 60
        self.builder.transaction([fx.R(ts, "codex", "cost", "monthly_subscription", 20, kind="monthly_rate",
                                       period_start="2026-09-01", period_end="2026-10-01", unit="USD/month",
                                       source="config")])
        built = self.build()
        self.assertEqual(built["collection"]["last_attempt_at"], report.iso(ts))
        self.assertEqual(self.provider(built, "codex")["setup"], "not_detected")

    def test_empty_check_is_read_from_the_log(self) -> None:
        self.check(self.now - 3 * HOUR, fx.codex_usage_summary(self.now - 3 * HOUR))
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now - 60))
        built = self.build(log=f"{stamp},000 INFO one-shot collection completed rows=0 errors=0\n")
        self.assertEqual(built["collection"]["last_attempt_at"], report.iso(self.now - 60))

    def test_pause_is_reported_only_when_the_installed_collector_supports_it(self) -> None:
        self.check(self.now - 20 * 60, fx.codex_usage_summary(self.now - 20 * 60))
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now - 20 * 60))
        log = f"{stamp},000 INFO collection completed rows=9 detected_providers=1 errors=0 next_poll_seconds=3600\n"
        running = {"state": "running", "pid": 1, "detail": ""}
        config = {"poll_paused": True}
        # Persisted pause with an old collector: requested but not in effect.
        old = self.build(log=log, service=running, extra=config, installed=(2, 1, 0))
        self.assertEqual(old["schedule"]["state"], "active")
        self.assertTrue(old["schedule"]["pause_requested"])
        self.assertFalse(old["collector"]["capabilities"]["pause"])
        self.assertEqual(old["collector"]["installed_version"], "2.1.0")
        new = self.build(log=log, service=running, extra=config, installed=(2, 2, 0))
        self.assertEqual(new["schedule"]["state"], "paused")
        self.assertIsNone(new["schedule"]["next_scheduled_at"], "no scheduled check while paused")
        self.assertTrue(new["collector"]["capabilities"]["progress"])

    def test_resume_schedules_the_next_check_one_interval_later(self) -> None:
        self.check(self.now - 50 * 60, fx.codex_usage_summary(self.now - 50 * 60))
        def at(minutes_ago):
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now - minutes_ago * 60))
        log = (f"{at(50)},000 INFO collection completed rows=9 detected_providers=1 errors=0 next_poll_seconds=3600\n"
               f"{at(40)},000 INFO scheduled checks paused; collector keeps running\n"
               f"{at(5)},000 INFO scheduled checks resumed next_poll_seconds=3600\n")
        built = self.build(log=log, service={"state": "running", "pid": 1, "detail": ""}, installed=(2, 2, 0))
        self.assertEqual(built["schedule"]["state"], "active")
        self.assertEqual(built["schedule"]["next_scheduled_at"], report.iso(self.now - 5 * 60 + 3600))

    def test_not_installed_provider_is_not_detected(self) -> None:
        self.check(self.now - 10 * 60, providers=("codex",))
        gemini = self.provider(self.build(), "gemini_cli")
        self.assertEqual(gemini["setup"], "not_detected")
        self.assertEqual(gemini["usage"]["status"], "none")


class AccountingTests(ReportCase):
    def test_reported_cost_and_entered_subscription_are_separate(self) -> None:
        ts = self.now - 30 * 60
        self.check(ts, *fx.grok_billing(ts, 46, 318, ts - DAY, ts + 6 * DAY, age=120), providers=("grok",))
        self.settings = {"grok": {"monthly_subscription_usd": 30}}
        costs = self.provider(self.build(), "grok")["costs"]
        self.assertEqual(costs["reported"]["amount_usd"], 3.18)
        self.assertEqual(costs["reported"]["measured_at"], report.iso(ts - 120))
        self.assertEqual(costs["subscription"], {"monthly_usd": 30, "source": "config", "entered_by_user": True})

    def test_grok_billing_measurement_time_subtracts_source_age(self) -> None:
        ts = self.now - 30 * 60
        self.check(ts, *fx.grok_billing(ts, 46, 0, ts - DAY, ts + 6 * DAY, age=4 * HOUR), providers=("grok",))
        window = self.provider(self.build(), "grok")["quota"]["windows"][0]
        self.assertEqual(window["measured_at"], report.iso(ts - 4 * HOUR))
        self.assertEqual((window["status"], window["stale_cause"]), ("stale", "source_old"))
        self.assertEqual((window["basis"], window["label"]), ("used", "Weekly credits"))


class ReadOnlyContractTests(unittest.TestCase):
    """The CLI never runs a provider binary, never collects, never writes."""

    def test_report_cli_is_read_only_and_runs_no_provider_binary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-usage-readonly-") as tmp:
            root = Path(tmp)
            config_path = fx.write_sandbox(root, report.parse_ts(NOW_ISO))
            tripwire = root / "tripwire"
            bin_dir = root / "bin"
            bin_dir.mkdir(exist_ok=True)
            for name in ("codex", "claude", "agy", "grok", "gemini", "launchctl"):
                sentinel = bin_dir / name
                sentinel.write_text(f"#!/bin/sh\necho {name} >> '{tripwire}'\nexit 0\n", encoding="utf-8")
                sentinel.chmod(0o755)

            def snapshot() -> dict[str, str]:
                return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(root.rglob("*")) if p.is_file()}

            before = snapshot()
            home = tempfile.TemporaryDirectory(prefix="ai-usage-readonly-home-")
            self.addCleanup(home.cleanup)
            # HOME lives outside the snapshot: some interpreters cache bytecode there.
            environment = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", HOME=home.name,
                               TZ="America/Denver", AI_USAGE_LAUNCHCTL=str(bin_dir / "launchctl"))
            result = subprocess.run(
                [sys.executable, str(ROOT / "ai_usage_report.py"), "--config", str(config_path),
                 "--now", NOW_ISO, "--service", "skip"],
                capture_output=True, text=True, env=environment, check=False, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "ai-usage/report/v1")
            self.assertFalse(tripwire.exists(), "the report executed a provider binary")
            self.assertEqual(before, snapshot(), "the report wrote to the collector's files")
            self.assertFalse((root / "data" / "state.json.lock").exists(), "the report took the collector lock")

    def test_invalid_config_exits_one_with_message_and_no_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "config.json"
            bad.write_text("{not json", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "ai_usage_report.py"), "--config", str(bad)],
                                    capture_output=True, text=True, check=False, timeout=60)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("ai-usage-report:", result.stderr)

    def test_missing_csv_yields_an_empty_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"paths": {
                "usage_csv": f"{tmp}/usage.csv", "log_file": f"{tmp}/collector.log",
                "state_file": f"{tmp}/state.json", "cache_dir": f"{tmp}/cache"}}), encoding="utf-8")
            built = report.build_report(collector.load_config(config), config_path=config,
                                        now=report.parse_ts(NOW_ISO), days=7)
            self.assertIsNone(built["collection"]["last_attempt_at"])
            self.assertEqual(len(built["providers"]), 5)
            self.assertTrue(all(len(p["days"]) == 7 for p in built["providers"]))


class FixtureContractTests(unittest.TestCase):
    def test_committed_app_fixture_matches_the_generator(self) -> None:
        committed = fx.FIXTURE_PATH.read_text(encoding="utf-8")
        self.assertEqual(committed, fx.render_fixture(), "run `make fixture` to refresh the app fixture")

    def test_fixture_covers_the_states_the_app_must_render(self) -> None:
        built = json.loads(fx.FIXTURE_PATH.read_text(encoding="utf-8"))
        providers = {p["id"]: p for p in built["providers"]}
        states = {d["state"] for p in built["providers"] for d in p["days"]}
        self.assertTrue({"measured", "zero", "missing", "not_collected", "partial"} <= states)
        self.assertEqual(providers["codex"]["quota"]["status"], "mixed")
        self.assertIn("reached", {w["limit"] for w in providers["codex"]["quota"]["windows"]})
        self.assertEqual(providers["grok"]["quota"]["windows"][0]["stale_cause"], "failed")
        self.assertEqual(providers["grok"]["usage"]["status"], "current")
        self.assertEqual(providers["gemini_cli"]["setup"], "not_detected")
        self.assertEqual(built["collection"]["last_attempt_result"], "partial")


if __name__ == "__main__":
    unittest.main()
