"""The provider plugin contract: what every built-in provider module exposes as `PLUGIN`.

Data only. Importing this module (or any provider module) defines things and
does nothing else: no collection, no subprocess, no file access.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional


Row = dict[str, Any]


@dataclass(frozen=True)
class CollectContext:
    """One provider's inputs for one collection pass."""

    collected_at: str
    settings: Mapping[str, Any]
    include_history: bool
    state: MutableMapping[str, Any]
    # The collector's version, sent as the client version by providers whose
    # metadata APIs ask for one.
    version: str


@dataclass(frozen=True)
class IntegrationSpec:
    """A setting the installer may add to the provider's own settings file.

    The installer writes `field` into the JSON settings file only when the
    field is absent, records ownership, and removes it on uninstall only while
    it still holds the value the installer wrote.
    """

    settings_setting: str  # provider setting naming the settings file
    settings_default: str
    field: str
    enable_setting: str  # provider setting that turns the integration on
    desired: Callable[[Mapping[str, Any], Optional[Path]], dict[str, Any]]  # (settings, wrapper path)
    display_name: str
    configured_label: str
    preserved_label: str
    # A small wrapper script the installer owns and the settings value points at.
    # It runs `collector.py <wrapper_command> --config <config>`.
    wrapper_command: Optional[str] = None
    wrapper_help: str = ""
    # Directories the integration's value needs (created while it is owned).
    owned_directories: Callable[[Mapping[str, Any]], list[Path]] = lambda value: []


@dataclass(frozen=True)
class ProviderPlugin:
    id: str
    # This provider's entry in the default config, key order included.
    default_settings: Mapping[str, Any]
    executable_names: tuple[str, ...]
    collect: Callable[[CollectContext], list[Row]]
    report: "ProviderDef"
    # Position in the report's provider list (the report's order predates the
    # registry and is kept).
    report_order: int
    doctor_extra: Callable[[Mapping[str, Any]], dict[str, Any]] = lambda settings: {}
    # (setting name, "file" | "directory") for every path setting validated with the config.
    path_roles: tuple[tuple[str, str], ...] = ()
    integration: Optional[IntegrationSpec] = None
    # Records one status-line payload; returns the text to show. Run by the
    # integration's wrapper command.
    status_line: Optional[Callable[[Mapping[str, Any], Mapping[str, Any]], str]] = None


# --------------------------------------------------------------------------- report catalogue
# Moved unchanged from ai_usage_report.py, which re-exports both names.

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
