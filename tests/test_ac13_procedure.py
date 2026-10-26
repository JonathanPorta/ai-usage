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

    def __init__(self, test: unittest.TestCase) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-usage-ac13-")
        test.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.ai = self.home / ".ai-usage"
        self.ai.mkdir(parents=True)
        self.ai.chmod(0o700)
        shutil.copy2(SCRIPT, self.ai / "collector.py")
        (self.ai / "collector.py").chmod(0o755)
        shutil.copytree(REPO / "ai_usage_plugins", self.ai / "ai_usage_plugins",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.ai / "config.json").write_text(
            json.dumps({"poll_interval_seconds": 3600, "providers": BUILT_INS_OFF}, indent=2) + "\n", encoding="utf-8")
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        with (agents / "codes.porta.ai-usage.plist").open("wb") as handle:
            plistlib.dump({"Label": "codes.porta.ai-usage",
                           "ProgramArguments": [sys.executable, str(self.ai / "collector.py"), "daemon"]}, handle)
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
        return {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "AI_USAGE_LAUNCHCTL": "",
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
        self.assertEqual(sorted(snippets()), ["foreground", "mark", "plugin", "report", "service", "snapshot", "values"])
        self.assertEqual(sorted(steps()), ["app", "clone", "install", "place", "preflight", "upgrade"])

    def test_a_check_used_twice_is_the_same_both_times(self) -> None:
        copies = snippet_copies(procedure())
        self.assertEqual(len(copies["mark"]), 2)
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


class AuditTests(unittest.TestCase):
    def test_the_audit_hook_records_the_plugin_start(self) -> None:
        laptop = first_run(self, **DAILY_AND_QUOTA)
        started = [json.loads(line)[1] for line in laptop.started.read_text(encoding="utf-8").splitlines()]
        self.assertIn(str(laptop.ai / "plugins" / "acme" / "collect"), [os.path.realpath(p) for p in started])
        self.assertEqual(laptop.rows()[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
