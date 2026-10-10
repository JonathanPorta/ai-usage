#!/usr/bin/env python3
"""Synthetic collector data for tests, the app's fixture report, and sandboxes.

Every value here is illustrative. Nothing reads personal data. The rows follow
the collector's real shapes (see ai_usage_service.metric_row and the provider
collectors), including the quirks the reporting layer must handle: event rows
carry the event time in ``collected_at``, Codex daily usage is total-only, and
Grok billing has two sources.

    python3 ai_usage_fixtures.py sandbox DIR [--now ISO]   # write a sandbox
    python3 ai_usage_fixtures.py report  [--now ISO]       # print the fixture report
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import ai_usage_service as collector

FIXTURE_NOW = "2026-09-29T16:40:00Z"  # Tue 29 Sep 2026, 10:40 in America/Denver
FIXTURE_TZ = "America/Denver"
HOUR = 3600
DAY = 86400


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class CsvBuilder:
    """Accumulates collector-shaped rows grouped into transactions."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._seq = 0

    def _tx(self) -> str:
        self._seq += 1
        return uuid.UUID(int=self._seq).hex

    @staticmethod
    def row(ts: float, provider: str, category: str, metric: str, value: Any = "", *,
            kind: str = "snapshot", scope: str = "", source: str = "", status: str = "ok",
            unit: str = "", window: Any = "", resets: Optional[float] = None,
            period_start: str = "", period_end: str = "", message: str = "") -> dict[str, Any]:
        return {
            "collected_at": iso(ts), "provider": provider, "category": category, "metric": metric,
            "record_kind": kind, "scope": scope, "period_start": period_start, "period_end": period_end,
            "value": value, "unit": unit, "window_seconds": window,
            "resets_at": iso(resets) if resets else "", "source": source, "status": status,
            "message": message,
        }

    def transaction(self, rows: list[dict[str, Any]]) -> None:
        tx = self._tx()
        for index, row in enumerate(rows):
            self.rows.append(dict(row, transaction_id=tx, transaction_index=index))

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=collector.CSV_FIELDS)
            writer.writeheader()
            for row in self.rows:
                writer.writerow(row)


R = CsvBuilder.row


def detected(ts: float, provider: str, found: bool = True) -> dict[str, Any]:
    return R(ts, provider, "availability", "detected", 1 if found else 0, unit="boolean",
             source="filesystem-discovery", status="ok" if found else "missing")


def disabled(ts: float, provider: str) -> dict[str, Any]:
    return R(ts, provider, "availability", "enabled", 0, unit="boolean", source="config", status="disabled",
             message="provider disabled in config; no discovery or command was run")


def codex_quota(ts: float, scope: str, used: float, window: int, resets: float) -> dict[str, Any]:
    return R(ts, "codex", "quota", "used_percent", used, scope=scope, unit="percent", window=window,
             resets=resets, source="codex-rate-limits")


def codex_usage_summary(ts: float) -> dict[str, Any]:
    return R(ts, "codex", "usage", "lifetime_tokens", 1_000_000_000, unit="tokens", source="codex-account-usage")


def codex_daily(ts: float, date: str, tokens: int) -> dict[str, Any]:
    return R(ts, "codex", "usage", "daily_tokens", tokens, kind="period_total", scope=date,
             period_start=date, unit="tokens", source="codex-account-usage")


def claude_event(ts: float, model: str, inp: int, out: int, cache_read: int = 0, session: str = "a1") -> list[dict[str, Any]]:
    scope = f"{session}:{model}"
    date = iso(ts)[:10]
    rows = [
        R(ts, "claude", "usage", "input_tokens", inp, kind="event_total", scope=scope, period_start=date,
          unit="tokens", source="claude-session-log"),
        R(ts, "claude", "usage", "output_tokens", out, kind="event_total", scope=scope, period_start=date,
          unit="tokens", source="claude-session-log"),
    ]
    if cache_read:
        rows.append(R(ts, "claude", "usage", "cache_read_input_tokens", cache_read, kind="event_total",
                      scope=scope, period_start=date, unit="tokens", source="claude-session-log"))
    return rows


def claude_stats(ts: float, stale: bool = False) -> list[dict[str, Any]]:
    rows = [R(ts, "claude", "activity", "total_messages", 1200, unit="messages", source="claude-stats-cache")]
    if stale:
        rows.append(R(ts, "claude", "freshness", "source_age_seconds", 3 * DAY, unit="seconds",
                      source="claude-stats-cache", status="stale",
                      message="Stats cache has not been rewritten; session transcripts are the live source"))
    rows.append(R(ts, "claude", "quota", "personal_subscription_quota", source="unsupported", status="unsupported"))
    return rows


def grok_event(ts: float, scope: str, inp: int, out: int, *, incomplete: bool = False, cost: float = 0.0) -> list[dict[str, Any]]:
    status = "incomplete" if incomplete else "ok"
    rows = [
        R(ts, "grok", "usage", "input_tokens", inp, kind="event_total", scope=scope, unit="tokens",
          source="grok-session-log", status=status),
        R(ts, "grok", "usage", "output_tokens", out, kind="event_total", scope=scope, unit="tokens",
          source="grok-session-log", status=status),
        R(ts, "grok", "usage", "cached_read_tokens", inp * 3, kind="event_total", scope=scope, unit="tokens",
          source="grok-session-log", status=status),
        R(ts, "grok", "usage", "model_usage",
          json.dumps({"grok-code-fast-1": {"inputTokens": inp, "outputTokens": out}}, separators=(",", ":")),
          kind="event_total", scope=scope, unit="json", source="grok-session-log", status=status),
    ]
    if not incomplete and cost:
        rows.append(R(ts, "grok", "cost", "api_cost", cost, kind="event_total", scope=scope, unit="USD",
                      source="grok-session-log"))
    return rows


def grok_billing(ts: float, used: float, cents: int, period_start: float, period_end: float,
                 age: int = 600) -> list[dict[str, Any]]:
    common = dict(scope="USAGE_PERIOD_TYPE_WEEKLY", period_start=iso(period_start), period_end=iso(period_end),
                  source="grok-unified-log")
    return [
        R(ts, "grok", "freshness", "billing_source_age_seconds", age, unit="seconds", source="grok-unified-log"),
        R(ts, "grok", "quota", "used_percent", used, unit="percent", resets=period_end,
          message="Weighted included-credit usage", **common),
        R(ts, "grok", "quota", "remaining_percent", 100 - used, unit="percent", resets=period_end, **common),
        R(ts, "grok", "billing", "on_demand_used", cents, unit="USD_cents", **common),
    ]


def antigravity_delta(ts: float, inp: int, out: int) -> list[dict[str, Any]]:
    scope = "s1:gemini-3-pro"
    return [
        R(ts, "antigravity", "usage", "input_tokens", inp, kind="delta", scope=scope, unit="tokens",
          source="antigravity-status-line-events"),
        R(ts, "antigravity", "usage", "output_tokens", out, kind="delta", scope=scope, unit="tokens",
          source="antigravity-status-line-events"),
    ]


def antigravity_quota(ts: float, remaining: float, resets: float) -> list[dict[str, Any]]:
    return [
        R(ts, "antigravity", "quota", "remaining_percent", remaining, scope="model", unit="percent",
          resets=resets, source="antigravity-status-line"),
        R(ts, "antigravity", "quota", "used_percent", 100 - remaining, scope="model", unit="percent",
          resets=resets, source="antigravity-status-line"),
    ]


def error(ts: float, provider: str, metric: str, source: str, message: str) -> dict[str, Any]:
    return R(ts, provider, "collection", metric, source=source, status="error", message=message)


# --------------------------------------------------------------------------- the fixture scenario

def fixture_rows(now: float) -> CsvBuilder:
    """A rich, deterministic scenario around `now` (local time America/Denver).

    - Collection began 20 days ago, hourly checks.
    - Codex: 5-hour window stale (omitted since 08:38), weekly window current at
      100% used (limit reached) — the "weekly limit reached, 5-hour stale" case.
      One day is missing from account usage. Day totals are revised once.
    - Claude: transcripts with one quiet day (measured zero); stats cache stale.
    - Antigravity: event deltas and an event-driven model quota.
    - Grok: sessions (ok and incomplete), weekly billing; the latest check's
      billing lookup failed, so quota keeps the previous reading and is stale
      (failed) while usage stays current. One day lost to log rotation.
    - Gemini CLI: not installed.
    """
    b = CsvBuilder()
    start = now - 20 * DAY
    start -= start % HOUR
    checks = []
    t = start + 17 * 60
    while t <= now - 2 * 60:
        checks.append(t)
        t += HOUR
    missing_codex_day = time.strftime("%Y-%m-%d", time.localtime(now - 6 * DAY))
    quiet_claude_day = time.localtime(now - 3 * DAY)
    rotation_day_ts = now - 9 * DAY
    five_hour_cut = now - 2 * HOUR  # 5-hour window omitted after this
    week_period_start = now - 2 * DAY - 5 * HOUR
    week_period_end = week_period_start + 7 * DAY
    for index, ts in enumerate(checks):
        last = index == len(checks) - 1
        lt = time.localtime(ts)
        rows: list[dict[str, Any]] = []
        # Codex
        rows.append(detected(ts, "codex"))
        rows.append(codex_usage_summary(ts))
        five_reset = ts - (ts % (5 * HOUR)) + 5 * HOUR
        week_reset = now + 3 * DAY + 6 * HOUR
        if ts < five_hour_cut:
            rows.append(codex_quota(ts, "codex:primary", min(95, (ts % (5 * HOUR)) / (5 * HOUR) * 80 + 10), 18000, five_reset))
        weekly_used = min(100.0, 40 + (ts - (now - 3 * DAY)) / (2.5 * DAY) * 60) if ts > now - 3 * DAY else 35.0
        rows.append(codex_quota(ts, "codex:secondary", round(weekly_used, 1), 604800, week_reset))
        if lt.tm_hour == 6 or last:
            for back in range(0, 3):
                day_ts = ts - back * DAY
                date = time.strftime("%Y-%m-%d", time.localtime(day_ts))
                if date == missing_codex_day or day_ts < start + DAY:
                    continue
                if back == 0 and not last:
                    continue
                base = 700_000 + (int(day_ts // DAY) * 7919) % 500_000
                rows.append(codex_daily(ts, date, base + (25_000 if last and back == 1 else 0)))
        # Claude
        rows.append(detected(ts, "claude"))
        rows.extend(claude_stats(ts, stale=ts > now - 3 * DAY))
        # Antigravity
        rows.append(detected(ts, "antigravity"))
        rows.append(R(ts, "antigravity", "context", "used_percentage", 12, unit="percent",
                      source="antigravity-status-line", status="ok"))
        # Grok
        rows.append(detected(ts, "grok"))
        if last:
            rows.append(error(ts, "grok", "billing_query", "grok-x.ai-billing-acp",
                              "x.ai/billing request timed out after 30 s"))
        else:
            used = round(min(99.0, max(0.0, (ts - week_period_start) / (7 * DAY) * 60)), 1) if ts > week_period_start else 55.0
            pstart = week_period_start if ts > week_period_start else week_period_start - 7 * DAY
            rows.extend(grok_billing(ts, used, 318 if ts > week_period_start else 0, pstart, pstart + 7 * DAY))
        if abs(ts - rotation_day_ts) < HOUR / 2:
            rows.append(R(ts, "grok", "collection", "rotation_data_loss", 1, kind="interval_total", unit="files",
                          source="grok-session-log", status="error", message="session log rotated before read"))
        # Gemini: not installed -> the collector emits no rows.
        b.transaction(rows)
        # Usage events between checks (event time in collected_at)
        events: list[dict[str, Any]] = []
        if 8 <= lt.tm_hour <= 18 and not (lt.tm_yday == quiet_claude_day.tm_yday):
            events += claude_event(ts + 600, "claude-sonnet-4-5", 30_000 + lt.tm_hour * 900, 6_000, 400_000)
            if lt.tm_hour % 3 == 0:
                events += claude_event(ts + 900, "claude-opus-4-1", 9_000, 2_000, 0, session="b2")
        if 9 <= lt.tm_hour <= 17 and lt.tm_wday < 5:
            scope = f"g{index:04d}"
            events += grok_event(ts + 1200, scope, 9_000, 1_300, incomplete=(lt.tm_hour == 12), cost=0.04)
        if ts > now - 7 * DAY and lt.tm_hour in (9, 14):
            events += antigravity_delta(ts + 300, 22_000, 3_100)
            events += antigravity_quota(ts + 300, max(5.0, 90 - lt.tm_hour * 2.5), ts + 5 * HOUR)
        if events:
            b.transaction(events)
    return b


def write_sandbox(root: Path, now: float, *, interval: int = 3600) -> Path:
    """Create an isolated collector home: config, CSV and log. Returns the config path."""
    root.mkdir(parents=True, exist_ok=True)
    data = root / "data"
    data.mkdir(exist_ok=True)
    config = {
        "poll_interval_seconds": interval,
        "poll_on_start": False,
        "paths": {
            "usage_csv": str(data / "usage.csv"),
            "log_file": str(data / "collector.log"),
            "state_file": str(data / "state.json"),
            "cache_dir": str(data / "cache"),
        },
        "providers": {
            # Sandbox providers point at absent binaries and empty dirs, so a
            # sandbox `once` never touches real provider accounts or files.
            "codex": {"enabled": True, "executable": str(root / "bin" / "codex"), "monthly_subscription_usd": 20},
            "claude": {"enabled": True, "stats_file": str(root / "claude" / "stats-cache.json"),
                       "projects_dir": str(root / "claude" / "projects"), "executable": str(root / "bin" / "claude"),
                       "monthly_subscription_usd": 100},
            "antigravity": {"enabled": True, "executable": str(root / "bin" / "agy"),
                            "cache_file": str(data / "cache" / "antigravity.json"),
                            "events_file": str(data / "cache" / "antigravity-events.jsonl"),
                            "settings_file": str(root / "antigravity" / "settings.json"),
                            "configure_status_line": False},
            "gemini_cli": {"enabled": True, "executable": str(root / "bin" / "gemini"),
                           "telemetry_file": str(data / "cache" / "gemini-telemetry.log"),
                           "settings_file": str(root / "gemini" / "settings.json")},
            "grok": {"enabled": True, "executable": str(root / "bin" / "grok"), "grok_home": str(root / "grok"),
                     "auth_file": str(root / "grok" / "auth.json"),
                     "billing_cache_file": str(root / "grok" / "logs" / "unified.jsonl"),
                     "collect_billing_via_acp": False},
        },
    }
    config_path = root / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    fixture_rows(now).write(data / "usage.csv")
    last_check = max(collector_check_times(data / "usage.csv"))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_check))
    (data / "collector.log").write_text(
        f"{stamp},000 INFO collection completed rows=40 detected_providers=4 errors=1 next_poll_seconds={interval}\n",
        encoding="utf-8")
    return config_path


def collector_check_times(path: Path) -> list[float]:
    import calendar
    with path.open(encoding="utf-8", newline="") as handle:
        return [calendar.timegm(time.strptime(r["collected_at"], "%Y-%m-%dT%H:%M:%SZ"))
                for r in csv.DictReader(handle) if r["category"] == "availability"]


def fixture_report(now_iso: str = FIXTURE_NOW) -> dict[str, Any]:
    """The committed app fixture: deterministic paths, TZ and service state."""
    import tempfile
    import ai_usage_report as report

    previous_tz = os.environ.get("TZ")
    os.environ["TZ"] = FIXTURE_TZ
    time.tzset()
    try:
        now = report.parse_ts(now_iso)
        with tempfile.TemporaryDirectory(prefix="ai-usage-fixture-") as tmp:
            root = Path(tmp)
            config_path = write_sandbox(root, now)
            config = collector.load_config(config_path)
            built = report.build_report(
                config, config_path=config_path, now=now, days=90,
                service={"state": "running", "pid": 4242, "disabled": False, "detail": "launchctl: state = running"},
                installed_version=tuple(int(part) for part in collector.VERSION.split(".")))
            text = json.dumps(built).replace(str(root.resolve()), "/sandbox").replace(str(root), "/sandbox")
            return json.loads(text)
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()


FIXTURE_PATH = Path(__file__).resolve().parent / "macos" / "Sources" / "AIUsageCore" / "Resources" / "fixtures" / "report-fixture.json"


def render_fixture() -> str:
    return json.dumps(fixture_report(), indent=2, sort_keys=False) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sandbox = sub.add_parser("sandbox", help="write an isolated collector sandbox")
    sandbox.add_argument("directory", type=Path)
    sandbox.add_argument("--now", default=None)
    sub.add_parser("fixture", help="regenerate the committed app fixture report")
    sub.add_parser("fixture-check", help="fail when the committed fixture is out of date")
    arguments = parser.parse_args(argv)
    if arguments.command == "sandbox":
        import ai_usage_report as report
        now = report.parse_ts(arguments.now) if arguments.now else time.time()
        print(write_sandbox(arguments.directory, now))
        return 0
    rendered = render_fixture()
    if arguments.command == "fixture":
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(rendered, encoding="utf-8")
        print(f"fixture: wrote {FIXTURE_PATH}")
        return 0
    current = FIXTURE_PATH.read_text(encoding="utf-8") if FIXTURE_PATH.exists() else ""
    if current != rendered:
        print(f"fixture-check: {FIXTURE_PATH} is out of date; run `make fixture`", file=sys.stderr)
        return 1
    print("fixture-check: committed fixture matches the generator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
