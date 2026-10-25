"""In-process tests never scan the developer's ~/.ai-usage/plugins (phase 3).

Collection, `doctor` and the report discover local plugins under the current
HOME. Tests that call them in this process point discovery at an empty
temporary folder for the whole module; subprocess tests set their own HOME.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Optional
from unittest import mock

from ai_usage_plugins import external

_STACK: list[tuple[Any, Any]] = []


def start() -> Path:
    directory = tempfile.TemporaryDirectory(prefix="ai-usage-no-plugins-")
    root = Path(directory.name) / "plugins"
    patcher = mock.patch.object(external, "plugins_root", lambda: root)
    patcher.start()
    _STACK.append((patcher, directory))
    return root


def stop() -> None:
    patcher, directory = _STACK.pop()
    patcher.stop()
    directory.cleanup()


def module_fixtures(namespace: dict[str, Any], before: Optional[Any] = None, after: Optional[Any] = None) -> None:
    """setUpModule/tearDownModule for a test module, wrapping any it already has."""
    def set_up_module() -> None:
        start()
        if before is not None:
            before()

    def tear_down_module() -> None:
        try:
            if after is not None:
                after()
        finally:
            stop()

    namespace["setUpModule"] = set_up_module
    namespace["tearDownModule"] = tear_down_module
