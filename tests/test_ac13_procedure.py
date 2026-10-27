"""The AC-13 procedure's checks, run against synthetic local plugins.

`tasks/phase-3/procedure-ac13-work-laptop.md` is run by hand on the owner's work
laptop. Its Python snippets (each starting `# ac13: <name>`) and its plugin-placement
step run here, so a check that could pass falsely fails CI instead of the owner's
acceptance.

No provider tool runs:
- each test has its own HOME with an installed 2.4.0 collector, and every built-in
  provider turned off (a turned-off provider runs no discovery and no command);
- PATH is this Python, then /usr/bin:/bin, and launchctl is switched off;
- an audit hook in every Python process the test starts records each program it
  starts. Each test asserts that only this Python, the test's own synthetic plugins
  and the plugin runner's `ps` (from /bin or /usr/bin) were started.

Nothing here reads the developer's ~/.ai-usage, a real plugin or a log.
"""

from __future__ import annotations

import csv
import datetime as datetime_module
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional
import unittest

from tests.parity.test_parity import require_history

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ai_usage_service.py"
PROCEDURE = REPO / "tasks" / "phase-3" / "procedure-ac13-work-laptop.md"
AC10_PROCEDURE = REPO / "tasks" / "phase-2" / "procedure-reinstall-ac10.md"
SNIPPET = re.compile(r"python3 - <<'EOF'\n(# ac(?:10|13): (\S+)\n.*?)\nEOF\n", re.DOTALL)
STEP = re.compile(r"bash -euo pipefail <<'STEP'\n(.*?)\nSTEP\n", re.DOTALL)
FENCE = re.compile(r"```bash\n(.*?)```", re.DOTALL)
BUILT_INS_OFF = {name: {"enabled": False} for name in ("codex", "claude", "antigravity", "gemini_cli", "grok")}
SERVICE_LINE = "{} INFO collection completed rows=1 detected_providers=1 errors=0\n"
# The plugin runner lists its process group with `ps` after every run.
SYSTEM_PROGRAMS = {"ps"}
PREVIOUS_SHA = "1b37aa78a4d7e1768c17d9c5db1984280059008d"  # collector 2.3.0, before phase 3
PINNED_SHA = "d9ea44543861bd18eaba5f1c86867d4ef2079d63"  # the procedure's pin, collector 2.4.0

# Every Python process a test starts loads this from PYTHONPATH and records each
# program it starts.
SITECUSTOMIZE = """\
import json, os, sys
_LOG = os.environ.get("AC13_TEST_STARTED")
_EVENTS = {"subprocess.Popen", "os.posix_spawn", "os.exec", "os.spawn", "os.system"}
def _hook(event, args):
    if event in _EVENTS:
        program = args[0]
        if event == "subprocess.Popen" and program is None:
            program = args[1] if isinstance(args[1], (str, bytes, os.PathLike)) else args[1][0]
        with open(_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps([event, os.fsdecode(program)]) + "\\n")
if _LOG:
    sys.addaudithook(_hook)
"""

# The synthetic plugin answers with the rows and exit status in answer.json beside it.
PLUGIN = """\
import json, os, sys
json.load(sys.stdin)
with open(os.path.join(os.environ["AI_USAGE_PLUGIN_DIR"], "answer.json"), encoding="utf-8") as handle:
    answer = json.load(handle)
sys.stdout.write(json.dumps({"protocol": 1, "rows": answer["rows"]}))
sys.exit(answer["exit"])
"""


def procedure() -> str:
    return PROCEDURE.read_text(encoding="utf-8")


def snippet_copies(text: str, prefix: str = "ac13") -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for code, name in SNIPPET.findall(text):
        if code.startswith(f"# {prefix}:"):
            found.setdefault(name, []).append(code)
    return found


def snippets() -> dict[str, str]:
    return {name: copies[0] for name, copies in snippet_copies(procedure()).items()}


def steps() -> dict[str, str]:
    named = {}
    for body in STEP.findall(procedure()):
        first = body.splitlines()[0]
        if first.startswith("# ac13: "):
            named[first.split()[2]] = body
    return named


def utc(offset: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset))


def daily(name: str, value: float) -> dict[str, Any]:
    return {"category": "usage", "metric": "daily_tokens", "period_start": datetime_module.date.today().isoformat(),
            "value": value, "source": f"{name}-usage"}


def quota(name: str, value: float, metric: str = "used_percent", scope: str = "weekly") -> dict[str, Any]:
    return {"category": "quota", "metric": metric, "scope": scope, "value": value, "source": f"{name}-limits",
            "window_seconds": 604800}


def event(name: str, metric: str, value: float) -> dict[str, Any]:
    return {"category": "usage", "metric": metric, "at": utc(-5), "value": value, "source": f"{name}-usage"}


def not_found() -> dict[str, Any]:
    return {"category": "availability", "metric": "detected", "value": False, "message": "nothing to read"}


def source_error(name: str) -> dict[str, Any]:
    return {"category": "collection", "metric": "plugin_error", "source": f"{name}-usage", "message": "read failed"}


class Laptop:
    """An isolated HOME with an installed 2.4.0 collector and every built-in provider off."""

    def __init__(self, test: unittest.TestCase, installed: bool = True) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-usage-ac13-")
        test.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.ai = self.home / ".ai-usage"
        self.ai.mkdir(parents=True)
        self.ai.chmod(0o700)
        if installed:
            shutil.copy2(SCRIPT, self.ai / "collector.py")
            (self.ai / "collector.py").chmod(0o755)
            shutil.copytree(REPO / "ai_usage_plugins", self.ai / "ai_usage_plugins",
                            ignore=shutil.ignore_patterns("__pycache__"))
            (self.ai / "config.json").write_text(
                json.dumps({"poll_interval_seconds": 3600, "providers": BUILT_INS_OFF}, indent=2) + "\n",
                encoding="utf-8")
            agents = self.home / "Library" / "LaunchAgents"
            agents.mkdir(parents=True)
            with (agents / "codes.porta.ai-usage.plist").open("wb") as handle:
                plistlib.dump({"Label": "codes.porta.ai-usage",
                               "ProgramArguments": [sys.executable, str(self.ai / "collector.py"), "daemon"]}, handle)
        self.launchctl = ""
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.audit = self.root / "audit"
        self.audit.mkdir()
        (self.audit / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        self.started = self.root / "started.jsonl"
        self.mark_path = self.root / "mark.json"
        test.addCleanup(self.assert_only_test_programs_started, test)

    # ---- set-up

    def plugin(self, name: str = "acme", *, usage: str = "daily_total", quota: str = "used",
               rows: Optional[list[dict[str, Any]]] = None, exit: int = 0, folder: Optional[Path] = None) -> Path:
        folder = folder or self.ai / "plugins" / name
        folder.mkdir(parents=True)
        for directory in (folder.parent, folder):
            directory.chmod(0o700)
        sources = []
        if usage != "none":
            sources.append({"id": "usage", "role": "usage", "label": "Usage", "kind": "Synthetic",
                            "csv_source": f"{name}-usage"})
        if quota != "none":
            sources.append({"id": "limits", "role": "quota", "label": "Limits", "kind": "Synthetic",
                            "csv_source": f"{name}-limits"})
        manifest = {"manifest_version": 1, "protocol": 1, "id": name, "executable": "collect", "timeout_seconds": 10,
                    "report": {"name": name.title(), "product": "Synthetic Tool", "usage": usage, "quota": quota,
                               "sources": sources}}
        (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "plugin.json").chmod(0o600)
        (folder / "collect").write_text(f"#!{sys.executable}\n{PLUGIN}", encoding="utf-8")
        (folder / "collect").chmod(0o700)
        self.answer(rows if rows is not None else [], exit=exit, folder=folder)
        return folder

    def answer(self, rows: list[dict[str, Any]], *, exit: int = 0, name: str = "acme",
               folder: Optional[Path] = None) -> None:
        path = (folder or self.ai / "plugins" / name) / "answer.json"
        path.write_text(json.dumps({"rows": rows, "exit": exit}), encoding="utf-8")
        path.chmod(0o600)

    # ---- running

    def environment(self, **extra: str) -> dict[str, str]:
        return {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "AI_USAGE_LAUNCHCTL": self.launchctl,
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(self.audit),
                "AC13_TEST_STARTED": str(self.started), "AC13_ID": "acme", **extra}

    def collector(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(self.ai / "collector.py"), *arguments], text=True,
                              capture_output=True, env=self.environment(), timeout=120, check=False)

    def enable(self, name: str = "acme") -> None:
        result = self.collector("plugin", "enable", name)
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)

    def once(self) -> int:
        result = self.collector("once")
        if result.returncode not in (0, 2):
            raise AssertionError(result.stdout + result.stderr)
        return result.returncode

    def snippet(self, name: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-"], input=snippets()[name], text=True, capture_output=True,
                              env=self.environment(AC13_MARK=str(self.mark_path), **extra), cwd=REPO, timeout=120,
                              check=False)

    def step(self, name: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-euo", "pipefail"], input=steps()[name] + "\n", text=True,
                              capture_output=True, env=self.environment(**extra), timeout=120, check=False)

    def mark(self) -> None:
        result = self.snippet("mark")
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)

    def log_text(self) -> str:
        return (self.ai / "collector.log").read_text(encoding="utf-8")

    def service_lines(self) -> int:
        return sum("collection completed" in line and "one-shot" not in line for line in self.log_text().splitlines())

    def service_collection(self) -> None:
        """One collection by the daemon, standing in for the LaunchAgent."""
        before = self.service_lines()
        daemon = subprocess.Popen([sys.executable, str(self.ai / "collector.py"), "daemon"], env=self.environment(),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 60
            while self.service_lines() == before:
                if time.monotonic() > deadline:
                    raise AssertionError("the daemon logged no collection")
                time.sleep(0.2)
        finally:
            daemon.terminate()
            daemon.wait(timeout=30)

    def rows(self, provider: str = "acme") -> list[dict[str, str]]:
        with (self.ai / "usage.csv").open(encoding="utf-8", newline="") as handle:
            return [row for row in csv.DictReader(handle) if row["provider"] == provider]

    def assert_only_test_programs_started(self, test: unittest.TestCase) -> None:
        if not self.started.exists():
            return
        python = os.path.realpath(sys.executable)
        for line in self.started.read_text(encoding="utf-8").splitlines():
            event, program = json.loads(line)
            if not os.path.isabs(program) and program in SYSTEM_PROGRAMS:
                found = shutil.which(program, path="/usr/bin:/bin")
                test.assertTrue(found and os.path.dirname(os.path.realpath(found)) in ("/bin", "/usr/bin"), program)
                continue
            real = os.path.realpath(program) if os.path.isabs(program) else program
            test.assertTrue(real == python or real.startswith(str(self.root) + os.sep),
                            f"{event} started a program outside the test: {program}")


def first_run(test: unittest.TestCase, **plugin: Any) -> Laptop:
    """A laptop whose plugin is turned on and has collected once."""
    laptop = Laptop(test)
    laptop.plugin(**plugin)
    laptop.enable()
    laptop.once()
    return laptop


DAILY_AND_QUOTA = {"rows": [daily("acme", 1234), quota("acme", 42.5)]}


class ProcedureShapeTests(unittest.TestCase):
    def test_the_procedure_has_each_check(self) -> None:
        self.assertEqual(sorted(snippets()), ["foreground", "managed", "mark", "plugin", "removed", "report", "restore",
                                              "service", "snapshot", "values"])
        self.assertEqual(sorted(steps()), ["app", "backup", "clone", "install", "place", "preflight", "previous",
                                           "rollback", "start", "stop"])

    def test_a_check_used_twice_is_the_same_both_times(self) -> None:
        copies = snippet_copies(procedure())
        for name in ("mark", "snapshot", "removed"):
            self.assertEqual(len(copies[name]), 2, name)
        for name, versions in copies.items():
            self.assertEqual(len(set(versions)), 1, name)

    def test_the_snapshot_is_phase_2s_tested_snapshot(self) -> None:
        phase2 = snippet_copies(AC10_PROCEDURE.read_text(encoding="utf-8"), "ac10")["snapshot"][0]
        self.assertEqual(snippets()["snapshot"], phase2.replace("ac10", "ac13").replace("AC10_", "AC13_"))

    def test_every_multi_line_block_is_a_step_that_stops_on_failure(self) -> None:
        for block in FENCE.findall(procedure()):
            lines = [line for line in block.splitlines() if line.strip()]
            if len(lines) == 1:
                continue
            self.assertTrue(block.startswith("bash -euo pipefail <<'STEP'\n") and block.rstrip().endswith("STEP"),
                            block[:80])
        for body in STEP.findall(procedure()):
            result = subprocess.run(["bash", "-n"], input=body + "\n", text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


class PlacementTests(unittest.TestCase):
    def test_placing_copies_the_plugin_owner_only_and_it_lists_valid_then_on(self) -> None:
        laptop = Laptop(self)
        source = laptop.plugin(folder=laptop.root / "work" / "acme", rows=[daily("acme", 5)])
        for path in (source.parent, source):
            path.chmod(0o755)
        (source / "plugin.json").chmod(0o644)
        (source / "collect").chmod(0o755)
        (source / "answer.json").chmod(0o644)
        placed = laptop.step("place", AC13_PLUGIN_SRC=str(source))
        self.assertEqual(placed.returncode, 0, placed.stdout + placed.stderr)
        target = laptop.ai / "plugins" / "acme"
        modes = {path.name: path.stat().st_mode & 0o777 for path in (target.parent, target, *target.iterdir())}
        self.assertEqual(modes, {"plugins": 0o700, "acme": 0o700, "plugin.json": 0o600, "collect": 0o700,
                                 "answer.json": 0o600})
        listed = laptop.snippet("plugin")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("PASS: acme is valid; usage daily_total, quota used", listed.stdout)
        laptop.enable()
        self.assertIn("PASS: acme is valid, and on", laptop.snippet("plugin", AC13_EXPECT_ENABLED="true").stdout)

    def test_placing_refuses_a_manifest_for_another_id(self) -> None:
        laptop = Laptop(self)
        source = laptop.plugin("other", folder=laptop.root / "work" / "other")
        placed = laptop.step("place", AC13_PLUGIN_SRC=str(source))
        self.assertNotEqual(placed.returncode, 0)
        self.assertIn("FAIL: the manifest id is 'other', not 'acme'", placed.stderr)
        self.assertFalse((laptop.ai / "plugins" / "acme").exists())

    def test_the_list_check_fails_for_a_missing_invalid_or_off_plugin(self) -> None:
        laptop = Laptop(self)
        (laptop.ai / "plugins").mkdir(mode=0o700)
        self.assertIn("FAIL: there is no plugin acme", laptop.snippet("plugin").stderr)
        folder = laptop.plugin()
        self.assertIn("FAIL: acme is off", laptop.snippet("plugin", AC13_EXPECT_ENABLED="true").stderr)
        folder.chmod(0o755)
        self.assertIn("FAIL: acme is invalid", laptop.snippet("plugin").stderr)


class ForegroundTests(unittest.TestCase):
    def foreground(self, laptop: Laptop, code: int) -> subprocess.CompletedProcess[str]:
        return laptop.snippet("foreground", AC13_ONCE_EXIT=str(code))

    def marked_run(self) -> tuple[Laptop, subprocess.CompletedProcess[str]]:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.mark()
        return laptop, self.foreground(laptop, laptop.once())

    def test_passes_when_the_new_collection_ran_the_plugin(self) -> None:
        _, result = self.marked_run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS: 1 new collection(s), each with a successful run of acme; once exited 0", result.stdout)

    def test_fails_for_a_disabled_plugin(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.collector("plugin", "disable", "acme")
        laptop.mark()
        result = self.foreground(laptop, laptop.once())
        self.assertIn("acme wrote ['availability/enabled: disabled']", result.stderr)

    def test_fails_when_the_plugin_wrote_no_rows(self) -> None:
        # A plugin that was never turned on writes nothing, not even a disabled marker.
        laptop = Laptop(self)
        laptop.plugin(**DAILY_AND_QUOTA)
        laptop.once()
        laptop.mark()
        code = laptop.once()
        self.assertEqual(code, 0)
        self.assertIn("has no successful run of acme", self.foreground(laptop, code).stderr)

    def test_fails_for_a_missing_plugin_folder(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        shutil.rmtree(laptop.ai / "plugins" / "acme")
        laptop.mark()
        code = laptop.once()
        self.assertEqual(code, 2)
        self.assertIn("acme wrote ['collection/unexpected_error: error']", self.foreground(laptop, code).stderr)

    def test_fails_when_the_plugin_finds_nothing_to_read(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.answer([not_found()])
        laptop.mark()
        code = laptop.once()
        self.assertEqual(code, 0, "a not-found result isn't an error row, so once exits 0")
        self.assertIn("acme wrote ['availability/detected: missing']", self.foreground(laptop, code).stderr)

    def test_fails_on_a_source_error_even_though_the_plugin_ran(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.answer([source_error("acme"), quota("acme", 10)])
        laptop.mark()
        self.assertIn("acme wrote ['collection/usage_error: error']", self.foreground(laptop, laptop.once()).stderr)

    def test_an_older_successful_collection_cannot_pass_a_failed_or_missing_new_one(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.mark()
        self.assertIn("FAIL: there is no collection after the mark", self.foreground(laptop, 0).stderr)
        laptop.answer([], exit=3)
        code = laptop.once()
        self.assertEqual(code, 2)
        self.assertIn("acme wrote ['collection/unexpected_error: error']", self.foreground(laptop, code).stderr)

    def test_the_exit_code_is_evaluated(self) -> None:
        laptop = Laptop(self)
        laptop.plugin(**DAILY_AND_QUOTA)
        laptop.plugin("broken", rows=[], exit=3)
        laptop.enable()
        laptop.enable("broken")
        laptop.once()
        laptop.mark()
        code = laptop.once()
        self.assertEqual(code, 2)
        self.assertIn("FAIL: once exited 1", self.foreground(laptop, 1).stderr)
        passed = self.foreground(laptop, code)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("once exited 2; other providers with errors: ['broken']", passed.stdout)
        laptop.collector("plugin", "disable", "broken")
        laptop.mark()
        self.assertEqual(laptop.once(), 0)
        self.assertIn("FAIL: once exited 2, but no other provider wrote an error row",
                      self.foreground(laptop, 2).stderr)

    def test_a_marked_collection_missing_from_the_csv_fails(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.mark()
        mark = json.loads(laptop.mark_path.read_text(encoding="utf-8"))
        laptop.mark_path.write_text(json.dumps({**mark, "last_transaction": "gone"}), encoding="utf-8")
        laptop.once()
        self.assertIn("FAIL: the marked collection is no longer in usage.csv", self.foreground(laptop, 0).stderr)


class ServiceTests(unittest.TestCase):
    def service(self, laptop: Laptop, timeout: str = "30") -> subprocess.CompletedProcess[str]:
        return laptop.snippet("service", AC13_TIMEOUT_SECONDS=timeout)

    def test_passes_on_the_services_collection_of_the_plugin(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.mark()
        laptop.service_collection()
        result = self.service(laptop)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS: the service collected acme; 1 new collection(s)", result.stdout)

    def test_a_foreground_collection_cannot_stand_in_for_the_service(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.mark()
        laptop.once()
        self.assertIn("FAIL: no service collection was logged within the timeout", self.service(laptop, "1").stderr)
        # A service line whose collection isn't in the CSV leaves only the foreground's collection.
        with (laptop.ai / "collector.log").open("a", encoding="utf-8") as log:
            log.write(SERVICE_LINE.format("2026-10-26 00:00:00,000"))
        self.assertIn("1 service and 1 foreground collection(s), but usage.csv has 1 new",
                      self.service(laptop, "1").stderr)

    def test_fails_when_the_services_collection_has_the_plugin_off_or_failing(self) -> None:
        for change, expected in (("disable", "acme wrote ['availability/enabled: disabled']"),
                                 ("fail", "acme wrote ['collection/unexpected_error: error']")):
            with self.subTest(change):
                laptop = first_run(self, **DAILY_AND_QUOTA)
                if change == "disable":
                    laptop.collector("plugin", "disable", "acme")
                else:
                    laptop.answer([], exit=3)
                laptop.mark()
                laptop.service_collection()
                self.assertIn(expected, self.service(laptop).stderr)

    def test_follows_a_log_rotation_after_the_mark(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.mark()
        laptop.service_collection()
        # The service's line is only in collector.log.1; the new log is empty.
        (laptop.ai / "collector.log").rename(laptop.ai / "collector.log.1")
        (laptop.ai / "collector.log").write_text("", encoding="utf-8")
        result = self.service(laptop)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reads_a_new_log_from_its_start(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        laptop.mark()
        offset = json.loads(laptop.mark_path.read_text(encoding="utf-8"))["log_offset"]
        laptop.service_collection()
        text = (laptop.ai / "collector.log").read_bytes()
        # Rotated right at the mark: the service's line opens a new log that has grown
        # past the old offset, so seeking to that offset would skip it.
        (laptop.ai / "collector.log").rename(laptop.ai / "collector.log.1")
        with (laptop.ai / "collector.log.1").open("r+b") as rotated:
            rotated.truncate(offset)
        (laptop.ai / "collector.log").write_bytes(text[offset:] + b"x" * (offset + 1000) + b"\n")
        result = self.service(laptop)
        self.assertEqual(result.returncode, 0, result.stderr)


class ReportTests(unittest.TestCase):
    def report(self, laptop: Laptop) -> subprocess.CompletedProcess[str]:
        return laptop.snippet("report")

    def test_passes_for_what_each_profile_can_show(self) -> None:
        cases = [
            ("daily_total", "used", [daily("acme", 1234), quota("acme", 42.5)],
             "usage current (daily_total), quota current (used)"),
            # Event usage with no events yet is "none"; quota none is "unsupported".
            ("events", "none", [], "usage none (events), quota unsupported (none)"),
            ("events", "used", [event("acme", "input_tokens", 100), quota("acme", 5)],
             "usage current (events), quota current (used)"),
            ("none", "remaining", [quota("acme", 80, "remaining_percent")],
             "usage none (none), quota current (remaining)"),
        ]
        for usage, quota_profile, rows, expected in cases:
            with self.subTest(usage=usage, quota=quota_profile):
                laptop = first_run(self, usage=usage, quota=quota_profile, rows=rows)
                result = self.report(laptop)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"PASS: acme is reported ready with every source ok; {expected}", result.stdout)

    def test_fails_for_a_disabled_missing_or_failing_plugin(self) -> None:
        cases = [
            ("disabled", "not enabled"),
            ("missing", "FAIL: there is no"),
            ("failing", "source usage failed"),
        ]
        for change, expected in cases:
            with self.subTest(change):
                laptop = first_run(self, **DAILY_AND_QUOTA)
                if change == "disabled":
                    laptop.collector("plugin", "disable", "acme")
                elif change == "missing":
                    shutil.rmtree(laptop.ai / "plugins" / "acme")
                else:
                    laptop.answer([source_error("acme"), quota("acme", 1)])
                laptop.once()
                result = self.report(laptop)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(expected, result.stderr)


class ValuesTests(unittest.TestCase):
    def test_the_reported_values_match_what_the_plugin_provided(self) -> None:
        cases = [
            ("daily_total", "used", [daily("acme", 1234), quota("acme", 42.5)],
             [f"daily total {datetime_module.date.today().isoformat()}: plugin 1234, report 1234",
              "quota weekly (used): plugin 42.5, report 42.5, basis used"]),
            ("events", "remaining", [event("acme", "input_tokens", 100), event("acme", "output_tokens", 7),
                                     quota("acme", 80, "remaining_percent")],
             ["today's input events: plugin 100, report 100", "today's output events: plugin 7, report 7",
              "quota weekly (remaining): plugin 80, report 80"]),
            ("none", "used", [quota("acme", 12, scope="session")],
             ["usage: not provided (usage none)", "quota session (used): plugin 12, report 12"]),
            ("events", "none", [], ["events: none today", "quota: not provided (quota none)"]),
        ]
        for usage, quota_profile, rows, expected in cases:
            with self.subTest(usage=usage, quota=quota_profile):
                laptop = first_run(self, usage=usage, quota=quota_profile, rows=rows)
                laptop.mark()
                result = laptop.snippet("values")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for line in expected:
                    self.assertIn(line, result.stdout)
                self.assertIn("PASS: the report shows the values acme provided", result.stdout)

    def test_fails_without_a_successful_run(self) -> None:
        laptop = Laptop(self)
        laptop.plugin(rows=[], exit=3)
        laptop.enable()
        laptop.once()
        laptop.mark()
        self.assertIn("FAIL: usage.csv has no successful run of acme", laptop.snippet("values").stderr)

class RollbackTests(unittest.TestCase):
    """Upgrade from 2.3.0, the documented rollback, then uninstall and reinstall (F1 on #25).

    The backup, install, rollback and previous blocks run as written. The stop and
    start blocks act on the real LaunchAgent, so they don't run: a stand-in
    launchctl reports the service as not loaded, and a stand-in pgrep finds nothing.
    """

    def setUp(self) -> None:
        require_history(self, PREVIOUS_SHA)
        require_history(self, PINNED_SHA)
        self.laptop = laptop = Laptop(self, installed=False)
        launchctl = laptop.root / "launchctl"
        launchctl.write_text('#!/bin/sh\ncase "$1" in print) exit 113 ;; *) exit 0 ;; esac\n', encoding="utf-8")
        launchctl.chmod(0o755)
        laptop.launchctl = str(launchctl)
        (laptop.bin / "pgrep").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (laptop.bin / "pgrep").chmod(0o755)
        self.previous = laptop.root / "previous"
        listed = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "--name-only", PREVIOUS_SHA,
                                 "ai_usage_service.py", "ai_usage_plugins"], capture_output=True, text=True, check=True)
        for name in listed.stdout.split():
            (self.previous / name).parent.mkdir(parents=True, exist_ok=True)
            (self.previous / name).write_bytes(subprocess.run(["git", "-C", str(REPO), "show", f"{PREVIOUS_SHA}:{name}"],
                                                              capture_output=True, check=True).stdout)
        self.work = laptop.home / "ai-usage-ac13"
        self.work.mkdir(mode=0o700)
        self.clone = self.work / "src"
        subprocess.run(["git", "clone", "--quiet", str(REPO), str(self.clone)], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(self.clone), "checkout", "--quiet", "--detach", PINNED_SHA],
                       capture_output=True, check=True)
        (self.work / "app-verified").write_text(PINNED_SHA + "\n", encoding="utf-8")
        # Two integrations: Gemini CLI's is managed before the upgrade; Antigravity's is
        # added by the upgrade, because its tool appears after 2.3.0 was installed. Each
        # settings file also holds the owner's own settings.
        self.gemini = laptop.home / ".gemini" / "settings.json"
        self.antigravity = laptop.home / ".gemini" / "antigravity-cli" / "settings.json"
        self.antigravity.parent.mkdir(parents=True)
        self.gemini.write_text(json.dumps({"theme": "Dracula"}), encoding="utf-8")
        self.antigravity.write_text(json.dumps({"vimMode": True}), encoding="utf-8")
        self.wrapper = laptop.ai / "antigravity-statusline"
        providers: dict[str, Any] = {name: {"enabled": False} for name in ("codex", "claude", "grok")}
        providers["gemini_cli"] = {"enabled": True, "configure_telemetry": True,
                                   "executable": str(laptop.bin / "gemini")}
        providers["antigravity"] = {"enabled": True, "executable": str(laptop.bin / "agy")}
        (laptop.ai / "config.json").write_text(json.dumps({"poll_interval_seconds": 3600, "providers": providers},
                                                          indent=2) + "\n", encoding="utf-8")

    # ---- helpers

    def tool(self, name: str) -> None:
        """A provider tool that is found but never run."""
        path = self.laptop.bin / name
        path.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        path.chmod(0o755)

    def run_python(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, *arguments], text=True, capture_output=True,
                                env=self.laptop.environment(), timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def passed(self, name: str) -> subprocess.CompletedProcess[str]:
        result = self.laptop.step(name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)
        return result

    def settings(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def own_settings(self, path: Path, **changes: Any) -> None:
        self.assertTrue(path.exists())
        path.write_text(json.dumps({**self.settings(path), **changes}), encoding="utf-8")

    def record(self) -> dict[str, Any]:
        return json.loads((self.laptop.ai / "install-ownership.json").read_text(encoding="utf-8"))

    def modules(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in sorted((self.laptop.ai / "ai_usage_plugins").glob("*.py"))}

    def assert_consistent(self) -> dict[str, Any]:
        """Every owned file matches the record, every module is recorded, and owned settings are in place."""
        record = self.record()
        for key, entry in record["files"].items():
            path = Path(entry["path"])
            if entry["owned"]:
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"], key)
                self.assertEqual(path.stat().st_mode & 0o777, entry["mode"], key)
        recorded = {Path(entry["path"]).name for key, entry in record["files"].items()
                    if key.startswith("plugin_module:") and entry["owned"]}
        self.assertEqual(set(self.modules()), recorded)
        for name, entry in record["integrations"].items():
            if entry["owned"] and entry["attached"]:
                self.assertEqual(self.settings(Path(entry["settings_path"])).get(entry["field"]), entry["value"], name)
        return record

    def data(self, folder: Path) -> dict[str, bytes]:
        return {name: (folder / name).read_bytes() for name in ("config.json", "state.json", "usage.csv")}

    def upgrade(self) -> Path:
        """2.3.0 installed and collected; then the backup and install blocks; then the plugin collects."""
        laptop = self.laptop
        self.tool("gemini")
        self.run_python(str(self.previous / "ai_usage_service.py"), "install", "--no-start")
        laptop.collector("once")
        before = self.assert_consistent()
        self.assertEqual((before["integrations"]["gemini_cli"]["owned"], before["integrations"]["antigravity"]["owned"]),
                         (True, False))
        self.assertNotIn("plugin_module:external", before["files"])
        self.tool("agy")
        self.passed("backup")
        self.passed("install")
        upgraded = self.assert_consistent()
        self.assertTrue(upgraded["integrations"]["antigravity"]["owned"], "the upgrade added the integration")
        self.assertTrue(upgraded["files"]["antigravity_wrapper"]["owned"])
        self.assertTrue(upgraded["files"]["plugin_module:external"]["owned"], "the upgrade added a module")
        laptop.plugin(rows=[daily("acme", 77)])
        laptop.enable()
        laptop.once()
        self.own_settings(self.gemini, fontSize=14)
        self.own_settings(self.antigravity, editor="zed")
        return Path((self.work / "backup-path").read_text(encoding="utf-8").strip())

    # ---- tests

    def test_upgrade_rollback_uninstall_and_reinstall_stay_consistent(self) -> None:
        laptop = self.laptop
        backup = self.upgrade()
        newer = self.data(laptop.ai)
        plugins = {str(path): path.read_bytes() for path in (laptop.ai / "plugins").rglob("*") if path.is_file()}

        self.passed("rollback")
        rollback = Path((self.work / "rollback-path").read_text(encoding="utf-8").strip())
        self.assertEqual(self.data(rollback), newer, "a copy of the newer data is kept")
        self.assertIn(b'"acme"', newer["config.json"])
        self.assertFalse((laptop.ai / "collector.py").exists())
        self.assertFalse(self.wrapper.exists())
        self.passed("previous")

        # The previous collector, exactly: its code, no module from the upgrade, its data.
        self.assertEqual((laptop.ai / "collector.py").read_bytes(),
                         (self.previous / "ai_usage_service.py").read_bytes())
        self.assertEqual(self.modules(), {path.name: path.read_bytes()
                                          for path in sorted((self.previous / "ai_usage_plugins").glob("*.py"))})
        self.assertNotIn("external.py", self.modules())
        self.assertEqual(self.data(laptop.ai), self.data(backup))
        record = self.assert_consistent()
        self.assertTrue(record["integrations"]["gemini_cli"]["owned"])
        # 2.3.0 sets up Antigravity's integration too now that its tool is here; it owns it.
        self.assertTrue(record["integrations"]["antigravity"]["owned"])
        self.assertTrue(record["files"]["antigravity_wrapper"]["owned"])
        self.assertEqual((self.settings(self.gemini)["theme"], self.settings(self.gemini)["fontSize"]),
                         ("Dracula", 14))
        self.assertEqual((self.settings(self.antigravity)["vimMode"], self.settings(self.antigravity)["editor"]),
                         (True, "zed"))

        self.run_python(str(laptop.ai / "collector.py"), "uninstall")
        for path in (laptop.ai / "collector.py", laptop.ai / "install-ownership.json", self.wrapper,
                     laptop.ai / "ai_usage_plugins", laptop.home / "Library" / "LaunchAgents" / "codes.porta.ai-usage.plist"):
            self.assertFalse(path.exists(), f"uninstall left {path}")
        self.assertEqual(self.settings(self.gemini), {"theme": "Dracula", "fontSize": 14})
        self.assertEqual(self.settings(self.antigravity), {"vimMode": True, "editor": "zed"})
        self.assertEqual(self.data(laptop.ai), self.data(backup))

        self.run_python(str(self.clone / "ai_usage_service.py"), "install", "--no-start")
        record = self.assert_consistent()
        self.assertEqual(sorted(self.modules()),
                         sorted(path.name for path in (self.clone / "ai_usage_plugins").glob("*.py")))
        self.assertEqual((laptop.ai / "collector.py").read_bytes(), (self.clone / "ai_usage_service.py").read_bytes())
        self.assertTrue(record["integrations"]["antigravity"]["owned"] and record["integrations"]["gemini_cli"]["owned"])
        self.assertEqual((self.settings(self.gemini)["theme"], self.settings(self.antigravity)["editor"]),
                         ("Dracula", "zed"))
        self.assertEqual({str(path): path.read_bytes() for path in (laptop.ai / "plugins").rglob("*")
                          if path.is_file()}, plugins, "the plugins folder is never touched")

    def test_a_module_changed_after_the_upgrade_stops_the_rollback_until_moved_aside(self) -> None:
        laptop = self.laptop
        self.upgrade()
        external = laptop.ai / "ai_usage_plugins" / "external.py"
        external.write_text(external.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        stopped = laptop.step("rollback")
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("FAIL: uninstall kept these, because they changed after 2.4.0 installed them", stopped.stderr)
        self.assertIn(f"  {external}", stopped.stderr)
        self.assertNotEqual(laptop.step("previous").returncode, 0, "the previous block checks it again")
        self.assertFalse((laptop.ai / "collector.py").exists(), "nothing was installed over the kept file")
        external.rename(laptop.root / "external.py")
        self.passed("previous")
        self.assertNotIn("external.py", self.modules())
        self.assert_consistent()

    def test_an_integration_setting_changed_after_the_upgrade_is_kept(self) -> None:
        laptop = self.laptop
        self.upgrade()
        own = {"enabled": False}
        self.own_settings(self.gemini, telemetry=own)
        self.passed("rollback")
        result = self.passed("previous")
        self.assertIn(f"kept: your own telemetry in {self.gemini}, which is no longer managed", result.stdout)
        self.assertEqual(self.settings(self.gemini)["telemetry"], own)
        self.assertFalse(self.assert_consistent()["integrations"]["gemini_cli"]["owned"])
        self.run_python(str(laptop.ai / "collector.py"), "uninstall")
        self.assertEqual(self.settings(self.gemini), {"theme": "Dracula", "fontSize": 14, "telemetry": own})

    def test_an_integration_setting_nothing_manages_is_caught(self) -> None:
        laptop = self.laptop
        backup = self.upgrade()
        self.passed("rollback")
        self.passed("previous")
        record = self.record()
        record["integrations"]["gemini_cli"]["owned"] = False
        (laptop.ai / "install-ownership.json").write_text(json.dumps(record), encoding="utf-8")
        checked = laptop.snippet("managed", AC13_BACKUP=str(backup))
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn(f"the gemini_cli integration is in {self.gemini}, but nothing manages it", checked.stderr)

    def test_copying_the_old_files_back_is_caught(self) -> None:
        # The rollback F1 reported: the old files copied over 2.4.0 under 2.4.0's ownership record.
        laptop = self.laptop
        backup = self.upgrade()
        shutil.copy2(backup / "collector.py", laptop.ai / "collector.py")
        shutil.copytree(backup / "ai_usage_plugins", laptop.ai / "ai_usage_plugins", dirs_exist_ok=True)
        for name in ("config.json", "state.json", "usage.csv"):
            shutil.copy2(backup / name, laptop.ai / name)
        checked = laptop.snippet("managed", AC13_BACKUP=str(backup))
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn(f"{laptop.ai / 'collector.py'} does not match the ownership record", checked.stderr)
        self.assertIn(f"{laptop.ai / 'collector.py'} is not managed as it was before the upgrade", checked.stderr)
        uninstalled = self.run_python(str(laptop.ai / "collector.py"), "uninstall")
        self.assertIn(f"Preserved modified installer file: {laptop.ai / 'collector.py'}", uninstalled.stdout)


class AuditTests(unittest.TestCase):
    def test_the_audit_hook_records_the_plugin_start(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        started = [json.loads(line)[1] for line in laptop.started.read_text(encoding="utf-8").splitlines()]
        self.assertIn(str(laptop.ai / "plugins" / "acme" / "collect"), [os.path.realpath(p) for p in started])
        self.assertEqual(laptop.rows()[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
