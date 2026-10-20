"""Antigravity: the sanitized status-line callback cache and its event log."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

from .base import CollectContext, IntegrationSpec, ProviderDef, ProviderPlugin, SourceDef
from .support import (
    utc_now,
    iso_utc,
    expand_path,
    atomic_write_bytes,
    exclusive_file_lock,
    find_executable,
    metric_row,
    availability_row,
    stable_scope,
    read_new_json_lines,
    rotation_loss_row,
)


def sanitized_antigravity_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    conversation_identifier = (
        payload.get("conversation_id")
        or payload.get("session_id")
        or payload.get("transcript_path")
    )
    sanitized: dict[str, Any] = {
        "captured_at": iso_utc(utc_now()),
        "session_scope": (
            stable_scope(conversation_identifier) if conversation_identifier else ""
        ),
        "product": payload.get("product"),
        "version": payload.get("version"),
        "plan_tier": payload.get("plan_tier"),
        "agent_state": payload.get("agent_state"),
        "execution_mode": payload.get("execution_mode"),
        "exceeds_200k_tokens": payload.get("exceeds_200k_tokens"),
    }
    model = payload.get("model")
    if isinstance(model, Mapping):
        sanitized["model"] = {
            "id": model.get("id"),
            "display_name": model.get("display_name"),
        }

    context = payload.get("context_window")
    if isinstance(context, Mapping):
        sanitized_context = {
            key: context.get(key)
            for key in (
                "total_input_tokens",
                "total_output_tokens",
                "context_window_size",
                "used_percentage",
                "remaining_percentage",
            )
        }
        current_usage = context.get("current_usage")
        if isinstance(current_usage, Mapping):
            sanitized_context["current_usage"] = {
                key: current_usage.get(key)
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            }
        sanitized["context_window"] = sanitized_context

    quota = payload.get("quota")
    if isinstance(quota, Mapping):
        sanitized_quota: dict[str, Any] = {}
        for bucket, details in quota.items():
            if not isinstance(details, Mapping):
                continue
            sanitized_quota[str(bucket)] = {
                "remaining_fraction": details.get("remaining_fraction"),
                "reset_time": details.get("reset_time"),
                "reset_in_seconds": details.get("reset_in_seconds"),
            }
        sanitized["quota"] = sanitized_quota
    return sanitized


def format_antigravity_status(payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    model = payload.get("model")
    if isinstance(model, Mapping):
        display_name = model.get("display_name") or model.get("id")
        if display_name:
            parts.append(str(display_name))
    context = payload.get("context_window")
    if isinstance(context, Mapping):
        remaining = context.get("remaining_percentage")
        if isinstance(remaining, (int, float)):
            parts.append(f"context {remaining:.0f}% left")
    quota = payload.get("quota")
    if isinstance(quota, Mapping):
        for bucket, details in list(quota.items())[:2]:
            if not isinstance(details, Mapping):
                continue
            fraction = details.get("remaining_fraction")
            if isinstance(fraction, (int, float)):
                parts.append(f"{bucket} {fraction * 100:.0f}% left")
    return " | ".join(parts) if parts else "Antigravity"


def antigravity_event_signature(snapshot: Mapping[str, Any]) -> tuple[Any, ...]:
    context = snapshot.get("context_window")
    if not isinstance(context, Mapping):
        context = {}
    current_usage = context.get("current_usage")
    if not isinstance(current_usage, Mapping):
        current_usage = {}
    model = snapshot.get("model")
    if not isinstance(model, Mapping):
        model = {}
    return (
        snapshot.get("session_scope"),
        model.get("id"),
        context.get("total_input_tokens"),
        context.get("total_output_tokens"),
        current_usage.get("input_tokens"),
        current_usage.get("output_tokens"),
        current_usage.get("cache_creation_input_tokens"),
        current_usage.get("cache_read_input_tokens"),
    )



def record_status_line(settings: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """Store one sanitized status-line payload; return the status text to show."""
    cache_setting = settings.get(
        "cache_file", "~/.ai-usage/cache/antigravity.json"
    )
    events_setting = settings.get(
        "events_file", "~/.ai-usage/cache/antigravity-events.jsonl"
    )
    cache_path = expand_path(str(cache_setting))
    events_path = expand_path(str(events_setting))
    sanitized = sanitized_antigravity_payload(payload)
    content = json.dumps(sanitized, separators=(",", ":")) + "\n"
    lock_path = cache_path.with_name(f"{cache_path.name}.lock")
    with exclusive_file_lock(lock_path):
        previous: Any = None
        if cache_path.is_file():
            try:
                with cache_path.open("r", encoding="utf-8") as handle:
                    previous = json.load(handle)
            except (OSError, json.JSONDecodeError):
                previous = None
        if (
            not isinstance(previous, Mapping)
            or antigravity_event_signature(previous)
            != antigravity_event_signature(sanitized)
        ):
            events_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(events_path, 0o600)
        atomic_write_bytes(cache_path, content.encode("utf-8"), mode=0o600)
    return format_antigravity_status(payload)


def collect_antigravity(
    collected_at: str,
    provider_config_value: Mapping[str, Any],
    include_history: bool,
    state: MutableMapping[str, Any],
) -> list[dict[str, Any]]:
    del include_history
    rows: list[dict[str, Any]] = []
    executable = find_executable(provider_config_value, ["agy"])
    cache_setting = provider_config_value.get(
        "cache_file", "~/.ai-usage/cache/antigravity.json"
    )
    events_setting = provider_config_value.get(
        "events_file", "~/.ai-usage/cache/antigravity-events.jsonl"
    )
    cache_path = expand_path(str(cache_setting))
    events_path = expand_path(str(events_setting))
    cache_available = cache_path.is_file() and os.access(cache_path, os.R_OK)
    events_available = events_path.is_file() and os.access(events_path, os.R_OK)
    detected = executable is not None or cache_available or events_available
    details = []
    if executable is not None:
        details.append(f"binary={executable}")
    if cache_available:
        details.append(f"cache={cache_path}")
    if events_available:
        details.append(f"events={events_path}")
    if not detected:
        # No binary and no status-line cache/events — skip entirely.
        return rows

    rows.append(
        availability_row(
            collected_at,
            "antigravity",
            True,
            "filesystem-discovery",
            "; ".join(details),
        )
    )

    if events_available:
        offsets = state.setdefault("antigravity_offsets", {})
        if not isinstance(offsets, MutableMapping):
            offsets = {}
            state["antigravity_offsets"] = offsets
        event_records, parse_errors, _, rotation_losses = read_new_json_lines(
            events_path, offsets
        )
        loss_row = rotation_loss_row(
            collected_at,
            "antigravity",
            "antigravity-status-line-events",
            rotation_losses,
        )
        if loss_row is not None:
            rows.append(loss_row)
        baselines = state.setdefault("antigravity_session_totals", {})
        if not isinstance(baselines, MutableMapping):
            baselines = {}
            state["antigravity_session_totals"] = baselines
        for event in event_records:
            context = event.get("context_window")
            if not isinstance(context, Mapping):
                continue
            session_scope = str(event.get("session_scope") or "unknown-session")
            model = event.get("model")
            model_name = (
                str(model.get("id") or model.get("display_name") or "")
                if isinstance(model, Mapping)
                else ""
            )
            row_scope = f"{session_scope}:{model_name}" if model_name else session_scope
            raw_baseline = baselines.get(session_scope, {})
            baseline = dict(raw_baseline) if isinstance(raw_baseline, Mapping) else {}
            event_time = str(event.get("captured_at") or collected_at)
            for source_key, metric in (
                ("total_input_tokens", "input_tokens"),
                ("total_output_tokens", "output_tokens"),
            ):
                current = context.get(source_key)
                if not isinstance(current, (int, float)) or isinstance(current, bool):
                    continue
                previous = baseline.get(source_key)
                if isinstance(previous, (int, float)) and not isinstance(previous, bool):
                    delta = current - previous if current >= previous else current
                else:
                    delta = current
                baseline[source_key] = current
                if delta > 0:
                    rows.append(
                        metric_row(
                            event_time,
                            "antigravity",
                            "usage",
                            metric,
                            record_kind="delta",
                            scope=row_scope,
                            value=delta,
                            unit="tokens",
                            source="antigravity-status-line-events",
                        )
                    )
            current_usage = context.get("current_usage")
            if isinstance(current_usage, Mapping):
                for source_key in (
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                ):
                    value = current_usage.get(source_key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        rows.append(
                            metric_row(
                                event_time,
                                "antigravity",
                                "usage",
                                source_key,
                                record_kind="event_total",
                                scope=row_scope,
                                value=value,
                                unit="tokens",
                                source="antigravity-status-line-events",
                            )
                        )
            baselines[session_scope] = baseline
        while len(baselines) > 5000:
            del baselines[next(iter(baselines))]
        if parse_errors:
            rows.append(
                metric_row(
                    collected_at,
                    "antigravity",
                    "collection",
                    "event_parse_errors",
                    record_kind="interval_total",
                    value=parse_errors,
                    unit="records",
                    source="antigravity-status-line-events",
                    status="error",
                    message="Malformed sanitized status events were skipped",
                )
            )

    if not cache_available:
        rows.append(
            metric_row(
                collected_at,
                "antigravity",
                "collection",
                "status_line_cache",
                source="antigravity-status-line",
                status="missing",
                message="No latest status-line snapshot is available; agy was not invoked",
            )
        )
        return rows

    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        rows.append(
            metric_row(
                collected_at,
                "antigravity",
                "collection",
                "status_line_cache",
                source="antigravity-status-line",
                status="error",
                message=str(error),
            )
        )
        return rows
    if not isinstance(snapshot, Mapping):
        return rows

    try:
        age_seconds = max(0, int(time.time() - cache_path.stat().st_mtime))
    except OSError:
        age_seconds = -1
    stale_after = provider_config_value.get("stale_after_seconds", 7200)
    try:
        stale_after = max(60, int(stale_after))
    except (TypeError, ValueError):
        stale_after = 7200
    source_status = "stale" if age_seconds < 0 or age_seconds > stale_after else "ok"
    rows.append(
        metric_row(
            collected_at,
            "antigravity",
            "freshness",
            "source_age_seconds",
            value=age_seconds,
            unit="seconds",
            source="antigravity-status-line",
            status=source_status,
            message=(
                "Snapshot is stale; values were not refreshed"
                if source_status == "stale"
                else ""
            ),
        )
    )

    plan_tier = snapshot.get("plan_tier")
    if plan_tier:
        rows.append(
            metric_row(
                collected_at,
                "antigravity",
                "subscription",
                "plan_tier",
                value=plan_tier,
                source="antigravity-status-line",
                status=source_status,
            )
        )
    model = snapshot.get("model")
    model_scope = ""
    if isinstance(model, Mapping):
        model_scope = str(model.get("id") or model.get("display_name") or "")
    session_scope = str(snapshot.get("session_scope") or "")
    context_scope = (
        f"{session_scope}:{model_scope}"
        if session_scope and model_scope
        else session_scope or model_scope
    )

    context = snapshot.get("context_window")
    if isinstance(context, Mapping):
        context_metrics = {
            "context_window_size": "tokens",
            "used_percentage": "percent",
            "remaining_percentage": "percent",
        }
        for key, unit in context_metrics.items():
            if context.get(key) is not None:
                rows.append(
                    metric_row(
                        collected_at,
                        "antigravity",
                        "context",
                        key,
                        scope=context_scope,
                        value=context[key],
                        unit=unit,
                        source="antigravity-status-line",
                        status=source_status,
                    )
                )
    quota = snapshot.get("quota")
    if isinstance(quota, Mapping):
        for bucket, details in quota.items():
            if not isinstance(details, Mapping):
                continue
            fraction = details.get("remaining_fraction")
            if isinstance(fraction, (int, float)):
                rows.append(
                    metric_row(
                        collected_at,
                        "antigravity",
                        "quota",
                        "remaining_percent",
                        scope=str(bucket),
                        value=fraction * 100,
                        unit="percent",
                        resets_at=str(details.get("reset_time") or ""),
                        source="antigravity-status-line",
                        status=source_status,
                    )
                )
                rows.append(
                    metric_row(
                        collected_at,
                        "antigravity",
                        "quota",
                        "used_percent",
                        scope=str(bucket),
                        value=(1 - fraction) * 100,
                        unit="percent",
                        resets_at=str(details.get("reset_time") or ""),
                        source="antigravity-status-line",
                        status=source_status,
                    )
                )
    return rows


def collect(context: CollectContext) -> list[dict[str, Any]]:
    return collect_antigravity(context.collected_at, context.settings, context.include_history, context.state)


def doctor_extra(settings: Mapping[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    cache_path = expand_path(
        str(
            settings.get(
                "cache_file", "~/.ai-usage/cache/antigravity.json"
            )
        )
    )
    extra["cache_file"] = str(cache_path)
    extra["cache_file_readable"] = (
        cache_path.is_file() and os.access(cache_path, os.R_OK)
    )
    events_path = expand_path(
        str(
            settings.get(
                "events_file",
                "~/.ai-usage/cache/antigravity-events.jsonl",
            )
        )
    )
    extra["events_file"] = str(events_path)
    extra["events_file_readable"] = (
        events_path.is_file() and os.access(events_path, os.R_OK)
    )
    return extra


def desired_status_line(settings: Mapping[str, Any], wrapper_path: Optional[Path]) -> dict[str, Any]:
    del settings
    return {"type": "command", "command": str(wrapper_path)}


PLUGIN = ProviderPlugin(
    id="antigravity",
    default_settings={
        "enabled": True,
        "executable": None,
        "executable_names": ["agy"],
        "cache_file": "~/.ai-usage/cache/antigravity.json",
        "events_file": "~/.ai-usage/cache/antigravity-events.jsonl",
        "settings_file": "~/.gemini/antigravity-cli/settings.json",
        "configure_status_line": True,
        "stale_after_seconds": 7200,
        "monthly_subscription_usd": None,
    },
    executable_names=("agy",),
    collect=collect,
    doctor_extra=doctor_extra,
    path_roles=(("cache_file", "file"), ("events_file", "file"), ("settings_file", "file")),
    integration=IntegrationSpec(
        settings_setting="settings_file",
        settings_default="~/.gemini/antigravity-cli/settings.json",
        field="statusLine",
        enable_setting="configure_status_line",
        desired=desired_status_line,
        display_name="Antigravity",
        configured_label="Antigravity status-line capture",
        preserved_label="Antigravity statusLine",
        wrapper_command="antigravity-statusline",
        wrapper_help="accept one Antigravity status-line payload on stdin",
    ),
    status_line=record_status_line,
    report=ProviderDef(
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
    report_order=2,
)
