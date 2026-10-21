"""Shared helpers for the built-in provider collectors: rows, paths, executable
discovery, incremental JSONL reading and provider-process control.

Moved unchanged from ai_usage_service.py. Nothing here knows any provider.
"""

from __future__ import annotations

from contextlib import contextmanager
import datetime as datetime_module
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable, Iterator, Mapping, MutableMapping, Optional, Union


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
    pending_inodes: list[dict[str, Any]] = []
    current_partial = b""
    if isinstance(raw_state, Mapping):
        raw_offset = raw_state.get("offset", 0)
        previous_inode = raw_state.get("inode")
        current_partial = decode_jsonl_partial(raw_state.get("partial_line"))
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
                pending_entry: dict[str, Any] = {
                    "inode": pending_inode,
                    "offset": pending_offset,
                }
                pending_partial = decode_jsonl_partial(item.get("partial_line"))
                if pending_partial:
                    pending_entry["partial_line"] = encode_jsonl_partial(pending_partial)
                pending_inodes.append(pending_entry)
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
                rotated: dict[str, Any] = {
                    "inode": previous_inode_value,
                    "offset": previous_offset_value,
                }
                if current_partial:
                    rotated["partial_line"] = encode_jsonl_partial(current_partial)
                pending_inodes.append(rotated)
            raw_offset = 0
            current_partial = b""
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
        current_partial = b""

    records: list[dict[str, Any]] = []
    parse_errors = 0
    bytes_read = 0
    remaining = max(0, max_bytes)
    still_pending: list[dict[str, Any]] = []
    for pending_index, pending in enumerate(pending_inodes):
        old_path = find_file_by_inode(path.parent, pending["inode"])
        if old_path is None:
            losses.append(
                f"rotated inode {pending['inode']} disappeared before unread offset "
                f"{pending['offset']} was drained"
            )
            continue
        budget_before = remaining
        (
            old_records,
            old_errors,
            new_old_offset,
            consumed,
            old_size,
            old_leftover,
        ) = read_json_lines_at(
            old_path,
            pending["offset"],
            remaining,
            partial=decode_jsonl_partial(pending.get("partial_line")),
        )
        records.extend(old_records)
        parse_errors += old_errors
        bytes_read += consumed
        remaining = max(0, remaining - consumed)
        if old_leftover and new_old_offset >= old_size:
            losses.append(
                f"rotated inode {pending['inode']} ended with an incomplete JSONL record"
            )
            continue
        if new_old_offset < old_size:
            if consumed < budget_before and not old_leftover:
                losses.append(
                    f"rotated inode {pending['inode']} ended with an incomplete JSONL record"
                )
                continue
            pending_entry: dict[str, Any] = {
                "inode": pending["inode"],
                "offset": new_old_offset,
            }
            if old_leftover:
                pending_entry["partial_line"] = encode_jsonl_partial(old_leftover)
            still_pending.append(pending_entry)
            still_pending.extend(pending_inodes[pending_index + 1 :])
            break
        if remaining <= 0:
            still_pending.extend(pending_inodes[pending_index + 1 :])
            break

    new_offset = offset
    leftover = b""
    if not still_pending and remaining > 0:
        (
            current_records,
            current_errors,
            new_offset,
            consumed,
            _current_size,
            leftover,
        ) = read_json_lines_at(
            path,
            offset,
            remaining,
            partial=current_partial,
        )
        records.extend(current_records)
        parse_errors += current_errors
        bytes_read += consumed
    elif not still_pending:
        leftover = current_partial
    stored_cursor: dict[str, Any] = {
        "offset": new_offset,
        "inode": file_stat.st_ino,
        "pending_inodes": still_pending,
    }
    if leftover:
        stored_cursor["partial_line"] = encode_jsonl_partial(leftover)
    offset_state[key] = stored_cursor
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


def encode_jsonl_partial(data: bytes) -> str:
    return data.decode("latin-1")


def decode_jsonl_partial(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        return b""
    return value.encode("latin-1")


def read_json_lines_at(
    path: Path, offset: int, max_bytes: int, partial: bytes = b""
) -> tuple[list[dict[str, Any]], int, int, int, int, bytes]:
    records: list[dict[str, Any]] = []
    parse_errors = 0
    leftover = partial
    incoming_partial = bool(partial)
    with path.open("rb") as handle:
        file_size = os.fstat(handle.fileno()).st_size
        safe_offset = offset if 0 <= offset <= file_size else 0
        handle.seek(safe_offset)
        consumed = 0
        while consumed < max_bytes:
            remaining = max_bytes - consumed
            line_start = handle.tell()
            chunk = handle.readline(remaining)
            if not chunk:
                break
            consumed += len(chunk)
            leftover += chunk
            if leftover.endswith(b"\n"):
                try:
                    decoded = json.loads(leftover.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    parse_errors += 1
                else:
                    if isinstance(decoded, dict):
                        records.append(decoded)
                leftover = b""
                continue
            at_eof = handle.tell() >= file_size
            if at_eof and not incoming_partial and leftover == chunk:
                handle.seek(line_start)
                leftover = b""
                consumed -= len(chunk)
            break
        new_offset = handle.tell()
    return records, parse_errors, new_offset, consumed, file_size, leftover


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


def pids_in_process_group(process_group: int) -> list[int]:
    """Best-effort PIDs currently in a process group (macOS + Linux)."""

    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,pgid="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    pids: list[int] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
            pgid = int(parts[1])
        except ValueError:
            continue
        if pgid == process_group:
            pids.append(pid)
    return pids


def signal_pids(pids: Iterable[int], provider_signal: int) -> None:
    """Signal each PID independently, ignoring processes that already exited."""

    for pid in pids:
        try:
            os.kill(pid, provider_signal)
        except (ProcessLookupError, PermissionError):
            continue


def terminate_provider_process(
    process: "subprocess.Popen[str]", process_group: Optional[int]
) -> None:
    """Bound cleanup and terminate descendants that inherited provider pipes.

    Best-effort and never raises. Darwin intermittently returns EPERM from
    killpg for a live session-leader group (and for an unreaped zombie leader);
    that must not convert a successful provider response into a collection
    failure, and descendants that inherited stdio still need to be stopped.
    """

    if process_group is not None and os.name == "posix":
        def list_targets() -> list[int]:
            pids = pids_in_process_group(process_group)
            if process.poll() is None and process.pid not in pids:
                pids.append(process.pid)
            return pids

        def send_signal(provider_signal: int) -> None:
            try:
                os.killpg(process_group, provider_signal)
                return
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
            # killpg unavailable (empty group) or Darwin EPERM: hit each member.
            signal_pids(list_targets(), provider_signal)

        def group_still_alive() -> bool:
            try:
                os.killpg(process_group, 0)
                return True
            except ProcessLookupError:
                return bool(list_targets())
            except PermissionError:
                return bool(list_targets())

        # Snapshot before the first signal so members that later drop out of the
        # process-group table can still be escalated.
        known_members = list_targets()
        send_signal(signal.SIGTERM)
        # Always target the direct child as well — covers killpg EPERM while the
        # leader is still alive (a case that previously raised and failed the query).
        if process.poll() is None:
            try:
                os.kill(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass

        # After the leader is reaped, killpg often starts working again on Darwin.
        # If not, the per-PID fallback inside send_signal covers remaining members.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if not group_still_alive() and process.poll() is not None:
                break
            time.sleep(0.02)
        else:
            send_signal(signal.SIGKILL)
            signal_pids(known_members, signal.SIGKILL)
            if process.poll() is None:
                try:
                    process.kill()
                except (ProcessLookupError, PermissionError, OSError):
                    pass

        if process.poll() is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                send_signal(signal.SIGKILL)
                try:
                    process.kill()
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        return

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


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
