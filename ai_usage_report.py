#!/usr/bin/env python3
"""Read-only reporting layer over the ai-usage collector.

Turns the collector's long-form ``usage.csv`` (plus a bounded ``collector.log``
tail and ``launchctl`` status) into one JSON document, schema
``ai-usage/report/v1`` (see docs/CLI.md). The macOS app renders this report;
all aggregation and freshness rules live here so views never re-derive them.

Guarantees: never executes a provider binary, never runs a collection, never
takes the collector's lock, never writes a file.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import ai_usage_service as collector

REPORT_SCHEMA = "ai-usage/report/v1"
LOG_TAIL_BYTES = 256 * 1024
GROK_BILLING_STALE_SECONDS = 3 * 3600
# A reset time moving ahead by more than this beyond the elapsed time is an early reset.
EARLY_RESET_MIN_JUMP_SECONDS = 3600
LOW_REMAINING_PERCENT = 10.0
DAY = 86400

TOKEN_METRICS = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_input_tokens": "cache_read",
    "cached_read_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_write",
    "cache_creation_tokens": "cache_write",
    "daily_tokens": "total",
    "daily_model_tokens": "total",
}
SUMMED_KINDS = {"event_total", "delta"}
NON_FATAL_ERROR_METRICS = {"session_log_parse_errors", "event_parse_errors", "rotation_data_loss"}
AUTH_PATTERN = re.compile(r"auth|sign.?in|log.?in|401|403|unauthori[sz]ed|credential", re.IGNORECASE)


# --------------------------------------------------------------------------- provider catalogue

class SourceDef:
    def __init__(
        self,
        sid: str,
        role: str,
        label: str,
        kind: str,
        csv_sources: Iterable[str],
        *,
        needs_rows: bool,
        error_metrics: Iterable[str] = (),
    ) -> None:
        self.id = sid
        self.role = role
        self.label = label
        self.kind = kind
        self.csv_sources = set(csv_sources)
        self.needs_rows = needs_rows
        self.error_metrics = set(error_metrics)


class ProviderDef:
    def __init__(self, pid: str, name: str, product: str, *, mode: str, split: str,
                 record_kind: str, quota: str, sources: list[SourceDef],
                 reported_note: Optional[str], quota_note: Optional[str] = None) -> None:
        self.id = pid
        self.name = name
        self.product = product
        self.mode = mode
        self.split = split
        self.record_kind = record_kind
        self.quota = quota  # polled | event | unsupported
        self.sources = sources
        self.reported_note = reported_note
        self.quota_note = quota_note

    def source_for_csv(self, csv_source: str) -> Optional[SourceDef]:
        for source in self.sources:
            if csv_source in source.csv_sources:
                return source
        return None



PROVIDERS = [
    ProviderDef(
        "codex", "Codex", "OpenAI Codex", mode="polled", split="total_only",
        record_kind="period_total", quota="polled",
        reported_note="Codex does not report a usage cost.",
        sources=[
            SourceDef("rate-limits", "quota", "Rate limits",
                      "Codex account API, read-only (account/rateLimits/read)",
                      ["codex-rate-limits"], needs_rows=True,
                      error_metrics=["rate_limits_query", "metadata_query", "unexpected_error"]),
            SourceDef("account-usage", "usage", "Account usage",
                      "Codex account API, read-only (account/usage/read)",
                      ["codex-account-usage"], needs_rows=True,
                      error_metrics=["account_usage_query", "metadata_query", "unexpected_error"]),
        ],
    ),
    ProviderDef(
        "claude", "Claude Code", "Claude Code", mode="event", split="input_output",
        record_kind="event_total", quota="unsupported",
        reported_note="Claude Code's local data doesn't include a usage cost.",
        quota_note="Claude subscription capacity isn't available through a supported API.",
        sources=[
            SourceDef("sessions", "usage", "Session transcripts",
                      "Local Claude Code session transcripts (~/.claude/projects)",
                      ["claude-session-log"], needs_rows=False,
                      error_metrics=["unexpected_error"]),
            SourceDef("stats-cache", "history", "Daily stats",
                      "Claude Code local stats cache (undocumented format)",
                      ["claude-stats-cache"], needs_rows=True,
                      error_metrics=["stats_cache"]),
        ],
    ),
    ProviderDef(
        "antigravity", "Antigravity", "Antigravity CLI", mode="event", split="input_output",
        record_kind="delta", quota="event",
        reported_note="Antigravity does not report a usage cost.",
        quota_note="Reported by Antigravity while it runs. Between sessions the last reading is kept; that is normal, not stale.",
        sources=[
            SourceDef("status-line", "both", "Status-line updates",
                      "Sent by Antigravity while it runs (status-line callback)",
                      ["antigravity-status-line", "antigravity-status-line-events"], needs_rows=True,
                      error_metrics=["status_line_cache", "unexpected_error"]),
        ],
    ),
    ProviderDef(
        "grok", "Grok Build", "Grok Build CLI", mode="event", split="input_output",
        record_kind="event_total", quota="polled",
        reported_note=None,
        quota_note="Credit percentage for the current billing period, from xAI billing. It is not your subscription price.",
        sources=[
            SourceDef("session-log", "usage", "Session log", "Local Grok session files",
                      ["grok-session-log"], needs_rows=False,
                      error_metrics=["unexpected_error"]),
            SourceDef("billing", "quota", "Billing snapshot",
                      "Local billing log; xAI billing lookup as fallback (no model calls)",
                      ["grok-unified-log", "grok-x.ai-billing-acp"], needs_rows=True,
                      error_metrics=["billing_query", "unexpected_error"]),
        ],
    ),
    ProviderDef(
        "gemini_cli", "Gemini CLI", "Gemini CLI", mode="event", split="input_output",
        record_kind="event_total", quota="unsupported",
        reported_note="Gemini CLI does not report a usage cost.",
        sources=[
            SourceDef("telemetry", "usage", "Local telemetry file", "Gemini CLI telemetry (opt-in)",
                      ["gemini-telemetry"], needs_rows=False,
                      error_metrics=["telemetry_file", "unexpected_error"]),
        ],
    ),
]
PROVIDER_BY_ID = {p.id: p for p in PROVIDERS}


# --------------------------------------------------------------------------- time helpers

_FRACTION = re.compile(r"\.(\d+)")


_TS_CACHE: dict[str, Optional[float]] = {}


def parse_ts(value: str) -> Optional[float]:
    """Parse the collector's ISO-8601 shapes to epoch seconds (UTC)."""
    if not value:
        return None
    # Fast path for the collector's own `YYYY-MM-DDTHH:MM:SS[.fff…]Z` shape.
    # Rows in one check share a timestamp, so whole-second prefixes are cached.
    if len(value) >= 20 and value[10] == "T" and value[-1] == "Z" and value[19] in ".Z":
        base = _TS_CACHE.get(value[:19])
        if base is None and value[:19] not in _TS_CACHE:
            try:
                base = float(calendar.timegm((int(value[0:4]), int(value[5:7]), int(value[8:10]),
                                              int(value[11:13]), int(value[14:16]), int(value[17:19]), 0, 0, 0)))
            except ValueError:
                base = None
            _TS_CACHE[value[:19]] = base
        if base is not None:
            if value[19] == ".":
                digits = value[20:-1]
                if digits.isdigit():
                    return base + int(digits) / (10 ** len(digits))
            else:
                return base
    text = value.strip()
    if len(text) == 10 and text[4] == "-":
        try:
            day = dt.date.fromisoformat(text)
        except ValueError:
            return None
        return time.mktime(day.timetuple())
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    match = _FRACTION.search(text)
    if match:
        digits = (match.group(1) + "000000")[:6]
        text = text[: match.start()] + "." + digits + text[match.end():]
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(round(ts), tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_DATE_CACHE: dict[int, str] = {}


def local_date(ts: float) -> str:
    # Local dates (including DST shifts) change only on hour boundaries in the
    # zones this runs in, so memoize per UTC hour.
    hour = int(ts // 3600)
    cached = _DATE_CACHE.get(hour)
    if cached is None:
        t = time.localtime(hour * 3600)
        cached = f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        _DATE_CACHE[hour] = cached
    return cached


def local_midnight(date_text: str) -> float:
    return time.mktime(dt.date.fromisoformat(date_text).timetuple())


def timezone_name() -> str:
    name = os.environ.get("TZ")
    if name:
        return name.lstrip(":")
    try:
        target = os.readlink("/etc/localtime")
        marker = "zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    except OSError:
        pass
    return time.tzname[0]


def number(value: str) -> Optional[float]:
    if value == "" or value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def clean(value: Optional[float]) -> Any:
    if value is None:
        return None
    rounded = round(value, 4)
    return int(rounded) if rounded == int(rounded) else rounded


# --------------------------------------------------------------------------- CSV scan

class Row:
    __slots__ = ("tx", "ts", "provider", "category", "metric", "kind", "scope",
                 "period_start", "period_end", "value", "unit", "window", "resets", "source", "status", "message")

    def __init__(self, fields: list[str], ix: tuple[int, ...]) -> None:
        # `fields` is padded to the header width; `ix` maps CSV_FIELDS order to columns.
        (tx, _index, collected, self.provider, self.category, self.metric, self.kind, self.scope,
         self.period_start, self.period_end, self.value, self.unit, self.window, self.resets,
         self.source, self.status, self.message) = [fields[i] for i in ix]
        self.tx = tx
        self.ts = parse_ts(collected)


class Attempt:
    def __init__(self, tx: str, order: int) -> None:
        self.tx = tx
        self.order = order
        self.at: Optional[float] = None
        self.rows: dict[str, list[Row]] = {}  # provider -> rows

    def provider_rows(self, provider: str) -> list[Row]:
        return self.rows.get(provider, [])


class Scan:
    def __init__(self) -> None:
        self.row_count = 0
        self.attempts: list[Attempt] = []
        self.usage: dict[str, list[Row]] = {}
        self.quota: dict[str, list[Row]] = {}
        self.billing: list[Row] = []
        self.api_cost: list[Row] = []
        self.monthly_rate: dict[str, list[Row]] = {}
        self.model_usage: list[Row] = []


ATTEMPT_CATEGORIES = {"availability", "collection", "freshness", "quota", "billing", "usage", "activity", "subscription", "cost", "context"}


def scan_csv(path: Path) -> Scan:
    scan = Scan()
    if not path.is_file():
        return scan
    attempts: dict[str, Attempt] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            return scan
        width = len(header) + 1
        # Columns absent from the header read as "" from the padding slot.
        ix = tuple(header.index(name) if name in header else len(header) for name in collector.CSV_FIELDS)
        padding = [""] * width
        for fields in reader:
            scan.row_count += 1
            if len(fields) < width:
                fields = fields + padding[len(fields):]
            row = Row(fields, ix)
            if not row.provider:
                continue
            attempt = attempts.get(row.tx)
            if attempt is None:
                attempt = Attempt(row.tx, len(attempts))
                attempts[row.tx] = attempt
            # Every row of a check shares the check time, except event usage rows,
            # which carry their event time. A transaction with only event rows is
            # ingestion, not a check.
            if row.ts is not None and not (row.category in ("usage", "cost") and row.kind in SUMMED_KINDS):
                attempt.at = row.ts if attempt.at is None else max(attempt.at, row.ts)
            # Event usage rows carry the event time, not the attempt time; they
            # are aggregated by day below and kept out of per-attempt rows.
            if not (row.category == "usage" and row.kind in SUMMED_KINDS):
                attempt.rows.setdefault(row.provider, []).append(row)
            if row.category == "usage" and row.metric in TOKEN_METRICS and row.kind in (SUMMED_KINDS | {"period_total"}):
                scan.usage.setdefault(row.provider, []).append(row)
            elif row.category == "usage" and row.metric == "model_usage":
                scan.model_usage.append(row)
            elif row.category == "quota" and row.metric in ("used_percent", "remaining_percent") and row.kind == "snapshot":
                scan.quota.setdefault(row.provider, []).append(row)
            elif row.category == "billing" and row.metric == "on_demand_used":
                scan.billing.append(row)
            elif row.category == "cost" and row.metric == "api_cost" and row.status == "ok":
                scan.api_cost.append(row)
            elif row.category == "cost" and row.kind == "monthly_rate":
                scan.monthly_rate.setdefault(row.provider, []).append(row)
    # Attempts are transactions with at least one check-time row; pure event
    # ingestion transactions (e.g. a migration backfill) are not attempts.
    scan.attempts = sorted((a for a in attempts.values() if a.at is not None), key=lambda a: (a.at, a.order))
    return scan


# --------------------------------------------------------------------------- source results

class SourceState:
    def __init__(self, definition: SourceDef) -> None:
        self.definition = definition
        self.last_attempt_at: Optional[float] = None
        self.last_read_at: Optional[float] = None
        self.status = "waiting"
        self.error: Optional[str] = None
        self.auth = False


class ProviderState:
    def __init__(self, definition: ProviderDef) -> None:
        self.definition = definition
        self.sources = {s.id: SourceState(s) for s in definition.sources}
        self.enabled = True
        self.setup = "waiting"
        self.setup_note: Optional[str] = None
        self.last_attempt_at: Optional[float] = None
        self.latest_quota_scopes: Optional[set[str]] = None  # windows in latest successful quota read
        self.latest_quota_read_at: Optional[float] = None
        self.notes: list[str] = []


def evaluate_attempt(state: ProviderState, attempt: Attempt) -> Optional[str]:
    """Apply one attempt to a provider. Returns 'updated' | 'partly' | 'failed' | 'skipped' | None."""
    rows = attempt.provider_rows(state.definition.id)
    if not rows:
        return None
    at = attempt.at
    availability = {r.metric: r for r in rows if r.category == "availability"}
    enabled_row = availability.get("enabled")
    if enabled_row is not None and enabled_row.status == "disabled":
        state.enabled = False
        state.setup = "disabled"
        state.setup_note = "Turned off in the collector config; cached values are kept."
        for source in state.sources.values():
            source.status = "off"
        return "skipped"
    state.enabled = True
    detected_row = availability.get("detected")
    if detected_row is None and all(r.category == "cost" for r in rows):
        # Only the configured subscription price was recorded: the collector
        # found nothing to discover or read for this provider.
        state.setup = "not_detected"
        state.setup_note = "Not found on this Mac at the last check."
        for source in state.sources.values():
            source.status = "not_detected"
        return None
    if detected_row is not None and detected_row.value in ("0", "False", "false"):
        state.setup = "not_detected"
        state.setup_note = detected_row.message or "Not installed on this Mac."
        for source in state.sources.values():
            source.status = "not_detected"
        return None
    state.last_attempt_at = at
    failed = 0
    attempted = 0
    quota_scopes: set[str] = set()
    for source in state.sources.values():
        definition = source.definition
        source.last_attempt_at = at
        data_rows = [
            r for r in rows
            if r.source in definition.csv_sources and r.status not in ("error", "missing")
            and r.category not in ("collection",)
        ]
        errors = [
            r for r in rows
            if r.category == "collection" and r.status in ("error", "missing")
            and r.metric in definition.error_metrics and r.metric not in NON_FATAL_ERROR_METRICS
        ]
        if state.definition.id == "gemini_cli" and any(r.metric == "telemetry_file" for r in errors):
            source.status = "off"
            source.error = None
            state.setup = "not_configured"
            state.setup_note = "Usage telemetry is off. Gemini's telemetry includes account identifiers, so turning it on is your choice."
            continue
        if definition.id == "status-line" and errors and not data_rows:
            # No status-line snapshot yet: Antigravity hasn't run since the callback was attached.
            source.status = "waiting" if source.last_read_at is None else "ok"
            continue
        attempted += 1
        succeeded = bool(data_rows) if definition.needs_rows else not errors or bool(data_rows)
        if errors and not data_rows:
            succeeded = False
        if succeeded:
            source.last_read_at = at
            source.status = "ok"
            source.error = None
            source.auth = False
            if definition.role in ("quota", "both"):
                scopes = {r.scope for r in data_rows if r.category == "quota" and r.metric in ("used_percent", "remaining_percent")}
                if definition.role == "quota" or scopes:
                    quota_scopes |= scopes
                    state.latest_quota_read_at = at
        elif errors:
            failed += 1
            message = errors[0].message or errors[0].metric
            source.error = message
            source.auth = bool(AUTH_PATTERN.search(message))
            source.status = "auth" if source.auth else "failed"
        else:
            # Read ran but produced nothing new (e.g. no fresh billing snapshot).
            source.status = "stale" if source.last_read_at is not None else "waiting"
    if state.latest_quota_read_at == at:
        state.latest_quota_scopes = quota_scopes
    if state.setup in ("waiting", "not_detected", "disabled"):
        state.setup = "ready"
        state.setup_note = None
    if attempted == 0:
        return None
    if failed == 0:
        return "updated"
    return "failed" if failed == attempted else "partly"


# --------------------------------------------------------------------------- days

def aggregate_days(rows: list[Row]) -> dict[str, dict[str, float]]:
    """Per local date: {'input','output','cache_read','cache_write','total','incomplete'}."""
    days: dict[str, dict[str, float]] = {}
    latest_period: dict[tuple[str, str], Row] = {}
    for row in rows:
        value = number(row.value)
        if value is None:
            continue
        if row.kind in SUMMED_KINDS:
            if row.ts is None:
                continue
            bucket = days.setdefault(local_date(row.ts), {})
            field = TOKEN_METRICS[row.metric]
            bucket[field] = bucket.get(field, 0.0) + value
            if row.status == "incomplete" and row.metric in ("input_tokens",):
                bucket["incomplete"] = bucket.get("incomplete", 0.0) + 1
        elif row.kind == "period_total":
            key = (row.metric, row.scope)
            prior = latest_period.get(key)
            if prior is None or (row.ts or 0) >= (prior.ts or 0):
                latest_period[key] = row
    period_days: dict[str, dict[str, float]] = {}
    for (metric, _scope), row in latest_period.items():
        date = (row.period_start or row.scope)[:10]
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            continue
        bucket = period_days.setdefault(date, {})
        field = TOKEN_METRICS[metric]
        bucket.setdefault(f"_{metric}", 0.0)
        bucket[f"_{metric}"] += number(row.value) or 0.0
    for date, bucket in period_days.items():
        target = days.setdefault(date, {})
        if "_daily_tokens" in bucket:
            target["total"] = bucket["_daily_tokens"]
        elif "_daily_model_tokens" in bucket and "input" not in target and "output" not in target:
            # Claude stats-cache daily totals fill days the transcripts don't cover.
            target["total"] = bucket["_daily_model_tokens"]
            target["total_only"] = 1
    return days


def build_days(definition: ProviderDef, aggregated: dict[str, dict[str, float]], *, today: str,
               count: int, usage_read_at: Optional[float], loss_dates: set[str],
               now: float, reads_complete: bool = True) -> tuple[list[dict[str, Any]], Optional[str], Optional[dict[str, Any]]]:
    dates = [local_date(local_midnight(today) - (count - 1 - i) * DAY + 3 * 3600) for i in range(count)]
    measured_dates = sorted(d for d in aggregated if d <= today)
    coverage_start = measured_dates[0] if measured_dates else None
    out: list[dict[str, Any]] = []
    today_record: Optional[dict[str, Any]] = None
    for date in dates:
        bucket = aggregated.get(date)
        record: dict[str, Any] = {
            "date": date, "state": "missing", "input": None, "output": None, "total": None,
            "cache_read": None, "cache_write": None, "incomplete_events": 0, "as_of": None,
        }
        if coverage_start is None or date < coverage_start:
            record["state"] = "not_collected"
        elif bucket is not None and date not in loss_dates:
            split = definition.split == "input_output" and not bucket.get("total_only") and ("input" in bucket or "output" in bucket)
            if split:
                record["input"] = clean(bucket.get("input", 0.0))
                record["output"] = clean(bucket.get("output", 0.0))
                record["total"] = clean(bucket.get("input", 0.0) + bucket.get("output", 0.0))
            else:
                record["total"] = clean(bucket.get("total", 0.0))
            if "cache_read" in bucket:
                record["cache_read"] = clean(bucket["cache_read"])
            if "cache_write" in bucket:
                record["cache_write"] = clean(bucket["cache_write"])
            record["incomplete_events"] = int(bucket.get("incomplete", 0))
            record["state"] = "measured" if (record["total"] or 0) > 0 else "zero"
            if date < today and (usage_read_at is None or usage_read_at < local_midnight(date) + DAY):
                # The source was last read before this day ended (e.g. the
                # collector stopped): its totals are a partial day, not complete.
                record["state"] = "partial"
                record["as_of"] = iso(usage_read_at) if usage_read_at is not None else None
        elif date in loss_dates:
            record["state"] = "missing"
        elif (definition.record_kind in SUMMED_KINDS and reads_complete
              and usage_read_at is not None and usage_read_at >= local_midnight(date) + DAY):
            # Event logs are cumulative: a later successful read covers this day,
            # so no events means a measured zero.
            record.update({"state": "zero", "total": 0})
            if definition.split == "input_output":
                record.update({"input": 0, "output": 0})
        if date == today and record["state"] != "not_collected":
            read_today = (
                definition.record_kind in SUMMED_KINDS and reads_complete
                and usage_read_at is not None and usage_read_at >= local_midnight(today)
            )
            if bucket is not None or read_today:
                if bucket is None:
                    record.update({"total": 0})
                    if definition.split == "input_output":
                        record.update({"input": 0, "output": 0})
                record["state"] = "partial"
                record["as_of"] = iso(min(now, usage_read_at)) if usage_read_at else None
                today_record = {k: record[k] for k in ("date", "input", "output", "total", "cache_read", "cache_write")}
                today_record["as_of"] = iso(min(now, usage_read_at)) if usage_read_at else None
                today_record["partial"] = True
            else:
                record["state"] = "missing"
        out.append(record)
    return out, coverage_start, today_record


def models_today(definition: ProviderDef, rows: list[Row], model_rows: list[Row], today: str) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    if definition.id == "grok":
        for row in model_rows:
            if row.provider != "grok" or row.ts is None or local_date(row.ts) != today:
                continue
            try:
                payload = json.loads(row.value)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            for model, usage in payload.items():
                if isinstance(usage, dict):
                    tokens = (usage.get("inputTokens") or 0) + (usage.get("outputTokens") or 0)
                    if isinstance(tokens, (int, float)):
                        totals[model] = totals.get(model, 0) + tokens
    else:
        for row in rows:
            if row.kind not in SUMMED_KINDS or row.ts is None or local_date(row.ts) != today:
                continue
            if TOKEN_METRICS.get(row.metric) not in ("input", "output") or ":" not in row.scope:
                continue
            model = row.scope.split(":", 1)[1]
            totals[model] = totals.get(model, 0) + (number(row.value) or 0)
    ordered = sorted(((m, t) for m, t in totals.items() if t > 0), key=lambda item: (-item[1], item[0]))
    return [{"name": name, "tokens": clean(tokens)} for name, tokens in ordered]


# --------------------------------------------------------------------------- quota

def window_identity(provider: str, scope: str, window_seconds: Optional[float]) -> tuple[str, str, list[str]]:
    if provider == "grok":
        period = scope.replace("USAGE_PERIOD_TYPE_", "").lower()
        if period == "weekly":
            return "Weekly credits", "Credits", ["7d", "35d"]
        if period == "monthly":
            return "Monthly credits", "Credits", ["30d", "90d"]
        return f"{period.title() or 'Billing'} credits", "Credits", ["7d", "30d"]
    if provider == "antigravity":
        name = scope.replace("_", " ").strip() or "Model"
        return f"{name[:1].upper()}{name[1:]} quota", name[:1].upper() + name[1:], ["24h", "7d"]
    seconds = int(window_seconds) if window_seconds else 0
    if seconds == 18000:
        label, short, ranges = "5-hour limit", "5-hour", ["24h", "7d"]
    elif seconds == 604800:
        label, short, ranges = "Weekly limit", "Weekly", ["7d", "35d"]
    elif 28 * DAY <= seconds <= 31 * DAY:
        label, short, ranges = "Monthly limit", "Monthly", ["30d", "90d"]
    elif seconds:
        hours = seconds / 3600
        text = f"{hours:g}-hour" if hours < 48 else f"{hours / 24:g}-day"
        label, short, ranges = f"{text} limit", text, ["24h", "7d"] if seconds <= DAY else ["7d", "35d"]
    else:
        label, short, ranges = "Limit", "Limit", ["7d", "35d"]
    limit_id = scope.split(":", 1)[0] if ":" in scope else ""
    if provider == "codex" and limit_id and limit_id != "codex":
        label = f"{limit_id} · {label}"
        short = f"{limit_id} · {short}"
    return label, short, ranges


def keep_seconds(ranges: list[str]) -> float:
    longest = ranges[-1]
    return float(int(longest[:-1]) * (3600 if longest.endswith("h") else DAY))


def collapse_jitter(times: list[float], tolerance: float = 600.0) -> list[float]:
    """One reset per event: providers report the same reset a few seconds apart."""
    kept: list[float] = []
    for t in times:
        if not kept or t - kept[-1] > tolerance:
            kept.append(t)
    return kept


def early_resets(ordered: list[dict[str, Any]], window_seconds: Optional[float], *, used_basis: bool) -> set[float]:
    """Resets that came before the reported reset time.

    Between two readings taken inside the same window, a reset time that jumps
    ahead by more than the time elapsed (an idle rolling window only moves with
    the clock) while usage didn't rise means the provider started a new window
    early. It is placed where that window began when that falls between the two
    readings, otherwise at the reading that first showed it.
    """
    found: set[float] = set()
    for before, after in zip(ordered, ordered[1:]):
        if before["resets_at"] is None or after["resets_at"] is None or before["resets_at"] <= after["t"]:
            continue
        jump = (after["resets_at"] - before["resets_at"]) - (after["t"] - before["t"])
        used_before = before["percent"] if used_basis else 100.0 - before["percent"]
        used_after = after["percent"] if used_basis else 100.0 - after["percent"]
        if jump <= EARLY_RESET_MIN_JUMP_SECONDS or used_after > used_before:
            continue
        start = after["resets_at"] - window_seconds if window_seconds else None
        found.add(start if start is not None and before["t"] < start <= after["t"] else after["t"])
    return found


def build_windows(state: ProviderState, rows: list[Row], freshness_rows: dict[str, float], *,
                  now: float, stale_after: float) -> list[dict[str, Any]]:
    definition = state.definition
    quota_source = next((s for s in state.sources.values() if s.definition.role in ("quota", "both")), None)
    groups: dict[str, list[Row]] = {}
    for row in rows:
        if definition.id == "codex" and row.metric != "used_percent":
            continue
        if definition.id == "grok" and row.metric != "used_percent":
            continue
        if definition.id == "antigravity" and row.metric != "remaining_percent":
            continue
        groups.setdefault(row.scope, []).append(row)
    windows: list[dict[str, Any]] = []
    for scope, group in groups.items():
        window_seconds = next((number(r.window) for r in reversed(group) if number(r.window)), None)
        label, short, ranges = window_identity(definition.id, scope, window_seconds)
        derived = definition.id == "codex"
        basis = "used" if definition.id == "grok" else "remaining"
        readings: dict[float, dict[str, Any]] = {}
        for row in group:
            value = number(row.value)
            if value is None or row.ts is None:
                continue
            t = row.ts
            if row.source == "grok-unified-log" and row.tx in freshness_rows:
                t = row.ts - freshness_rows[row.tx]
            percent = 100.0 - value if derived else value
            resets = parse_ts(row.resets)
            readings[round(t)] = {"t": t, "percent": round(max(0.0, min(100.0, percent)), 1), "resets_at": resets}
        ordered = [readings[k] for k in sorted(readings)]
        if not ordered:
            continue
        last = ordered[-1]
        keep_from = now - keep_seconds(ranges)
        kept = [r for r in ordered if r["t"] >= keep_from] or [last]
        resets = {r["resets_at"] for r in ordered if r["resets_at"] and ordered[0]["t"] < r["resets_at"] <= now}
        resets.update(early_resets(ordered, window_seconds, used_basis=basis == "used"))
        resets = collapse_jitter(sorted(r for r in resets if r <= now))
        measured_at = last["t"]
        resets_at = last["resets_at"]
        reset_passed = bool(resets_at is not None and measured_at < resets_at <= now)
        omitted = (
            definition.quota == "polled"
            and state.latest_quota_scopes is not None
            and scope not in state.latest_quota_scopes
        )
        span = window_seconds or 7 * DAY
        retired = omitted and now - measured_at > max(2 * span, DAY)
        cause: Optional[str] = None
        collected_at = quota_source.last_read_at if quota_source else None
        threshold = GROK_BILLING_STALE_SECONDS if definition.id == "grok" else stale_after
        if quota_source is not None and quota_source.status == "auth":
            cause = "auth"
        elif quota_source is not None and quota_source.status == "failed":
            cause = "failed"
        elif definition.quota == "polled" and (collected_at is None or now - collected_at > stale_after):
            cause = "not_collected"
        elif omitted or (definition.quota == "polled" and now - measured_at > threshold):
            cause = "source_old"
        elif reset_passed:
            cause = "reset_passed"
        status = "stale" if cause else "current"
        # When a current reading will turn stale with no new data, so a cached
        # report re-evaluated later can never show it as fresh.
        becomes: list[tuple[float, str]] = []
        if status == "current":
            if definition.quota == "polled" and collected_at is not None:
                becomes.append((collected_at + stale_after, "not_collected"))
                becomes.append((measured_at + threshold, "source_old"))
            if resets_at is not None and resets_at > measured_at:
                becomes.append((resets_at, "reset_passed"))
        next_stale = min(becomes) if becomes else None
        remaining = last["percent"] if basis == "remaining" else 100.0 - last["percent"]
        limit = None
        if status == "current":
            if remaining <= 0:
                limit = "reached"
            elif remaining <= LOW_REMAINING_PERCENT:
                limit = "low"
        windows.append({
            "id": scope or "default",
            "label": label,
            "short": short,
            "basis": basis,
            "derived": derived,
            "percent": clean(last["percent"]),
            "window_seconds": clean(window_seconds),
            "measured_at": iso(measured_at),
            "collected_at": iso(collected_at),
            "resets_at": iso(resets_at),
            "reset_passed": reset_passed,
            "status": status,
            "stale_cause": cause,
            "becomes_stale_at": iso(next_stale[0]) if next_stale else None,
            "becomes_stale_cause": next_stale[1] if next_stale else None,
            "omitted": omitted,
            "retired": retired,
            "limit": limit,
            "readings": [{"t": iso(r["t"]), "percent": clean(r["percent"]), "resets_at": iso(r["resets_at"])} for r in kept],
            "resets": [iso(r) for r in resets if r >= keep_from],
            "ranges": ranges,
        })
    order = {"5-hour": 0, "Weekly": 1, "Monthly": 2}
    windows.sort(key=lambda w: (w["retired"], order.get(w["short"], 3), w["label"]))
    return windows


def quota_summary(definition: ProviderDef, state: ProviderState, windows: list[dict[str, Any]]) -> dict[str, Any]:
    quota_source = next((s for s in state.sources.values() if s.definition.role in ("quota", "both")), None)
    active = [w for w in windows if not w["retired"]]
    summary: dict[str, Any] = {
        "status": "none", "stale_cause": None, "measured_at": None,
        "collected_at": iso(quota_source.last_read_at) if quota_source else None,
        "note": definition.quota_note, "windows": windows,
    }
    if definition.quota == "unsupported":
        summary["status"] = "unsupported"
        return summary
    if not active:
        summary["status"] = "unavailable" if state.setup == "ready" else "none"
        return summary
    statuses = sorted({w["status"] for w in active})
    summary["status"] = statuses[0] if len(statuses) == 1 else "mixed"
    stale = next((w for w in active if w["status"] == "stale"), None)
    summary["stale_cause"] = stale["stale_cause"] if stale else None
    summary["measured_at"] = max(w["measured_at"] for w in active if w["measured_at"])
    return summary


# --------------------------------------------------------------------------- log, service, schedule

LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ (\w+) (.*)$")


def read_log_events(path: Path) -> list[tuple[float, str, str]]:
    if not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - LOG_TAIL_BYTES))
        data = handle.read().decode("utf-8", errors="replace")
    events = []
    for line in data.splitlines():
        match = LOG_LINE.match(line)
        if not match:
            continue
        try:
            ts = time.mktime(time.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        events.append((ts, match.group(2), match.group(3)))
    return events


def probe_service(runner: Optional[Callable[[list[str]], subprocess.CompletedProcess]] = None) -> dict[str, Any]:
    """Service state via the collector's own read-only `launchctl print` probe."""
    try:
        status = collector.service_status()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return {"state": "unknown", "pid": None, "disabled": None, "detail": f"launchctl failed: {error}"}
    return {key: status.get(key) for key in ("state", "pid", "disabled", "detail")}


PAUSE_MIN_VERSION = (2, 2, 0)
_VERSION_LINE = re.compile(r'^VERSION = "([0-9]+)\.([0-9]+)\.([0-9]+)"', re.MULTILINE)


def installed_collector_script() -> Optional[Path]:
    """The script the LaunchAgent actually runs (ProgramArguments[1])."""
    try:
        import plistlib
        with collector.DEFAULT_PLIST_PATH.open("rb") as handle:
            arguments = plistlib.load(handle).get("ProgramArguments") or []
    except (OSError, ValueError):
        return None
    return Path(arguments[1]) if len(arguments) > 1 else None


def installed_collector_version(script: Optional[Path]) -> Optional[tuple[int, int, int]]:
    """Read VERSION from the installed script's text. Never executes it."""
    if script is None:
        return None
    try:
        with script.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(16384)
    except OSError:
        return None
    match = _VERSION_LINE.search(head)
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def default_runner(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=5, check=False)


# --------------------------------------------------------------------------- report

def build_report(
    config: dict[str, Any],
    *,
    config_path: Path,
    now: float,
    days: int = 90,
    service: Optional[dict[str, Any]] = None,
    installed_version: Optional[tuple[int, int, int]] = None,
) -> dict[str, Any]:
    usage_path, log_path = collector.configured_paths(config)
    interval = int(config.get("poll_interval_seconds", 3600))
    stale_after = 2 * interval
    scan = scan_csv(usage_path)
    log_events = read_log_events(log_path)
    today = local_date(now)
    service = dict(service or {"state": "unknown", "pid": None, "detail": "service probe skipped"})
    service.setdefault("disabled", None)
    supports_pause = installed_version is not None and installed_version >= PAUSE_MIN_VERSION
    capabilities = {
        # Read from the installed collector's VERSION; older collectors ignore
        # poll_paused and have no `once --progress`.
        "pause": supports_pause,
        "progress": supports_pause,
        "service_control": True,
        "settings": True,
    }
    pause_requested = config.get("poll_paused") is True

    states = {p.id: ProviderState(p) for p in PROVIDERS}
    latest_results: dict[str, Optional[str]] = {}
    for attempt in scan.attempts:
        if attempt.at is not None and attempt.at > now:
            continue
        for pid, state in states.items():
            result = evaluate_attempt(state, attempt)
            if attempt.provider_rows(pid):
                latest_results[pid] = result
    attempts = [a for a in scan.attempts if a.at is not None and a.at <= now]
    latest = attempts[-1] if attempts else None

    # A provider the latest check didn't mention at all was not found
    # (collectors emit no rows for a missing binary and missing local data).
    deferred: dict[str, float] = {}
    if latest is not None:
        for pid, state in states.items():
            rows = latest.provider_rows(pid)
            if not rows:
                state.setup = "not_detected"
                state.setup_note = "Not found on this Mac at the last check."
                for source in state.sources.values():
                    source.status = "not_detected"
            for row in rows:
                if row.metric == "session_files_deferred":
                    deferred[pid] = deferred.get(pid, 0) + (number(row.value) or 0)

    # Configured enablement wins for providers the latest attempt hasn't seen yet.
    for pid, state in states.items():
        settings = collector.provider_config(config, pid)
        if settings.get("enabled", True) is not True and state.setup != "disabled":
            state.enabled = False
            state.setup = "disabled"
            state.setup_note = "Turned off in the collector config; cached values are kept."
            for source in state.sources.values():
                source.status = "off"

    # Collection summary for the latest attempt.
    collection: dict[str, Any] = {
        "last_attempt_at": None, "last_attempt_result": None, "last_success_at": None,
        "failures": [], "summary": None,
    }
    if latest is not None:
        counts = {"updated": 0, "partly": 0, "failed": 0}
        skipped: list[str] = []
        failures: list[dict[str, Any]] = []
        for pid, state in states.items():
            rows = latest.provider_rows(pid)
            if not rows:
                continue
            result = latest_results.get(pid)
            if result == "skipped":
                skipped.append(state.definition.name)
            elif result in counts:
                counts[result] += 1
                for source in state.sources.values():
                    if source.status in ("failed", "auth") and source.last_attempt_at == latest.at:
                        failures.append({
                            "provider": pid, "source": source.definition.id,
                            "kind": source.definition.role if source.definition.role != "history" else "usage",
                            "at": iso(latest.at), "message": source.error, "auth": source.auth,
                        })
        attempted = sum(counts.values())
        result = "success"
        if counts["failed"] and not (counts["updated"] or counts["partly"]):
            result = "failed"
        elif failures:
            result = "partial"
        successes = [a.at for a in attempts if any(
            evaluate_ok(a, s) for s in states.values())]
        collection.update({
            "last_attempt_at": iso(latest.at),
            "last_attempt_result": result,
            "last_success_at": iso(successes[-1]) if successes else None,
            "failures": failures,
            "summary": {
                "providers_attempted": attempted,
                "providers_updated": counts["updated"],
                "providers_partly": counts["partly"],
                "providers_failed": counts["failed"],
                "skipped": skipped,
            },
        })
    completions = [e[0] for e in log_events if e[0] <= now
                   and re.search(r"(^|one-shot )collection completed rows=0\b", e[2])
                   and (latest is None or e[0] > (latest.at or 0))]
    if completions:
        # A check that found nothing writes no CSV transaction; the log still records it.
        collection["last_attempt_at"] = iso(completions[-1])
        collection["last_attempt_result"] = "success"
        collection["last_success_at"] = iso(completions[-1])
        collection["failures"] = []
        collection["summary"] = {"providers_attempted": 0, "providers_updated": 0, "providers_partly": 0,
                                 "providers_failed": 0, "skipped": []}
    cycle_failures = [e for e in log_events if "collection cycle failed" in e[2] and e[0] <= now
                      and (latest is None or e[0] > (latest.at or 0))]
    if cycle_failures:
        ts, _level, message = cycle_failures[-1]
        collection["last_attempt_at"] = iso(ts)
        collection["last_attempt_result"] = "failed"
        collection["failures"] = [{
            "provider": None, "source": None, "kind": "collector", "at": iso(ts),
            "message": message.split("collection cycle failed:", 1)[-1].strip() or message,
            "auth": False,
        }]
        collection["summary"] = None

    next_scheduled = None
    paused = pause_requested and supports_pause
    if service.get("state") == "running" and not paused:
        for ts, _level, message in reversed(log_events):
            # Anything before a pause or a daemon (re)start is not the current schedule.
            if message.startswith(("scheduled checks paused", "collector daemon started")):
                break
            match = re.search(r"(?:^collection completed .*|^scheduled checks resumed )next_poll_seconds=(\d+)", message)
            if match:
                next_scheduled = ts + int(match.group(1))
                break
    schedule = {
        "state": "paused" if paused else ("active" if service.get("state") == "running" else "unknown"),
        "interval_minutes": interval // 60,
        "pause_supported": supports_pause,
        "pause_requested": pause_requested,
        "next_scheduled_at": iso(next_scheduled),
    }

    freshness_rows: dict[str, float] = {}
    loss_dates: dict[str, set[str]] = {}
    for attempt in attempts:
        for pid, rows in attempt.rows.items():
            for row in rows:
                if row.metric == "billing_source_age_seconds" and row.source == "grok-unified-log":
                    age = number(row.value)
                    if age is not None:
                        freshness_rows[row.tx] = age
                elif row.metric == "rotation_data_loss" and row.ts is not None:
                    loss_dates.setdefault(pid, set()).add(local_date(row.ts))

    providers_out = []
    for definition in PROVIDERS:
        state = states[definition.id]
        usage_rows = scan.usage.get(definition.id, [])
        usage_source = next((s for s in state.sources.values() if s.definition.role in ("usage", "both")), None)
        usage_read_at = usage_source.last_read_at if usage_source else None
        aggregated = aggregate_days(usage_rows)
        day_records, coverage_start, today_record = build_days(
            definition, aggregated, today=today, count=days, usage_read_at=usage_read_at,
            loss_dates=loss_dates.get(definition.id, set()), now=now,
            reads_complete=definition.id not in deferred)
        if definition.id in deferred:
            state.notes.append(
                f"{int(deferred[definition.id])} session files are still queued for reading; "
                "recent days may be incomplete, so empty days are shown as missing rather than zero.")
        event_times = [r.ts for r in usage_rows if r.kind in SUMMED_KINDS and r.ts is not None and r.ts <= now]
        if definition.mode == "event":
            usage_measured = max(event_times) if event_times else None
        else:
            usage_measured = usage_read_at
        usage_cause = None
        if not usage_rows and (usage_read_at is None or definition.mode == "event"):
            # Event sources with no events yet, or a source never read: nothing measured.
            usage_status = "none"
        elif usage_source is not None and usage_source.status in ("failed", "auth"):
            usage_status, usage_cause = "stale", "failed"
        elif usage_read_at is None or now - usage_read_at > stale_after:
            usage_status, usage_cause = "stale", "not_collected"
        elif definition.mode != "event" and usage_measured is not None and now - usage_measured > stale_after:
            usage_status, usage_cause = "stale", "source_old"
        else:
            usage_status = "current"
        usage_next: Optional[tuple[float, str]] = None
        if usage_status == "current" and usage_read_at is not None:
            candidates = [(usage_read_at + stale_after, "not_collected")]
            if definition.mode != "event" and usage_measured is not None:
                candidates.append((usage_measured + stale_after, "source_old"))
            usage_next = min(candidates)
        windows = build_windows(state, scan.quota.get(definition.id, []), freshness_rows, now=now, stale_after=stale_after)
        quota = quota_summary(definition, state, windows)

        costs: dict[str, Any] = {"reported": None, "reported_note": definition.reported_note,
                                 "api_equivalent": None, "subscription": None}
        if definition.id == "grok":
            billing = [r for r in scan.billing if r.ts is not None and r.ts <= now and number(r.value) is not None]
            if billing:
                latest_bill = max(billing, key=lambda r: r.ts)
                measured = latest_bill.ts - freshness_rows.get(latest_bill.tx, 0.0) if latest_bill.source == "grok-unified-log" else latest_bill.ts
                costs["reported"] = {
                    "amount_usd": clean((number(latest_bill.value) or 0) / 100.0),
                    "basis": "On-demand usage reported by xAI billing",
                    "period_start": iso(parse_ts(latest_bill.period_start)),
                    "period_end": iso(parse_ts(latest_bill.period_end)),
                    "measured_at": iso(measured),
                }
            else:
                costs["reported_note"] = "No billing snapshot has been read yet."
            window_start = now - 30 * DAY
            api = [number(r.value) or 0 for r in scan.api_cost
                   if r.provider == "grok" and r.ts is not None and window_start <= r.ts <= now]
            if api:
                costs["api_equivalent"] = {"amount_usd": clean(round(sum(api), 2)),
                                           "basis": "API-equivalent cost reported by Grok sessions",
                                           "range_days": 30}
        month = today[:7]
        rate_rows = [r for r in scan.monthly_rate.get(definition.id, []) if r.period_start[:7] == month and number(r.value) is not None]
        configured = collector.provider_config(config, definition.id).get("monthly_subscription_usd")
        if rate_rows:
            costs["subscription"] = {"monthly_usd": clean(number(max(rate_rows, key=lambda r: r.ts or 0).value)),
                                     "source": "csv", "entered_by_user": True}
        elif isinstance(configured, (int, float)) and not isinstance(configured, bool):
            costs["subscription"] = {"monthly_usd": clean(float(configured)), "source": "config", "entered_by_user": True}

        sources_out = []
        for source in state.sources.values():
            measured = None
            if source.definition.role in ("quota", "both") and quota["measured_at"]:
                measured = quota["measured_at"]
            if source.definition.role in ("usage", "both") and usage_measured:
                measured = max(filter(None, [measured, iso(usage_measured)]))
            sources_out.append({
                "id": source.definition.id, "role": source.definition.role,
                "label": source.definition.label, "kind": source.definition.kind,
                "status": source.status, "last_attempt_at": iso(source.last_attempt_at),
                "last_read_at": iso(source.last_read_at), "measured_at": measured,
                "error": source.error,
            })
        if definition.id == "claude":
            cache = state.sources["stats-cache"]
            if cache.status == "ok":
                stale_note = any(
                    r.metric == "source_age_seconds" and r.status == "stale"
                    for r in (latest.provider_rows("claude") if latest else []))
                if stale_note:
                    cache.status = "stale"
                    sources_out[1]["status"] = "stale"
                    state.notes.append("The daily stats cache has stopped updating; session transcripts keep today current.")
        incomplete_today = (today_record or {}).get("total") is not None and any(
            d["incomplete_events"] for d in day_records if d["date"] == today)
        if incomplete_today:
            state.notes.append("Some of today's Grok turns reported incomplete usage; their counts may be low.")
        missing_days = [d["date"] for d in day_records if d["state"] == "missing" and d["date"] != today]
        if missing_days and definition.id in loss_dates and set(missing_days) & loss_dates[definition.id]:
            state.notes.append("A session log rotated before it was read; affected days are shown as missing, not zero.")

        providers_out.append({
            "id": definition.id, "name": definition.name, "product": definition.product,
            "enabled": state.enabled, "setup": state.setup, "setup_note": state.setup_note,
            "usage": {
                "status": usage_status, "stale_cause": usage_cause,
                "becomes_stale_at": iso(usage_next[0]) if usage_next else None,
                "becomes_stale_cause": usage_next[1] if usage_next else None,
                "measured_at": iso(usage_measured), "collected_at": iso(usage_read_at),
                "record_kind": definition.record_kind, "mode": definition.mode, "split": definition.split,
            },
            "coverage_start": coverage_start,
            "today": today_record,
            "days": day_records,
            "models_today": models_today(definition, usage_rows, scan.model_usage, today),
            "quota": quota,
            "costs": costs,
            "sources": sources_out,
            "notes": state.notes,
        })

    return {
        "schema": REPORT_SCHEMA,
        "generated_at": iso(now),
        "timezone": timezone_name(),
        "today": today,
        "collector": {
            "version": collector.VERSION,
            "config_path": str(config_path),
            "usage_csv": str(usage_path),
            "log_file": str(log_path),
            "interval_seconds": interval,
            "csv_rows": scan.row_count,
            "installed_version": ".".join(str(part) for part in installed_version) if installed_version else None,
            "capabilities": capabilities,
        },
        "thresholds": {"stale_after_seconds": stale_after,
                       "grok_billing_stale_after_seconds": GROK_BILLING_STALE_SECONDS},
        "service": service,
        "schedule": schedule,
        "collection": collection,
        "providers": providers_out,
    }


def evaluate_ok(attempt: Attempt, state: ProviderState) -> bool:
    """True when the attempt produced at least one successful data row for this provider."""
    for row in attempt.provider_rows(state.definition.id):
        if row.category in ("availability", "collection") or row.status in ("error", "missing", "disabled"):
            continue
        if state.definition.source_for_csv(row.source) is not None:
            return True
    return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Print the ai-usage report (ai-usage/report/v1) as JSON.")
    parser.add_argument("--config", type=collector.expand_path, default=collector.DEFAULT_CONFIG_PATH)
    parser.add_argument("--now", help="evaluation time (ISO-8601); default: current time")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--service", choices=("auto", "skip"), default="auto")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--installed-collector", type=Path,
                        help="installed collector script to read VERSION from (default: the LaunchAgent's)")
    arguments = parser.parse_args(argv)
    try:
        if not 7 <= arguments.days <= 400:
            raise ValueError("--days must be between 7 and 400")
        now = parse_ts(arguments.now) if arguments.now else time.time()
        if now is None:
            raise ValueError(f"--now is not an ISO-8601 time: {arguments.now}")
        config = collector.load_config(arguments.config)
        service = probe_service() if arguments.service == "auto" else None
        script = arguments.installed_collector or installed_collector_script()
        report = build_report(config, config_path=arguments.config, now=now, days=arguments.days,
                              service=service, installed_version=installed_collector_version(script))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ai-usage-report: {error}", file=sys.stderr)
        return 1
    json.dump(report, sys.stdout, indent=2 if arguments.pretty else None, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
