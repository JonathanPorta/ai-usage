"""External plugins (phase 3): an executable plus a JSON manifest in ~/.ai-usage/plugins/<id>/.

A plugin is trusted local code: it runs as the owner, with the owner's
permissions. Discovery reads manifests and stats files and never runs a plugin.
The runner bounds time and output and stops the plugin's process group, which
isolates faults. None of this is a security sandbox.

Every reason text here names a field, a path, a position, an exit status or an
errno name. None includes a rejected value, raw output or an exception message,
because those could hold whatever the plugin printed.

Importing this module defines things and does nothing else.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
import datetime as datetime_module
import errno as errno_module
import glob
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
from typing import Any, Callable, Mapping, Optional
import unicodedata

from .base import ProviderDef, SourceDef
from .support import COMMON_EXECUTABLE_DIRS, expand_path, metric_row, terminate_provider_process

MANIFEST = "plugin.json"
MANIFEST_VERSION = 1
PROTOCOL = 1
MANIFEST_MAX_BYTES = 64 * 1024
STDOUT_MAX_BYTES = 1024 * 1024
READ_CHUNK = 64 * 1024
STATE_MAX_BYTES = 256 * 1024
MAX_ROWS = 2000
MAX_SOURCES = 4
MESSAGE_MAX = 500
TEXT_MAX = 256
LABEL_MAX = 64
TIMEOUT_DEFAULT = 30
TIMEOUT_MAX = 120
# After the plugin exits, how long its stdout and stderr may stay open (a child
# holding the inherited pipes) before the collector stops reading.
PIPE_GRACE_SECONDS = 1.0
EVENT_AHEAD_SECONDS = 120
WINDOW_MAX_SECONDS = 366 * 86400

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SOURCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
CSV_SOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
UTC_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$")

# Settings the owner's config controls; a manifest can't default them, so it can
# never turn execution on.
RESERVED_SETTINGS = ("enabled", "timeout_seconds", "monthly_subscription_usd")
# Collector settings that aren't sent to the plugin.
COLLECTOR_ONLY_SETTINGS = ("enabled", "monthly_subscription_usd")
USAGE_PROFILES = ("daily_total", "events", "none")
QUOTA_PROFILES = ("used", "remaining", "none")
EVENT_METRICS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
# Copied from the collector's environment when set there. Their values can differ
# between a shell run and the launchd service.
COPIED_ENVIRONMENT = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG")
# The CSV source of the collector's own failure rows for a plugin.
FAILURE_SOURCE = "plugin"

MANIFEST_KEYS = ("manifest_version", "protocol", "id", "executable", "timeout_seconds", "default_settings", "report")
REPORT_KEYS = ("name", "product", "usage", "quota", "quota_note", "sources")
SOURCE_KEYS = ("id", "role", "label", "kind", "csv_source")
RESPONSE_KEYS = ("protocol", "rows", "state")


def plugins_root() -> Path:
    """~/.ai-usage/plugins for the current HOME."""
    return Path(os.path.expanduser("~")) / ".ai-usage" / "plugins"


def errno_name(error: OSError) -> str:
    return errno_module.errorcode.get(error.errno or 0, "error")


def has_control_characters(text: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in text)


# --------------------------------------------------------------------------- discovery

@dataclass(frozen=True)
class ExternalPlugin:
    """A valid plugin folder."""

    id: str
    directory: Path  # resolved
    executable: Path  # resolved
    timeout_seconds: int
    default_settings: Mapping[str, Any]
    usage: str
    quota: str
    report: ProviderDef


@dataclass(frozen=True)
class Discovery:
    plugins: dict[str, ExternalPlugin]
    # Folder name -> reason. "plugins" itself is keyed "" when the folder can't be used.
    invalid: dict[str, str]

    def ids(self) -> list[str]:
        return sorted(set(self.plugins) | {name for name in self.invalid if ID_PATTERN.match(name)},
                      key=lambda name: name.encode("utf-8"))


class ManifestError(ValueError):
    """A manifest or folder problem, as a fixed reason text."""


StatFunction = Callable[[Path], os.stat_result]


class Checker:
    """Owner-only checks; `uid`, `lstat` and `stat` are injectable for tests."""

    def __init__(self, uid: Optional[int] = None, lstat: Optional[StatFunction] = None,
                 stat_: Optional[StatFunction] = None) -> None:
        self.uid = os.getuid() if uid is None else uid
        self.lstat = lstat or (lambda path: os.lstat(path))
        self.stat = stat_ or (lambda path: os.stat(path))

    def owner_only(self, path: Path, label: str, kind: str, needed: str) -> None:
        """Refuse `path` unless it (and a symlink's target) is the uid's and owner-only."""
        try:
            link = self.lstat(path)
        except FileNotFoundError:
            raise ManifestError(f"{label} is missing") from None
        except OSError as error:
            raise ManifestError(f"{label} can't be read ({errno_name(error)})") from None
        target, where = link, label
        if stat.S_ISLNK(link.st_mode):
            if link.st_uid != self.uid:
                raise ManifestError(f"{label} is a symlink owned by another user")
            try:
                target = self.stat(path)
            except OSError as error:
                raise ManifestError(f"{label} is a symlink whose target can't be read ({errno_name(error)})") from None
            where = f"{label} (its symlink target)"
        if kind == "directory" and not stat.S_ISDIR(target.st_mode):
            raise ManifestError(f"{where} is not a folder")
        if kind == "file" and not stat.S_ISREG(target.st_mode):
            raise ManifestError(f"{where} is not a regular file")
        if target.st_uid != self.uid:
            raise ManifestError(f"{where} is owned by another user")
        if stat.S_IMODE(target.st_mode) & 0o077:
            raise ManifestError(
                f"{where} must be owner-only ({needed}); it has group or other permissions")

    def protected_ancestors(self, resolved: Path, label: str) -> None:
        """Refuse `resolved` (a symlink-free path) unless no other user can rename or replace
        any folder above it: each must be root's or the uid's, and not writable by group or
        others unless it is sticky (as /tmp is), where others can't move what they don't own."""
        for folder in reversed(resolved.parents):
            try:
                info = self.stat(folder)
            except OSError as error:
                raise ManifestError(f"{label}: {folder} can't be read ({errno_name(error)})") from None
            if info.st_uid not in (0, self.uid):
                raise ManifestError(f"{label}: {folder} is owned by another user")
            if stat.S_IMODE(info.st_mode) & 0o022 and not info.st_mode & stat.S_ISVTX:
                raise ManifestError(f"{label}: {folder} is writable by other users")


def discover(reserved: set[str], root: Optional[Path] = None, checker: Optional[Checker] = None) -> Discovery:
    """Every plugin folder under `root`, valid or not. Runs nothing."""
    root = plugins_root() if root is None else root
    checker = checker or Checker()
    plugins: dict[str, ExternalPlugin] = {}
    invalid: dict[str, str] = {}
    try:
        checker.lstat(root)
    except FileNotFoundError:
        return Discovery(plugins, invalid)
    except OSError as error:
        return Discovery(plugins, {"": f"plugins can't be read ({errno_name(error)})"})
    try:
        checker.owner_only(root, "plugins", "directory", "0700")
        names = os.listdir(root)
    except ManifestError as error:
        return Discovery(plugins, {"": str(error)})
    except OSError as error:
        return Discovery(plugins, {"": f"plugins can't be listed ({errno_name(error)})"})
    for name in sorted(names, key=lambda value: value.encode("utf-8", "surrogateescape")):
        if name.startswith("."):
            continue
        try:
            plugins[name] = load_plugin(root, name, reserved, checker)
        except ManifestError as error:
            invalid[name] = str(error)
        except OSError as error:  # a file changed or vanished while it was checked
            invalid[name] = f"plugins/{name} changed while it was checked ({errno_name(error)})"
    return Discovery(plugins, invalid)


def load_plugin(root: Path, name: str, reserved: set[str], checker: Checker) -> ExternalPlugin:
    folder = f"plugins/{name}"
    if not ID_PATTERN.match(name):
        raise ManifestError(f"{folder}: the folder name is not a valid plugin id")
    if name in reserved:
        raise ManifestError(f"{folder}: the id is a built-in provider's or an added account's")
    directory = root / name
    checker.owner_only(directory, folder, "directory", "0700")
    manifest_path = directory / MANIFEST
    checker.owner_only(manifest_path, f"{folder}/{MANIFEST}", "file", "0600")
    checker.protected_ancestors(manifest_path.resolve(strict=True), f"{folder}/{MANIFEST}")
    manifest = read_manifest(manifest_path, f"{folder}/{MANIFEST}")
    fields = validate_manifest(manifest, name, f"{folder}/{MANIFEST}")
    resolved_directory = directory.resolve()
    relative = Path(fields["executable"])
    executable_label = f"{folder}/{fields['executable']}"
    current = directory
    for part in relative.parts[:-1]:
        current = current / part
        checker.owner_only(current, f"{folder}/{current.relative_to(directory)}", "directory", "0700")
    candidate = directory / relative
    checker.owner_only(candidate, executable_label, "file", "0700")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_directory)
    except (OSError, RuntimeError):
        raise ManifestError(f"{executable_label} can't be resolved") from None
    except ValueError:
        raise ManifestError(f"{executable_label} resolves outside the plugin folder") from None
    if not stat.S_IMODE(checker.stat(candidate).st_mode) & stat.S_IXUSR:
        raise ManifestError(f"{executable_label} is not executable (it needs 0700)")
    # The runner starts the resolved path: no other user may be able to swap any folder on it.
    checker.protected_ancestors(resolved, executable_label)
    return ExternalPlugin(
        id=name,
        directory=resolved_directory,
        executable=resolved,
        timeout_seconds=fields["timeout_seconds"],
        default_settings=fields["default_settings"],
        usage=fields["usage"],
        quota=fields["quota"],
        report=fields["report"],
    )


def read_manifest(path: Path, label: str) -> Any:
    try:
        with path.open("rb") as handle:
            data = handle.read(MANIFEST_MAX_BYTES + 1)
    except OSError as error:
        raise ManifestError(f"{label} can't be read ({errno_name(error)})") from None
    if len(data) > MANIFEST_MAX_BYTES:
        raise ManifestError(f"{label} is larger than 64 KiB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError(f"{label} is not UTF-8") from None
    try:
        return json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise ManifestError(f"{label} is not valid JSON at line {error.lineno} column {error.colno}") from None
    except (ValueError, RecursionError):
        raise ManifestError(f"{label} is not valid JSON") from None


def reject_constant(_name: str) -> Any:
    raise ValueError("NaN and Infinity are not JSON")


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def text_field(container: Mapping[str, Any], key: str, where: str, maximum: int) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or has_control_characters(value):
        raise ManifestError(f"{where}.{key} must be a non-empty string of at most {maximum} characters")
    return value


def only_keys(container: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    if set(container) - set(allowed):
        raise ManifestError(f"{where} has an unknown key")


def validate_manifest(manifest: Any, name: str, label: str) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ManifestError(f"{label} must be a JSON object")
    only_keys(manifest, MANIFEST_KEYS, label)
    for key, expected in (("manifest_version", MANIFEST_VERSION), ("protocol", PROTOCOL)):
        if not is_int(manifest.get(key)) or manifest[key] != expected:
            raise ManifestError(f"{label}: {key} must be {expected}")
    if manifest.get("id") != name:
        raise ManifestError(f"{label}: id does not match the folder name")
    executable = manifest.get("executable")
    if (not isinstance(executable, str) or not executable or len(executable) > TEXT_MAX
            or has_control_characters(executable) or Path(executable).is_absolute()
            or any(part in ("..", "") for part in executable.split("/")) or "\\" in executable):
        raise ManifestError(f"{label}: executable must be a relative path inside the plugin folder")
    timeout = manifest.get("timeout_seconds", TIMEOUT_DEFAULT)
    if not is_int(timeout) or not 1 <= timeout <= TIMEOUT_MAX:
        raise ManifestError(f"{label}: timeout_seconds must be an integer from 1 to {TIMEOUT_MAX}")
    defaults = manifest.get("default_settings", {})
    if not isinstance(defaults, dict):
        raise ManifestError(f"{label}: default_settings must be an object")
    for key, value in defaults.items():
        if not isinstance(key, str) or not key or len(key) > LABEL_MAX or has_control_characters(key):
            raise ManifestError(f"{label}: default_settings has an invalid key")
        if key in RESERVED_SETTINGS:
            raise ManifestError(f"{label}: default_settings may not set enabled, timeout_seconds "
                                "or monthly_subscription_usd")
        if not (value is None or isinstance(value, (str, bool)) or is_number(value)):
            raise ManifestError(f"{label}: default_settings values must be strings, numbers, booleans or null")
    report = manifest.get("report")
    where = f"{label}: report"
    if not isinstance(report, dict):
        raise ManifestError(f"{where} must be an object")
    only_keys(report, REPORT_KEYS, where)
    display = text_field(report, "name", where, LABEL_MAX)
    product = text_field(report, "product", where, LABEL_MAX)
    usage, quota = report.get("usage"), report.get("quota")
    if usage not in USAGE_PROFILES:
        raise ManifestError(f"{where}.usage is not an allowed value")
    if quota not in QUOTA_PROFILES:
        raise ManifestError(f"{where}.quota is not an allowed value")
    quota_note = report.get("quota_note")
    if quota_note is not None:
        quota_note = text_field(report, "quota_note", where, TEXT_MAX)
    sources = report.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ManifestError(f"{where}.sources must list 1 to {MAX_SOURCES} sources")
    definitions: list[SourceDef] = []
    seen_ids: set[str] = set()
    seen_csv: set[str] = set()
    for index, source in enumerate(sources):
        at = f"{where}.sources[{index}]"
        if not isinstance(source, dict):
            raise ManifestError(f"{at} must be an object")
        only_keys(source, SOURCE_KEYS, at)
        source_id, csv_source = source.get("id"), source.get("csv_source")
        if not isinstance(source_id, str) or not SOURCE_ID_PATTERN.match(source_id) or source_id in seen_ids:
            raise ManifestError(f"{at}.id must be a unique lowercase id")
        if not isinstance(csv_source, str) or not CSV_SOURCE_PATTERN.match(csv_source) or csv_source in seen_csv:
            raise ManifestError(f"{at}.csv_source must be a unique lowercase name")
        if csv_source == FAILURE_SOURCE:
            raise ManifestError(f"{at}.csv_source is reserved for the collector")
        role = source.get("role")
        if role not in ("usage", "quota"):
            raise ManifestError(f"{at}.role is not an allowed value")
        if (role == "usage" and usage == "none") or (role == "quota" and quota == "none"):
            raise ManifestError(f"{at}.role has no profile: report.{role} is none")
        seen_ids.add(source_id)
        seen_csv.add(csv_source)
        definitions.append(SourceDef(
            source_id, role, text_field(source, "label", at, LABEL_MAX), text_field(source, "kind", at, 128),
            [csv_source], needs_rows=True, error_metrics=[f"{source_id}_error", "unexpected_error"]))
    for role, profile in (("usage", usage), ("quota", quota)):
        if profile != "none" and not any(source.role == role for source in definitions):
            raise ManifestError(f"{where}.sources needs a {role} source for report.{role}")
    mode, split, record_kind = {
        "daily_total": ("polled", "total_only", "period_total"),
        "events": ("event", "input_output", "event_total"),
        "none": ("polled", "total_only", "period_total"),
    }[usage]
    definition = ProviderDef(
        name, display, product, mode=mode, split=split, record_kind=record_kind,
        quota="unsupported" if quota == "none" else "polled", sources=definitions,
        reported_note=f"{display} does not report a usage cost.", quota_note=quota_note,
        quota_basis=None if quota == "none" else quota, external=True,
    )
    return {"executable": executable, "timeout_seconds": timeout, "default_settings": dict(defaults),
            "usage": usage, "quota": quota, "report": definition}


def placeholder_report(plugin_id: str) -> ProviderDef:
    """The report entry for a configured id whose folder is missing or invalid."""
    return ProviderDef(
        plugin_id, plugin_id, "Local plugin", mode="polled", split="total_only", record_kind="period_total",
        quota="unsupported", sources=[SourceDef(
            "plugin", "usage", "Plugin", "Local plugin", [FAILURE_SOURCE], needs_rows=True,
            error_metrics=["unexpected_error"])],
        reported_note=None, external=True,
    )


# --------------------------------------------------------------------------- running

class PluginFailure(Exception):
    """A failed run, as a fixed reason text."""


@dataclass
class RunInfo:
    exit: str = "not started"
    duration: float = 0.0
    stderr_bytes: int = 0  # counted; none of them is kept
    stdout_peak: int = 0  # the most stdout ever buffered
    largest_read: int = 0  # the largest single read from either pipe


@dataclass
class Outcome:
    rows: list[dict[str, Any]]
    state: Optional[dict[str, Any]]  # None: keep the stored state
    info: RunInfo


def plugin_environment(plugin: ExternalPlugin) -> dict[str, str]:
    environment = {key: os.environ[key] for key in COPIED_ENVIRONMENT if key in os.environ}
    environment["PATH"] = plugin_path()
    environment["AI_USAGE_PLUGIN_DIR"] = str(plugin.directory)
    environment["AI_USAGE_PROTOCOL"] = str(PROTOCOL)
    return environment


def plugin_path() -> str:
    """The collector's executable-discovery folders (support.discovery_path) without the
    environment's own PATH, so a plugin gets the same PATH from a shell and from launchd."""
    candidates = [str(expand_path(raw_path)) for raw_path in COMMON_EXECUTABLE_DIRS]
    for pattern in ("~/.nvm/versions/node/*/bin", "~/Library/pnpm"):
        candidates.extend(sorted(glob.glob(os.path.expanduser(pattern))))
    return os.pathsep.join(dict.fromkeys(candidates))


def run_plugin(plugin: ExternalPlugin, payload: bytes, timeout_seconds: int, info: RunInfo) -> bytes:
    """Run `<executable> collect` once and return its stdout, or raise PluginFailure.

    One deadline covers the whole run. stderr is read in fixed chunks and
    discarded. After the plugin exits, its pipes may stay open for at most
    PIPE_GRACE_SECONDS. The process group is stopped after every run.
    """
    started = time.monotonic()
    deadline = started + timeout_seconds
    try:
        process = subprocess.Popen(
            [str(plugin.executable), "collect"],
            cwd=str(plugin.directory),
            env=plugin_environment(plugin),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError as error:
        raise PluginFailure(f"could not be started ({errno_name(error)})") from None
    # start_new_session makes the plugin a session leader: its PID is its process group.
    process_group = process.pid if os.name == "posix" else None
    stdout = bytearray()
    reason: Optional[str] = None
    selector = selectors.DefaultSelector()
    streams = [stream for stream in (process.stdin, process.stdout, process.stderr) if stream is not None]
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        selector.register(process.stdin, selectors.EVENT_WRITE, "in")
        selector.register(process.stdout, selectors.EVENT_READ, "out")
        selector.register(process.stderr, selectors.EVENT_READ, "err")
        offset = 0
        exited_at: Optional[float] = None
        while selector.get_map():
            now = time.monotonic()
            if exited_at is None and process.poll() is not None:
                exited_at = now
            limit = deadline if exited_at is None else min(deadline, exited_at + PIPE_GRACE_SECONDS)
            if now >= limit:
                if exited_at is None:
                    reason = f"timed out after {timeout_seconds}s"
                break
            for key, _mask in selector.select(timeout=min(limit - now, 0.05)):
                stream = key.fileobj
                if key.data == "in":
                    try:
                        offset += os.write(stream.fileno(), payload[offset:offset + READ_CHUNK])  # type: ignore[union-attr]
                    except BlockingIOError:
                        continue
                    except OSError:  # the plugin closed stdin or exited
                        offset = len(payload)
                    if offset >= len(payload):
                        selector.unregister(stream)
                        stream.close()  # type: ignore[union-attr]
                    continue
                try:
                    chunk = os.read(stream.fileno(), READ_CHUNK)  # type: ignore[union-attr]
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                info.largest_read = max(info.largest_read, len(chunk))
                if not chunk:
                    selector.unregister(stream)
                elif key.data == "out":
                    stdout.extend(chunk)
                    info.stdout_peak = len(stdout)
                    if len(stdout) > STDOUT_MAX_BYTES:
                        reason = "wrote more than 1 MiB to stdout"
                        break
                else:
                    info.stderr_bytes += len(chunk)  # counted, never kept
            if reason is not None:
                break
        if reason is None and process.poll() is None:
            # Both pipes closed, but the plugin is still running.
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                reason = f"timed out after {timeout_seconds}s"
    finally:
        selector.close()
        returncode = process.poll()
        terminate_provider_process(process, process_group)
        for stream in streams:
            try:
                stream.close()
            except OSError:
                pass
        info.duration = time.monotonic() - started
    if returncode is None:
        info.exit = "killed"
    elif returncode < 0:
        info.exit = f"signal {-returncode}"
    else:
        info.exit = f"status {returncode}"
    if reason is not None:
        raise PluginFailure(reason)
    if returncode is None:
        raise PluginFailure(f"timed out after {timeout_seconds}s")
    if returncode < 0:
        raise PluginFailure(f"was stopped by signal {-returncode}")
    if returncode > 0:
        raise PluginFailure(f"exited with status {returncode}")
    return bytes(stdout)


def parse_response(data: bytes) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PluginFailure("returned output that is not UTF-8") from None
    start = len(text) - len(text.lstrip())
    if start == len(text):
        raise PluginFailure("returned no output")
    decoder = json.JSONDecoder(parse_constant=reject_constant)
    try:
        value, end = decoder.raw_decode(text, start)
    except json.JSONDecodeError as error:
        raise PluginFailure(f"returned invalid JSON at line {error.lineno} column {error.colno}") from None
    except (ValueError, RecursionError):
        raise PluginFailure("returned invalid JSON") from None
    if text[end:].strip():
        raise PluginFailure("returned more than one JSON value")
    return value


def parse_utc(value: str) -> float:
    whole = value[:19]
    return float(calendar.timegm(time.strptime(whole, "%Y-%m-%dT%H:%M:%S")))


class ResponseError(PluginFailure):
    def __init__(self, where: str) -> None:
        super().__init__(f"returned an invalid response at {where}")


def validate_response(value: Any, plugin: ExternalPlugin, collected_at: str,
                      local_today: datetime_module.date) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    """The rows to write and the state to keep, from one response, or raise ResponseError."""
    if not isinstance(value, dict):
        raise ResponseError("the top level (not an object)")
    if set(value) - set(RESPONSE_KEYS):
        raise ResponseError("the top level (unknown key)")
    if not is_int(value.get("protocol")) or value["protocol"] != PROTOCOL:
        raise ResponseError("protocol")
    rows = value.get("rows")
    if not isinstance(rows, list):
        raise ResponseError("rows")
    if len(rows) > MAX_ROWS:
        raise ResponseError(f"rows (more than {MAX_ROWS})")
    state: Optional[dict[str, Any]] = None
    if "state" in value:
        state = value["state"]
        if not isinstance(state, dict):
            raise ResponseError("state")
        if len(json.dumps(state, separators=(",", ":")).encode("utf-8")) > STATE_MAX_BYTES:
            raise ResponseError("state (larger than 256 KiB)")
    poll_ts = parse_utc(collected_at)
    out = [stamp_row(row, index, plugin, collected_at, poll_ts, local_today) for index, row in enumerate(rows)]
    return out, state


ROW_KEYS = {
    "daily": ("category", "metric", "period_start", "value", "source"),
    "event": ("category", "metric", "at", "value", "source", "scope"),
    "quota": ("category", "metric", "scope", "value", "source", "window_seconds", "resets_at"),
    "detected": ("category", "metric", "value", "message"),
    "error": ("category", "metric", "source", "message"),
}
REQUIRED_KEYS = {
    "daily": ("metric", "period_start", "value", "source"),
    "event": ("metric", "at", "value", "source"),
    "quota": ("metric", "scope", "value", "source"),
    "detected": ("metric", "value"),
    "error": ("metric", "source"),
}


def message_text(row: Mapping[str, Any], where: str) -> str:
    message = row.get("message", "")
    if not isinstance(message, str) or len(message) > MESSAGE_MAX or has_control_characters(message):
        raise ResponseError(f"{where}.message")
    return message


def stamp_row(row: Any, index: int, plugin: ExternalPlugin, collected_at: str, poll_ts: float,
              local_today: datetime_module.date) -> dict[str, Any]:
    where = f"rows[{index}]"
    if not isinstance(row, dict):
        raise ResponseError(where)
    category = row.get("category")
    kind = {
        ("usage", "daily_total"): "daily",
        ("usage", "events"): "event",
        ("availability", None): "detected",
        ("collection", None): "error",
    }.get((category, plugin.usage) if category == "usage" else (category, None))
    if category == "quota" and plugin.quota != "none":
        kind = "quota"
    if kind is None:
        raise ResponseError(f"{where}.category")
    if set(row) - set(ROW_KEYS[kind]):
        raise ResponseError(f"{where} (unknown key)")
    for key in REQUIRED_KEYS[kind]:
        if key not in row:
            raise ResponseError(f"{where}.{key}")
    sources = {next(iter(source.csv_sources)): source for source in plugin.report.sources}
    source = None
    if "source" in row:
        source = sources.get(row["source"]) if isinstance(row["source"], str) else None
        if source is None or (kind in ("daily", "event") and source.role != "usage") or (
                kind == "quota" and source.role != "quota"):
            raise ResponseError(f"{where}.source")
    value = row.get("value")
    if kind == "daily":
        if row["metric"] != "daily_tokens":
            raise ResponseError(f"{where}.metric")
        date = row["period_start"]
        try:
            if not isinstance(date, str) or not DATE_PATTERN.match(date):
                raise ValueError
            day = datetime_module.date.fromisoformat(date)
        except ValueError:
            raise ResponseError(f"{where}.period_start") from None
        if day > local_today + datetime_module.timedelta(days=1):
            raise ResponseError(f"{where}.period_start (too far ahead)")
        if not is_number(value) or value < 0:
            raise ResponseError(f"{where}.value")
        return metric_row(collected_at, plugin.id, "usage", "daily_tokens", record_kind="period_total",
                          scope=date, period_start=date, value=value, unit="tokens", source=row["source"])
    if kind == "event":
        if row["metric"] not in EVENT_METRICS:
            raise ResponseError(f"{where}.metric")
        at = row["at"]
        try:
            if not isinstance(at, str) or not UTC_PATTERN.match(at):
                raise ValueError
            at_ts = parse_utc(at)
        except ValueError:
            raise ResponseError(f"{where}.at") from None
        if at_ts > poll_ts + EVENT_AHEAD_SECONDS:
            raise ResponseError(f"{where}.at (in the future)")
        scope = row.get("scope", "")
        if not isinstance(scope, str) or len(scope) > TEXT_MAX or has_control_characters(scope):
            raise ResponseError(f"{where}.scope")
        if not is_number(value) or value < 0:
            raise ResponseError(f"{where}.value")
        return metric_row(at, plugin.id, "usage", row["metric"], record_kind="event_total", scope=scope,
                          period_start=at[:10], value=value, unit="tokens", source=row["source"])
    if kind == "quota":
        if row["metric"] != f"{plugin.quota}_percent":
            raise ResponseError(f"{where}.metric")
        scope = row["scope"]
        if not isinstance(scope, str) or not SCOPE_PATTERN.match(scope):
            raise ResponseError(f"{where}.scope")
        if not is_number(value) or not 0 <= value <= 100:
            raise ResponseError(f"{where}.value")
        window = row.get("window_seconds", "")
        if window != "" and (not is_int(window) or not 0 < window <= WINDOW_MAX_SECONDS):
            raise ResponseError(f"{where}.window_seconds")
        resets = row.get("resets_at", "")
        if resets != "":
            try:
                if not isinstance(resets, str) or not UTC_PATTERN.match(resets):
                    raise ValueError
                parse_utc(resets)
            except ValueError:
                raise ResponseError(f"{where}.resets_at") from None
        return metric_row(collected_at, plugin.id, "quota", row["metric"], record_kind="snapshot", scope=scope,
                          value=value, unit="percent", window_seconds=window, resets_at=resets,
                          source=row["source"])
    if kind == "detected":
        if row["metric"] != "detected":
            raise ResponseError(f"{where}.metric")
        if value is not False:
            raise ResponseError(f"{where}.value")
        return metric_row(collected_at, plugin.id, "availability", "detected", value=False, unit="boolean",
                          source=FAILURE_SOURCE, status="missing", message=message_text(row, where))
    if row["metric"] != "plugin_error":
        raise ResponseError(f"{where}.metric")
    assert source is not None
    return metric_row(collected_at, plugin.id, "collection", f"{source.id}_error", source=row["source"],
                      status="error", message=message_text(row, where))


def collect(plugin: ExternalPlugin, settings: Mapping[str, Any], state: Mapping[str, Any], *,
            collected_at: str, include_history: bool, version: str,
            local_today: datetime_module.date) -> Outcome:
    """Run the plugin once. Raises PluginFailure (with `.info`) when nothing may be kept."""
    timeout = effective_timeout(plugin, settings)
    request = {
        "protocol": PROTOCOL,
        "collector_version": version,
        "provider": plugin.id,
        "collected_at": collected_at,
        "include_history": include_history,
        "settings": {**{key: value for key, value in settings.items() if key not in COLLECTOR_ONLY_SETTINGS},
                     "timeout_seconds": timeout},
        "state": dict(state),
    }
    info = RunInfo()
    try:
        stdout = run_plugin(plugin, json.dumps(request, separators=(",", ":")).encode("utf-8"), timeout, info)
        rows, new_state = validate_response(parse_response(stdout), plugin, collected_at, local_today)
    except PluginFailure as failure:
        failure.info = info  # type: ignore[attr-defined]
        raise
    if not any(row["category"] == "availability" for row in rows):
        # The plugin ran and found its tool, even when it had nothing new to report.
        rows.insert(0, metric_row(collected_at, plugin.id, "availability", "detected", value=True,
                                  unit="boolean", source=FAILURE_SOURCE, message="the plugin ran"))
    return Outcome(rows, new_state, info)


def effective_timeout(plugin: ExternalPlugin, settings: Mapping[str, Any]) -> int:
    configured = settings.get("timeout_seconds")
    if is_int(configured) and 1 <= configured < plugin.timeout_seconds:
        return configured
    return plugin.timeout_seconds
