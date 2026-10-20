"""Grok Build: local session logs, and billing from its log or a read-only ACP lookup."""

from __future__ import annotations

import datetime as datetime_module
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Mapping, MutableMapping, Optional

from .base import CollectContext, ProviderDef, ProviderPlugin, SourceDef
from .support import (
    expand_path,
    discovery_path,
    is_executable_file,
    find_executable,
    metric_row,
    availability_row,
    stable_scope,
    read_new_json_lines,
    rotation_loss_row,
    record_timestamp,
    response_error,
    start_json_line_reader,
    next_json_message,
    terminate_provider_process,
    parse_iso_timestamp,
)


def query_grok_billing(executable: Path, timeout_seconds: int, client_version: str) -> Any:
    if not is_executable_file(executable):
        raise FileNotFoundError(f"Grok executable is unavailable: {executable}")
    timeout_seconds = max(5, min(int(timeout_seconds), 120))
    environment = os.environ.copy()
    environment["PATH"] = discovery_path()
    environment["GROK_DISABLE_AUTOUPDATER"] = "1"
    stderr_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    process: Optional[subprocess.Popen[str]] = None
    process_group: Optional[int] = None
    try:
        process = subprocess.Popen(
            [str(executable), "agent", "--no-leader", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            bufsize=1,
            cwd=str(Path.home()),
            env=environment,
            start_new_session=os.name == "posix",
        )
        if os.name == "posix":
            # start_new_session guarantees this without a cross-session
            # getpgid() call, which macOS rejects with EPERM.
            process_group = process.pid
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("failed to open Grok ACP pipes")

        def send(payload: Mapping[str, Any]) -> None:
            if process is None or process.stdin is None:
                raise RuntimeError("Grok ACP stdin is unavailable")
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "_meta": {
                        "startupHints": {
                            "nonInteractive": True,
                            "skipGitStatus": True,
                            "skipProjectLayout": True,
                        },
                        "clientType": "ai-usage-collector",
                        "clientVersion": client_version,
                    },
                },
            }
        )
        responses: dict[int, Any] = {}
        billing_sent = False
        deadline = time.monotonic() + timeout_seconds
        message_queue = start_json_line_reader(process.stdout)
        while time.monotonic() < deadline:
            if 2 in responses:
                break
            message = next_json_message(message_queue, deadline)
            if message == {}:
                if process.poll() is not None:
                    break
                continue
            if message is None:
                break
            response_id = message.get("id")
            if isinstance(response_id, int):
                responses[response_id] = message
            if response_id == 1 and not billing_sent:
                if message.get("error") is not None:
                    break
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        # ACP custom methods are wire-named with a leading underscore.
                        # The agent extension handler matches "x.ai/billing" after that.
                        "method": "_x.ai/billing",
                        "params": {},
                    }
                )
                billing_sent = True
        if 1 not in responses:
            raise RuntimeError("Grok ACP did not initialize before timeout")
        if responses[1].get("error") is not None:
            raise RuntimeError(response_error(responses[1], "Grok ACP initialization failed"))
        billing = responses.get(2)
        if not isinstance(billing, Mapping) or billing.get("result") is None:
            raise RuntimeError(response_error(billing, "Grok billing returned no result"))
        return billing["result"]
    finally:
        if process is not None:
            terminate_provider_process(process, process_group)
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.stdout is not None:
                process.stdout.close()
        stderr_file.close()


def grok_usage_rows(
    collected_at: str,
    record: Mapping[str, Any],
) -> list[dict[str, Any]]:
    params = record.get("params")
    if not isinstance(params, Mapping):
        return []
    update = params.get("update")
    if not isinstance(update, Mapping) or update.get("sessionUpdate") != "turn_completed":
        return []
    usage = update.get("usage")
    if not isinstance(usage, Mapping):
        return []

    meta = params.get("_meta")
    event_id = meta.get("eventId") if isinstance(meta, Mapping) else None
    prompt_id = update.get("prompt_id") or update.get("promptId")
    scope = stable_scope(event_id or f"{params.get('sessionId')}:{prompt_id}")
    event_time = record_timestamp(record, collected_at)
    metric_map = {
        "inputTokens": ("input_tokens", "tokens"),
        "outputTokens": ("output_tokens", "tokens"),
        "totalTokens": ("total_tokens", "tokens"),
        "cachedReadTokens": ("cached_read_tokens", "tokens"),
        "cacheCreationTokens": ("cache_creation_tokens", "tokens"),
        "reasoningTokens": ("reasoning_tokens", "tokens"),
        "modelCalls": ("model_calls", "calls"),
        "apiDurationMs": ("api_duration", "milliseconds"),
        "numTurns": ("turns", "turns"),
    }
    rows: list[dict[str, Any]] = []
    incomplete = usage.get("usageIsIncomplete") is True
    partial_cost = usage.get("costIsPartial") is True
    usage_status = "incomplete" if incomplete else "ok"
    for source_key, (metric, unit) in metric_map.items():
        if usage.get(source_key) is not None:
            rows.append(
                metric_row(
                    event_time,
                    "grok",
                    "usage",
                    metric,
                    record_kind="event_total",
                    scope=scope,
                    value=usage[source_key],
                    unit=unit,
                    source="grok-session-log",
                    status=usage_status,
                )
            )
    cost_ticks = usage.get("costUsdTicks")
    if isinstance(cost_ticks, (int, float)) and not incomplete and not partial_cost:
        rows.append(
            metric_row(
                event_time,
                "grok",
                "cost",
                "api_cost",
                record_kind="event_total",
                scope=scope,
                value=cost_ticks / 10_000_000_000,
                unit="USD",
                source="grok-session-log",
            )
        )
    elif cost_ticks is not None:
        rows.append(
            metric_row(
                event_time,
                "grok",
                "cost",
                "api_cost",
                record_kind="event_total",
                scope=scope,
                source="grok-session-log",
                status="incomplete",
                message="Cost was partial or usage was incomplete; value was not recorded as zero",
            )
        )
    model_usage = usage.get("modelUsage")
    if isinstance(model_usage, Mapping):
        rows.append(
            metric_row(
                event_time,
                "grok",
                "usage",
                "model_usage",
                record_kind="event_total",
                scope=scope,
                value=dict(model_usage),
                unit="json",
                source="grok-session-log",
                status=usage_status,
            )
        )
    return rows


def grok_billing_rows(
    collected_at: str,
    billing: Any,
    *,
    source: str = "grok-x.ai-billing-acp",
    status: str = "experimental",
) -> list[dict[str, Any]]:
    if not isinstance(billing, Mapping):
        return []
    config = billing.get("config")
    if not isinstance(config, Mapping):
        return [
            metric_row(
                collected_at,
                "grok",
                "collection",
                "billing_schema",
                source=source,
                status="unsupported_format",
                message="Billing response did not contain a config object",
            )
        ]

    current_period = config.get("currentPeriod")
    if not isinstance(current_period, Mapping):
        current_period = {}
    period_start = str(
        current_period.get("start") or config.get("billingPeriodStart") or ""
    )
    period_end = str(
        current_period.get("end") or config.get("billingPeriodEnd") or ""
    )
    scope = str(current_period.get("type") or "included_credits")
    rows: list[dict[str, Any]] = []

    used_percent = config.get("creditUsagePercent")
    if not isinstance(used_percent, (int, float)) or isinstance(used_percent, bool):
        monthly_limit = config.get("monthlyLimit")
        used = config.get("used")
        limit_value = (
            monthly_limit.get("val") if isinstance(monthly_limit, Mapping) else None
        )
        used_value = used.get("val") if isinstance(used, Mapping) else None
        if (
            isinstance(limit_value, (int, float))
            and not isinstance(limit_value, bool)
            and limit_value > 0
            and isinstance(used_value, (int, float))
            and not isinstance(used_value, bool)
        ):
            used_percent = used_value / limit_value * 100
    if isinstance(used_percent, (int, float)) and not isinstance(used_percent, bool):
        normalized_percent = max(0, min(100, float(used_percent)))
        rows.append(
            metric_row(
                collected_at,
                "grok",
                "quota",
                "used_percent",
                scope=scope,
                period_start=period_start,
                period_end=period_end,
                value=normalized_percent,
                unit="percent",
                resets_at=period_end,
                source=source,
                status=status,
                message="Weighted included-credit usage",
            )
        )
        rows.append(
            metric_row(
                collected_at,
                "grok",
                "quota",
                "remaining_percent",
                scope=scope,
                period_start=period_start,
                period_end=period_end,
                value=100 - normalized_percent,
                unit="percent",
                resets_at=period_end,
                source=source,
                status=status,
                message="Derived as 100 minus weighted included-credit usage",
            )
        )

    cent_fields = {
        "monthlyLimit": "included_credit_limit",
        "used": "included_credits_used",
        "onDemandCap": "on_demand_cap",
        "onDemandUsed": "on_demand_used",
        "prepaidBalance": "prepaid_balance",
    }
    for source_key, metric in cent_fields.items():
        cent = config.get(source_key)
        if not isinstance(cent, Mapping):
            continue
        value = cent.get("val", 0)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        rows.append(
            metric_row(
                collected_at,
                "grok",
                "billing",
                metric,
                scope=scope,
                period_start=period_start,
                period_end=period_end,
                value=value,
                unit="USD_cents",
                source=source,
                status=status,
                message="Consumer coding-credit value; not the recurring subscription fee",
            )
        )

    for source_key, metric in (
        ("isUnifiedBillingUser", "unified_billing_user"),
        ("historyLen", "history_period_count"),
    ):
        value = config.get(source_key)
        if isinstance(value, (bool, int, float)):
            rows.append(
                metric_row(
                    collected_at,
                    "grok",
                    "billing",
                    metric,
                    value=value,
                    unit="boolean" if isinstance(value, bool) else "periods",
                    source=source,
                    status=status,
                )
            )

    for source_key, metric in (
        ("onDemandEnabled", "on_demand_enabled"),
        ("subscriptionTier", "subscription_tier"),
    ):
        value = billing.get(source_key)
        if isinstance(value, (str, bool, int, float)):
            rows.append(
                metric_row(
                    collected_at,
                    "grok",
                    "subscription",
                    metric,
                    value=value,
                    unit="boolean" if isinstance(value, bool) else "",
                    source=source,
                    status=status,
                )
            )

    history_entries = config.get("history")
    entries = list(history_entries) if isinstance(history_entries, list) else []
    latest_history = config.get("latestHistory")
    if isinstance(latest_history, Mapping):
        entries.append(latest_history)
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        entry_period = entry.get("period")
        cycle = entry.get("billingCycle")
        history_scope = "history"
        history_start = ""
        history_end = ""
        if isinstance(entry_period, Mapping):
            history_start = str(entry_period.get("start") or "")
            history_end = str(entry_period.get("end") or "")
            period_type = str(entry_period.get("type") or "history")
            history_scope = (
                f"{period_type}:{history_start}" if history_start else period_type
            )
        elif isinstance(cycle, Mapping):
            year = cycle.get("year")
            month = cycle.get("month")
            if isinstance(year, int) and isinstance(month, int) and 1 <= month <= 12:
                start_date = datetime_module.date(year, month, 1)
                end_date = (
                    datetime_module.date(year + 1, 1, 1)
                    if month == 12
                    else datetime_module.date(year, month + 1, 1)
                )
                history_start = start_date.isoformat()
                history_end = end_date.isoformat()
                history_scope = history_start
        for source_key, metric in (
            ("includedUsed", "included_credits_used"),
            ("onDemandUsed", "on_demand_used"),
            ("totalUsed", "total_credits_used"),
        ):
            cent = entry.get(source_key)
            if not isinstance(cent, Mapping):
                continue
            value = cent.get("val", 0)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            rows.append(
                metric_row(
                    collected_at,
                    "grok",
                    "billing",
                    metric,
                    record_kind="period_total",
                    scope=history_scope,
                    period_start=history_start,
                    period_end=history_end,
                    value=value,
                    unit="USD_cents",
                    source=source,
                    status=status,
                )
            )
    return rows


def read_recent_grok_billing_cache(
    path: Path, max_age_seconds: int, tail_bytes: int
) -> tuple[Optional[dict[str, Any]], Optional[int]]:
    """Read Grok's latest local billing snapshot without starting Grok."""

    if not path.is_file() or not os.access(path, os.R_OK):
        return None, None
    newest: Optional[Mapping[str, Any]] = None
    try:
        file_size = path.stat().st_size
        start = max(0, file_size - max(65536, tail_bytes))
        with path.open("rb") as handle:
            handle.seek(start)
            if start:
                handle.readline()
            for raw_line in handle:
                if b"billing: fetched credits config" not in raw_line:
                    continue
                try:
                    candidate = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(candidate, Mapping)
                    and candidate.get("msg") == "billing: fetched credits config"
                ):
                    newest = candidate
    except OSError:
        return None, None
    if newest is None:
        return None, None

    timestamp = parse_iso_timestamp(newest.get("ts"))
    if timestamp is None:
        return None, None
    age_seconds = max(0, int(time.time() - timestamp))
    if age_seconds > max_age_seconds:
        return None, age_seconds

    context = newest.get("ctx")
    if not isinstance(context, Mapping):
        return None, age_seconds
    config = context.get("config")
    if not isinstance(config, Mapping):
        return None, age_seconds
    sanitized: dict[str, Any] = {"config": dict(config)}
    for key in ("onDemandEnabled", "subscriptionTier"):
        value = context.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[key] = value
    return sanitized, age_seconds


def collect_grok(
    collected_at: str,
    provider_config_value: Mapping[str, Any],
    include_history: bool,
    state: MutableMapping[str, Any],
    client_version: str,
) -> list[dict[str, Any]]:
    del include_history
    executable = find_executable(provider_config_value, ["grok"])
    grok_home = expand_path(str(provider_config_value.get("grok_home", "~/.grok")))
    auth_path = expand_path(
        str(provider_config_value.get("auth_file", "~/.grok/auth.json"))
    )
    billing_cache_path = expand_path(
        str(
            provider_config_value.get(
                "billing_cache_file", "~/.grok/logs/unified.jsonl"
            )
        )
    )
    session_files = [
        path
        for path in grok_home.glob("sessions/**/updates.jsonl")
        if "subagents" not in path.parts and path.is_file()
    ]
    billing_cache_available = billing_cache_path.is_file() and os.access(
        billing_cache_path, os.R_OK
    )
    detected = executable is not None or bool(session_files) or billing_cache_available
    details = []
    if executable is not None:
        details.append(f"binary={executable}")
    if session_files:
        details.append(f"session_logs={len(session_files)}")
    if billing_cache_available:
        details.append(f"billing_cache={billing_cache_path}")
    if not detected:
        # No binary, session logs, or billing cache — skip entirely.
        return []

    rows = [
        availability_row(
            collected_at,
            "grok",
            True,
            "filesystem-discovery",
            "; ".join(details),
        )
    ]

    offsets = state.setdefault("grok_offsets", {})
    if not isinstance(offsets, MutableMapping):
        offsets = {}
        state["grok_offsets"] = offsets
    recent_ids_raw = state.get("grok_recent_event_ids", [])
    recent_ids_list = recent_ids_raw if isinstance(recent_ids_raw, list) else []
    recent_ids = set(recent_ids_list)
    new_ids: list[str] = []
    parse_errors = 0
    rotation_losses: list[str] = []
    raw_session_budget = provider_config_value.get(
        "max_session_bytes_per_poll", 25 * 1024 * 1024
    )
    try:
        remaining_session_bytes = max(1024 * 1024, int(raw_session_budget))
    except (TypeError, ValueError):
        remaining_session_bytes = 25 * 1024 * 1024
    deferred_files = 0
    for index, session_file in enumerate(sorted(session_files)):
        if remaining_session_bytes <= 0:
            deferred_files = len(session_files) - index
            break
        records, errors, bytes_read, file_rotation_losses = read_new_json_lines(
            session_file,
            offsets,
            max_bytes=remaining_session_bytes,
        )
        remaining_session_bytes -= bytes_read
        parse_errors += errors
        rotation_losses.extend(file_rotation_losses)
        for record in records:
            if record.get("method") != "_x.ai/session/update":
                continue
            params = record.get("params")
            meta = params.get("_meta") if isinstance(params, Mapping) else None
            event_id = meta.get("eventId") if isinstance(meta, Mapping) else None
            event_key = (
                str(event_id)
                if event_id
                else stable_scope(json.dumps(record, sort_keys=True))
            )
            if event_key in recent_ids:
                continue
            recent_ids.add(event_key)
            new_ids.append(event_key)
            rows.extend(grok_usage_rows(collected_at, record))
    state["grok_recent_event_ids"] = (recent_ids_list + new_ids)[-5000:]
    loss_row = rotation_loss_row(
        collected_at,
        "grok",
        "grok-session-log",
        rotation_losses,
    )
    if loss_row is not None:
        rows.append(loss_row)
    if parse_errors:
        rows.append(
            metric_row(
                collected_at,
                "grok",
                "collection",
                "session_log_parse_errors",
                record_kind="interval_total",
                value=parse_errors,
                unit="records",
                source="grok-session-log",
                status="error",
                message="Malformed records were skipped",
            )
        )
    if deferred_files:
        rows.append(
            metric_row(
                collected_at,
                "grok",
                "collection",
                "session_files_deferred",
                record_kind="interval_total",
                value=deferred_files,
                unit="files",
                source="grok-session-log",
                status="partial",
                message="Per-poll local scan budget reached; remaining files will be read later",
            )
        )

    billing_enabled = provider_config_value.get("collect_billing_via_acp", True) is True
    auth_available = auth_path.is_file() and os.access(auth_path, os.R_OK)
    raw_cache_age = provider_config_value.get(
        "billing_cache_max_age_seconds", 7200
    )
    try:
        cache_max_age = max(60, int(raw_cache_age))
    except (TypeError, ValueError):
        cache_max_age = 7200
    raw_tail_bytes = provider_config_value.get(
        "billing_cache_tail_bytes", 2 * 1024 * 1024
    )
    try:
        tail_bytes = max(65536, int(raw_tail_bytes))
    except (TypeError, ValueError):
        tail_bytes = 2 * 1024 * 1024
    cached_billing, cache_age = read_recent_grok_billing_cache(
        billing_cache_path, cache_max_age, tail_bytes
    )
    if cached_billing is not None:
        rows.append(
            metric_row(
                collected_at,
                "grok",
                "freshness",
                "billing_source_age_seconds",
                value=cache_age,
                unit="seconds",
                source="grok-unified-log",
            )
        )
        rows.extend(
            grok_billing_rows(
                collected_at,
                cached_billing,
                source="grok-unified-log",
                status="ok",
            )
        )
    elif billing_enabled and executable is not None and auth_available:
        timeout = provider_config_value.get("timeout_seconds", 30)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = 30
        try:
            billing = query_grok_billing(executable, timeout, client_version)
            rows.extend(grok_billing_rows(collected_at, billing))
        except Exception as error:
            rows.append(
                metric_row(
                    collected_at,
                    "grok",
                    "collection",
                    "billing_query",
                    source="grok-x.ai-billing-acp",
                    status="error",
                    message=str(error),
                )
            )
    elif billing_enabled:
        reason = (
            "Grok binary is missing; billing command was not run"
            if executable is None
            else "Grok OAuth cache is missing; billing command was not run"
        )
        rows.append(
            metric_row(
                collected_at,
                "grok",
                "collection",
                "billing_query",
                source="grok-x.ai-billing-acp",
                status="missing",
                message=reason,
            )
        )
    return rows


def collect(context: CollectContext) -> list[dict[str, Any]]:
    return collect_grok(
        context.collected_at, context.settings, context.include_history, context.state, context.version
    )


def doctor_extra(settings: Mapping[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    grok_home = expand_path(str(settings.get("grok_home", "~/.grok")))
    session_files = [
        path
        for path in grok_home.glob("sessions/**/updates.jsonl")
        if "subagents" not in path.parts and path.is_file()
    ]
    billing_cache = expand_path(
        str(
            settings.get(
                "billing_cache_file", "~/.grok/logs/unified.jsonl"
            )
        )
    )
    auth_file = expand_path(
        str(settings.get("auth_file", "~/.grok/auth.json"))
    )
    extra["session_log_count"] = len(session_files)
    extra["billing_cache_file"] = str(billing_cache)
    extra["billing_cache_readable"] = (
        billing_cache.is_file() and os.access(billing_cache, os.R_OK)
    )
    extra["auth_file_present"] = auth_file.is_file()
    return extra


PLUGIN = ProviderPlugin(
    id="grok",
    default_settings={
        "enabled": True,
        "executable": None,
        "executable_names": ["grok"],
        "grok_home": "~/.grok",
        "auth_file": "~/.grok/auth.json",
        "billing_cache_file": "~/.grok/logs/unified.jsonl",
        "billing_cache_max_age_seconds": 7200,
        "billing_cache_tail_bytes": 2097152,
        "max_session_bytes_per_poll": 26214400,
        "collect_billing_via_acp": True,
        "timeout_seconds": 30,
        "monthly_subscription_usd": None,
    },
    executable_names=("grok",),
    collect=collect,
    doctor_extra=doctor_extra,
    path_roles=(("grok_home", "directory"), ("auth_file", "file"), ("billing_cache_file", "file")),
    report=ProviderDef(
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
    report_order=3,
)
