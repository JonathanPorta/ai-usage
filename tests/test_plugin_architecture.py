"""The provider plugin architecture (phase 1: AC-1, AC-2, AC-5).

- Shared collection, doctor and install code holds no provider ids: the AST
  check below replaces a repository-wide grep. The report keeps a few
  provider-specific presentation rules, listed with their reasons in
  REPORT_PROVIDER_RULES; any other provider-id literal in it fails.
- Plugin modules never import the service or the report, and importing them
  starts no process and writes no file.
- A provider that fails to load or to collect becomes one unexpected_error row,
  and the other providers' output is unchanged (checked against the parity
  golden files).
- The provider code moved out of ai_usage_service.py unchanged, apart from the
  collector version now passed in (needs the BASELINE commit in history).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
from typing import Any, Optional
import unittest

from tests.parity import driver
from tests.parity.test_parity import require_history

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "ai_usage_plugins"
PROVIDER_IDS = ("codex", "claude", "antigravity", "gemini_cli", "grok")

# Presentation rules in ai_usage_report.py that still name a provider. They
# turn the collector's provider-specific CSV shapes into the report's display
# model; the provider set itself comes from the registry.
REPORT_PROVIDER_RULES = {
    "evaluate_attempt": "Gemini CLI telemetry that is off by choice is shown as a setup note, not an error",
    "models_today": "Grok reports per-model usage as one JSON payload row",
    "window_identity": "window names: Grok credit periods, Antigravity model quotas, Claude per-model weekly limits, Codex limit ids",
    "build_windows": "which quota metric each provider reports (used vs remaining) and Grok's billing freshness",
    "build_report": "Grok's billing and API-equivalent costs, the Claude stats-cache note and the Grok incomplete-usage note",
}


def provider_literals(source: str) -> dict[str, list[str]]:
    """Provider-id string literals per top-level function (or "<module>")."""
    found: dict[str, list[str]] = {}
    tree = ast.parse(source)
    for node in tree.body:
        owner = node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else "<module>"
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and child.value in PROVIDER_IDS:
                found.setdefault(owner, []).append(child.value)
    return found


def imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def copy_tree(destination: Path) -> Path:
    destination.mkdir(parents=True)
    for name in ("ai_usage_service.py", "ai_usage_report.py"):
        shutil.copy2(REPO / name, destination / name)
    shutil.copytree(PACKAGE, destination / "ai_usage_plugins", ignore=shutil.ignore_patterns("__pycache__"))
    return destination


class ArchitectureTests(unittest.TestCase):
    def test_shared_service_code_names_no_provider(self) -> None:
        found = provider_literals((REPO / "ai_usage_service.py").read_text(encoding="utf-8"))
        self.assertEqual(found, {}, "provider ids in shared service code; derive them from the registry")

    def test_report_names_providers_only_in_its_documented_rules(self) -> None:
        found = provider_literals((REPO / "ai_usage_report.py").read_text(encoding="utf-8"))
        self.assertEqual(sorted(found), sorted(REPORT_PROVIDER_RULES),
                         "a new provider-specific report rule must be listed in REPORT_PROVIDER_RULES")

    def test_the_check_catches_a_reintroduced_provider_branch(self) -> None:
        source = (REPO / "ai_usage_service.py").read_text(encoding="utf-8")
        mutated = source.replace(
            "    for name in names:\n        settings = provider_config(config, name)\n",
            "    for name in names:\n        settings = provider_config(config, name)\n"
            "        if name == \"claude\":\n            pass\n", 1)
        self.assertNotEqual(mutated, source)
        self.assertEqual(provider_literals(mutated), {"collect_snapshot": ["claude"]})

    def test_registry_lists_the_bundled_providers_in_order(self) -> None:
        import ai_usage_plugins
        import ai_usage_report
        import ai_usage_service

        self.assertEqual(ai_usage_plugins.BUNDLED, PROVIDER_IDS)
        self.assertEqual(list(ai_usage_service.PLUGINS), list(PROVIDER_IDS))
        self.assertEqual(ai_usage_service.PLUGIN_LOAD_ERRORS, {})
        self.assertEqual(list(ai_usage_service.DEFAULT_CONFIG["providers"]), list(PROVIDER_IDS))
        # The report keeps its established order.
        self.assertEqual([p.id for p in ai_usage_report.PROVIDERS],
                         ["codex", "claude", "antigravity", "grok", "gemini_cli"])
        for name, plugin in ai_usage_service.PLUGINS.items():
            self.assertEqual(plugin.report.id, name)
            self.assertEqual(plugin.default_settings["executable_names"], list(plugin.executable_names))
            for setting, kind in plugin.path_roles:
                self.assertIn(setting, plugin.default_settings)
                self.assertIn(kind, ("file", "directory"))

    def test_plugins_never_import_the_service_or_the_report(self) -> None:
        for path in sorted(PACKAGE.glob("*.py")):
            with self.subTest(path.name):
                imported = imported_modules(path)
                self.assertFalse({"ai_usage_service", "ai_usage_report"} & imported, imported)

    def test_importing_the_plugins_runs_nothing_and_writes_nothing(self) -> None:
        probe = textwrap.dedent(
            """
            import json, sys
            events = []
            WRITE = ("w", "a", "x", "+")
            def hook(event, args):
                if event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork",
                             "os.mkdir", "os.rename", "os.remove", "os.chmod", "os.utime", "shutil.rmtree"):
                    events.append([event, repr(args)[:200]])
                elif event == "open" and len(args) > 1 and isinstance(args[1], str) and any(c in args[1] for c in WRITE):
                    events.append([event, repr(args)[:200]])
            sys.addaudithook(hook)
            sys.path.insert(0, sys.argv[1])
            import ai_usage_plugins
            plugins, errors = ai_usage_plugins.load_plugins()
            print(json.dumps({"events": events, "plugins": list(plugins), "errors": errors}))
            """
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(REPO)],
                                capture_output=True, text=True, check=True)
        observed = json.loads(result.stdout)
        self.assertEqual(observed, {"events": [], "plugins": list(PROVIDER_IDS), "errors": {}})


class FailureIsolationTests(unittest.TestCase):
    def run_with_broken(self, module: str, replace: Optional[tuple[str, str]] = None,
                        append: str = "") -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="ai-usage-broken-") as temporary:
            src = copy_tree(Path(temporary) / "src")
            path = src / "ai_usage_plugins" / f"{module}.py"
            text = path.read_text(encoding="utf-8")
            if replace is not None:
                self.assertIn(replace[0], text)
                text = text.replace(replace[0], replace[1], 1)
            path.write_text(text + append, encoding="utf-8")
            return driver.run_scenario(src, "all_providers")

    def assert_others_unchanged(self, observed: dict[str, Any], broken: str) -> list[dict[str, Any]]:
        golden = json.loads(driver.golden_path("all_providers").read_text(encoding="utf-8"))
        broken_rows: list[dict[str, Any]] = []
        for expected, actual in zip(golden["results"], observed["results"]):
            if expected["op"] != "once":
                continue
            # transaction_index is a row's position in its poll, so it shifts when the
            # broken provider's rows change; every other field must be identical.
            def others(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [{k: v for k, v in row.items() if k != "transaction_index"}
                        for row in rows if row["provider"] != broken]
            self.assertEqual(others(actual["csv"]), others(expected["csv"]))
            broken_rows = [row for row in actual["csv"] if row["provider"] == broken]
        return broken_rows

    def test_a_provider_that_fails_to_import_is_one_error_row_and_others_still_collect(self) -> None:
        observed = self.run_with_broken("claude", append="\nraise RuntimeError('claude module is broken')\n")
        rows = self.assert_others_unchanged(observed, "claude")
        # Per poll: the unexpected_error row, then the configured subscription cost.
        new_rows = [row for row in rows if row["category"] == "collection"]
        self.assertTrue(new_rows)
        for row in new_rows:
            self.assertEqual((row["metric"], row["status"], row["source"]), ("unexpected_error", "error", "collector"))
            self.assertIn("provider module failed to load: RuntimeError: claude module is broken", row["message"])
        doctor = json.loads(next(r for r in observed["results"] if r["op"] == "doctor_json")["stdout"])
        self.assertEqual(doctor["claude"]["load_error"], "RuntimeError: claude module is broken")
        report = next(r for r in observed["results"] if r["op"] == "report")
        self.assertEqual(report["report"]["collector"]["plugin_errors"],
                         {"claude": "RuntimeError: claude module is broken"})
        self.assertNotIn("claude", [p["id"] for p in report["report"]["providers"]])

    def test_a_provider_whose_collect_raises_is_one_error_row_and_others_still_collect(self) -> None:
        observed = self.run_with_broken(
            "grok", replace=("def collect(context: CollectContext) -> list[dict[str, Any]]:\n",
                             "def collect(context: CollectContext) -> list[dict[str, Any]]:\n"
                             "    raise ValueError('grok exploded')\n"))
        rows = self.assert_others_unchanged(observed, "grok")
        errors = [row for row in rows if row["category"] == "collection"]
        self.assertTrue(errors)
        self.assertEqual({(row["metric"], row["message"]) for row in errors}, {("unexpected_error", "grok exploded")})

    def test_install_refuses_an_incomplete_module_set(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-usage-broken-") as temporary:
            src = copy_tree(Path(temporary) / "src")
            path = src / "ai_usage_plugins" / "gemini_cli.py"
            path.write_text(path.read_text(encoding="utf-8") + "\nraise ImportError('missing piece')\n", encoding="utf-8")
            home = Path(temporary) / "home"
            home.mkdir()
            result = subprocess.run(
                [sys.executable, str(src / "ai_usage_service.py"), "install", "--no-start",
                 "--config", str(home / ".ai-usage" / "config.json")],
                env={"HOME": str(home), "PATH": "/usr/bin:/bin", "AI_USAGE_LAUNCHCTL": ""},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("refusing to install an incomplete collector", result.stderr)
            self.assertFalse((home / ".ai-usage").exists(), "nothing may be written")


class VerbatimMoveTests(unittest.TestCase):
    """Every function and constant that left ai_usage_service.py is unchanged in its new module."""

    # The only edits: the collector version is passed in instead of read from the service.
    VERSION_PARAMETER = "client_version"

    def normalized(self, node: ast.AST) -> str:
        parameter = self.VERSION_PARAMETER

        class Normalize(ast.NodeTransformer):
            def visit_Name(self, name: ast.Name) -> ast.AST:
                return ast.copy_location(ast.Name(id=parameter if name.id == "VERSION" else name.id, ctx=name.ctx), name)

            def visit_arguments(self, arguments: ast.arguments) -> ast.AST:
                arguments.args = [a for a in arguments.args if a.arg != parameter]
                return self.generic_visit(arguments)

            def visit_Call(self, call: ast.Call) -> ast.AST:
                call.args = [a for a in call.args if not (isinstance(a, ast.Name) and a.id == parameter)]
                return self.generic_visit(call)

        return ast.dump(Normalize().visit(node), include_attributes=False)

    def test_moved_code_is_unchanged(self) -> None:
        require_history(self)
        baseline = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{driver.baseline_sha()}:ai_usage_service.py"],
            capture_output=True, text=True, check=True,
        ).stdout
        before = {}
        for node in ast.parse(baseline).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                before[node.name] = node
            elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                before[node.targets[0].id] = node
        moved = 0
        for path in sorted(PACKAGE.glob("*.py")):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                name = node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else (
                    node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None)
                if name is None or name not in before:
                    continue
                if name == "claude_usage_rows" and path.stem == "claude":
                    continue  # defined twice in the baseline; compared below
                with self.subTest(f"{path.stem}.{name}"):
                    self.assertEqual(self.normalized(node), self.normalized(before[name]))
                    moved += 1
        self.assertGreaterEqual(moved, 70)
        # The baseline defines claude_usage_rows twice; the second definition wins, in both.
        def last(source: str) -> str:
            nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "claude_usage_rows"]
            self.assertEqual(len(nodes), 2)
            return self.normalized(nodes[-1])
        self.assertEqual(last((PACKAGE / "claude.py").read_text(encoding="utf-8")), last(baseline))


if __name__ == "__main__":
    unittest.main()
