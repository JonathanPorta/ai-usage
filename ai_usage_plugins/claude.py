"""Claude Code: local session transcripts, the stats cache, and plan limits from /usage."""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Iterable, Mapping, MutableMapping, Optional, Union

from .base import CollectContext, ProviderDef, ProviderPlugin, SourceDef
from .support import (
    timestamp_to_iso,
    expand_path,
    discovery_path,
    find_executable,
    metric_row,
    availability_row,
    stable_scope,
    read_new_json_lines,
    find_file_by_inode,
    encode_jsonl_partial,
    decode_jsonl_partial,
    read_json_lines_at,
    rotation_loss_row,
    record_timestamp,
    state_value_changed,
    terminate_provider_process,
    parse_iso_timestamp,
)


def claude_session_files(projects_dir: Path) -> list[Path]:
    if not projects_dir.is_dir():
        return []
    files = [
        path
        for path in projects_dir.rglob("*.jsonl")
        if path.is_file() and not path.is_symlink()
    ]
    # String paths so `session.jsonl` precedes `session/subagents/...`
    # (Path part-tuples would put the directory first).
    return sorted(files, key=lambda path: str(path))


CLAUDE_USAGE_METRIC_KEYS = (
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_read_input_tokens", "cache_read_input_tokens"),
    ("cache_creation_input_tokens", "cache_creation_input_tokens"),
)
CLAUDE_RECENT_MESSAGE_ID_CAP = 20000


def claude_message_id(record: Mapping[str, Any]) -> str:
    message = record.get("message")
    if isinstance(message, Mapping) and message.get("id"):
        return str(message["id"])
    if record.get("uuid"):
        return str(record["uuid"])
    return ""


def claude_numeric_usage_value(value: Any) -> Optional[Union[int, float]]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return None


def claude_usage_metrics(record: Mapping[str, Any]) -> dict[str, Union[int, float]]:
    if record.get("type") != "assistant":
        return {}
    message = record.get("message")
    if not isinstance(message, Mapping):
        return {}
    usage = message.get("usage")
    if not isinstance(usage, Mapping):
        return {}
    model = str(message.get("model") or "")
    if not model or model == "<synthetic>":
        return {}
    metrics: dict[str, Union[int, float]] = {}
    for source_key, metric in CLAUDE_USAGE_METRIC_KEYS:
        numeric = claude_numeric_usage_value(usage.get(source_key))
        if numeric is None:
            continue
        metrics[metric] = numeric
    return metrics


def merge_claude_metrics(
    left: Mapping[str, Union[int, float]],
    right: Mapping[str, Union[int, float]],
) -> dict[str, Union[int, float]]:
    merged = dict(left)
    for key, value in right.items():
        previous = merged.get(key)
        if previous is None or value > previous:
            merged[key] = value
    return merged


def claude_metric_deltas(
    previous: Mapping[str, Union[int, float]],
    current: Mapping[str, Union[int, float]],
) -> dict[str, Union[int, float]]:
    deltas: dict[str, Union[int, float]] = {}
    for key, value in current.items():
        prior = previous.get(key, 0)
        delta = value - prior
        if delta > 0:
            deltas[key] = delta
    return deltas


def claude_accounted_metrics(
    raw: Mapping[str, Any],
) -> dict[str, Union[int, float]]:
    metrics: dict[str, Union[int, float]] = {}
    for key, value in raw.items():
        numeric = claude_numeric_usage_value(value)
        if numeric is None:
            continue
        metrics[str(key)] = numeric
    return metrics


def claude_usage_rows_from_metrics(
    record: Mapping[str, Any],
    fallback_collected_at: str,
    metrics: Mapping[str, Union[int, float]],
) -> list[dict[str, Any]]:
    message = record.get("message")
    if not isinstance(message, Mapping):
        return []
    model = str(message.get("model") or "")
    if not model or model == "<synthetic>":
        return []
    session_id = record.get("sessionId") or record.get("session_id") or ""
    event_time = record_timestamp(record, fallback_collected_at)
    period_start = ""
    if isinstance(event_time, str) and len(event_time) >= 10 and event_time[4] == "-":
        period_start = event_time[:10]
    scope = f"{stable_scope(session_id or claude_message_id(record))}:{model}"
    rows: list[dict[str, Any]] = []
    for _source_key, metric in CLAUDE_USAGE_METRIC_KEYS:
        if metric not in metrics:
            continue
        rows.append(
            metric_row(
                event_time,
                "claude",
                "usage",
                metric,
                record_kind="event_total",
                scope=scope,
                period_start=period_start,
                value=metrics[metric],
                unit="tokens",
                source="claude-session-log",
            )
        )
    return rows


def claude_usage_rows(record: Mapping[str, Any], fallback_collected_at: str) -> list[dict[str, Any]]:
    metrics = claude_usage_metrics(record)
    if not metrics:
        return []
    return claude_usage_rows_from_metrics(record, fallback_collected_at, metrics)


def claude_stored_offset(offsets: Mapping[str, Any], path: Path) -> int:
    return int(claude_offset_record(offsets, path)["offset"])


def claude_offset_record(offsets: Mapping[str, Any], path: Path) -> dict[str, Any]:
    raw = offsets.get(str(path), 0)
    offset = 0
    inode: Optional[int] = None
    pending: list[dict[str, int]] = []
    if isinstance(raw, Mapping):
        try:
            offset = max(0, int(raw.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0
        if raw.get("inode") is not None:
            try:
                inode = int(raw.get("inode"))
            except (TypeError, ValueError):
                inode = None
        raw_pending = raw.get("pending_inodes", [])
        if isinstance(raw_pending, list):
            for item in raw_pending:
                if isinstance(item, Mapping):
                    pending.append(dict(item))
    else:
        try:
            offset = max(0, int(raw))
        except (TypeError, ValueError):
            offset = 0
    return {"offset": offset, "inode": inode, "pending_inodes": pending}


def claude_prefix_replay_path(
    session_file: Path, stored: Mapping[str, Any]
) -> Optional[Path]:
    stored_inode = stored.get("inode")
    try:
        current_inode = session_file.stat().st_ino
    except OSError:
        current_inode = None
    if stored_inode is None:
        return session_file if session_file.is_file() else None
    try:
        stored_inode_value = int(stored_inode)
    except (TypeError, ValueError):
        return session_file if session_file.is_file() else None
    if current_inode == stored_inode_value:
        return session_file
    return find_file_by_inode(session_file.parent, stored_inode_value)


def fold_claude_usage_snapshot(
    snapshots: dict[str, dict[str, Any]],
    order: list[str],
    record: Mapping[str, Any],
    metrics: Mapping[str, Union[int, float]],
    message_id: str,
) -> None:
    previous = snapshots.get(message_id)
    if previous is None:
        snapshots[message_id] = {
            "first_record": record,
            "first_metrics": dict(metrics),
            "best_record": record,
            "best_metrics": dict(metrics),
        }
        order.append(message_id)
        return
    best_metrics = merge_claude_metrics(previous["best_metrics"], metrics)
    if (metrics.get("output_tokens") or 0) >= (
        previous["best_metrics"].get("output_tokens") or 0
    ):
        best_record = record
    else:
        best_record = previous["best_record"]
    snapshots[message_id] = {
        "first_record": previous["first_record"],
        "first_metrics": previous["first_metrics"],
        "best_record": best_record,
        "best_metrics": best_metrics,
    }


def claude_snapshots_from_records(
    records: Iterable[Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    list[str],
    list[tuple[Mapping[str, Any], dict[str, Union[int, float]]]],
]:
    snapshots: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    anonymous: list[tuple[Mapping[str, Any], dict[str, Union[int, float]]]] = []
    for record in records:
        message_id = claude_message_id(record)
        metrics = claude_usage_metrics(record)
        if not message_id:
            if metrics:
                anonymous.append((record, metrics))
            continue
        if not metrics:
            continue
        fold_claude_usage_snapshot(snapshots, order, record, metrics, message_id)
    return snapshots, order, anonymous


def merge_claude_snapshots(
    prefix: Mapping[str, dict[str, Any]],
    incoming: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = {key: dict(value) for key, value in prefix.items()}
    for message_id, snap in incoming.items():
        previous = merged.get(message_id)
        if previous is None:
            merged[message_id] = dict(snap)
            continue
        best_metrics = merge_claude_metrics(
            previous["best_metrics"], snap["best_metrics"]
        )
        if (snap["best_metrics"].get("output_tokens") or 0) >= (
            previous["best_metrics"].get("output_tokens") or 0
        ):
            best_record = snap["best_record"]
        else:
            best_record = previous["best_record"]
        merged[message_id] = {
            "first_record": previous["first_record"],
            "first_metrics": previous["first_metrics"],
            "best_record": best_record,
            "best_metrics": best_metrics,
        }
    return merged


def claude_snapshots_in_byte_range(
    path: Path,
    start_offset: int,
    end_offset: int,
    max_bytes: int,
    partial: bytes = b"",
) -> tuple[dict[str, dict[str, Any]], list[str], int, int, int, bytes]:
    if end_offset <= start_offset or max_bytes <= 0:
        return {}, [], 0, start_offset, 0, partial
    try:
        file_size = path.stat().st_size
    except OSError:
        return {}, [], 0, start_offset, 0, b""
    if start_offset < 0 or start_offset > file_size:
        return {}, [], 0, start_offset, 0, b""
    limit = min(max_bytes, end_offset - start_offset, file_size - start_offset)
    if limit <= 0:
        return {}, [], 0, start_offset, 0, partial
    records, errors, new_offset, consumed, _size, leftover = read_json_lines_at(
        path, start_offset, limit, partial=partial
    )
    if new_offset > end_offset and not leftover:
        new_offset = end_offset
    snapshots, order, _anonymous = claude_snapshots_from_records(records)
    return snapshots, order, errors, new_offset, consumed, leftover


def collect_claude_session_logs(
    collected_at: str,
    projects_dir: Path,
    provider_config: Mapping[str, Any],
    state: MutableMapping[str, Any],
) -> list[dict[str, Any]]:
    session_files = claude_session_files(projects_dir)
    if not session_files:
        return []
    rows: list[dict[str, Any]] = []
    offsets = state.setdefault("claude_offsets", {})
    if not isinstance(offsets, MutableMapping):
        offsets = {}
        state["claude_offsets"] = offsets
    recent_ids_raw = state.get("claude_recent_message_ids", [])
    recent_ids_list = recent_ids_raw if isinstance(recent_ids_raw, list) else []
    recent_ids = set(recent_ids_list)
    new_ids: list[str] = []
    accounted_raw = state.get("claude_accounted_usage")
    if isinstance(accounted_raw, MutableMapping):
        accounted = accounted_raw
    else:
        accounted = {}
    state["claude_accounted_usage"] = accounted
    legacy_ids = {
        message_id
        for message_id in recent_ids
        if not isinstance(accounted.get(message_id), Mapping)
        or accounted.get(message_id, {}).get("awaiting_baseline")
    }
    scan_raw = state.get("claude_legacy_scan")
    if isinstance(scan_raw, MutableMapping):
        legacy_scan = scan_raw
    else:
        legacy_scan = {}
    state["claude_legacy_scan"] = legacy_scan
    parse_errors = 0
    rotation_losses: list[str] = []
    raw_session_budget = provider_config.get(
        "max_session_bytes_per_poll", 25 * 1024 * 1024
    )
    try:
        remaining_session_bytes = max(1024 * 1024, int(raw_session_budget))
    except (TypeError, ValueError):
        remaining_session_bytes = 25 * 1024 * 1024
    deferred_files = 0
    incomplete_legacy_scans = False
    for index, session_file in enumerate(session_files):
        if remaining_session_bytes <= 0:
            deferred_files = len(session_files) - index
            incomplete_legacy_scans = True
            break
        stored = claude_offset_record(offsets, session_file)
        stored_offset = int(stored["offset"])
        stored_inode = stored.get("inode")
        records, errors, bytes_read, file_rotation_losses = read_new_json_lines(
            session_file,
            offsets,
            max_bytes=remaining_session_bytes,
        )
        remaining_session_bytes -= bytes_read
        parse_errors += errors
        rotation_losses.extend(file_rotation_losses)
        file_key = str(session_file)
        new_snapshots, new_order, anonymous = claude_snapshots_from_records(records)
        for record, metrics in anonymous:
            rows.extend(claude_usage_rows_from_metrics(record, collected_at, metrics))
        prefix_snapshots: dict[str, dict[str, Any]] = {}
        prefix_order: list[str] = []
        prefix_source = claude_prefix_replay_path(session_file, stored)
        target_inode = stored_inode
        if target_inode is None and prefix_source is not None:
            try:
                target_inode = prefix_source.stat().st_ino
            except OSError:
                target_inode = None
        scan_entry = legacy_scan.get(file_key)
        scan_partial = b""
        if (
            isinstance(scan_entry, Mapping)
            and scan_entry.get("inode") == target_inode
        ):
            try:
                scan_at = max(0, int(scan_entry.get("offset", 0)))
            except (TypeError, ValueError):
                scan_at = 0
            scan_partial = decode_jsonl_partial(scan_entry.get("partial_line"))
        else:
            scan_at = 0
        scan_in_progress = (
            isinstance(scan_entry, Mapping)
            and scan_entry.get("inode") == target_inode
            and (scan_at < stored_offset or bool(scan_partial))
        )
        needs_prefix = (
            prefix_source is not None
            and stored_offset > 0
            and (scan_at < stored_offset or bool(scan_partial))
            and (legacy_ids or scan_in_progress)
        )
        if needs_prefix and remaining_session_bytes <= 0:
            incomplete_legacy_scans = True
        elif needs_prefix:
            (
                prefix_snapshots,
                prefix_order,
                prefix_errors,
                scan_at,
                prefix_consumed,
                scan_partial,
            ) = claude_snapshots_in_byte_range(
                prefix_source,
                scan_at,
                stored_offset,
                remaining_session_bytes,
                partial=scan_partial,
            )
            parse_errors += prefix_errors
            remaining_session_bytes -= prefix_consumed
            if target_inode is not None:
                scan_record: dict[str, Any] = {
                    "inode": target_inode,
                    "offset": scan_at,
                }
                if scan_partial:
                    scan_record["partial_line"] = encode_jsonl_partial(scan_partial)
                legacy_scan[file_key] = scan_record
            if scan_at < stored_offset or scan_partial:
                incomplete_legacy_scans = True
        combined = merge_claude_snapshots(prefix_snapshots, new_snapshots)
        visit_order: list[str] = []
        visited = set()
        for message_id in new_order + prefix_order:
            if message_id in visited:
                continue
            visit_order.append(message_id)
            visited.add(message_id)
        new_id_set = set(new_order)
        for message_id in visit_order:
            snap = combined[message_id]
            record = snap["best_record"]
            metrics = snap["best_metrics"]
            prior = accounted.get(message_id)
            if isinstance(prior, Mapping):
                if prior.get("unrecoverable"):
                    continue
                prior_file = str(prior.get("file") or "")
                if prior_file and prior_file != file_key:
                    continue
                prior_metrics_raw = prior.get("metrics")
                prior_metrics = (
                    claude_accounted_metrics(prior_metrics_raw)
                    if isinstance(prior_metrics_raw, Mapping)
                    else {}
                )
                if prior.get("awaiting_baseline"):
                    prefix_snap = prefix_snapshots.get(message_id)
                    if prefix_snap is None:
                        accounted[message_id] = {
                            "file": prior_file or file_key,
                            "inode": prior.get("inode", target_inode),
                            "metrics": merge_claude_metrics(prior_metrics, metrics),
                            "awaiting_baseline": True,
                        }
                        continue
                    baseline = prefix_snap["first_metrics"]
                    best_metrics = merge_claude_metrics(prior_metrics, metrics)
                    deltas = claude_metric_deltas(baseline, best_metrics)
                    if deltas:
                        rows.extend(
                            claude_usage_rows_from_metrics(
                                record, collected_at, deltas
                            )
                        )
                    accounted[message_id] = {
                        "file": prior_file or file_key,
                        "inode": prior.get("inode", target_inode),
                        "metrics": best_metrics,
                    }
                    legacy_ids.discard(message_id)
                    continue
                deltas = claude_metric_deltas(prior_metrics, metrics)
                if deltas:
                    rows.extend(
                        claude_usage_rows_from_metrics(record, collected_at, deltas)
                    )
                    accounted[message_id] = {
                        "file": prior_file or file_key,
                        "inode": prior.get("inode", target_inode),
                        "metrics": merge_claude_metrics(prior_metrics, metrics),
                    }
                continue
            if message_id in recent_ids:
                prefix_snap = prefix_snapshots.get(message_id)
                if prefix_snap is None:
                    if message_id in new_id_set:
                        accounted[message_id] = {
                            "file": file_key,
                            "inode": target_inode,
                            "metrics": dict(metrics),
                            "awaiting_baseline": True,
                        }
                    continue
                baseline = prefix_snap["first_metrics"]
                deltas = claude_metric_deltas(baseline, metrics)
                if deltas:
                    rows.extend(
                        claude_usage_rows_from_metrics(record, collected_at, deltas)
                    )
                accounted[message_id] = {
                    "file": file_key,
                    "inode": target_inode,
                    "metrics": dict(metrics),
                }
                legacy_ids.discard(message_id)
                continue
            if message_id not in new_id_set:
                continue
            rows.extend(claude_usage_rows_from_metrics(record, collected_at, metrics))
            accounted[message_id] = {
                "file": file_key,
                "inode": target_inode,
                "metrics": dict(metrics),
            }
            recent_ids.add(message_id)
            new_ids.append(message_id)
    if not deferred_files and not incomplete_legacy_scans:
        leftover_legacy = [
            message_id
            for message_id in recent_ids_list
            if message_id in legacy_ids
            and (
                not isinstance(accounted.get(message_id), Mapping)
                or accounted.get(message_id, {}).get("awaiting_baseline")
            )
        ]
        if leftover_legacy:
            for message_id in leftover_legacy:
                accounted[message_id] = {
                    "file": "",
                    "metrics": {},
                    "unrecoverable": True,
                }
            rows.append(
                metric_row(
                    collected_at,
                    "claude",
                    "collection",
                    "legacy_message_baselines_unrecovered",
                    record_kind="interval_total",
                    value=len(leftover_legacy),
                    unit="messages",
                    source="claude-session-log",
                    status="partial",
                    message=(
                        "Message ids marked seen before usage baselines existed "
                        "could not be recovered from retained transcripts"
                    ),
                )
            )
    kept_ids = (recent_ids_list + new_ids)[-CLAUDE_RECENT_MESSAGE_ID_CAP:]
    state["claude_recent_message_ids"] = kept_ids
    kept_id_set = set(kept_ids)
    for message_id in list(accounted.keys()):
        if message_id not in kept_id_set:
            accounted.pop(message_id, None)
    loss_row = rotation_loss_row(
        collected_at,
        "claude",
        "claude-session-log",
        rotation_losses,
    )
    if loss_row is not None:
        rows.append(loss_row)
    if parse_errors:
        rows.append(
            metric_row(
                collected_at,
                "claude",
                "collection",
                "session_log_parse_errors",
                record_kind="interval_total",
                value=parse_errors,
                unit="records",
                source="claude-session-log",
                status="error",
                message="Malformed records were skipped",
            )
        )
    if deferred_files:
        rows.append(
            metric_row(
                collected_at,
                "claude",
                "collection",
                "session_files_deferred",
                record_kind="interval_total",
                value=deferred_files,
                unit="files",
                source="claude-session-log",
                status="partial",
                message="Per-poll local scan budget reached; remaining files will be read later",
            )
        )
    return rows


def collect_claude(
    collected_at: str,
    provider_config: Mapping[str, Any],
    include_history: bool,
    state: MutableMapping[str, Any],
) -> list[dict[str, Any]]:
    del include_history
    rows: list[dict[str, Any]] = []
    executable = find_executable(provider_config, ["claude"])
    stats_setting = provider_config.get("stats_file", "~/.claude/stats-cache.json")
    stats_path = expand_path(str(stats_setting))
    stats_available = stats_path.is_file() and os.access(stats_path, os.R_OK)
    projects_setting = provider_config.get("projects_dir", "~/.claude/projects")
    projects_dir = expand_path(str(projects_setting))
    session_files = claude_session_files(projects_dir)
    detected = executable is not None or stats_available or bool(session_files)

    details = []
    if executable is not None:
        details.append(f"binary={executable}")
    if stats_available:
        details.append(f"stats={stats_path}")
    if session_files:
        details.append(f"session_logs={len(session_files)}")
    if not detected:
        # No binary, stats cache, or session transcripts — skip rather than writing missing spam.
        return rows

    rows.append(
        availability_row(
            collected_at,
            "claude",
            True,
            "filesystem-discovery",
            "; ".join(details),
        )
    )

    if not stats_available and provider_config.get("config_dir"):
        # An added account: Claude Code doesn't always write a stats cache, and the
        # transcripts already cover usage, so its absence is not an error.
        pass
    elif not stats_available:
        rows.append(
            metric_row(
                collected_at,
                "claude",
                "collection",
                "stats_cache",
                source="local-file",
                status="missing",
                message=f"stats cache not found: {stats_path}; Claude was not executed",
            )
        )
    else:
        raw_stale_after = provider_config.get("stale_after_seconds", 172800)
        try:
            stale_after = max(60, int(raw_stale_after))
        except (TypeError, ValueError):
            stale_after = 172800
        try:
            age_seconds = max(0, int(time.time() - stats_path.stat().st_mtime))
        except OSError:
            age_seconds = 0
        rows.append(
            metric_row(
                collected_at,
                "claude",
                "freshness",
                "source_age_seconds",
                value=age_seconds,
                unit="seconds",
                source="claude-stats-cache",
                status="stale" if age_seconds > stale_after else "ok",
                message=(
                    "Stats cache has not been rewritten; session transcripts are the live source"
                    if age_seconds > stale_after
                    else ""
                ),
            )
        )
        try:
            with stats_path.open("r", encoding="utf-8") as handle:
                stats = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            rows.append(
                metric_row(
                    collected_at,
                    "claude",
                    "collection",
                    "stats_cache",
                    source="claude-stats-cache",
                    status="error",
                    message=str(error),
                )
            )
            stats = None
        if stats is not None and not isinstance(stats, Mapping):
            rows.append(
                metric_row(
                    collected_at,
                    "claude",
                    "collection",
                    "stats_cache",
                    source="claude-stats-cache",
                    status="error",
                    message="stats cache root is not an object",
                )
            )
            stats = None
        if isinstance(stats, Mapping):
            activity_metrics = {
                "total_sessions": ("totalSessions", "sessions"),
                "total_messages": ("totalMessages", "messages"),
            }
            for metric, (source_key, unit) in activity_metrics.items():
                value = stats.get(source_key)
                if value is not None:
                    rows.append(
                        metric_row(
                            collected_at,
                            "claude",
                            "activity",
                            metric,
                            value=value,
                            unit=unit,
                            source="claude-stats-cache",
                        )
                    )

            model_usage = stats.get("modelUsage")
            if isinstance(model_usage, Mapping):
                model_metric_map = {
                    "inputTokens": "input_tokens",
                    "outputTokens": "output_tokens",
                    "cacheReadInputTokens": "cache_read_input_tokens",
                    "cacheCreationInputTokens": "cache_creation_input_tokens",
                }
                for model, metrics in model_usage.items():
                    if not isinstance(metrics, Mapping):
                        continue
                    for source_key, metric in model_metric_map.items():
                        value = metrics.get(source_key)
                        if value is None:
                            continue
                        rows.append(
                            metric_row(
                                collected_at,
                                "claude",
                                "usage",
                                metric,
                                scope=str(model),
                                value=value,
                                unit="tokens",
                                source="claude-stats-cache",
                            )
                        )
                    for cost_key in ("costUSD", "costUsd"):
                        if metrics.get(cost_key) is not None:
                            rows.append(
                                metric_row(
                                    collected_at,
                                    "claude",
                                    "cost",
                                    "estimated_api_cost",
                                    scope=str(model),
                                    value=metrics[cost_key],
                                    unit="USD",
                                    source="claude-stats-cache",
                                )
                            )
                            break

            daily_model_tokens = stats.get("dailyModelTokens")
            if isinstance(daily_model_tokens, list):
                for day in daily_model_tokens:
                    if not isinstance(day, Mapping):
                        continue
                    date = day.get("date")
                    by_model = day.get("tokensByModel")
                    if not date or not isinstance(by_model, Mapping):
                        continue
                    for model, tokens in by_model.items():
                        scope = f"{date}:{model}"
                        if not state_value_changed(
                            state,
                            "claude_daily_model_tokens",
                            scope,
                            tokens,
                            max_entries=2500,
                        ):
                            continue
                        rows.append(
                            metric_row(
                                collected_at,
                                "claude",
                                "usage",
                                "daily_model_tokens",
                                record_kind="period_total",
                                scope=scope,
                                period_start=str(date),
                                value=tokens,
                                unit="tokens",
                                source="claude-stats-cache",
                            )
                        )

    rows.extend(
        collect_claude_session_logs(
            collected_at,
            projects_dir,
            provider_config,
            state,
        )
    )

    rows.extend(collect_claude_subscription_usage(collected_at, provider_config, executable))
    return rows


# --- Claude subscription usage (/usage) -------------------------------------
# Claude Code keeps the limits its `/usage` command shows in ~/.claude.json
# (`cachedUsageUtilization`, with `fetchedAtMs`). The collector reads that
# cache; only when it is older than `usage_cache_max_age_seconds` does it run
# `claude -p /usage` once, locked down as far as Claude Code allows while still
# using the subscription login:
# - no customizations, settings files, MCP servers, tools, permission prompts
#   or saved session (`--safe-mode --restricted --setting-sources ""
#   --strict-mcp-config --tools "" --no-session-persistence ...`);
# - an empty temporary working directory and no stdin;
# - telemetry, error reporting, the auto-updater and the bug command off.
# Measured on Claude Code 2.1.302: only api.anthropic.com is contacted
# (account bootstrap and the usage endpoint), and the only file it changes is
# that cache entry. It is not `--bare` or CLAUDE_CODE_SIMPLE (both drop the
# subscription login), not --disable-slash-commands (it disables /usage), and
# not CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC (it classifies the usage fetch
# as non-essential and silently replays the cache). Because Claude Code
# answers from its cache when it cannot fetch, the exit status proves nothing:
# freshness is decided by `fetchedAtMs` alone. Claude Code (read from 2.1.309)
# also answers from its cache, without fetching, while that reading is under an
# hour old, so a run within the hour returns the newest reading it will give.
CLAUDE_USAGE_ARGUMENTS = (
    "--safe-mode",
    "--restricted",
    "--setting-sources",
    "",
    "--strict-mcp-config",
    "--tools",
    "",
    "--no-session-persistence",
    "--permission-mode",
    "dontAsk",
    "--permission-prompts",
    "none",
    "--max-budget-usd",
    "0.01",
    "--print",
    "/usage",
    "--output-format",
    "json",
)
CLAUDE_USAGE_ENVIRONMENT = {
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_BUG_COMMAND": "1",
}
CLAUDE_USAGE_REFRESH_GRACE_MS = 120_000
CLAUDE_USAGE_REUSE_MS = 3_600_000
CLAUDE_USAGE_WINDOWS = {"session": ("session", 18000), "weekly_all": ("weekly", 604800)}


def claude_usage_environment(config_dir: Optional[Path] = None) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        # Nothing inherited may redirect, re-authenticate or reconfigure the
        # run (a base URL, an API key, simple/non-essential-traffic modes), or
        # point it at another account's config dir.
        if not key.startswith(("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDECODE")) and key != "CLAUDE_CONFIG_DIR"
    }
    environment["PATH"] = discovery_path()
    environment.update(CLAUDE_USAGE_ENVIRONMENT)
    if config_dir is not None:
        # An added account: its own absolute config dir (the environment does not expand "~").
        environment["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return environment


def read_claude_usage_cache(path: Path) -> Optional[dict[str, Any]]:
    """The cached limits, or None when Claude Code has not cached any.

    Raises ValueError when the entry exists but has an unrecognized shape.
    """

    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, Mapping):
        raise ValueError("Claude Code's config is not a JSON object")
    cached = document.get("cachedUsageUtilization")
    if cached is None:
        return None
    if not isinstance(cached, Mapping):
        raise ValueError("cachedUsageUtilization is not an object")
    fetched_at_ms = cached.get("fetchedAtMs")
    utilization = cached.get("utilization")
    limits = utilization.get("limits") if isinstance(utilization, Mapping) else None
    if isinstance(fetched_at_ms, bool) or not isinstance(fetched_at_ms, (int, float)):
        raise ValueError("cachedUsageUtilization.fetchedAtMs is missing")
    if not isinstance(limits, list):
        raise ValueError("cachedUsageUtilization.utilization.limits is missing")
    windows: list[dict[str, Any]] = []
    for limit in limits:
        if not isinstance(limit, Mapping):
            raise ValueError("a usage limit is not an object")
        kind, percent = limit.get("kind"), limit.get("percent")
        if kind in CLAUDE_USAGE_WINDOWS:
            scope, seconds = CLAUDE_USAGE_WINDOWS[kind]
        elif kind == "weekly_scoped":
            model = (limit.get("scope") or {}).get("model") if isinstance(limit.get("scope"), Mapping) else None
            name = model.get("display_name") if isinstance(model, Mapping) else None
            if not isinstance(name, str) or not name.strip():
                continue
            scope, seconds = f"weekly:{name.strip()}", 604800
        else:
            continue  # A limit kind this collector does not know yet.
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            raise ValueError(f"usage limit {kind} has no numeric percent")
        resets_at = parse_iso_timestamp(limit.get("resets_at"))
        windows.append(
            {
                "scope": scope,
                "window_seconds": seconds,
                "used_percent": percent,
                "resets_at": timestamp_to_iso(resets_at) if resets_at is not None else "",
            }
        )
    if not windows:
        raise ValueError("no recognized usage limit in cachedUsageUtilization")
    cached_account = cached.get("accountUuid")
    account = document.get("oauthAccount")
    signed_in = account.get("accountUuid") if isinstance(account, Mapping) else None
    return {
        "fetched_at_ms": float(fetched_at_ms),
        "windows": windows,
        "same_account": not (cached_account and signed_in and cached_account != signed_in),
    }


def run_claude_usage(executable: Path, timeout_seconds: int, config_dir: Optional[Path] = None) -> None:
    """Run the locked-down `/usage` once; it refreshes the cache as a side effect."""

    timeout_seconds = max(5, min(int(timeout_seconds), 120))
    with tempfile.TemporaryDirectory(prefix="ai-usage-claude-") as workdir:
        process = subprocess.Popen(
            [str(executable), *CLAUDE_USAGE_ARGUMENTS],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=workdir,
            env=claude_usage_environment(config_dir),
            start_new_session=os.name == "posix",
        )
        process_group = process.pid if os.name == "posix" else None
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            terminate_provider_process(process, process_group)
            raise RuntimeError(f"claude /usage did not finish within {timeout_seconds}s")
        finally:
            terminate_provider_process(process, process_group)
    if process.returncode != 0:
        detail = (stderr or "").strip().splitlines()[-1:] or [f"exit {process.returncode}"]
        raise RuntimeError(f"claude /usage failed: {detail[0][:200]}")
    try:
        answer = json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError("claude /usage did not answer with JSON") from None
    if not isinstance(answer, Mapping) or answer.get("is_error") or answer.get("subtype") != "success":
        raise RuntimeError("claude /usage reported an error")


def claude_usage_rows(
    collected_at: str, cache: Mapping[str, Any], source: str, status: str, message: str = ""
) -> list[dict[str, Any]]:
    # The limits were measured when Claude Code fetched them, not when they were
    # collected: the age row lets the report date them (negative when the
    # refresh fetched them after the collection started).
    collected = parse_iso_timestamp(collected_at)
    seconds = round(collected - cache["fetched_at_ms"] / 1000, 3) if collected is not None else 0.0
    age = [
        metric_row(
            collected_at,
            "claude",
            "freshness",
            "usage_source_age_seconds",
            value=int(seconds) if seconds.is_integer() else seconds,
            unit="seconds",
            source=source,
            status=status,
        )
    ] if collected is not None else []
    return age + [
        metric_row(
            collected_at,
            "claude",
            "quota",
            "used_percent",
            scope=window["scope"],
            value=window["used_percent"],
            unit="percent",
            window_seconds=window["window_seconds"],
            resets_at=window["resets_at"],
            source=source,
            status=status,
            message=message,
        )
        for window in cache["windows"]
    ]


def collect_claude_subscription_usage(
    collected_at: str, provider_config: Mapping[str, Any], executable: Optional[Path]
) -> list[dict[str, Any]]:
    if provider_config.get("subscription_usage", True) is not True:
        return [
            metric_row(
                collected_at,
                "claude",
                "quota",
                "personal_subscription_quota",
                source="config",
                status="disabled",
                message="subscription usage disabled in config; nothing was read or run",
            )
        ]
    cache_path = expand_path(str(provider_config.get("usage_cache_file", "~/.claude.json")))
    max_age_ms = 1000 * max(60, int(provider_config.get("usage_cache_max_age_seconds", 900)))

    def error_row(message: str) -> dict[str, Any]:
        return metric_row(
            collected_at,
            "claude",
            "collection",
            "subscription_usage_query",
            source="claude-usage-command",
            status="error",
            message=message,
        )

    try:
        cache = read_claude_usage_cache(cache_path)
    except (OSError, ValueError) as error:
        return [error_row(f"Claude Code's usage cache is unreadable: {error}")]
    now_ms = time.time() * 1000
    if cache is not None and cache["same_account"] and now_ms - cache["fetched_at_ms"] <= max_age_ms:
        return claude_usage_rows(collected_at, cache, "claude-usage-cache", "ok")
    if executable is None:
        rows = [error_row("claude is not installed, so its usage could not be refreshed")]
        if cache is not None and cache["same_account"]:
            rows += claude_usage_rows(collected_at, cache, "claude-usage-cache", "stale",
                                      "last reading Claude Code cached; not refreshed")
        return rows

    started_ms = time.time() * 1000
    try:
        config_dir = provider_config.get("config_dir")
        run_claude_usage(executable, int(provider_config.get("timeout_seconds", 30)),
                         account_config_dir(str(config_dir)) if config_dir else None)
        refreshed = read_claude_usage_cache(cache_path)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        rows = [error_row(str(error))]
        if cache is not None and cache["same_account"]:
            rows += claude_usage_rows(collected_at, cache, "claude-usage-cache", "stale",
                                      "last reading Claude Code cached; the refresh failed")
        return rows
    if refreshed is None:
        return [error_row("claude /usage ran but cached no usage (is it signed in to a subscription?)")]
    if not refreshed["same_account"]:
        return [error_row("Claude Code's cached usage belongs to another account than the one signed in")]
    if refreshed["fetched_at_ms"] < started_ms - CLAUDE_USAGE_REFRESH_GRACE_MS:
        if started_ms - refreshed["fetched_at_ms"] <= CLAUDE_USAGE_REUSE_MS:
            # Claude Code reused its reading from the last hour: not a failure,
            # and still dated by when it was fetched.
            return claude_usage_rows(collected_at, refreshed, "claude-usage-cache", "ok")
        # Claude Code answered from its cache: offline, signed out, or blocked.
        return [error_row("claude /usage did not fetch fresh usage; showing its older cached reading")] + \
            claude_usage_rows(collected_at, refreshed, "claude-usage-cache", "stale",
                              "Claude Code did not refresh its usage")
    return claude_usage_rows(collected_at, refreshed, "claude-usage-command", "ok")


def collect(context: CollectContext) -> list[dict[str, Any]]:
    return collect_claude(context.collected_at, context.settings, context.include_history, context.state)


def doctor_extra(settings: Mapping[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    stats_setting = settings.get(
        "stats_file", "~/.claude/stats-cache.json"
    )
    stats_path = expand_path(str(stats_setting))
    extra["stats_file"] = str(stats_path)
    extra["stats_file_readable"] = (
        stats_path.is_file() and os.access(stats_path, os.R_OK)
    )
    projects_dir = expand_path(
        str(settings.get("projects_dir", "~/.claude/projects"))
    )
    extra["projects_dir"] = str(projects_dir)
    extra["session_log_count"] = len(claude_session_files(projects_dir))
    return extra


DEFAULT_SETTINGS = {
    "enabled": True,
    "executable": None,
    "executable_names": ["claude"],
    "stats_file": "~/.claude/stats-cache.json",
    "projects_dir": "~/.claude/projects",
    "stale_after_seconds": 172800,
    "max_session_bytes_per_poll": 26214400,
    # Subscription limits (/usage), read from Claude Code's own cache in
    # ~/.claude.json and refreshed by one locked-down headless `/usage`
    # run only when that cache is older than the max age.
    "subscription_usage": True,
    "usage_cache_file": "~/.claude.json",
    "usage_cache_max_age_seconds": 900,
    "timeout_seconds": 30,
    "monthly_subscription_usd": None,
}


def account_config_dir(config_dir: str) -> Path:
    """An added account's CLAUDE_CONFIG_DIR: absolute, with "~" expanded but symlinks
    kept, because Claude Code keys an account's login to the folder as it was named."""
    expanded = os.path.expandvars(os.path.expanduser(config_dir))
    if not os.path.isabs(expanded):
        # The daemon has no working directory of its own to resolve it against.
        raise ValueError(f"config_dir must be an absolute path or start with ~: {config_dir}")
    return Path(os.path.normpath(expanded))


def instance_settings(explicit: Mapping[str, Any]) -> dict[str, Any]:
    """An added Claude account: every path comes from its own config_dir, never ~/.claude.

    Claude Code keeps an account's transcripts, stats cache and `.claude.json`
    inside the CLAUDE_CONFIG_DIR it runs with. Explicit settings apply last.
    """
    config_dir = explicit.get("config_dir")
    if not isinstance(config_dir, str) or not config_dir.strip():
        raise ValueError("config_dir must be a non-empty string: the account's CLAUDE_CONFIG_DIR")
    directory = account_config_dir(config_dir)
    derived = {
        **copy.deepcopy(DEFAULT_SETTINGS),
        "stats_file": str(directory / "stats-cache.json"),
        "projects_dir": str(directory / "projects"),
        "usage_cache_file": str(directory / ".claude.json"),
    }
    return {**derived, **explicit, "config_dir": str(directory)}


def refresh_dir(settings: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    """The folder `/usage` refreshes: an added account's CLAUDE_CONFIG_DIR, or the default
    account's ~/.claude (it always runs with CLAUDE_CONFIG_DIR removed). None when the
    account is disabled or its plan limits are off, so it never runs `claude`."""
    if settings.get("enabled", True) is not True or settings.get("subscription_usage", True) is not True:
        return None
    config_dir = settings.get("config_dir")
    if config_dir:
        return "config_dir", str(config_dir)
    return "(Claude Code's default folder)", "~/.claude"


def refresh_cache(settings: Mapping[str, Any]) -> Optional[str]:
    """The `.claude.json` that `/usage` refreshes, inside the folder it runs with, even when
    `usage_cache_file` points the account's reading elsewhere."""
    target = refresh_dir(settings)
    if target is None:
        return None
    return "~/.claude.json" if target[0].startswith("(") else str(Path(target[1]) / ".claude.json")


PLUGIN = ProviderPlugin(
    id="claude",
    default_settings=DEFAULT_SETTINGS,
    executable_names=("claude",),
    collect=collect,
    doctor_extra=doctor_extra,
    path_roles=(("stats_file", "file"), ("projects_dir", "directory"), ("usage_cache_file", "file")),
    instance_settings=instance_settings,
    instance_only_settings=("config_dir",),
    refresh_dir=refresh_dir,
    refresh_cache=refresh_cache,
    report=ProviderDef(
        "claude", "Claude Code", "Claude Code", mode="event", split="input_output",
        record_kind="event_total", quota="polled",
        reported_note="Claude Code's local data doesn't include a usage cost.",
        quota_note="Your plan's session and weekly limits, as Claude Code's /usage shows them.",
        sources=[
            SourceDef("sessions", "usage", "Session transcripts",
                      "Local Claude Code session transcripts (~/.claude/projects)",
                      ["claude-session-log"], needs_rows=False,
                      error_metrics=["unexpected_error"]),
            SourceDef("stats-cache", "history", "Daily stats",
                      "Claude Code local stats cache (undocumented format)",
                      ["claude-stats-cache"], needs_rows=True,
                      error_metrics=["stats_cache"]),
            SourceDef("subscription-usage", "quota", "Plan limits",
                      "Claude Code's /usage: its cached limits, refreshed by one locked-down headless run when stale",
                      ["claude-usage-command", "claude-usage-cache"], needs_rows=True,
                      error_metrics=["subscription_usage_query"]),
        ],
    ),
    report_order=1,
)
