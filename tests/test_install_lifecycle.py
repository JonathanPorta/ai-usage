"""Install lifecycle of the multi-file collector (phase 1: AC-4, AC-6, AC-7).

Every test runs in an isolated HOME with a fake launchctl (or --no-start);
none touches the installed personal collector or the real launchd domain.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import csv
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterator
import unittest
from unittest import mock

import ai_usage_plugins
import test_ai_usage_service as black_box
from tests.parity import driver
from tests.parity.test_parity import require_history

SERVICE = black_box.SERVICE
SCRIPT = black_box.SCRIPT
PACKAGE = SCRIPT.parent / "ai_usage_plugins"
PLIST_NAME = "codes.porta.ai-usage.plist"


def tree_state(home: Path) -> dict[str, Any]:
    """Every installer-relevant file (bytes and mode) and directory under the isolated HOME."""
    state: dict[str, Any] = {}
    for base in (home / ".ai-usage", home / "Library" / "LaunchAgents", home / ".gemini"):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            key = str(path.relative_to(home))
            if path.is_dir():
                state[key] = ("dir", stat.S_IMODE(path.stat().st_mode))
            else:
                state[key] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    return state


def program_files(home: Path) -> dict[str, tuple[int, int]]:
    """(inode, mtime_ns) of the installed program files: unchanged means not rewritten."""
    root = home / ".ai-usage"
    paths = [root / "collector.py", home / "Library" / "LaunchAgents" / PLIST_NAME,
             *sorted((root / "ai_usage_plugins").glob("*.py"))]
    return {str(p): (p.stat().st_ino, p.stat().st_mtime_ns) for p in paths if p.exists()}


def variant_tree(destination: Path, marker: str) -> Path:
    """A collector copy whose service and every module differ by one comment line."""
    shutil.copytree(PACKAGE, destination / "ai_usage_plugins", ignore=shutil.ignore_patterns("__pycache__"))
    for path in [*(destination / "ai_usage_plugins").glob("*.py")]:
        path.write_text(path.read_text(encoding="utf-8") + f"\n# {marker}\n", encoding="utf-8")
    script = destination / "ai_usage_service.py"
    script.write_text(SCRIPT.read_text(encoding="utf-8") + f"\n# {marker}\n", encoding="utf-8")
    return script


class InstallLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = black_box.IsolatedHome()
        self.root = self.case.home / ".ai-usage"
        self.config = self.root / "config.json"
        self.plist = self.case.home / "Library" / "LaunchAgents" / PLIST_NAME

    def tearDown(self) -> None:
        self.case.close()

    def install_with(self, script: Path) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(script), "install", "--no-start", "--config", str(self.config)],
                                env=self.case.environment(), capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def ownership(self) -> dict[str, Any]:
        return json.loads((self.root / "install-ownership.json").read_text(encoding="utf-8"))

    def assert_current_module_set(self) -> None:
        package = self.root / "ai_usage_plugins"
        self.assertEqual(stat.S_IMODE(package.stat().st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in package.iterdir()),
                         sorted(f"{name}.py" for name in ai_usage_plugins.MODULES))
        files = self.ownership()["files"]
        for name in ai_usage_plugins.MODULES:
            installed = package / f"{name}.py"
            self.assertEqual(installed.read_bytes(), (PACKAGE / f"{name}.py").read_bytes())
            self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o600)
            record = files[f"plugin_module:{name}"]
            self.assertEqual((record["owned"], record["path"], record["mode"]), (True, str(installed), 0o600))
        self.assertEqual((self.root / "collector.py").read_bytes(), SCRIPT.read_bytes())

    @contextmanager
    def in_process_install_paths(self) -> Iterator[None]:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(SERVICE, "DEFAULT_ROOT", self.root))
            stack.enter_context(mock.patch.object(SERVICE, "DEFAULT_CONFIG_PATH", self.config))
            stack.enter_context(mock.patch.object(SERVICE, "DEFAULT_INSTALLED_SCRIPT", self.root / "collector.py"))
            stack.enter_context(mock.patch.object(SERVICE, "DEFAULT_PLIST_PATH", self.plist))
            stack.enter_context(mock.patch.dict(os.environ, {
                "HOME": str(self.case.home), SERVICE.LAUNCHCTL_OVERRIDE_ENV: str(self.case.fake_launchctl())}))
            stack.enter_context(mock.patch("sys.stdout", new=open(os.devnull, "w")))
            yield

    # ---- fresh install, reinstall, running outside the checkout ------------

    def test_fresh_install_writes_the_whole_module_set(self) -> None:
        self.case.run("install", "--no-start", "--config", str(self.config))
        self.assert_current_module_set()
        self.assertFalse(list(self.root.rglob("__pycache__")))

    def test_reinstall_rewrites_no_program_file(self) -> None:
        self.case.run("install", "--no-start", "--config", str(self.config))
        before = program_files(self.case.home)
        self.assertGreaterEqual(len(before), 2 + len(ai_usage_plugins.MODULES))
        self.case.run("install", "--no-start", "--config", str(self.config))
        self.assertEqual(program_files(self.case.home), before)

    def test_installed_collector_runs_from_outside_the_checkout(self) -> None:
        self.case.run("install", "--no-start", "--config", str(self.config))
        box = driver.Sandbox(self.case.root / "providers")
        driver.scenario_all_providers(box)
        empty_cwd = Path(tempfile.mkdtemp(prefix="ai-usage-cwd-", dir=self.case.root))
        environment = {"HOME": str(self.case.home), "PATH": f"{box.bin}:/usr/bin:/bin",
                       "AI_USAGE_LAUNCHCTL": str(self.case.fake_launchctl())}
        installed = self.root / "collector.py"
        doctor = subprocess.run([sys.executable, "-I", str(installed), "doctor", "--json", "--config", str(box.config)],
                                cwd=empty_cwd, env=environment, capture_output=True, text=True, check=False)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertTrue(all(entry["detected"] for entry in json.loads(doctor.stdout).values()))
        once = subprocess.run([sys.executable, "-I", str(installed), "once", "--config", str(box.config)],
                              cwd=empty_cwd, env=environment, capture_output=True, text=True, check=False)
        # The fixtures are dated for the parity clock, so a real-time run reports some
        # provider errors (exit 2); every provider must still load and collect.
        self.assertIn(once.returncode, (0, 2), once.stderr)
        self.assertEqual(once.stderr, "")
        with Path(box.paths["usage_csv"]).open(newline="", encoding="utf-8") as handle:
            providers = {row["provider"] for row in csv.DictReader(handle) if row["status"] == "ok"}
        self.assertEqual(providers, set(ai_usage_plugins.BUNDLED))
        self.assertFalse(list(self.root.rglob("__pycache__")), "no unowned bytecode beside the collector")
        self.assertEqual(list(empty_cwd.iterdir()), [])

    # ---- upgrades -----------------------------------------------------------

    def test_upgrade_from_the_single_file_install(self) -> None:
        require_history(self)
        old = self.case.root / "baseline" / "ai_usage_service.py"
        old.parent.mkdir()
        old.write_text(subprocess.run(
            ["git", "-C", str(SCRIPT.parent), "show", f"{driver.baseline_sha()}:ai_usage_service.py"],
            capture_output=True, text=True, check=True).stdout, encoding="utf-8")
        self.install_with(old)
        self.assertFalse((self.root / "ai_usage_plugins").exists())
        self.assertFalse([key for key in self.ownership()["files"] if key.startswith("plugin_module:")])
        config_before = self.config.read_bytes()
        csv_before = (self.root / "usage.csv").read_bytes()

        self.case.run("install", "--no-start", "--config", str(self.config))

        self.assert_current_module_set()
        self.assertEqual(self.config.read_bytes(), config_before, "no install-time config rewrite")
        self.assertEqual((self.root / "usage.csv").read_bytes(), csv_before)

    def test_a_failed_upgrade_restores_every_file_at_every_write(self) -> None:
        older = variant_tree(self.case.root / "older", "older collector")
        self.install_with(older)
        before = tree_state(self.case.home)
        real_write = SERVICE.atomic_write_bytes
        failures = 0
        for failing_write in range(1, 200):
            calls = {"n": 0}

            def write(path: Path, content: bytes, mode: int = 0o600) -> None:
                calls["n"] += 1
                if calls["n"] == failing_write:
                    raise OSError(f"injected failure at write {failing_write}")
                real_write(path, content, mode=mode)

            with self.in_process_install_paths(), mock.patch.object(SERVICE, "atomic_write_bytes", side_effect=write):
                try:
                    SERVICE.install_service(self.config, no_start=True)
                except OSError as error:
                    self.assertIn("injected failure", str(error))
                    failures += 1
                    with self.subTest(failing_write=failing_write):
                        self.assertEqual(tree_state(self.case.home), before, "mixed versions after a failed upgrade")
                        self.assertFalse(list(self.case.home.glob(".ai-usage-install-recovery-*")))
                    continue
            break  # every write succeeded: the upgrade completed
        self.assertGreater(failures, len(ai_usage_plugins.MODULES), "each module write was a failure point")
        self.assert_current_module_set()

    def test_the_rollback_check_detects_a_missing_rollback(self) -> None:
        # Negative control for the test above: without rollback, a failed upgrade is mixed.
        older = variant_tree(self.case.root / "older", "older collector")
        self.install_with(older)
        before = tree_state(self.case.home)
        real_write = SERVICE.atomic_write_bytes
        calls = {"n": 0}

        def write(path: Path, content: bytes, mode: int = 0o600) -> None:
            calls["n"] += 1
            if "ai_usage_plugins" in str(path) and path.name == "claude.py":
                raise OSError("injected failure")
            real_write(path, content, mode=mode)

        with self.in_process_install_paths(), \
                mock.patch.object(SERVICE, "atomic_write_bytes", side_effect=write), \
                mock.patch.object(SERVICE.FilesystemTransaction, "rollback", lambda self, preserve_recovery=False: None):
            with self.assertRaises(OSError):
                SERVICE.install_service(self.config, no_start=True)
        self.assertNotEqual(tree_state(self.case.home), before)

    def test_an_interrupted_upgrade_restores_the_files_and_restarts_the_service(self) -> None:
        # Ctrl-C (KeyboardInterrupt) is not an Exception; it must still roll back.
        older = variant_tree(self.case.root / "older", "older collector")
        self.install_with(older)
        before = tree_state(self.case.home)
        real_write = SERVICE.atomic_write_bytes
        calls: list[list[str]] = []

        def write(path: Path, content: bytes, mode: int = 0o600) -> None:
            if path.parent.name == "ai_usage_plugins" and path.name == "claude.py":
                raise KeyboardInterrupt
            real_write(path, content, mode=mode)

        def launchctl(arguments: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
            calls.append(arguments)
            stdout = f'"{SERVICE.SERVICE_LABEL}" => enabled\n' if arguments[0] == "print-disabled" else ""
            return subprocess.CompletedProcess(["launchctl", *arguments], 0, stdout, "")

        with self.in_process_install_paths(), \
                mock.patch.object(SERVICE.sys, "platform", "darwin"), \
                mock.patch.object(SERVICE, "find_launchctl", return_value=self.case.fake_launchctl()), \
                mock.patch.object(SERVICE, "run_launchctl", side_effect=launchctl), \
                mock.patch.object(SERVICE, "atomic_write_bytes", side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                SERVICE.install_service(self.config, no_start=False)

        self.assertEqual(tree_state(self.case.home), before, "mixed versions after an interrupted upgrade")
        self.assertFalse(list(self.case.home.glob(".ai-usage-install-recovery-*")))
        commands = [arguments[0] for arguments in calls]
        self.assertEqual(commands[commands.index("bootout"):], ["bootout", "enable", "bootstrap", "kickstart"],
                         "the service stopped for the upgrade is started again")

    def test_an_interrupted_uninstall_restores_the_files_and_restarts_the_service(self) -> None:
        self.case.run("install", "--no-start", "--config", str(self.config))
        before = tree_state(self.case.home)
        calls: list[list[str]] = []

        def launchctl(arguments: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
            calls.append(arguments)
            return subprocess.CompletedProcess(["launchctl", *arguments], 0, "", "")

        real_unlink = Path.unlink

        def unlink(path: Path, missing_ok: bool = False) -> None:
            if path.name == "install-ownership.json":
                raise KeyboardInterrupt
            real_unlink(path, missing_ok=missing_ok)

        with self.in_process_install_paths(), \
                mock.patch.object(SERVICE.sys, "platform", "darwin"), \
                mock.patch.object(SERVICE, "find_launchctl", return_value=self.case.fake_launchctl()), \
                mock.patch.object(SERVICE, "run_launchctl", side_effect=launchctl), \
                mock.patch.object(Path, "unlink", unlink):
            with self.assertRaises(KeyboardInterrupt):
                SERVICE.uninstall_service(self.config)

        self.assertEqual(tree_state(self.case.home), before)
        self.assertFalse(list(self.case.home.glob(".ai-usage-install-recovery-*")))
        commands = [arguments[0] for arguments in calls]
        self.assertEqual(commands[commands.index("bootout"):], ["bootout", "enable", "bootstrap", "kickstart"])

    def test_an_owned_module_this_version_no_longer_ships_is_removed_only_if_unchanged(self) -> None:
        self.case.run("install", "--no-start", "--config", str(self.config))
        package = self.root / "ai_usage_plugins"
        retired, edited = package / "retired.py", package / "edited.py"
        retired.write_text("# shipped by an earlier version\n", encoding="utf-8")
        edited.write_text("# shipped by an earlier version\n", encoding="utf-8")
        ownership = self.ownership()
        for path in (retired, edited):
            ownership["files"][f"plugin_module:{path.stem}"] = {
                "owned": True, "path": str(path), "sha256": SERVICE.file_sha256(path), "mode": 0o600}
        (self.root / "install-ownership.json").write_text(json.dumps(ownership), encoding="utf-8")
        edited.write_text("# changed by the user\n", encoding="utf-8")

        result = self.case.run("install", "--no-start", "--config", str(self.config))

        self.assertFalse(retired.exists())
        self.assertEqual(edited.read_text(encoding="utf-8"), "# changed by the user\n")
        self.assertIn(f"Preserved modified installer file: {edited}", result.stdout)
        self.assertFalse([key for key in self.ownership()["files"] if key in ("plugin_module:retired", "plugin_module:edited")])

    # ---- uninstall ----------------------------------------------------------

    def test_uninstall_removes_unchanged_program_files_and_keeps_data_and_modified_files(self) -> None:
        self.case.run("install", "--no-start", "--config", str(self.config))
        self.case.run("once", "--config", str(self.config), check=False)
        package = self.root / "ai_usage_plugins"
        modified = package / "grok.py"
        modified.write_text(modified.read_text(encoding="utf-8") + "\n# local change\n", encoding="utf-8")
        unowned = package / "notes.txt"
        unowned.write_text("mine\n", encoding="utf-8")
        kept = {path: path.read_bytes() for path in (self.config, self.root / "usage.csv", self.root / "collector.log")
                if path.exists()}

        result = self.case.run("uninstall", "--config", str(self.config))

        self.assertFalse((self.root / "collector.py").exists())
        self.assertFalse(self.plist.exists())
        self.assertEqual(sorted(p.name for p in package.iterdir()), ["grok.py", "notes.txt"])
        self.assertIn(f"Preserved modified installer file: {modified}", result.stdout)
        for path, content in kept.items():
            self.assertEqual(path.read_bytes(), content, f"{path} must be preserved")
        self.assertFalse((self.root / "install-ownership.json").exists())

    def test_a_kept_modified_collector_keeps_its_plugins(self) -> None:
        self.case.run("install", "--no-start", "--config", str(self.config))
        installed = self.root / "collector.py"
        installed.write_text(installed.read_text(encoding="utf-8") + "\n# local change\n", encoding="utf-8")
        result = self.case.run("uninstall", "--config", str(self.config))
        self.assertIn(f"Preserved modified installer file: {installed}", result.stdout)
        self.assertIn("Preserved the provider plugins because collector.py was kept", result.stdout)
        self.assertEqual(sorted(p.name for p in (self.root / "ai_usage_plugins").iterdir()),
                         sorted(f"{name}.py" for name in ai_usage_plugins.MODULES))
        version = subprocess.run([sys.executable, "-I", str(installed), "version"], env=self.case.environment(),
                                 capture_output=True, text=True, check=False)
        self.assertEqual((version.returncode, version.stderr), (0, ""), "the kept collector still runs")

    def test_install_reports_each_integration_as_before(self) -> None:
        # The integration messages are generic now; their text must not change.
        self.config.parent.mkdir(mode=0o700)
        self.config.write_text(json.dumps({"providers": {"gemini_cli": {"configure_telemetry": True}}}), encoding="utf-8")
        missing = self.case.run("install", "--no-start", "--config", str(self.config))
        self.assertIn("Skipped Antigravity integration: agy was not found", missing.stdout)
        self.assertIn("Skipped Gemini CLI integration: gemini was not found", missing.stdout)
        self.case.write_executable("agy")
        self.case.write_executable("gemini")
        configured = self.case.run("install", "--no-start", "--config", str(self.config))
        antigravity = (self.case.home / ".gemini" / "antigravity-cli" / "settings.json").resolve()
        gemini = (self.case.home / ".gemini" / "settings.json").resolve()
        self.assertIn(f"Configured Antigravity status-line capture: {antigravity}", configured.stdout)
        self.assertIn(f"Configured local Gemini CLI telemetry: {gemini}", configured.stdout)
        managed = self.case.run("install", "--no-start", "--config", str(self.config))
        self.assertIn(f"Preserved Antigravity statusLine (managed): {antigravity}", managed.stdout)
        self.assertIn(f"Preserved Gemini CLI telemetry (managed): {gemini}", managed.stdout)

    def test_uninstall_removes_the_package_directory_once_empty(self) -> None:
        self.case.run("install", "--no-start", "--config", str(self.config))
        self.case.run("uninstall", "--config", str(self.config))
        self.assertFalse((self.root / "ai_usage_plugins").exists())
        self.assertTrue(self.config.exists())

    def test_a_dependent_unowned_hook_keeps_the_collector_and_its_modules(self) -> None:
        self.case.write_executable("agy")
        settings = self.case.home / ".gemini" / "antigravity-cli" / "settings.json"
        settings.parent.mkdir(parents=True)
        # The user pointed their own hook at the managed wrapper path before installing.
        settings.write_text(json.dumps({"statusLine": {"type": "command",
                                                       "command": str(self.root / "antigravity-statusline")}}),
                            encoding="utf-8")
        self.case.run("install", "--no-start", "--config", str(self.config))
        result = self.case.run("uninstall", "--config", str(self.config))
        self.assertIn("Preserved collector.py because an unowned Antigravity hook may still depend on it", result.stdout)
        self.assertTrue((self.root / "collector.py").exists())
        self.assertEqual(sorted(p.name for p in (self.root / "ai_usage_plugins").iterdir()),
                         sorted(f"{name}.py" for name in ai_usage_plugins.MODULES))

    def test_a_symlinked_config_stays_linked_through_install_and_configure(self) -> None:
        target = self.case.root / "dotfiles" / "ai-usage.json"
        target.parent.mkdir()
        target.write_text("{}\n", encoding="utf-8")
        self.root.mkdir(mode=0o700)
        self.config.symlink_to(target)
        self.case.run("install", "--no-start", "--config", str(self.config))
        installed = self.root / "collector.py"
        subprocess.run([sys.executable, str(installed), "configure", "--config", str(self.config),
                        "--set", "providers.codex.enabled=false"],
                       env=self.case.environment(), capture_output=True, text=True, check=True)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"providers": {"codex": {"enabled": False}}})


if __name__ == "__main__":
    unittest.main()
