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


VERSION = "2.0.0"
SERVICE_LABEL = "codes.porta.ai-usage"
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

COMMON_EXECUTABLE_DIRS = [
    "~/.local/bin",
    "~/.claude/local",
    "~/.grok/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.volta/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]


DEFAULT_CONFIG: dict[str, Any] = {
    "format_version": 1,
    "poll_interval_seconds": 3600,
    "poll_on_start": True,
    "paths": {
        "usage_csv": "~/.ai-usage/usage.csv",
        "log_file": "~/.ai-usage/collector.log",
        "state_file": "~/.ai-usage/state.json",
        "cache_dir": "~/.ai-usage/cache",
    },
    "providers": {
        "codex": {
            "enabled": True,
            "executable": None,
            "executable_names": ["codex"],
            "timeout_seconds": 30,
            "monthly_subscription_usd": None,
        },
        "claude": {
            "enabled": True,
            "executable": None,
            "executable_names": ["claude"],
            "stats_file": "~/.claude/stats-cache.json",
            "monthly_subscription_usd": None,
        },
        "antigravity": {
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
        "gemini_cli": {
            "enabled": True,
            "executable": None,
            "executable_names": ["gemini"],
            "telemetry_file": "~/.ai-usage/cache/gemini-telemetry.log",
            "settings_file": "~/.gemini/settings.json",
            "configure_telemetry": False,
            "monthly_subscription_usd": None,
        },
        "grok": {
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
    },
}


LOGGER = logging.getLogger("ai_usage")


def utc_now() -> datetime_module.datetime:
    return datetime_module.datetime.now(datetime_module.timezone.utc)


def iso_utc(value: datetime_module.datetime) -> str:
    return value.astimezone(datetime_module.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def timestamp_to_iso(value: Any) -> str:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return ""
    try:
        converted = datetime_module.datetime.fromtimestamp(
            timestamp, tz=datetime_module.timezone.utc
        )
    except (OverflowError, OSError, ValueError):
        return ""
    return iso_utc(converted)


def expand_path(value: Union[str, Path]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


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
    provider_paths = (
        ("providers.claude.stats_file", "claude", "stats_file", "file"),
        ("providers.antigravity.cache_file", "antigravity", "cache_file", "file"),
        ("providers.antigravity.events_file", "antigravity", "events_file", "file"),
        (
            "providers.antigravity.settings_file",
            "antigravity",
            "settings_file",
            "file",
        ),
        (
            "providers.gemini_cli.telemetry_file",
            "gemini_cli",
            "telemetry_file",
            "file",
        ),
        (
            "providers.gemini_cli.settings_file",
            "gemini_cli",
            "settings_file",
            "file",
        ),
        ("providers.grok.grok_home", "grok", "grok_home", "directory"),
        ("providers.grok.auth_file", "grok", "auth_file", "file"),
        (
            "providers.grok.billing_cache_file",
            "grok",
            "billing_cache_file",
            "file",
        ),
    )
    for role, provider_name, setting_name, expected_type in provider_paths:
        provider = providers.get(provider_name)
        if not isinstance(provider, Mapping):
            raise ValueError(f"providers.{provider_name} must be an object")
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


def atomic_write_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        fsync_directory(path.parent)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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


@contextmanager
def exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a", encoding="utf-8") as handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def discovery_path() -> str:
    candidates = list(filter(None, os.environ.get("PATH", "").split(os.pathsep)))
    for raw_path in COMMON_EXECUTABLE_DIRS:
        candidates.append(str(expand_path(raw_path)))
    for pattern in (
        "~/.nvm/versions/node/*/bin",
        "~/Library/pnpm",
    ):
        candidates.extend(glob.glob(os.path.expanduser(pattern)))

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return os.pathsep.join(unique)


def is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_executable(
    provider_config: Mapping[str, Any], default_names: Iterable[str]
) -> Optional[Path]:
    override = provider_config.get("executable")
    if isinstance(override, str) and override.strip():
        candidate = expand_path(override)
        return candidate if is_executable_file(candidate) else None

    configured_names = provider_config.get("executable_names", list(default_names))
    if not isinstance(configured_names, list):
        configured_names = list(default_names)

    search_path = discovery_path()
    for name in configured_names:
        if not isinstance(name, str) or not name:
            continue
        discovered = shutil.which(name, path=search_path)
        if discovered:
            candidate = Path(discovered).resolve()
            if is_executable_file(candidate):
                return candidate
    return None


def metric_row(
    collected_at: str,
    provider: str,
    category: str,
    metric: str,
    *,
    record_kind: str = "snapshot",
    scope: str = "",
    period_start: str = "",
    period_end: str = "",
    value: Any = "",
    unit: str = "",
    window_seconds: Any = "",
    resets_at: str = "",
    source: str = "",
    status: str = "ok",
    message: str = "",
) -> dict[str, Any]:
    if isinstance(value, bool):
        value = 1 if value else 0
    elif isinstance(value, (dict, list)):
        value = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return {
        "collected_at": collected_at,
        "provider": provider,
        "category": category,
        "metric": metric,
        "record_kind": record_kind,
        "scope": scope,
        "period_start": period_start,
        "period_end": period_end,
        "value": value,
        "unit": unit,
        "window_seconds": window_seconds,
        "resets_at": resets_at,
        "source": source,
        "status": status,
        "message": message,
    }


def availability_row(
    collected_at: str,
    provider: str,
    detected: bool,
    source: str,
    message: str,
) -> dict[str, Any]:
    return metric_row(
        collected_at,
        provider,
        "availability",
        "detected",
        value=detected,
        unit="boolean",
        source=source,
        status="ok" if detected else "missing",
        message=message,
    )


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


def snake_case(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    return value.strip("_").lower()


def stable_scope(value: Any) -> str:
    encoded = str(value).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()[:16]


def scalar_leaves(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from scalar_leaves(child, child_prefix)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}.{index}" if prefix else str(index)
            yield from scalar_leaves(child, child_prefix)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        yield prefix, value


def read_new_json_lines(
    path: Path,
    offset_state: MutableMapping[str, Any],
    *,
    max_bytes: int = 50 * 1024 * 1024,
) -> tuple[list[dict[str, Any]], int, int, list[str]]:
    """Read JSONL increments while draining renamed inodes before replacements."""

    key = str(path)
    try:
        file_stat = path.stat()
    except OSError:
        return [], 0, 0, []
    raw_state = offset_state.get(key, 0)
    pending_inodes: list[dict[str, int]] = []
    if isinstance(raw_state, Mapping):
        raw_offset = raw_state.get("offset", 0)
        previous_inode = raw_state.get("inode")
        raw_pending = raw_state.get("pending_inodes", [])
        if isinstance(raw_pending, list):
            for item in raw_pending:
                if not isinstance(item, Mapping):
                    continue
                try:
                    pending_inode = int(item.get("inode"))
                    pending_offset = max(0, int(item.get("offset", 0)))
                except (TypeError, ValueError):
                    continue
                pending_inodes.append(
                    {"inode": pending_inode, "offset": pending_offset}
                )
        if previous_inode is not None and previous_inode != file_stat.st_ino:
            try:
                previous_inode_value = int(previous_inode)
                previous_offset_value = max(0, int(raw_offset))
            except (TypeError, ValueError):
                previous_inode_value = 0
                previous_offset_value = 0
            if previous_inode_value and not any(
                item["inode"] == previous_inode_value for item in pending_inodes
            ):
                pending_inodes.append(
                    {
                        "inode": previous_inode_value,
                        "offset": previous_offset_value,
                    }
                )
            raw_offset = 0
    else:
        raw_offset = raw_state
    try:
        offset = int(raw_offset)
    except (TypeError, ValueError):
        offset = 0
    losses: list[str] = []
    if offset < 0 or offset > file_stat.st_size:
        if offset > file_stat.st_size:
            losses.append(
                f"inode {file_stat.st_ino} was truncated before offset {offset} could be read"
            )
        offset = 0

    records: list[dict[str, Any]] = []
    parse_errors = 0
    bytes_read = 0
    remaining = max(0, max_bytes)
    still_pending: list[dict[str, int]] = []
    for pending_index, pending in enumerate(pending_inodes):
        old_path = find_file_by_inode(path.parent, pending["inode"])
        if old_path is None:
            losses.append(
                f"rotated inode {pending['inode']} disappeared before unread offset "
                f"{pending['offset']} was drained"
            )
            continue
        budget_before = remaining
        old_records, old_errors, new_old_offset, consumed, old_size = read_json_lines_at(
            old_path,
            pending["offset"],
            remaining,
        )
        records.extend(old_records)
        parse_errors += old_errors
        bytes_read += consumed
        remaining = max(0, remaining - consumed)
        if new_old_offset < old_size:
            if consumed < budget_before:
                losses.append(
                    f"rotated inode {pending['inode']} ended with an incomplete JSONL record"
                )
                continue
            still_pending.append(
                {"inode": pending["inode"], "offset": new_old_offset}
            )
            still_pending.extend(pending_inodes[pending_index + 1 :])
            break
        if remaining <= 0:
            still_pending.extend(pending_inodes[pending_index + 1 :])
            break

    new_offset = offset
    if not still_pending and remaining > 0:
        current_records, current_errors, new_offset, consumed, _current_size = (
            read_json_lines_at(path, offset, remaining)
        )
        records.extend(current_records)
        parse_errors += current_errors
        bytes_read += consumed
    offset_state[key] = {
        "offset": new_offset,
        "inode": file_stat.st_ino,
        "pending_inodes": still_pending,
    }
    return records, parse_errors, bytes_read, losses


def find_file_by_inode(directory: Path, inode: int) -> Optional[Path]:
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return None
    for entry in entries:
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(entry_stat.st_mode) and entry_stat.st_ino == inode:
            return Path(entry.path)
    return None


def read_json_lines_at(
    path: Path, offset: int, max_bytes: int
) -> tuple[list[dict[str, Any]], int, int, int, int]:
    records: list[dict[str, Any]] = []
    parse_errors = 0
    with path.open("rb") as handle:
        file_size = os.fstat(handle.fileno()).st_size
        safe_offset = offset if 0 <= offset <= file_size else 0
        handle.seek(safe_offset)
        consumed = 0
        while consumed < max_bytes:
            line_start = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                handle.seek(line_start)
                break
            consumed += len(line)
            try:
                decoded = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parse_errors += 1
                continue
            if isinstance(decoded, dict):
                records.append(decoded)
        new_offset = handle.tell()
    return records, parse_errors, new_offset, new_offset - safe_offset, file_size


def rotation_loss_row(
    collected_at: str, provider: str, source: str, losses: list[str]
) -> Optional[dict[str, Any]]:
    if not losses:
        return None
    return metric_row(
        collected_at,
        provider,
        "collection",
        "rotation_data_loss",
        record_kind="interval_total",
        value=len(losses),
        unit="files",
        source=source,
        status="error",
        message="; ".join(losses),
    )


def record_timestamp(record: Mapping[str, Any], fallback: str) -> str:
    timestamp_names = {
        "timestamp",
        "time",
        "event_time",
        "created_at",
        "time_unix_nano",
        "observed_time_unix_nano",
        "timestamp_ms",
        "time_unix_ms",
    }
    for path, value in scalar_leaves(record):
        leaf = snake_case(path.rsplit(".", 1)[-1])
        if leaf not in timestamp_names:
            continue
        if isinstance(value, str) and value:
            if value.endswith("Z") or "T" in value:
                return value
            try:
                numeric = float(value)
            except ValueError:
                continue
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
        else:
            continue
        magnitude = abs(numeric)
        if magnitude >= 1e17:
            numeric /= 1e9
        elif magnitude >= 1e14:
            numeric /= 1e6
        elif magnitude >= 1e11:
            numeric /= 1e3
        converted = timestamp_to_iso(numeric)
        if converted:
            return converted
    return fallback


def state_value_changed(
    state: MutableMapping[str, Any],
    state_key: str,
    scope: str,
    value: Any,
    *,
    max_entries: int = 1000,
) -> bool:
    raw_values = state.setdefault(state_key, {})
    if not isinstance(raw_values, MutableMapping):
        raw_values = {}
        state[state_key] = raw_values
    changed = raw_values.get(scope) != value
    raw_values[scope] = value
    while len(raw_values) > max_entries:
        first_key = next(iter(raw_values))
        del raw_values[first_key]
    return changed


def response_error(envelope: Any, fallback: str) -> str:
    if isinstance(envelope, Mapping):
        error = envelope.get("error")
        if isinstance(error, Mapping):
            code = error.get("code")
            message = error.get("message")
            if message:
                return f"{code}: {message}" if code is not None else str(message)
    return fallback


def start_json_line_reader(
    stream: Any,
) -> "queue.Queue[tuple[bool, Optional[dict[str, Any]]]]":
    messages: "queue.Queue[tuple[bool, Optional[dict[str, Any]]]]" = queue.Queue()

    def read_lines() -> None:
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    messages.put((True, message))
        finally:
            messages.put((False, None))

    threading.Thread(target=read_lines, daemon=True).start()
    return messages


def next_json_message(
    messages: "queue.Queue[tuple[bool, Optional[dict[str, Any]]]]",
    deadline: float,
) -> Optional[dict[str, Any]]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        has_message, message = messages.get(timeout=min(0.5, remaining))
    except queue.Empty:
        return {}
    return message if has_message else None


def terminate_provider_process(
    process: "subprocess.Popen[str]", process_group: Optional[int]
) -> None:
    """Bound cleanup and terminate descendants that inherited provider pipes."""

    if process_group is not None and os.name == "posix":
        def signal_group(provider_signal: int) -> bool:
            try:
                os.killpg(process_group, provider_signal)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                # Darwin can report EPERM while an exited group leader remains
                # unreaped.  Reap our direct child and retry the group once.
                if process.poll() is None:
                    raise
                try:
                    os.killpg(process_group, provider_signal)
                    return True
                except ProcessLookupError:
                    return False

        # Reap a provider that exited on its own before signalling descendants.
        process.poll()
        signal_group(signal.SIGTERM)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if not signal_group(0):
                break
            time.sleep(0.02)
        else:
            signal_group(signal.SIGKILL)

        if process.poll() is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                signal_group(signal.SIGKILL)
                process.wait(timeout=1)
        return

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def query_codex_app_server(
    executable: Path, timeout_seconds: int
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
                        "version": VERSION,
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
) -> list[dict[str, Any]]:
    del include_history
    rows: list[dict[str, Any]] = []
    executable = find_executable(provider_config, ["codex"])
    if executable is None:
        rows.append(
            availability_row(
                collected_at,
                "codex",
                False,
                "filesystem-discovery",
                "Codex executable not found; no Codex command was run",
            )
        )
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
        usage_envelope, limits_envelope = query_codex_app_server(executable, timeout)
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
    detected = executable is not None or stats_available

    details = []
    if executable is not None:
        details.append(f"binary={executable}")
    if stats_available:
        details.append(f"stats={stats_path}")
    rows.append(
        availability_row(
            collected_at,
            "claude",
            detected,
            "filesystem-discovery",
            "; ".join(details)
            if details
            else "Claude executable and stats cache not found; no Claude command was run",
        )
    )

    if not stats_available:
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
        return rows

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
        return rows

    if not isinstance(stats, Mapping):
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
        return rows

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

    rows.append(
        metric_row(
            collected_at,
            "claude",
            "quota",
            "personal_subscription_quota",
            source="unsupported",
            status="unsupported",
            message=(
                "No supported personal Claude Max quota API; no private "
                "endpoint or TUI was invoked"
            ),
        )
    )
    return rows


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


def log_antigravity_statusline_error(config_path: Path, error: Exception) -> None:
    try:
        log_path = DEFAULT_ROOT / "collector.log"
        try:
            config = load_config(config_path)
            _, log_path = configured_paths(config)
        except Exception:
            pass
        configure_logging(log_path)
        LOGGER.error(
            "Antigravity status-line capture failed error_type=%s message=%s",
            type(error).__name__,
            str(error)[:500],
        )
    except Exception:
        print(
            f"ai-usage Antigravity capture failed: {type(error).__name__}",
            file=sys.stderr,
        )


def run_antigravity_statusline(config_path: Path) -> int:
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("status payload exceeded 2 MiB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("status payload was not an object")
        try:
            config = load_config(config_path)
            settings = provider_config(config, "antigravity")
            cache_setting = settings.get(
                "cache_file", "~/.ai-usage/cache/antigravity.json"
            )
            events_setting = settings.get(
                "events_file", "~/.ai-usage/cache/antigravity-events.jsonl"
            )
        except (OSError, ValueError, json.JSONDecodeError):
            cache_setting = "~/.ai-usage/cache/antigravity.json"
            events_setting = "~/.ai-usage/cache/antigravity-events.jsonl"
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
        print(format_antigravity_status(payload))
        return 0
    except Exception as error:
        # Stdout is Antigravity's UI channel. Never print diagnostics there.
        log_antigravity_statusline_error(config_path, error)
        print("Antigravity")
        return 0


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
    rows.append(
        availability_row(
            collected_at,
            "antigravity",
            detected,
            "filesystem-discovery",
            "; ".join(details)
            if details
            else "agy and its status cache were not found; no command was run",
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
    rows = [
        availability_row(
            collected_at,
            "gemini_cli",
            detected,
            "filesystem-discovery",
            "; ".join(details)
            if details
            else "Gemini CLI and telemetry file were not found; no command was run",
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


def query_grok_billing(executable: Path, timeout_seconds: int) -> Any:
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
                        "clientVersion": VERSION,
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
                        "method": "x.ai/billing",
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


def parse_iso_timestamp(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return numeric
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime_module.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_module.timezone.utc)
    return parsed.timestamp()


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
    rows = [
        availability_row(
            collected_at,
            "grok",
            detected,
            "filesystem-discovery",
            "; ".join(details)
            if details
            else "Grok CLI and session logs were not found; no command was run",
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
            billing = query_grok_billing(executable, timeout)
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


def provider_config(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    providers = config.get("providers", {})
    value = providers.get(name, {}) if isinstance(providers, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def collect_snapshot(
    config: Mapping[str, Any],
    include_history: bool,
    state: MutableMapping[str, Any],
) -> list[dict[str, Any]]:
    collected_at = iso_utc(utc_now())
    handlers: dict[
        str,
        Callable[
            [str, Mapping[str, Any], bool, MutableMapping[str, Any]],
            list[dict[str, Any]],
        ],
    ] = {
        "codex": collect_codex,
        "claude": collect_claude,
        "antigravity": collect_antigravity,
        "gemini_cli": collect_gemini_cli,
        "grok": collect_grok,
    }

    rows: list[dict[str, Any]] = []
    for name, handler in handlers.items():
        settings = provider_config(config, name)
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
            provider_rows = handler(collected_at, settings, include_history, state)
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
    definitions = {
        "codex": ["codex"],
        "claude": ["claude"],
        "antigravity": ["agy"],
        "gemini_cli": ["gemini"],
        "grok": ["grok"],
    }
    for name, executable_names in definitions.items():
        settings = provider_config(config, name)
        enabled = settings.get("enabled", True) is True
        if not enabled:
            results[name] = {
                "enabled": False,
                "detected": False,
                "executable": None,
            }
            continue
        executable = (
            find_executable(settings, executable_names) if enabled else None
        )
        extra: dict[str, Any] = {}
        if name == "claude":
            stats_setting = settings.get(
                "stats_file", "~/.claude/stats-cache.json"
            )
            stats_path = expand_path(str(stats_setting))
            extra["stats_file"] = str(stats_path)
            extra["stats_file_readable"] = (
                stats_path.is_file() and os.access(stats_path, os.R_OK)
            )
        elif name == "antigravity":
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
        elif name == "gemini_cli":
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
        elif name == "grok":
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


def antigravity_wrapper_content(config_path: Path) -> str:
    return (
        "#!/bin/sh\n"
        f"exec {shlex.quote(str(Path(sys.executable).resolve()))} "
        f"{shlex.quote(str(DEFAULT_INSTALLED_SCRIPT))} "
        "antigravity-statusline --config "
        f"{shlex.quote(str(config_path))}\n"
    )


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
    antigravity = provider_config(config, "antigravity")
    wrapper_path = DEFAULT_ROOT / "antigravity-statusline"
    antigravity_path = expand_path(
        str(antigravity.get("settings_file", "~/.gemini/antigravity-cli/settings.json"))
    )
    antigravity_plan = prepare_integration_plan(
        "antigravity",
        antigravity,
        antigravity_path,
        "statusLine",
        {"type": "command", "command": str(wrapper_path)},
        antigravity.get("enabled", True) is True
        and antigravity.get("configure_status_line", True) is True,
        find_executable(antigravity, ["agy"]),
        previous_integration_record(ownership, "antigravity"),
    )
    antigravity_plan["wrapper_path"] = wrapper_path
    antigravity_plan["wrapper_bytes"] = antigravity_wrapper_content(config_path).encode(
        "utf-8"
    )

    gemini = provider_config(config, "gemini_cli")
    gemini_path = expand_path(str(gemini.get("settings_file", "~/.gemini/settings.json")))
    gemini_plan = prepare_integration_plan(
        "gemini_cli",
        gemini,
        gemini_path,
        "telemetry",
        desired_gemini_telemetry(gemini),
        gemini.get("enabled", True) is True
        and gemini.get("configure_telemetry", True) is True,
        find_executable(gemini, ["gemini"]),
        previous_integration_record(ownership, "gemini_cli"),
    )
    return {"antigravity": antigravity_plan, "gemini_cli": gemini_plan}


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
    name = str(plan["name"])
    if plan.get("write") is True:
        settings_path = Path(plan["settings_path"])
        document = dict(plan["document"])
        document[str(plan["field"])] = plan["value"]
        tx.snapshot(settings_path)
        tx.ensure_directory(settings_path.parent)
        write_json_object(settings_path, document)
    if name == "gemini_cli" and plan.get("owned") is True:
        telemetry_path = expand_path(str(plan["value"]["outfile"]))
        tx.ensure_directory(telemetry_path.parent)

    status = str(plan["status"])
    if name == "antigravity":
        if status == "configured":
            print(f"Configured Antigravity status-line capture: {plan['settings_path']}")
        elif status in {"preexisting identical", "managed"}:
            print(f"Preserved Antigravity statusLine ({status}): {plan['settings_path']}")
        elif status == "missing executable":
            print("Skipped Antigravity integration: agy was not found")
        elif status != "disabled":
            print(f"Preserved Antigravity settings ({status}): {plan['settings_path']}")
    else:
        if status == "configured":
            print(f"Configured local Gemini CLI telemetry: {plan['settings_path']}")
        elif status in {"preexisting identical", "managed"}:
            print(f"Preserved Gemini CLI telemetry ({status}): {plan['settings_path']}")
        elif status == "missing executable":
            print("Skipped Gemini CLI integration: gemini was not found")
        elif status != "disabled":
            print(f"Preserved Gemini CLI settings ({status}): {plan['settings_path']}")


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


def find_launchctl() -> Optional[Path]:
    for candidate in (Path("/bin/launchctl"), Path("/usr/bin/launchctl")):
        if is_executable_file(candidate):
            return candidate
    return None


def run_launchctl(arguments: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    launchctl = find_launchctl()
    if launchctl is None:
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
    bootstrap = run_launchctl(
        ["bootstrap", domain, str(DEFAULT_PLIST_PATH)], check=False
    )
    if bootstrap.returncode != 0:
        run_launchctl(["load", "-w", str(DEFAULT_PLIST_PATH)], check=True)
    run_launchctl(["enable", f"{domain}/{SERVICE_LABEL}"], check=True)
    run_launchctl(["kickstart", "-k", f"{domain}/{SERVICE_LABEL}"], check=True)


def install_service(config_path: Path, no_start: bool) -> None:
    config_path = config_path.resolve(strict=False)
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
    python_executable = Path(sys.executable).resolve()
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
    antigravity_plan = integration_plans["antigravity"]
    if antigravity_plan.get("owned") is True:
        file_plans["antigravity_wrapper"] = prepare_managed_file(
            "antigravity_wrapper",
            Path(antigravity_plan["wrapper_path"]),
            bytes(antigravity_plan["wrapper_bytes"]),
            0o700,
            previous_ownership,
        )

    domain = f"gui/{os.getuid()}"
    service_loaded = False
    if not no_start:
        service_loaded = (
            run_launchctl(["print", f"{domain}/{SERVICE_LABEL}"], check=False).returncode
            == 0
        )

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

        for key in ("installed_script",):
            apply_managed_file(file_plans[key], tx)
        print_doctor(config)
        if "antigravity_wrapper" in file_plans:
            apply_managed_file(file_plans["antigravity_wrapper"], tx)
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
            raise RuntimeError(
                f"installation failed ({error}) and the new service could not be "
                f"stopped safely; installed files and recovery artifacts were preserved at "
                f"{tx.recovery_dir}"
            ) from error
        tx.rollback(preserve_recovery=service_stopped)
        if service_stopped:
            try:
                launch_agent(domain)
            except Exception as recovery_error:
                raise RuntimeError(
                    f"installation failed ({error}); files were restored but the prior "
                    f"service could not be restarted ({recovery_error}); recovery artifacts: "
                    f"{tx.recovery_dir}"
                ) from error
            tx.cleanup()
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
        antigravity_record = integrations.get("antigravity", {})
        gemini_record = integrations.get("gemini_cli", {})
        antigravity_detached = detach_owned_integration(
            "antigravity", antigravity_record, tx
        )
        detach_owned_integration("gemini_cli", gemini_record, tx)

        files = ownership["files"]
        wrapper_record = files.get("antigravity_wrapper")
        wrapper_removed = remove_owned_file(
            "antigravity_wrapper", wrapper_record, tx
        )
        unowned_hook_uses_managed_path = bool(
            isinstance(antigravity_record, Mapping)
            and antigravity_record.get("owned") is not True
            and antigravity_record.get("attached") is True
            and antigravity_record.get("value")
            == {
                "type": "command",
                "command": str(DEFAULT_ROOT / "antigravity-statusline"),
            }
        )
        script_safe = (
            antigravity_detached
            or wrapper_removed
            or (
                not isinstance(wrapper_record, Mapping)
                and not unowned_hook_uses_managed_path
            )
        )
        remove_owned_file("plist", files.get("plist"), tx)
        if script_safe:
            remove_owned_file("installed_script", files.get("installed_script"), tx)
        else:
            print(
                "Preserved collector.py because an unowned Antigravity hook may still depend on it"
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


def run_once(config_path: Path) -> int:
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
    errors = sum(1 for row in rows if row.get("status") == "error")
    LOGGER.info("one-shot collection completed rows=%d errors=%d", len(rows), errors)
    print(f"Appended {len(rows)} rows to {usage_path}; errors={errors}")
    return 0 if errors == 0 else 2


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
                LOGGER.info("initial poll deferred interval_seconds=%d", interval)
                stop_event.wait(interval)
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
            stop_event.wait(interval)
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

    daemon_parser = subparsers.add_parser(
        "daemon", help="run the polling loop (normally launched by launchd)"
    )
    add_config_argument(daemon_parser)

    antigravity_parser = subparsers.add_parser(
        "antigravity-statusline",
        help="accept one Antigravity status-line payload on stdin",
    )
    add_config_argument(antigravity_parser)

    doctor_parser = subparsers.add_parser(
        "doctor", help="discover providers without executing them"
    )
    add_config_argument(doctor_parser)
    doctor_parser.add_argument("--json", action="store_true")

    subparsers.add_parser("status", help="show LaunchAgent status")
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
            return run_once(arguments.config)
        if arguments.command == "daemon":
            return run_daemon(arguments.config)
        if arguments.command == "antigravity-statusline":
            return run_antigravity_statusline(arguments.config)
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
