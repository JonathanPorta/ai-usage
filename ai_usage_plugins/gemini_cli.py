"""Gemini CLI: its opt-in local telemetry file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

from .base import CollectContext, IntegrationSpec, ProviderDef, ProviderPlugin, SourceDef
from .support import (
    expand_path,
    find_executable,
    metric_row,
    availability_row,
    snake_case,
    scalar_leaves,
    read_new_json_lines,
    rotation_loss_row,
    record_timestamp,
)


def find_scalar_by_leaf(record: Mapping[str, Any], leaf_names: set[str]) -> Any:
    for path, value in scalar_leaves(record):
        leaf = snake_case(path.rsplit(".", 1)[-1])
        if leaf in leaf_names:
            return value
    return None


def gemini_event_name(record: Mapping[str, Any]) -> Optional[str]:
    recognized = {"gemini_cli.api_response"}
    for path, value in scalar_leaves(record):
        if isinstance(value, str) and value in recognized:
            return value
        leaf = snake_case(path.rsplit(".", 1)[-1])
        if leaf in {"event_name", "event", "name", "metric_name"}:
            if isinstance(value, str) and value in recognized:
                return value
    return None


def collect_gemini_cli(
    collected_at: str,
    provider_config_value: Mapping[str, Any],
    include_history: bool,
    state: MutableMapping[str, Any],
) -> list[dict[str, Any]]:
    del include_history
    executable = find_executable(provider_config_value, ["gemini"])
    telemetry_setting = provider_config_value.get(
        "telemetry_file", "~/.ai-usage/cache/gemini-telemetry.log"
    )
    telemetry_path = expand_path(str(telemetry_setting))
    telemetry_available = telemetry_path.is_file() and os.access(
        telemetry_path, os.R_OK
    )
    detected = executable is not None or telemetry_available
    details = []
    if executable is not None:
        details.append(f"binary={executable}")
    if telemetry_available:
        details.append(f"telemetry={telemetry_path}")
    if not detected:
        # No binary and no telemetry file — skip rather than writing missing spam.
        return []

    rows = [
        availability_row(
            collected_at,
            "gemini_cli",
            True,
            "filesystem-discovery",
            "; ".join(details),
        )
    ]
    if not telemetry_available:
        rows.append(
            metric_row(
                collected_at,
                "gemini_cli",
                "collection",
                "telemetry_file",
                source="gemini-telemetry",
                status="missing",
                message="No telemetry file is available; Gemini CLI was not invoked",
            )
        )
        return rows

    offsets = state.setdefault("gemini_offsets", {})
    if not isinstance(offsets, MutableMapping):
        offsets = {}
        state["gemini_offsets"] = offsets
    records, parse_errors, bytes_read, rotation_losses = read_new_json_lines(
        telemetry_path, offsets
    )
    loss_row = rotation_loss_row(
        collected_at,
        "gemini_cli",
        "gemini-telemetry",
        rotation_losses,
    )
    if loss_row is not None:
        rows.append(loss_row)
    recognized_records = 0
    for record in records:
        event_name = gemini_event_name(record)
        if event_name is None:
            continue
        recognized_records += 1
        timestamp = record_timestamp(record, collected_at)
        model = find_scalar_by_leaf(record, {"model", "model_name", "model_id"})
        model_scope = str(model or "")

        token_fields = {
            "input_tokens": {"input_token_count"},
            "output_tokens": {"output_token_count"},
            "cached_content_tokens": {"cached_content_token_count"},
            "thought_tokens": {"thoughts_token_count"},
            "tool_tokens": {"tool_token_count"},
            "total_tokens": {"total_token_count"},
        }
        for metric_name, source_names in token_fields.items():
            value = find_scalar_by_leaf(record, source_names)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            rows.append(
                metric_row(
                    timestamp,
                    "gemini_cli",
                    "usage",
                    metric_name,
                    record_kind="event_total",
                    scope=model_scope,
                    value=value,
                    unit="tokens",
                    source="gemini-telemetry",
                )
            )

    if parse_errors:
        rows.append(
            metric_row(
                collected_at,
                "gemini_cli",
                "collection",
                "telemetry_parse_errors",
                record_kind="interval_total",
                value=parse_errors,
                unit="records",
                source="gemini-telemetry",
                status="unsupported_format",
                message=(
                    "Unparseable telemetry records were skipped; Gemini was "
                    "not run as a fallback"
                ),
            )
        )
    if bytes_read and records and recognized_records == 0:
        rows.append(
            metric_row(
                collected_at,
                "gemini_cli",
                "collection",
                "telemetry_schema",
                source="gemini-telemetry",
                status="unsupported_format",
                message="New JSON telemetry did not match documented event names",
            )
        )
    return rows


def collect(context: CollectContext) -> list[dict[str, Any]]:
    return collect_gemini_cli(context.collected_at, context.settings, context.include_history, context.state)


def doctor_extra(settings: Mapping[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    telemetry_path = expand_path(
        str(
            settings.get(
                "telemetry_file",
                "~/.ai-usage/cache/gemini-telemetry.log",
            )
        )
    )
    extra["telemetry_file"] = str(telemetry_path)
    extra["telemetry_file_readable"] = (
        telemetry_path.is_file() and os.access(telemetry_path, os.R_OK)
    )
    return extra


def desired_gemini_telemetry(settings: Mapping[str, Any]) -> dict[str, Any]:
    telemetry_path = expand_path(
        str(
            settings.get(
                "telemetry_file", "~/.ai-usage/cache/gemini-telemetry.log"
            )
        )
    )
    return {
        "enabled": True,
        "target": "local",
        "outfile": str(telemetry_path),
        "logPrompts": False,
        "traces": False,
    }


def telemetry_directories(value: Mapping[str, Any]) -> list[Path]:
    return [expand_path(str(value["outfile"])).parent]


PLUGIN = ProviderPlugin(
    id="gemini_cli",
    default_settings={
        "enabled": True,
        "executable": None,
        "executable_names": ["gemini"],
        "telemetry_file": "~/.ai-usage/cache/gemini-telemetry.log",
        "settings_file": "~/.gemini/settings.json",
        "configure_telemetry": False,
        "monthly_subscription_usd": None,
    },
    executable_names=("gemini",),
    collect=collect,
    doctor_extra=doctor_extra,
    path_roles=(("telemetry_file", "file"), ("settings_file", "file")),
    integration=IntegrationSpec(
        settings_setting="settings_file",
        settings_default="~/.gemini/settings.json",
        field="telemetry",
        enable_setting="configure_telemetry",
        desired=lambda settings, wrapper_path: desired_gemini_telemetry(settings),
        display_name="Gemini CLI",
        configured_label="local Gemini CLI telemetry",
        preserved_label="Gemini CLI telemetry",
        owned_directories=telemetry_directories,
    ),
    report=ProviderDef(
        "gemini_cli", "Gemini CLI", "Gemini CLI", mode="event", split="input_output",
        record_kind="event_total", quota="unsupported",
        reported_note="Gemini CLI does not report a usage cost.",
        sources=[
            SourceDef("telemetry", "usage", "Local telemetry file", "Gemini CLI telemetry (opt-in)",
                      ["gemini-telemetry"], needs_rows=False,
                      error_metrics=["telemetry_file", "unexpected_error"]),
        ],
    ),
    report_order=4,
)
