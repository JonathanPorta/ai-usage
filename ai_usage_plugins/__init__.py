"""The registry of bundled provider plugins.

BUNDLED is the only list of built-in providers, in collection order. Each
provider module defines `PLUGIN` (see base.ProviderPlugin). load_plugins()
imports them one by one, so a provider that fails to load is reported on its
own and the others still load.

Importing this package defines things and does nothing else.
"""

from __future__ import annotations

import importlib
from typing import Any

BUNDLED = ("codex", "claude", "antigravity", "gemini_cli", "grok")

# Every file of the package, installed together beside the collector.
MODULES = ("__init__", "base", "support", "external") + BUNDLED


def load_plugins() -> tuple[dict[str, Any], dict[str, str]]:
    """Return (plugins by id in BUNDLED order, load errors by id)."""
    plugins: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name in BUNDLED:
        try:
            plugin = importlib.import_module(f"{__name__}.{name}").PLUGIN
            if plugin.id != name:
                raise ValueError(f"module {name} declares plugin id {plugin.id!r}")
            plugins[name] = plugin
        except Exception as error:  # One provider cannot disable the others.
            errors[name] = f"{type(error).__name__}: {error}"
    return plugins, errors
