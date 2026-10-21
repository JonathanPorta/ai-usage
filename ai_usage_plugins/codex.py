"""Codex: account usage and rate limits over `codex app-server` (metadata only)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Mapping, MutableMapping, Optional

from .base import CollectContext, ProviderDef, ProviderPlugin, SourceDef
from .support import (
    timestamp_to_iso,
    discovery_path,
    is_executable_file,
    find_executable,
    metric_row,
    availability_row,
    state_value_changed,
    response_error,
    start_json_line_reader,
    next_json_message,
    terminate_provider_process,
)


def query_codex_app_server(
    executable: Path, timeout_seconds: int, client_version: str
) -> tuple[Any, Any]:
    """Fetch Codex usage and rate limits without creating a model turn."""

    if not is_executable_file(executable):
        raise FileNotFoundError(f"Codex executable is unavailable: {executable}")

    timeout_seconds = max(5, min(int(timeout_seconds), 120))
    environment = os.environ.copy()
    environment["PATH"] = discovery_path()

    stderr_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    process: Optional[subprocess.Popen[str]] = None
    process_group: Optional[int] = None
    try:
        process = subprocess.Popen(
            [str(executable), "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            bufsize=1,
            env=environment,
            start_new_session=os.name == "posix",
        )
        if os.name == "posix":
            # start_new_session makes the child a session leader, so its PID is
            # also its process-group ID.  macOS rejects getpgid() across that
            # new session with EPERM even though this is our direct child.
            process_group = process.pid
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("failed to open Codex app-server pipes")

        def send(payload: Mapping[str, Any]) -> None:
            if process is None or process.stdin is None:
                raise RuntimeError("Codex app-server stdin is unavailable")
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()

        send(
            {
                "method": "initialize",
                "id": 100,
                "params": {
                    "clientInfo": {
                        "name": "ai_usage_collector",
                        "title": "AI Usage Collector",
                        "version": client_version,
                    }
                },
            }
        )

        responses: dict[int, Any] = {}
        initialized = False
        deadline = time.monotonic() + timeout_seconds
        message_queue = start_json_line_reader(process.stdout)

        while time.monotonic() < deadline:
            if 101 in responses and 102 in responses:
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

            if response_id == 100 and not initialized:
                if message.get("error") is not None:
                    break
                send({"method": "initialized", "params": {}})
                send({"method": "account/usage/read", "id": 101})
                send({"method": "account/rateLimits/read", "id": 102})
                initialized = True

        if 100 not in responses:
            raise RuntimeError("Codex app-server did not initialize before timeout")
        if responses[100].get("error") is not None:
            raise RuntimeError(response_error(responses[100], "Codex initialization failed"))
        return responses.get(101), responses.get(102)
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


def collect_codex(
    collected_at: str,
    provider_config: Mapping[str, Any],
    include_history: bool,
    state: MutableMapping[str, Any],
    client_version: str,
) -> list[dict[str, Any]]:
    del include_history
    rows: list[dict[str, Any]] = []
    executable = find_executable(provider_config, ["codex"])
    if executable is None:
        # No binary and no alternate local source — do not emit noise rows.
        return rows

    rows.append(
        availability_row(
            collected_at,
            "codex",
            True,
            "filesystem-discovery",
            str(executable),
        )
    )

    timeout = provider_config.get("timeout_seconds", 30)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30

    try:
        usage_envelope, limits_envelope = query_codex_app_server(executable, timeout, client_version)
    except Exception as error:  # Provider failures must not stop other collectors.
        rows.append(
            metric_row(
                collected_at,
                "codex",
                "collection",
                "metadata_query",
                source="codex-app-server",
                status="error",
                message=str(error),
            )
        )
        return rows

    usage_result = (
        usage_envelope.get("result")
        if isinstance(usage_envelope, Mapping)
        else None
    )
    if isinstance(usage_result, Mapping):
        summary = usage_result.get("summary")
        if isinstance(summary, Mapping):
            summary_metrics = {
                "lifetime_tokens": ("lifetimeTokens", "tokens"),
                "peak_daily_tokens": ("peakDailyTokens", "tokens"),
                "longest_running_turn_seconds": (
                    "longestRunningTurnSec",
                    "seconds",
                ),
                "current_streak_days": ("currentStreakDays", "days"),
                "longest_streak_days": ("longestStreakDays", "days"),
            }
            for metric, (source_key, unit) in summary_metrics.items():
                value = summary.get(source_key)
                if value is not None:
                    rows.append(
                        metric_row(
                            collected_at,
                            "codex",
                            "usage",
                            metric,
                            value=value,
                            unit=unit,
                            source="codex-account-usage",
                        )
                    )

        buckets = usage_result.get("dailyUsageBuckets")
        if isinstance(buckets, list):
            for bucket in buckets:
                if not isinstance(bucket, Mapping):
                    continue
                date = bucket.get("startDate")
                tokens = bucket.get("tokens")
                if date is None or tokens is None:
                    continue
                if not state_value_changed(
                    state,
                    "codex_daily_tokens",
                    str(date),
                    tokens,
                    max_entries=800,
                ):
                    continue
                rows.append(
                    metric_row(
                        collected_at,
                        "codex",
                            "usage",
                            "daily_tokens",
                            record_kind="period_total",
                            scope=str(date),
                            period_start=str(date),
                        value=tokens,
                        unit="tokens",
                        source="codex-account-usage",
                    )
                )
    else:
        rows.append(
            metric_row(
                collected_at,
                "codex",
                "collection",
                "account_usage_query",
                source="codex-app-server",
                status="error",
                message=response_error(
                    usage_envelope, "account/usage/read returned no result"
                ),
            )
        )

    limits_result = (
        limits_envelope.get("result")
        if isinstance(limits_envelope, Mapping)
        else None
    )
    if isinstance(limits_result, Mapping):
        limits_by_id = limits_result.get("rateLimitsByLimitId")
        if isinstance(limits_by_id, Mapping) and limits_by_id:
            rate_limit_groups = list(limits_by_id.items())
        else:
            single_group = limits_result.get("rateLimits")
            limit_id = (
                single_group.get("limitId", "default")
                if isinstance(single_group, Mapping)
                else "default"
            )
            rate_limit_groups = [(str(limit_id), single_group)]

        for limit_id, group in rate_limit_groups:
            if not isinstance(group, Mapping):
                continue
            for window_name in ("primary", "secondary"):
                window = group.get(window_name)
                if not isinstance(window, Mapping):
                    continue
                used_percent = window.get("usedPercent")
                minutes = window.get("windowDurationMins")
                window_seconds: Any = ""
                if isinstance(minutes, (int, float)):
                    window_seconds = int(minutes * 60)
                if used_percent is not None:
                    rows.append(
                        metric_row(
                            collected_at,
                            "codex",
                            "quota",
                            "used_percent",
                            scope=f"{limit_id}:{window_name}",
                            value=used_percent,
                            unit="percent",
                            window_seconds=window_seconds,
                            resets_at=timestamp_to_iso(window.get("resetsAt")),
                            source="codex-rate-limits",
                        )
                    )

            plan_type = group.get("planType")
            if plan_type:
                rows.append(
                    metric_row(
                        collected_at,
                        "codex",
                        "subscription",
                        "plan_type",
                        scope=str(limit_id),
                        value=str(plan_type),
                        source="codex-rate-limits",
                    )
                )
            reached_type = group.get("rateLimitReachedType")
            if reached_type:
                rows.append(
                    metric_row(
                        collected_at,
                        "codex",
                        "quota",
                        "reached_type",
                        scope=str(limit_id),
                        value=str(reached_type),
                        source="codex-rate-limits",
                    )
                )

        reset_credits = limits_result.get("rateLimitResetCredits")
        if isinstance(reset_credits, Mapping):
            available_count = reset_credits.get("availableCount")
            if available_count is not None:
                rows.append(
                    metric_row(
                        collected_at,
                        "codex",
                        "quota",
                        "rate_limit_reset_credits",
                        value=available_count,
                        unit="credits",
                        source="codex-rate-limits",
                    )
                )
    else:
        rows.append(
            metric_row(
                collected_at,
                "codex",
                "collection",
                "rate_limits_query",
                source="codex-app-server",
                status="error",
                message=response_error(
                    limits_envelope, "account/rateLimits/read returned no result"
                ),
            )
        )
    return rows


def collect(context: CollectContext) -> list[dict[str, Any]]:
    return collect_codex(
        context.collected_at, context.settings, context.include_history, context.state, context.version
    )


PLUGIN = ProviderPlugin(
    id="codex",
    default_settings={
        "enabled": True,
        "executable": None,
        "executable_names": ["codex"],
        "timeout_seconds": 30,
        "monthly_subscription_usd": None,
    },
    executable_names=("codex",),
    collect=collect,
    report=ProviderDef(
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
    report_order=0,
)
