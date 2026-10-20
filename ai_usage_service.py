#!/usr/bin/env python3
"""Zero-inference AI usage collector and macOS LaunchAgent manager.

The collector only uses documented account-metadata interfaces or local usage
files. It never submits prompts or starts model turns.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import csv
import datetime as datetime_module
import fcntl
import glob
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import plistlib
import queue
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, MutableMapping, Optional, Union
import uuid

# The provider plugins are installed beside this file (~/.ai-usage/ai_usage_plugins,
# or the app bundle's collector/). Find them there even under `python -I`, and never
# leave an unowned __pycache__ next to the installed collector.
sys.dont_write_bytecode = True
_COLLECTOR_DIR = os.path.dirname(os.path.realpath(__file__))
if _COLLECTOR_DIR not in sys.path:
    sys.path.insert(0, _COLLECTOR_DIR)

import ai_usage_plugins  # noqa: E402
from ai_usage_plugins.base import CollectContext, ProviderPlugin  # noqa: E402
from ai_usage_plugins.support import (  # noqa: E402  (re-exported: tests and fixtures use them)
    COMMON_EXECUTABLE_DIRS,
    atomic_write_bytes,
    availability_row,
    decode_jsonl_partial,
    discovery_path,
    encode_jsonl_partial,
    exclusive_file_lock,
    expand_path,
    find_executable,
    find_file_by_inode,
    fsync_directory,
    is_executable_file,
    iso_utc,
    metric_row,
    next_json_message,
    parse_iso_timestamp,
    pids_in_process_group,
    read_json_lines_at,
    read_new_json_lines,
    record_timestamp,
    response_error,
    rotation_loss_row,
    scalar_leaves,
    signal_pids,
    snake_case,
    stable_scope,
    start_json_line_reader,
    state_value_changed,
    terminate_provider_process,
    timestamp_to_iso,
    utc_now,
)


VERSION = "2.2.0"
PRODUCTION_SERVICE_LABEL = "codes.porta.ai-usage"
TEST_SERVICE_LABEL_PREFIX = f"{PRODUCTION_SERVICE_LABEL}.test."


def resolve_service_label() -> str:
    """The LaunchAgent label. AI_USAGE_SERVICE_LABEL exists only for the
    explicitly invoked real-launchd integration check, and may only name a
    disposable `codes.porta.ai-usage.test.*` agent, never the real one."""
    override = os.environ.get("AI_USAGE_SERVICE_LABEL")
    if override is None:
        return PRODUCTION_SERVICE_LABEL
    if not re.fullmatch(re.escape(TEST_SERVICE_LABEL_PREFIX) + r"[A-Za-z0-9-]{4,64}", override):
        raise SystemExit(
            f"ai-usage: AI_USAGE_SERVICE_LABEL must look like {TEST_SERVICE_LABEL_PREFIX}<id>; refusing {override!r}"
        )
    return override


SERVICE_LABEL = resolve_service_label()
DEFAULT_ROOT = Path.home() / ".ai-usage"
DEFAULT_CONFIG_PATH = DEFAULT_ROOT / "config.json"
DEFAULT_INSTALLED_SCRIPT = DEFAULT_ROOT / "collector.py"
DEFAULT_PLIST_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
)

CSV_FIELDS = [
    "transaction_id",
    "transaction_index",
    "collected_at",
    "provider",
    "category",
    "metric",
    "record_kind",
    "scope",
    "period_start",
    "period_end",
    "value",
    "unit",
    "window_seconds",
    "resets_at",
    "source",
    "status",
    "message",
]


# The bundled providers, in collection order (ai_usage_plugins.BUNDLED). A provider
# whose module fails to load is left out here and reported per provider.
PLUGINS: dict[str, ProviderPlugin]
PLUGIN_LOAD_ERRORS: dict[str, str]
PLUGINS, PLUGIN_LOAD_ERRORS = ai_usage_plugins.load_plugins()

DEFAULT_CONFIG: dict[str, Any] = {
    "format_version": 1,
    "poll_interval_seconds": 3600,
    "poll_on_start": True,
    "poll_paused": False,
    "paths": {
        "usage_csv": "~/.ai-usage/usage.csv",
        "log_file": "~/.ai-usage/collector.log",
        "state_file": "~/.ai-usage/state.json",
        "cache_dir": "~/.ai-usage/cache",
    },
    "providers": {
        name: copy.deepcopy(dict(plugin.default_settings)) for name, plugin in PLUGINS.items()
    },
}


LOGGER = logging.getLogger("ai_usage")


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError("config root must be a JSON object")
    config = deep_merge(DEFAULT_CONFIG, loaded)
    validate_config(config)
    config_canonical = path.resolve(strict=False)
    for role, configured_path, _expected_type in configured_path_roles(config):
        if configured_path.resolve(strict=False) == config_canonical:
            raise ValueError(
                f"path role collision: config file and {role} both resolve to {config_canonical}"
            )
    return config


def validate_config(config: Mapping[str, Any]) -> None:
    interval = config.get("poll_interval_seconds")
    if not isinstance(interval, int) or isinstance(interval, bool):
        raise ValueError("poll_interval_seconds must be an integer")
    if interval < 60:
        raise ValueError("poll_interval_seconds must be at least 60")
    if interval > 604800:
        raise ValueError("poll_interval_seconds must not exceed 604800")
    for flag in ("poll_on_start", "poll_paused"):
        if flag in config and not isinstance(config[flag], bool):
            raise ValueError(f"{flag} must be true or false")

    paths = config.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("paths must be an object")
    for key in ("usage_csv", "log_file", "state_file", "cache_dir"):
        if not isinstance(paths.get(key), str) or not paths[key].strip():
            raise ValueError(f"paths.{key} must be a non-empty string")
    providers = config.get("providers")
    if not isinstance(providers, Mapping):
        raise ValueError("providers must be an object")
    validate_configured_paths(config)


def pending_state_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.name}.pending")


def configured_path_roles(
    config: Mapping[str, Any]
) -> list[tuple[str, Path, str]]:
    paths = config["paths"]
    providers = config["providers"]
    usage_path = expand_path(str(paths["usage_csv"]))
    log_path = expand_path(str(paths["log_file"]))
    state_path = expand_path(str(paths["state_file"]))
    cache_path = expand_path(str(paths["cache_dir"]))
    roles: list[tuple[str, Path, str]] = [
        ("paths.usage_csv", usage_path, "file"),
        ("paths.log_file", log_path, "file"),
        ("paths.state_file", state_path, "file"),
        ("paths.cache_dir", cache_path, "directory"),
        ("usage CSV lock", usage_path.with_name(f"{usage_path.name}.lock"), "file"),
        (
            "state transaction lock",
            state_path.with_name(f"{state_path.name}.lock"),
            "file",
        ),
        ("pending state journal", pending_state_path(state_path), "file"),
    ]
    for provider_name, plugin in PLUGINS.items():
        if not plugin.path_roles:
            continue
        provider = providers.get(provider_name)
        if not isinstance(provider, Mapping):
            raise ValueError(f"providers.{provider_name} must be an object")
        for setting_name, expected_type in plugin.path_roles:
            role = f"providers.{provider_name}.{setting_name}"
            value = provider.get(setting_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{role} must be a non-empty string")
            roles.append((role, expand_path(value), expected_type))
    return roles


def validate_configured_paths(config: Mapping[str, Any]) -> None:
    """Reject aliases, derived-lock collisions, and wrong path roles pre-mutation."""

    canonical_roles: dict[Path, str] = {}
    roles = configured_path_roles(config)
    for role, path, _expected_type in roles:
        canonical = path.resolve(strict=False)
        previous = canonical_roles.get(canonical)
        if previous is not None:
            raise ValueError(
                f"path role collision: {previous} and {role} both resolve to {canonical}"
            )
        canonical_roles[canonical] = role

    for role, path, expected_type in roles:
        if path.exists():
            if expected_type == "file" and not path.is_file():
                raise ValueError(f"{role} must be a regular file when it exists: {path}")
            if expected_type == "directory" and not path.is_dir():
                raise ValueError(f"{role} must be a directory when it exists: {path}")
        if path.parent.exists() and not path.parent.is_dir():
            raise ValueError(f"parent of {role} must be a directory: {path.parent}")


def write_default_config(path: Path) -> None:
    content = json.dumps(DEFAULT_CONFIG, indent=2, sort_keys=False) + "\n"
    atomic_write_bytes(path, content.encode("utf-8"), mode=0o600)


def configured_paths(config: Mapping[str, Any]) -> tuple[Path, Path]:
    paths = config["paths"]
    return expand_path(paths["usage_csv"]), expand_path(paths["log_file"])


def state_path_for(config: Mapping[str, Any]) -> Path:
    return expand_path(config["paths"]["state_file"])


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"state file is not a regular file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"state file is malformed and was preserved unchanged: {path}: {error}"
        ) from error
    if not isinstance(state, dict):
        raise ValueError(
            f"state file root is not an object and was preserved unchanged: {path}"
        )
    return state


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    content = json.dumps(state, indent=2, sort_keys=True) + "\n"
    atomic_write_bytes(path, content.encode("utf-8"), mode=0o600)


def state_transaction_lock(state_path: Path) -> Any:
    return exclusive_file_lock(state_path.with_name(f"{state_path.name}.lock"))


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path.touch(exist_ok=True, mode=0o600)
    os.chmod(log_path, 0o600)

    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def add_subscription_cost(
    rows: list[dict[str, Any]],
    collected_at: str,
    provider: str,
    provider_config: Mapping[str, Any],
    state: MutableMapping[str, Any],
) -> None:
    configured_cost = provider_config.get("monthly_subscription_usd")
    if configured_cost is None or configured_cost == "":
        return
    try:
        cost = float(configured_cost)
    except (TypeError, ValueError):
        rows.append(
            metric_row(
                collected_at,
                provider,
                "cost",
                "monthly_subscription",
                source="config",
                status="error",
                message="monthly_subscription_usd is not numeric",
            )
        )
        return
    if not math.isfinite(cost) or cost < 0:
        rows.append(
            metric_row(
                collected_at,
                provider,
                "cost",
                "monthly_subscription",
                source="config",
                status="error",
                message="monthly_subscription_usd must be finite and non-negative",
            )
        )
        return
    today = datetime_module.datetime.now().date()
    period_start_date = today.replace(day=1)
    if period_start_date.month == 12:
        period_end_date = period_start_date.replace(
            year=period_start_date.year + 1, month=1
        )
    else:
        period_end_date = period_start_date.replace(
            month=period_start_date.month + 1
        )
    state_scope = f"{provider}:{period_start_date.isoformat()}"
    if not state_value_changed(
        state,
        "monthly_subscription_rates",
        state_scope,
        cost,
        max_entries=240,
    ):
        return
    rows.append(
        metric_row(
            collected_at,
            provider,
            "cost",
            "monthly_subscription",
            record_kind="monthly_rate",
            period_start=period_start_date.isoformat(),
            period_end=period_end_date.isoformat(),
            value=cost,
            unit="USD/month",
            source="config",
        )
    )


def csv_backup_path(path: Path) -> Path:
    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.stem}.legacy-{timestamp}{path.suffix}")
    sequence = 1
    while candidate.exists():
        candidate = path.with_name(
            f"{path.stem}.legacy-{timestamp}-{sequence}{path.suffix}"
        )
        sequence += 1
    return candidate


def ensure_csv_unlocked(path: Path) -> Optional[Path]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup: Optional[Path] = None
    if path.exists() and not path.is_file():
        raise ValueError(f"usage CSV path is not a regular file: {path}")
    if path.is_file() and path.stat().st_size:
        with path.open("r", newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        if header != CSV_FIELDS:
            backup = csv_backup_path(path)
            os.replace(path, backup)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=CSV_FIELDS).writeheader()
            handle.flush()
            os.fsync(handle.fileno())
    os.chmod(path, 0o600)
    return backup


def ensure_csv(path: Path) -> Optional[Path]:
    lock_path = path.with_name(f"{path.name}.lock")
    with exclusive_file_lock(lock_path):
        return ensure_csv_unlocked(path)


def transaction_rows(
    transaction_id: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["transaction_id"] = transaction_id
        item["transaction_index"] = index
        prepared.append(item)
    return prepared


def write_pending_transaction(
    state_path: Path,
    transaction_id: str,
    rows: list[dict[str, Any]],
    candidate_state: Mapping[str, Any],
) -> Path:
    journal_path = pending_state_path(state_path)
    if journal_path.exists():
        raise ValueError(
            f"pending state journal already exists and must be recovered first: {journal_path}"
        )
    payload = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "rows": rows,
        "state": candidate_state,
    }
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    atomic_write_bytes(journal_path, content.encode("utf-8"), mode=0o600)
    return journal_path


def load_pending_transaction(state_path: Path) -> Optional[dict[str, Any]]:
    journal_path = pending_state_path(state_path)
    if not journal_path.exists():
        return None
    if not journal_path.is_file():
        raise ValueError(f"pending state journal is not a regular file: {journal_path}")
    try:
        with journal_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"pending state journal is malformed and was preserved: {journal_path}: {error}"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"pending state journal has an unsupported schema: {journal_path}")
    transaction_id = payload.get("transaction_id")
    rows = payload.get("rows")
    state = payload.get("state")
    if not isinstance(transaction_id, str) or not transaction_id:
        raise ValueError(f"pending state journal has no transaction id: {journal_path}")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"pending state journal rows are invalid: {journal_path}")
    if not isinstance(state, dict):
        raise ValueError(f"pending state journal state is invalid: {journal_path}")
    for index, row in enumerate(rows):
        if row.get("transaction_id") != transaction_id or row.get(
            "transaction_index"
        ) != index:
            raise ValueError(
                f"pending state journal row provenance is invalid: {journal_path}"
            )
    return payload


def append_transaction_rows(
    path: Path,
    transaction_id: str,
    rows: list[dict[str, Any]],
    *,
    recovering: bool,
) -> None:
    if not rows:
        return
    lock_path = path.with_name(f"{path.name}.lock")
    with exclusive_file_lock(lock_path):
        backup = ensure_csv_unlocked(path)
        if backup is not None:
            LOGGER.warning(
                "moved incompatible usage CSV to %s before creating current schema",
                backup,
            )
        if recovering:
            rewrite_transaction_rows_unlocked(path, transaction_id, rows)
            return
        if rows:
            with path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=CSV_FIELDS, extrasaction="ignore"
                )
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)


def rewrite_transaction_rows_unlocked(
    path: Path, transaction_id: str, rows: list[dict[str, Any]]
) -> None:
    """Atomically replace every pending transaction row during recovery."""

    retained: list[dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError(f"usage CSV schema changed during recovery: {path}")
        for existing in reader:
            if existing.get("transaction_id") == transaction_id:
                continue
            if None in existing or any(existing.get(field) is None for field in CSV_FIELDS):
                raise ValueError(
                    f"usage CSV has an unrelated partial row; preserved for recovery: {path}"
                )
            retained.append({field: existing.get(field, "") for field in CSV_FIELDS})

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.recover-", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(retained)
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def remove_pending_transaction(state_path: Path) -> None:
    journal_path = pending_state_path(state_path)
    journal_path.unlink()
    fsync_directory(journal_path.parent)


def recover_pending_transaction(
    state_path: Path, usage_path: Path, current_state: Mapping[str, Any]
) -> dict[str, Any]:
    payload = load_pending_transaction(state_path)
    if payload is None:
        return dict(current_state)
    transaction_id = str(payload["transaction_id"])
    rows = list(payload["rows"])
    candidate_state = dict(payload["state"])
    append_transaction_rows(
        usage_path,
        transaction_id,
        rows,
        recovering=True,
    )
    save_state(state_path, candidate_state)
    remove_pending_transaction(state_path)
    LOGGER.warning("recovered pending collection transaction %s", transaction_id)
    return candidate_state


def commit_collection_transaction(
    state_path: Path,
    usage_path: Path,
    rows: list[dict[str, Any]],
    candidate_state: Mapping[str, Any],
) -> None:
    transaction_id = uuid.uuid4().hex
    prepared_rows = transaction_rows(transaction_id, rows)
    write_pending_transaction(
        state_path,
        transaction_id,
        prepared_rows,
        candidate_state,
    )
    append_transaction_rows(
        usage_path,
        transaction_id,
        prepared_rows,
        recovering=False,
    )
    save_state(state_path, candidate_state)
    remove_pending_transaction(state_path)


def log_status_line_error(config_path: Path, error: Exception, display_name: str) -> None:
    try:
        log_path = DEFAULT_ROOT / "collector.log"
        try:
            config = load_config(config_path)
            _, log_path = configured_paths(config)
        except Exception:
            pass
        configure_logging(log_path)
        LOGGER.error(
            "%s status-line capture failed error_type=%s message=%s",
            display_name,
            type(error).__name__,
            str(error)[:500],
        )
    except Exception:
        print(
            f"ai-usage {display_name} capture failed: {type(error).__name__}",
            file=sys.stderr,
        )


def status_line_plugin(command: str) -> Optional[ProviderPlugin]:
    """The provider whose installed wrapper runs `collector.py <command>`."""
    for plugin in PLUGINS.values():
        if plugin.integration is not None and plugin.integration.wrapper_command == command:
            return plugin
    return None


def run_status_line(plugin: ProviderPlugin, config_path: Path) -> int:
    assert plugin.integration is not None and plugin.status_line is not None
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("status payload exceeded 2 MiB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("status payload was not an object")
        try:
            config = load_config(config_path)
            settings = provider_config(config, plugin.id)
        except (OSError, ValueError, json.JSONDecodeError):
            settings = {}
        print(plugin.status_line(settings, payload))
        return 0
    except Exception as error:
        # Stdout is the provider's UI channel. Never print diagnostics there.
        log_status_line_error(config_path, error, plugin.integration.display_name)
        print(plugin.integration.display_name)
        return 0


def provider_config(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    providers = config.get("providers", {})
    value = providers.get(name, {}) if isinstance(providers, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def provider_names() -> list[str]:
    """Every bundled provider id in collection order, including any that failed to load."""
    return [name for name in ai_usage_plugins.BUNDLED if name in PLUGINS or name in PLUGIN_LOAD_ERRORS]


def collect_snapshot(
    config: Mapping[str, Any],
    include_history: bool,
    state: MutableMapping[str, Any],
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> list[dict[str, Any]]:
    collected_at = iso_utc(utc_now())
    names = provider_names()

    rows: list[dict[str, Any]] = []
    enabled = [
        name for name in names if provider_config(config, name).get("enabled", True) is True
    ]
    for name in names:
        settings = provider_config(config, name)
        if progress is not None and name in enabled:
            progress(name, enabled.index(name) + 1, len(enabled))
        if settings.get("enabled", True) is not True:
            rows.append(
                metric_row(
                    collected_at,
                    name,
                    "availability",
                    "enabled",
                    value=False,
                    unit="boolean",
                    source="config",
                    status="disabled",
                    message="provider disabled in config; no discovery or command was run",
                )
            )
            continue
        try:
            plugin = PLUGINS.get(name)
            if plugin is None:
                raise RuntimeError(f"provider module failed to load: {PLUGIN_LOAD_ERRORS[name]}")
            provider_rows = plugin.collect(
                CollectContext(collected_at, settings, include_history, state, VERSION)
            )
        except Exception as error:  # One provider cannot suppress the others.
            provider_rows = [
                metric_row(
                    collected_at,
                    name,
                    "collection",
                    "unexpected_error",
                    source="collector",
                    status="error",
                    message=str(error),
                )
            ]
        rows.extend(provider_rows)
        add_subscription_cost(rows, collected_at, name, settings, state)
    return rows


def detect_providers(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for name in provider_names():
        settings = provider_config(config, name)
        enabled = settings.get("enabled", True) is True
        plugin = PLUGINS.get(name)
        if plugin is None:
            results[name] = {
                "enabled": enabled,
                "detected": False,
                "executable": None,
                "load_error": PLUGIN_LOAD_ERRORS[name],
            }
            continue
        if not enabled:
            results[name] = {
                "enabled": False,
                "detected": False,
                "executable": None,
            }
            continue
        executable = (
            find_executable(settings, list(plugin.executable_names)) if enabled else None
        )
        extra: dict[str, Any] = plugin.doctor_extra(settings)
        source_available = any(
            bool(value)
            for key, value in extra.items()
            if key.endswith("_readable") or key == "session_log_count"
        )
        results[name] = {
            "enabled": enabled,
            "detected": bool(executable or source_available),
            "executable": str(executable) if executable else None,
            **extra,
        }
    return results


def print_doctor(config: Mapping[str, Any], as_json: bool = False) -> None:
    results = detect_providers(config)
    if as_json:
        print(json.dumps(results, indent=2, sort_keys=True))
        return
    print("Provider discovery (no provider commands executed):")
    for name, result in results.items():
        if result.get("load_error"):
            print(f"  {name:13} could not be loaded — {result['load_error']}")
            continue
        if not result["enabled"]:
            print(f"  {name:13} disabled")
            continue
        state = "detected" if result["detected"] else "not found"
        details = []
        if result.get("executable"):
            details.append(f"binary {result['executable']}")
        if result.get("stats_file_readable"):
            details.append(f"stats {result['stats_file']}")
        if result.get("cache_file_readable"):
            details.append(f"cache {result['cache_file']}")
        if result.get("events_file_readable"):
            details.append(f"events {result['events_file']}")
        if result.get("telemetry_file_readable"):
            details.append(f"telemetry {result['telemetry_file']}")
        if result.get("session_log_count"):
            details.append(f"session logs {result['session_log_count']}")
        if result.get("billing_cache_readable"):
            details.append(f"billing cache {result['billing_cache_file']}")
        if result.get("auth_file_present"):
            details.append("OAuth cache present")
        suffix = f" — {', '.join(details)}" if details else ""
        print(f"  {name:13} {state}{suffix}")


def read_json_object_for_merge(path: Path) -> tuple[Optional[dict[str, Any]], str]:
    if not path.exists():
        return {}, "new"
    if not path.is_file():
        return None, "path is not a regular file"
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        return None, str(error)
    if not isinstance(value, dict):
        return None, "settings root is not a JSON object"
    return value, "existing"


def write_json_object(path: Path, value: Mapping[str, Any]) -> None:
    content = json.dumps(value, indent=2, sort_keys=False) + "\n"
    atomic_write_bytes(path, content.encode("utf-8"), mode=0o600)


def stable_python_executable(executable: Optional[str] = None) -> Path:
    """The interpreter path to record in the LaunchAgent and wrappers.

    Never the symlink-resolved path: for Homebrew that is
    `.../Cellar/python@X.Y/X.Y.Z/...`, which every patch upgrade deletes, so
    the collector and the app would stop starting. Keep the path Python was
    run as, and map a Cellar path onto Homebrew's stable `opt/<formula>` link
    when that link points at the same interpreter.
    """

    path = Path(executable or sys.executable)
    parts = path.parts
    if "Cellar" in parts:
        index = parts.index("Cellar")
        if len(parts) > index + 3:
            prefix = Path(*parts[:index])
            formula, rest = parts[index + 1], parts[index + 3:]
            stable = prefix.joinpath("opt", formula, *rest)
            try:
                if stable.exists() and os.path.samefile(stable, path):
                    return stable
            except OSError:
                pass
    return path


def wrapper_content(config_path: Path, command: str) -> str:
    return (
        "#!/bin/sh\n"
        f"exec {shlex.quote(str(stable_python_executable()))} "
        f"{shlex.quote(str(DEFAULT_INSTALLED_SCRIPT))} "
        f"{command} --config "
        f"{shlex.quote(str(config_path))}\n"
    )


def ownership_path() -> Path:
    return DEFAULT_ROOT / "install-ownership.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FilesystemTransaction:
    """Disk-backed file snapshots for reversible install/uninstall mutations."""

    def __init__(self, parent: Path):
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.recovery_dir = Path(
            tempfile.mkdtemp(prefix=".ai-usage-install-recovery-", dir=str(parent))
        )
        os.chmod(self.recovery_dir, 0o700)
        self.snapshots: dict[Path, dict[str, Any]] = {}
        self.created_directories: list[Path] = []
        self._write_manifest()

    def _write_manifest(self) -> None:
        entries = {
            str(path): {key: value for key, value in record.items() if key != "backup"}
            for path, record in self.snapshots.items()
        }
        content = json.dumps({"schema_version": 1, "files": entries}, indent=2) + "\n"
        atomic_write_bytes(
            self.recovery_dir / "manifest.json", content.encode("utf-8"), mode=0o600
        )

    def ensure_directory(self, path: Path, mode: int = 0o700) -> None:
        missing: list[Path] = []
        candidate = path
        while not candidate.exists():
            missing.append(candidate)
            candidate = candidate.parent
        if not candidate.is_dir():
            raise ValueError(f"directory parent is not a directory: {candidate}")
        for directory in reversed(missing):
            directory.mkdir(mode=mode)
            self.created_directories.append(directory)

    def snapshot(self, path: Path) -> None:
        if path in self.snapshots:
            return
        if path.is_symlink():
            raise ValueError(f"refusing to mutate symlinked installer path: {path}")
        if not path.exists():
            self.snapshots[path] = {"kind": "absent"}
        elif path.is_file():
            backup = self.recovery_dir / f"file-{len(self.snapshots):04d}.backup"
            shutil.copy2(path, backup, follow_symlinks=False)
            self.snapshots[path] = {
                "kind": "file",
                "backup": backup,
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
        else:
            raise ValueError(f"installer target is not a regular file: {path}")
        self._write_manifest()

    def mark_created(self, path: Path) -> None:
        if path not in self.snapshots:
            self.snapshots[path] = {"kind": "absent"}
            self._write_manifest()

    def rollback(self, preserve_recovery: bool = False) -> None:
        failures: list[str] = []
        for path, record in reversed(list(self.snapshots.items())):
            try:
                if record["kind"] == "absent":
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    elif path.exists():
                        raise ValueError(f"rollback target became a directory: {path}")
                else:
                    if (
                        path.is_file()
                        and not path.is_symlink()
                        and stat.S_IMODE(path.stat().st_mode) == int(record["mode"])
                        and file_sha256(path) == file_sha256(Path(record["backup"]))
                    ):
                        # Unchanged since the snapshot (e.g. the write that failed
                        # never replaced it): nothing to restore.
                        continue
                    self.ensure_directory(path.parent)
                    descriptor, temporary_name = tempfile.mkstemp(
                        prefix=f".{path.name}.rollback-", dir=str(path.parent)
                    )
                    os.close(descriptor)
                    temporary = Path(temporary_name)
                    try:
                        shutil.copy2(record["backup"], temporary)
                        os.chmod(temporary, int(record["mode"]))
                        os.replace(temporary, path)
                        fsync_directory(path.parent)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
            except Exception as error:
                failures.append(f"{path}: {error}")
        for directory in reversed(self.created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        if failures:
            raise RuntimeError(
                "rollback incomplete; recovery artifacts preserved at "
                f"{self.recovery_dir}: {'; '.join(failures)}"
            )
        if not preserve_recovery:
            self.cleanup()

    def cleanup(self) -> None:
        if self.recovery_dir.exists():
            shutil.rmtree(self.recovery_dir)


def load_ownership() -> Optional[dict[str, Any]]:
    path = ownership_path()
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"install ownership record is not a regular file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as error:
        raise ValueError(f"install ownership record is malformed: {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError(f"install ownership record has an unsupported schema: {path}")
    if not isinstance(value.get("files"), dict) or not isinstance(
        value.get("integrations"), dict
    ):
        raise ValueError(f"install ownership record is incomplete: {path}")
    return value


def previous_integration_record(
    ownership: Optional[Mapping[str, Any]], name: str
) -> Optional[Mapping[str, Any]]:
    integrations = ownership.get("integrations") if isinstance(ownership, Mapping) else None
    record = integrations.get(name) if isinstance(integrations, Mapping) else None
    return record if isinstance(record, Mapping) else None


def prepare_integration_plan(
    name: str,
    settings: Mapping[str, Any],
    settings_path: Path,
    field: str,
    desired: Mapping[str, Any],
    enabled: bool,
    executable: Optional[Path],
    previous: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    previous_owned = bool(
        isinstance(previous, Mapping)
        and previous.get("owned") is True
        and previous.get("settings_path") == str(settings_path)
        and previous.get("field") == field
        and previous.get("value") == desired
    )
    plan: dict[str, Any] = {
        "name": name,
        "settings": settings,
        "settings_path": settings_path,
        "field": field,
        "value": dict(desired),
        "owned": previous_owned,
        "write": False,
        "document": None,
        "status": "inactive",
    }
    if not enabled or executable is None:
        plan["status"] = "disabled" if not enabled else "missing executable"
        return plan
    if settings_path.is_symlink():
        plan["owned"] = False
        plan["status"] = "symlinked settings were preserved"
        return plan
    document, read_status = read_json_object_for_merge(settings_path)
    if document is None:
        plan["owned"] = False
        plan["status"] = f"unsafe settings were preserved: {read_status}"
        return plan
    existing = document.get(field)
    if existing is None:
        plan.update(owned=True, write=True, document=document, status="configured")
    elif existing == desired and previous_owned:
        plan.update(owned=True, document=document, status="managed")
    elif existing == desired:
        plan.update(owned=False, document=document, status="preexisting identical")
    else:
        plan.update(owned=False, document=document, status="preexisting custom")
    return plan


def prepare_integration_plans(
    config: Mapping[str, Any],
    config_path: Path,
    ownership: Optional[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    plans: dict[str, dict[str, Any]] = {}
    for name, plugin in PLUGINS.items():
        spec = plugin.integration
        if spec is None:
            continue
        settings = provider_config(config, name)
        wrapper_path = DEFAULT_ROOT / spec.wrapper_command if spec.wrapper_command else None
        settings_path = expand_path(str(settings.get(spec.settings_setting, spec.settings_default)))
        plan = prepare_integration_plan(
            name,
            settings,
            settings_path,
            spec.field,
            spec.desired(settings, wrapper_path),
            settings.get("enabled", True) is True
            and settings.get(spec.enable_setting, True) is True,
            find_executable(settings, list(plugin.executable_names)),
            previous_integration_record(ownership, name),
        )
        if wrapper_path is not None and spec.wrapper_command is not None:
            plan["wrapper_path"] = wrapper_path
            plan["wrapper_bytes"] = wrapper_content(config_path, spec.wrapper_command).encode(
                "utf-8"
            )
        plans[name] = plan
    return plans


def integration_ownership_record(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "owned": plan.get("owned") is True,
        "attached": plan.get("status")
        in {"configured", "managed", "preexisting identical"},
        "settings_path": str(plan["settings_path"]),
        "field": str(plan["field"]),
        "value": plan["value"],
    }


def apply_integration_plan(plan: MutableMapping[str, Any], tx: FilesystemTransaction) -> None:
    plugin = PLUGINS[str(plan["name"])]
    spec = plugin.integration
    assert spec is not None
    if plan.get("write") is True:
        settings_path = Path(plan["settings_path"])
        document = dict(plan["document"])
        document[str(plan["field"])] = plan["value"]
        tx.snapshot(settings_path)
        tx.ensure_directory(settings_path.parent)
        write_json_object(settings_path, document)
    if plan.get("owned") is True:
        for directory in spec.owned_directories(plan["value"]):
            tx.ensure_directory(directory)

    status = str(plan["status"])
    if status == "configured":
        print(f"Configured {spec.configured_label}: {plan['settings_path']}")
    elif status in {"preexisting identical", "managed"}:
        print(f"Preserved {spec.preserved_label} ({status}): {plan['settings_path']}")
    elif status == "missing executable":
        print(f"Skipped {spec.display_name} integration: {plugin.executable_names[0]} was not found")
    elif status != "disabled":
        print(f"Preserved {spec.display_name} settings ({status}): {plan['settings_path']}")


def build_launch_agent_plist(
    python_executable: Path,
    installed_script: Path,
    config_path: Path,
    log_path: Path,
) -> bytes:
    payload = {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [
            str(python_executable),
            str(installed_script),
            "daemon",
            "--config",
            str(config_path),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 30,
        "EnvironmentVariables": {"PATH": discovery_path()},
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


LAUNCHCTL_OVERRIDE_ENV = "AI_USAGE_LAUNCHCTL"


def find_launchctl() -> Optional[Path]:
    # Test seam: hermetic suites point this at a fake so they can never
    # bootstrap or boot out the real per-user LaunchAgent. An explicit
    # override fails closed: when set (even empty) but not an executable file,
    # there is no launchctl, and the system binary is never used instead.
    if LAUNCHCTL_OVERRIDE_ENV in os.environ:
        value = os.environ[LAUNCHCTL_OVERRIDE_ENV]
        if not value:
            return None
        # Resolve to an absolute path now: a relative value such as ./launchctl
        # would otherwise reach subprocess as a bare name and be looked up on
        # PATH, executing a different binary from the one validated here.
        candidate = Path(os.path.abspath(os.path.expanduser(value)))
        return candidate if is_executable_file(candidate) else None
    for candidate in (Path("/bin/launchctl"), Path("/usr/bin/launchctl")):
        if is_executable_file(candidate):
            return candidate
    return None


def run_launchctl(arguments: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    launchctl = find_launchctl()
    if launchctl is None:
        if LAUNCHCTL_OVERRIDE_ENV in os.environ:
            raise FileNotFoundError(
                f"{LAUNCHCTL_OVERRIDE_ENV} is set but is not an executable file: "
                f"{os.environ[LAUNCHCTL_OVERRIDE_ENV]!r}"
            )
        raise FileNotFoundError("launchctl was not found")
    result = subprocess.run(
        [str(launchctl), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "launchctl failed"
        raise RuntimeError(message)
    return result


def previous_file_record(
    ownership: Optional[Mapping[str, Any]], key: str
) -> Optional[Mapping[str, Any]]:
    files = ownership.get("files") if isinstance(ownership, Mapping) else None
    record = files.get(key) if isinstance(files, Mapping) else None
    return record if isinstance(record, Mapping) else None


def prepare_managed_file(
    key: str,
    path: Path,
    content: bytes,
    mode: int,
    ownership: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    previous = previous_file_record(ownership, key)
    previously_owned = bool(
        isinstance(previous, Mapping)
        and previous.get("owned") is True
        and previous.get("path") == str(path)
    )
    if path.is_symlink():
        raise ValueError(f"refusing to manage symlinked installer file: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"installer file path is not a regular file: {path}")
    desired_hash = hashlib.sha256(content).hexdigest()
    if not path.exists():
        owned = True
        write = True
    elif previously_owned:
        owned = True
        write = path.read_bytes() != content or stat.S_IMODE(path.stat().st_mode) != mode
    elif path.read_bytes() == content:
        owned = False
        write = False
    else:
        raise ValueError(
            f"installer target already exists without an ownership record: {path}"
        )
    return {
        "key": key,
        "path": path,
        "content": content,
        "mode": mode,
        "owned": owned,
        "write": write,
        "sha256": desired_hash,
    }


def managed_file_record(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "owned": plan.get("owned") is True,
        "path": str(plan["path"]),
        "sha256": str(plan["sha256"]),
        "mode": int(plan["mode"]),
    }


def apply_managed_file(plan: Mapping[str, Any], tx: FilesystemTransaction) -> None:
    if plan.get("write") is not True:
        return
    path = Path(plan["path"])
    tx.snapshot(path)
    tx.ensure_directory(path.parent)
    atomic_write_bytes(path, bytes(plan["content"]), mode=int(plan["mode"]))


def launch_agent(domain: str) -> None:
    # Explicit order: clear any persistent disable (e.g. from `stop`) first,
    # then bootstrap, then make sure it runs. No legacy `load -w` side effects.
    run_launchctl(["enable", f"{domain}/{SERVICE_LABEL}"], check=True)
    run_launchctl(["bootstrap", domain, str(DEFAULT_PLIST_PATH)], check=True)
    run_launchctl(["kickstart", "-k", f"{domain}/{SERVICE_LABEL}"], check=True)


PLUGIN_PACKAGE = "ai_usage_plugins"


def plugin_module_plans(ownership: Optional[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Managed-file plans for every module of the plugin package, read from beside this file."""
    source_dir = Path(__file__).resolve().parent / PLUGIN_PACKAGE
    loaded_dir = Path(ai_usage_plugins.__file__).resolve().parent
    if loaded_dir != source_dir:
        raise ValueError(
            f"refusing to install: the loaded plugins ({loaded_dir}) are not the ones beside "
            f"this collector ({source_dir})"
        )
    return {
        f"plugin_module:{name}": prepare_managed_file(
            f"plugin_module:{name}",
            DEFAULT_ROOT / PLUGIN_PACKAGE / f"{name}.py",
            (source_dir / f"{name}.py").read_bytes(),
            0o600,
            ownership,
        )
        for name in ai_usage_plugins.MODULES
    }


def install_service(config_path: Path, no_start: bool) -> None:
    config_path = config_path.resolve(strict=False)
    if PLUGIN_LOAD_ERRORS:
        raise ValueError(
            "refusing to install an incomplete collector; provider modules failed to load: "
            + "; ".join(f"{name}: {error}" for name, error in PLUGIN_LOAD_ERRORS.items())
        )
    if not no_start:
        if sys.platform != "darwin":
            raise RuntimeError("LaunchAgent installation can only be started on macOS")
        if find_launchctl() is None:
            raise RuntimeError("launchctl is unavailable; no installation changes were made")
    if DEFAULT_ROOT.exists() and not DEFAULT_ROOT.is_dir():
        raise ValueError(
            f"install root is not a directory; move it explicitly before installing: {DEFAULT_ROOT}"
        )
    if config_path.exists():
        if not config_path.is_file():
            raise ValueError(f"config path is not a regular file: {config_path}")
        config = load_config(config_path)
        create_config = False
    else:
        config = copy.deepcopy(DEFAULT_CONFIG)
        validate_config(config)
        create_config = True

    previous_ownership = load_ownership()
    if (
        previous_ownership is not None
        and previous_ownership.get("config_path") != str(config_path)
    ):
        raise ValueError(
            "existing ownership record belongs to a different config path; "
            "uninstall that instance before reinstalling"
        )
    usage_path, log_path = configured_paths(config)
    cache_path = expand_path(config["paths"]["cache_dir"])
    integration_plans = prepare_integration_plans(
        config, config_path, previous_ownership
    )
    python_executable = stable_python_executable()
    plist_bytes = build_launch_agent_plist(
        python_executable,
        DEFAULT_INSTALLED_SCRIPT,
        config_path,
        log_path,
    )
    file_plans: dict[str, dict[str, Any]] = {
        "installed_script": prepare_managed_file(
            "installed_script",
            DEFAULT_INSTALLED_SCRIPT,
            Path(__file__).resolve().read_bytes(),
            0o700,
            previous_ownership,
        ),
        "plist": prepare_managed_file(
            "plist",
            DEFAULT_PLIST_PATH,
            plist_bytes,
            0o600,
            previous_ownership,
        ),
    }
    for name, plan in integration_plans.items():
        if "wrapper_path" in plan and plan.get("owned") is True:
            file_plans[f"{name}_wrapper"] = prepare_managed_file(
                f"{name}_wrapper",
                Path(plan["wrapper_path"]),
                bytes(plan["wrapper_bytes"]),
                0o700,
                previous_ownership,
            )
    module_plans = plugin_module_plans(previous_ownership)
    file_plans.update(module_plans)
    previous_files = previous_ownership.get("files", {}) if previous_ownership else {}
    obsolete_modules = {
        key: record
        for key, record in previous_files.items()
        if key.startswith("plugin_module:") and key not in module_plans
    }

    domain = f"gui/{os.getuid()}"
    service_loaded = False
    prior_disabled: Optional[bool] = None
    if not no_start:
        service_loaded = (
            run_launchctl(["print", f"{domain}/{SERVICE_LABEL}"], check=False).returncode
            == 0
        )
        # Starting re-enables the agent; a failed install must put this back.
        prior_disabled = launchd_disabled(domain)

    def restore_disabled_state(error: BaseException) -> None:
        """After a failed install, re-disable an agent that was disabled before."""
        if prior_disabled is not True or not launch_attempted:
            return
        target = f"{domain}/{SERVICE_LABEL}"
        try:
            run_launchctl(["disable", target], check=True)
        except Exception as restore_error:
            raise RuntimeError(
                f"installation failed ({error}); files were restored, but the collector "
                f"could not restore its disabled state ({restore_error}). It was disabled "
                f"(stopped at login) before this install; run `launchctl disable {target}`"
            ) from error

    tx = FilesystemTransaction(DEFAULT_ROOT.parent)
    service_stopped = False
    launch_attempted = False
    try:
        if service_loaded:
            run_launchctl(["bootout", domain, str(DEFAULT_PLIST_PATH)], check=True)
            service_stopped = True

        tx.ensure_directory(DEFAULT_ROOT)
        os.chmod(DEFAULT_ROOT, 0o700)
        if create_config:
            tx.snapshot(config_path)
            tx.ensure_directory(config_path.parent)
            write_default_config(config_path)
            print(f"Created config: {config_path}")
        else:
            print(f"Preserved existing config: {config_path}")

        # The collector and its plugin package are written together, in one
        # transaction: a failure restores every one of them.
        for key in ("installed_script", *module_plans):
            apply_managed_file(file_plans[key], tx)
        for key, record in obsolete_modules.items():
            remove_owned_file(key, record, tx)
        print_doctor(config)
        for key, plan in file_plans.items():
            if key.endswith("_wrapper"):
                apply_managed_file(plan, tx)
        for plan in integration_plans.values():
            apply_integration_plan(plan, tx)

        tx.ensure_directory(cache_path)
        os.chmod(cache_path, 0o700)
        tx.snapshot(usage_path)
        tx.snapshot(usage_path.with_name(f"{usage_path.name}.lock"))
        csv_backup = ensure_csv(usage_path)
        if csv_backup is not None:
            tx.mark_created(csv_backup)
            print(f"Preserved incompatible usage CSV as: {csv_backup}")
        tx.snapshot(log_path)
        tx.ensure_directory(log_path.parent)
        log_path.touch(exist_ok=True, mode=0o600)
        os.chmod(log_path, 0o600)

        apply_managed_file(file_plans["plist"], tx)
        ownership = {
            "schema_version": 1,
            "config_path": str(config_path),
            "files": {
                key: managed_file_record(plan) for key, plan in file_plans.items()
            },
            "integrations": {
                name: integration_ownership_record(plan)
                for name, plan in integration_plans.items()
            },
        }
        tx.snapshot(ownership_path())
        write_json_object(ownership_path(), ownership)

        if not no_start:
            launch_attempted = True
            launch_agent(domain)
        tx.cleanup()
    except Exception as error:
        cleanup_failed = False
        if launch_attempted:
            try:
                cleanup = run_launchctl(
                    ["bootout", domain, str(DEFAULT_PLIST_PATH)], check=False
                )
                if cleanup.returncode != 0:
                    cleanup_failed = (
                        run_launchctl(
                            ["print", f"{domain}/{SERVICE_LABEL}"], check=False
                        ).returncode
                        == 0
                    )
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            # The new service may still be running: leave launchd as it is.
            raise RuntimeError(
                f"installation failed ({error}) and the new service could not be "
                f"stopped safely; installed files and recovery artifacts were preserved at "
                f"{tx.recovery_dir}"
            ) from error
        try:
            tx.rollback(preserve_recovery=service_stopped)
        except RuntimeError as rollback_error:
            restore_disabled_state(error)
            if service_stopped:
                raise RuntimeError(
                    f"installation failed ({error}); {rollback_error}. The collector was stopped "
                    "for this install and has NOT been restarted: restore the recovery artifacts, "
                    "then run `collector.py start`"
                ) from error
            raise
        if service_stopped:
            try:
                launch_agent(domain)
            except Exception as recovery_error:
                restore_disabled_state(error)
                raise RuntimeError(
                    f"installation failed ({error}); files were restored but the prior "
                    f"service could not be restarted ({recovery_error}); recovery artifacts: "
                    f"{tx.recovery_dir}"
                ) from error
            tx.cleanup()
        # launch_agent() enables the agent; restore a prior disable (applies to
        # the restarted service too, as a loaded agent can be disabled).
        restore_disabled_state(error)
        raise

    if no_start:
        print(f"Installed without starting: {DEFAULT_PLIST_PATH}")
    else:
        print(f"Installed service: {SERVICE_LABEL}")
        print(f"Config: {config_path}")
        print(f"Usage CSV: {usage_path}")
        print(f"Log: {log_path}")


def print_status() -> int:
    print(f"LaunchAgent: {DEFAULT_PLIST_PATH}")
    print(f"Installed script: {DEFAULT_INSTALLED_SCRIPT}")
    if sys.platform != "darwin" or find_launchctl() is None:
        print("launchctl status unavailable on this system")
        return 1
    domain = f"gui/{os.getuid()}"
    result = run_launchctl(["print", f"{domain}/{SERVICE_LABEL}"], check=False)
    if result.returncode == 0:
        print("Status: loaded")
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("state =") or stripped.startswith("pid ="):
                print(f"  {stripped}")
        return 0
    print("Status: not loaded")
    return 1


def launchd_disabled(domain: str) -> Optional[bool]:
    """launchd's persistent disable flag for the agent, or None if unknown."""
    result = run_launchctl(["print-disabled", domain], check=False)
    if result.returncode != 0:
        return None
    match = re.search(rf'"{re.escape(SERVICE_LABEL)}"\s*=>\s*(\w+)', result.stdout)
    if not match:
        return False
    return match.group(1) in ("disabled", "true")


def service_status() -> dict[str, Any]:
    """LaunchAgent state for the menu app: installed, loaded/running, pid, disabled."""
    status: dict[str, Any] = {
        "label": SERVICE_LABEL,
        "plist": str(DEFAULT_PLIST_PATH),
        "installed": DEFAULT_PLIST_PATH.is_file(),
        "state": "unknown",
        "pid": None,
        "disabled": None,
        "detail": "",
    }
    if sys.platform != "darwin" or find_launchctl() is None:
        status["detail"] = "launchctl unavailable on this system"
        return status
    domain = f"gui/{os.getuid()}"
    printed = run_launchctl(["print", f"{domain}/{SERVICE_LABEL}"], check=False)
    if printed.returncode == 0:
        status["state"] = "loaded"
        for line in printed.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("state =") and status["state"] == "loaded":
                status["state"] = "running" if stripped.split("=", 1)[1].strip() == "running" else "loaded"
            elif stripped.startswith("pid =") and status["pid"] is None:
                try:
                    status["pid"] = int(stripped.split("=", 1)[1].strip())
                except ValueError:
                    pass
    else:
        status["state"] = "stopped" if status["installed"] else "not_installed"
    status["disabled"] = launchd_disabled(domain)
    status["detail"] = f"launchctl: state = {status['state']}"
    return status


SERVICE_SETTLE_SECONDS = 10.0


def wait_for_service_state(accept: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """Poll service_status() until `accept` holds or SERVICE_SETTLE_SECONDS pass.

    launchctl bootout/bootstrap can return before the job has finished exiting
    or starting, so the reported state is re-read until it settles."""
    deadline = time.monotonic() + SERVICE_SETTLE_SECONDS
    status = service_status()
    while not accept(status) and time.monotonic() < deadline:
        time.sleep(0.2)
        status = service_status()
    return status


def start_service() -> dict[str, Any]:
    """Re-enable the LaunchAgent and start it. Never rewrites installed files."""
    if sys.platform != "darwin" or find_launchctl() is None:
        raise RuntimeError("launchctl is unavailable; the collector was not started")
    if not DEFAULT_PLIST_PATH.is_file():
        raise RuntimeError(
            f"the collector is not installed ({DEFAULT_PLIST_PATH} is missing); run install first"
        )
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{SERVICE_LABEL}"
    run_launchctl(["enable", target], check=True)
    if run_launchctl(["print", target], check=False).returncode != 0:
        run_launchctl(["bootstrap", domain, str(DEFAULT_PLIST_PATH)], check=True)
    status = wait_for_service_state(lambda current: current["state"] == "running")
    if status["state"] != "running":
        run_launchctl(["kickstart", target], check=True)
        status = wait_for_service_state(lambda current: current["state"] == "running")
    return status


def stop_service() -> dict[str, Any]:
    """Disable the LaunchAgent (so it stays stopped across login) and boot it out.

    Installation, configuration, and collected data are preserved."""
    if sys.platform != "darwin" or find_launchctl() is None:
        raise RuntimeError("launchctl is unavailable; the collector was not stopped")
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{SERVICE_LABEL}"
    run_launchctl(["disable", target], check=True)
    if run_launchctl(["print", target], check=False).returncode == 0:
        run_launchctl(["bootout", target], check=True)
    # bootout can return while the job is still exiting; report the settled state.
    return wait_for_service_state(lambda current: current["state"] in ("stopped", "not_installed"))


CONFIGURABLE_PROVIDER_KEYS = {"enabled", "monthly_subscription_usd"}


def parse_config_assignment(assignment: str) -> tuple[list[str], Any]:
    if "=" not in assignment:
        raise ValueError(f"expected KEY=JSON_VALUE, got {assignment!r}")
    key, raw = assignment.split("=", 1)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{key}: value is not JSON: {raw!r}") from error
    parts = key.split(".")
    if parts in (["poll_paused"], ["poll_interval_seconds"]):
        return parts, value
    if (
        len(parts) == 3
        and parts[0] == "providers"
        and parts[1] in DEFAULT_CONFIG["providers"]
        and parts[2] in CONFIGURABLE_PROVIDER_KEYS
    ):
        if parts[2] == "enabled" and not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        if parts[2] == "monthly_subscription_usd" and value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
        ):
            raise ValueError(f"{key} must be a non-negative number or null")
        return parts, value
    raise ValueError(f"{key} is not a setting the menu app may change")


def configure(config_path: Path, assignments: list[str]) -> dict[str, Any]:
    """Apply validated settings to config.json atomically, preserving every other key.

    Writes are serialized with a lock beside the config, validated against the
    full merged config before replacing the file, and keep mode 0600."""
    parsed = [parse_config_assignment(item) for item in assignments]
    if not parsed:
        raise ValueError("no settings given")
    # Write the file the collector actually reads: replacing a symlinked alias
    # would detach it from its target, and the lock must sit beside the target
    # so every alias serializes on the same one.
    config_path = config_path.resolve(strict=True)
    with exclusive_file_lock(config_path.with_name(f"{config_path.name}.lock")):
        with config_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("config root must be a JSON object")
        updated = copy.deepcopy(raw)
        for parts, value in parsed:
            node = updated
            for part in parts[:-1]:
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
            node[parts[-1]] = value
        merged = deep_merge(DEFAULT_CONFIG, updated)
        validate_config(merged)
        mode = stat.S_IMODE(config_path.stat().st_mode) if config_path.exists() else 0o600
        atomic_write_bytes(config_path, (json.dumps(updated, indent=2) + "\n").encode("utf-8"), mode=mode)
    load_config(config_path)  # the written file must load exactly as the collector will
    return {".".join(parts): value for parts, value in parsed}


def detach_owned_integration(
    name: str, record: Mapping[str, Any], tx: FilesystemTransaction
) -> bool:
    if record.get("owned") is not True:
        return False
    settings_path = Path(str(record.get("settings_path", "")))
    field = record.get("field")
    desired = record.get("value")
    if not settings_path.is_absolute() or not isinstance(field, str):
        raise ValueError(f"ownership record for {name} is invalid")
    if settings_path.is_symlink():
        raise ValueError(f"refusing to detach integration through symlink: {settings_path}")
    document, read_status = read_json_object_for_merge(settings_path)
    if document is None:
        raise ValueError(f"could not inspect owned {name} settings: {read_status}")
    if field not in document:
        return True
    if document.get(field) != desired:
        print(f"Preserved modified {name} integration: {settings_path}")
        return False
    tx.snapshot(settings_path)
    del document[field]
    write_json_object(settings_path, document)
    print(f"Removed owned {name} integration: {settings_path}")
    return True


def remove_owned_file(
    key: str, record: Optional[Mapping[str, Any]], tx: FilesystemTransaction
) -> bool:
    if not isinstance(record, Mapping) or record.get("owned") is not True:
        return False
    path = Path(str(record.get("path", "")))
    expected_hash = record.get("sha256")
    if not path.is_absolute() or not isinstance(expected_hash, str):
        raise ValueError(f"ownership record for {key} is invalid")
    if not path.exists():
        return True
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"refusing to remove non-regular owned file: {path}")
    if file_sha256(path) != expected_hash:
        print(f"Preserved modified installer file: {path}")
        return False
    tx.snapshot(path)
    path.unlink()
    fsync_directory(path.parent)
    return True


def wrapper_integrations(
    integrations: Mapping[str, Any], files: Mapping[str, Any]
) -> list[str]:
    """Integrations whose hook runs collector.py through an installer-owned wrapper."""
    names = []
    for name in dict.fromkeys([*integrations, *PLUGINS]):
        plugin = PLUGINS.get(name)
        has_wrapper = plugin is not None and plugin.integration is not None and bool(
            plugin.integration.wrapper_command
        )
        if has_wrapper or f"{name}_wrapper" in files:
            names.append(name)
    return names


def hook_may_use_managed_wrapper(name: str, value: Any) -> bool:
    plugin = PLUGINS.get(name)
    spec = plugin.integration if plugin is not None else None
    if spec is None or not spec.wrapper_command:
        return True  # unknown provider: assume it may
    return value == spec.desired({}, DEFAULT_ROOT / spec.wrapper_command)


def integration_display_name(name: str) -> str:
    plugin = PLUGINS.get(name)
    return plugin.integration.display_name if plugin is not None and plugin.integration else name


def uninstall_service(config_path: Path) -> None:
    del config_path
    ownership = load_ownership()
    if ownership is None:
        raise RuntimeError(
            "no install ownership record exists; preserving all files and integrations"
        )
    domain = f"gui/{os.getuid()}"
    service_loaded = False
    if sys.platform == "darwin":
        if find_launchctl() is None:
            raise RuntimeError("launchctl is unavailable; uninstall made no changes")
        service_loaded = (
            run_launchctl(["print", f"{domain}/{SERVICE_LABEL}"], check=False).returncode
            == 0
        )
    tx = FilesystemTransaction(DEFAULT_ROOT.parent)
    service_stopped = False
    try:
        if service_loaded:
            run_launchctl(["bootout", domain, str(DEFAULT_PLIST_PATH)], check=True)
            service_stopped = True
        integrations = ownership["integrations"]
        detached: dict[str, bool] = {}
        for name, record in integrations.items():
            detached[name] = detach_owned_integration(
                name, record if isinstance(record, Mapping) else {}, tx
            )

        files = ownership["files"]
        # collector.py may only go when no hook can still run it through a wrapper:
        # each wrapper-backed hook was detached, its wrapper removed, or it never
        # pointed at the managed wrapper path.
        unsafe_hooks: list[str] = []
        for name in wrapper_integrations(integrations, files):
            record = integrations.get(name, {})
            wrapper_record = files.get(f"{name}_wrapper")
            wrapper_removed = remove_owned_file(f"{name}_wrapper", wrapper_record, tx)
            unowned_hook_uses_managed_path = bool(
                isinstance(record, Mapping)
                and record.get("owned") is not True
                and record.get("attached") is True
                and hook_may_use_managed_wrapper(name, record.get("value"))
            )
            hook_safe = (
                detached.get(name, False)
                or wrapper_removed
                or (
                    not isinstance(wrapper_record, Mapping)
                    and not unowned_hook_uses_managed_path
                )
            )
            if not hook_safe:
                unsafe_hooks.append(integration_display_name(name))
        remove_owned_file("plist", files.get("plist"), tx)
        if not unsafe_hooks:
            remove_owned_file("installed_script", files.get("installed_script"), tx)
            for key in [key for key in files if key.startswith("plugin_module:")]:
                remove_owned_file(key, files.get(key), tx)
            package_dir = DEFAULT_ROOT / PLUGIN_PACKAGE
            if package_dir.is_dir() and not package_dir.is_symlink() and not any(package_dir.iterdir()):
                package_dir.rmdir()
        else:
            print(
                f"Preserved collector.py because an unowned {' and '.join(unsafe_hooks)} hook "
                "may still depend on it"
            )

        tx.snapshot(ownership_path())
        ownership_path().unlink()
        fsync_directory(ownership_path().parent)
        tx.cleanup()
    except Exception as error:
        tx.rollback(preserve_recovery=service_stopped)
        if service_stopped:
            try:
                launch_agent(domain)
            except Exception as recovery_error:
                raise RuntimeError(
                    f"uninstall failed ({error}); files were restored but the service "
                    f"could not be restarted ({recovery_error}); recovery artifacts: "
                    f"{tx.recovery_dir}"
                ) from error
            tx.cleanup()
        raise

    print("Service removed. Config, CSV, and logs were preserved in ~/.ai-usage/.")


def emit_progress(provider: str, index: int, total: int) -> None:
    """One JSON line per provider, for the menu app's Collect now progress."""
    print(
        json.dumps({"event": "provider", "provider": provider, "index": index, "total": total}),
        flush=True,
    )


def run_once(config_path: Path, progress: bool = False) -> int:
    config = load_config(config_path)
    usage_path, log_path = configured_paths(config)
    state_path = state_path_for(config)
    configure_logging(log_path)
    LOGGER.info("one-shot collection started version=%s", VERSION)
    with state_transaction_lock(state_path):
        state = load_state(state_path)
        state = recover_pending_transaction(state_path, usage_path, state)
        candidate_state = copy.deepcopy(state)
        local_date = datetime_module.datetime.now().date().isoformat()
        include_history = state.get("history_date") != local_date
        # Without --progress the call is unchanged from earlier versions.
        progress_argument = {"progress": emit_progress} if progress else {}
        rows = collect_snapshot(
            config,
            include_history=include_history,
            state=candidate_state,
            **progress_argument,
        )
        candidate_state["history_date"] = local_date
        commit_collection_transaction(
            state_path,
            usage_path,
            rows,
            candidate_state,
        )
    errors = sum(1 for row in rows if row.get("status") == "error")
    LOGGER.info("one-shot collection completed rows=%d errors=%d", len(rows), errors)
    print(f"Appended {len(rows)} rows to {usage_path}; errors={errors}")
    return 0 if errors == 0 else 2


SCHEDULE_TICK_ENV = "AI_USAGE_SCHEDULE_TICK_SECONDS"
DEFAULT_SCHEDULE_TICK_SECONDS = 15.0


def schedule_tick_seconds() -> float:
    """How often an idle daemon re-reads its config (to notice pause/resume).

    The environment override exists for tests; launchd never sets it."""
    try:
        value = float(os.environ.get(SCHEDULE_TICK_ENV, DEFAULT_SCHEDULE_TICK_SECONDS))
    except ValueError:
        return DEFAULT_SCHEDULE_TICK_SECONDS
    return min(max(value, 0.05), DEFAULT_SCHEDULE_TICK_SECONDS)


def schedule_decision(
    *,
    paused: bool,
    was_paused: bool,
    next_due: Optional[float],
    now: float,
    interval: int,
) -> tuple[str, Optional[float]]:
    """Decide what an idle daemon does on one tick.

    Returns (action, next_due). Actions: "pause" (paused just now), "paused"
    (still paused), "resume" (pause cleared just now; next check one interval
    later), "wait" (not due yet) or "collect".
    """
    if paused:
        return ("paused" if was_paused else "pause"), next_due
    if was_paused:
        return "resume", now + interval
    if next_due is not None and now < next_due:
        return "wait", next_due
    return "collect", next_due


def schedule_wait_seconds(action: str, next_due: Optional[float], now: float, tick: float) -> float:
    """How long an idle daemon sleeps before re-reading its config.

    Always positive and at most `tick`, so pause/resume and shutdown stay
    responsive. While paused the collection deadline is irrelevant: an
    expired deadline must not turn the pause into a zero-wait loop.
    """
    if action in ("pause", "paused") or next_due is None:
        return tick
    return min(tick, max(next_due - now, 0.01))


def run_daemon(config_path: Path) -> int:
    stop_event = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        del frame
        LOGGER.info("stop requested signal=%d", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    current_log_path: Optional[Path] = None
    first_cycle = True
    was_paused = False
    next_due: Optional[float] = None
    tick = schedule_tick_seconds()

    while not stop_event.is_set():
        try:
            config = load_config(config_path)
            usage_path, log_path = configured_paths(config)
            state_path = state_path_for(config)
            if current_log_path != log_path:
                configure_logging(log_path)
                current_log_path = log_path
                LOGGER.info("collector daemon started version=%s pid=%d", VERSION, os.getpid())
            interval = int(config["poll_interval_seconds"])
            if first_cycle and config.get("poll_on_start", True) is not True:
                first_cycle = False
                next_due = time.monotonic() + interval
                LOGGER.info("initial poll deferred interval_seconds=%d", interval)
            action, next_due = schedule_decision(
                paused=config.get("poll_paused") is True,
                was_paused=was_paused,
                next_due=next_due,
                now=time.monotonic(),
                interval=interval,
            )
            if action == "pause":
                # Scheduled checks stop; the daemon, Collect now (`once`) and
                # event-driven collection are unaffected.
                was_paused = True
                first_cycle = False
                LOGGER.info("scheduled checks paused; collector keeps running")
            if action == "resume":
                was_paused = False
                LOGGER.info("scheduled checks resumed next_poll_seconds=%d", interval)
            if action != "collect":
                stop_event.wait(schedule_wait_seconds(action, next_due, time.monotonic(), tick))
                continue

            with state_transaction_lock(state_path):
                state = load_state(state_path)
                state = recover_pending_transaction(state_path, usage_path, state)
                candidate_state = copy.deepcopy(state)
                local_date = datetime_module.datetime.now().date().isoformat()
                include_history = state.get("history_date") != local_date
                rows = collect_snapshot(
                    config,
                    include_history=include_history,
                    state=candidate_state,
                )
                candidate_state["history_date"] = local_date
                commit_collection_transaction(
                    state_path,
                    usage_path,
                    rows,
                    candidate_state,
                )
            first_cycle = False
            next_due = time.monotonic() + interval
            errors = sum(1 for row in rows if row.get("status") == "error")
            detected = sum(
                1
                for row in rows
                if row.get("category") == "availability"
                and row.get("metric") == "detected"
                and str(row.get("value")) == "1"
            )
            LOGGER.info(
                "collection completed rows=%d detected_providers=%d errors=%d next_poll_seconds=%d",
                len(rows),
                detected,
                errors,
                interval,
            )
        except Exception as error:
            if not LOGGER.handlers:
                fallback_log = DEFAULT_ROOT / "collector.log"
                configure_logging(fallback_log)
            LOGGER.exception("collection cycle failed: %s", error)
            stop_event.wait(60)

    LOGGER.info("collector daemon stopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect AI usage without generating model traffic."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_config_argument(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--config",
            type=expand_path,
            default=DEFAULT_CONFIG_PATH,
            help="config path (default: ~/.ai-usage/config.json)",
        )

    install_parser = subparsers.add_parser(
        "install", help="install and start the macOS LaunchAgent"
    )
    add_config_argument(install_parser)
    install_parser.add_argument(
        "--no-start",
        action="store_true",
        help="write files and plist without invoking launchctl",
    )

    once_parser = subparsers.add_parser("once", help="collect one snapshot")
    add_config_argument(once_parser)
    once_parser.add_argument(
        "--progress",
        action="store_true",
        help="print one JSON line per provider as it is read",
    )

    daemon_parser = subparsers.add_parser(
        "daemon", help="run the polling loop (normally launched by launchd)"
    )
    add_config_argument(daemon_parser)

    for plugin in PLUGINS.values():
        if plugin.integration is not None and plugin.integration.wrapper_command:
            status_line_parser = subparsers.add_parser(
                plugin.integration.wrapper_command,
                help=plugin.integration.wrapper_help,
            )
            add_config_argument(status_line_parser)

    doctor_parser = subparsers.add_parser(
        "doctor", help="discover providers without executing them"
    )
    add_config_argument(doctor_parser)
    doctor_parser.add_argument("--json", action="store_true")

    subparsers.add_parser("status", help="show LaunchAgent status")
    service_status_parser = subparsers.add_parser(
        "service-status", help="LaunchAgent state as JSON (installed, state, pid, disabled)"
    )
    service_status_parser.add_argument("--json", action="store_true")
    subparsers.add_parser("start", help="re-enable and start the LaunchAgent")
    subparsers.add_parser(
        "stop", help="stop and disable the LaunchAgent (stays stopped at login; data kept)"
    )
    configure_parser = subparsers.add_parser(
        "configure", help="atomically change menu-app settings in config.json"
    )
    add_config_argument(configure_parser)
    configure_parser.add_argument(
        "--set", action="append", default=[], metavar="KEY=JSON",
        help="poll_paused, poll_interval_seconds, providers.<name>.enabled|monthly_subscription_usd",
    )
    uninstall_parser = subparsers.add_parser(
        "uninstall", help="remove the service but preserve config and data"
    )
    add_config_argument(uninstall_parser)
    subparsers.add_parser("version", help="print collector version")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "install":
            install_service(arguments.config, arguments.no_start)
            return 0
        if arguments.command == "once":
            return run_once(arguments.config, progress=arguments.progress)
        if arguments.command == "service-status":
            print(json.dumps(service_status()))
            return 0
        if arguments.command == "start":
            print(json.dumps(start_service()))
            return 0
        if arguments.command == "stop":
            print(json.dumps(stop_service()))
            return 0
        if arguments.command == "configure":
            print(json.dumps(configure(arguments.config, arguments.set)))
            return 0
        if arguments.command == "daemon":
            return run_daemon(arguments.config)
        status_plugin = status_line_plugin(arguments.command)
        if status_plugin is not None:
            return run_status_line(status_plugin, arguments.config)
        if arguments.command == "doctor":
            config = load_config(arguments.config)
            print_doctor(config, arguments.json)
            return 0
        if arguments.command == "status":
            return print_status()
        if arguments.command == "uninstall":
            uninstall_service(arguments.config)
            return 0
        if arguments.command == "version":
            print(VERSION)
            return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"ai-usage: {error}", file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
