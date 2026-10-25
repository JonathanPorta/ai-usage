"""The AC-10 procedure's checks, run against an isolated installed collector.

`tasks/phase-2/procedure-reinstall-ac10.md` is run by hand on the owner's Mac. Its
Python snippets (each starting `# ac10: <name>`) are run here, with HOME pointing at
a sandbox that has two Claude accounts, a fake `claude` and a fake launchctl, so a
broken snippet fails CI instead of the owner's acceptance. Nothing here touches the
real ~/.claude, ~/.claude-az or ~/.ai-usage.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Optional
import unittest

import test_ai_usage_service as black_box
from tests.parity import driver
from tests.parity.test_parity import require_history
from tests.test_provider_instances import FAKE_CLAUDE, message, usage_cache

from tests import isolation  # noqa: E402

# Phase 3: in-process collection and reports never scan the developer's ~/.ai-usage/plugins.
isolation.module_fixtures(globals())

SERVICE = black_box.SERVICE
REPO = black_box.SCRIPT.parent
PROCEDURE = REPO / "tasks" / "phase-2" / "procedure-reinstall-ac10.md"
SNIPPET = re.compile(r"python3 - <<'EOF'\n(# ac10: (\S+)\n.*?)\nEOF\n", re.DOTALL)


def snippets() -> dict[str, str]:
    return {name: code for code, name in SNIPPET.findall(PROCEDURE.read_text(encoding="utf-8"))}


class ProcedureHome:
    """An installed collector in an isolated HOME with `claude` and `claude-az`."""

    def __init__(self, source: Path = REPO) -> None:
        self.case = black_box.IsolatedHome()
        self.home = self.case.home
        self.runs = self.case.root / "claude-runs.jsonl"
        self.ai = self.home / ".ai-usage"
        (self.home / ".claude" / "projects" / "work").mkdir(parents=True)
        (self.home / ".claude" / "projects" / "work" / "s1.jsonl").write_text(
            message("msg_default", 100), encoding="utf-8")
        (self.home / ".claude.json").write_text(usage_cache(8), encoding="utf-8")
        (self.home / ".claude-az" / "projects" / "work").mkdir(parents=True)
        (self.home / ".claude-az" / "projects" / "work" / "s1.jsonl").write_text(
            message("msg_az", 200), encoding="utf-8")
        (self.home / ".claude-az" / ".claude.json").write_text(usage_cache(50), encoding="utf-8")
        fake = self.case.bin / "claude"
        fake.write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}", encoding="utf-8")
        fake.chmod(0o755)
        self.collector("install", "--no-start", script=source / "ai_usage_service.py")

    def environment(self, **extra: str) -> dict[str, str]:
        return {**self.case.environment(), "AI_USAGE_TEST_CLAUDE_RUNS": str(self.runs), **extra}

    def collector(self, *arguments: str, script: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(script or self.ai / "collector.py"), *arguments],
                                text=True, capture_output=True, env=self.environment(), timeout=120, check=False)
        if check and result.returncode not in (0, 2):
            raise AssertionError(f"{arguments}: {result.stdout}{result.stderr}")
        return result

    def add_account(self) -> None:
        self.collector("instance", "add", "--plugin", "claude", "--id", "claude-az", "--label", "Claude (az)",
                       "--config-dir", "~/.claude-az")

    def snippet(self, name: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-"], input=snippets()[name], text=True, capture_output=True,
                              env=self.environment(**extra), cwd=REPO, timeout=120, check=False)

    def close(self) -> None:
        self.case.close()


class ProcedureSnippetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = ProcedureHome()
        self.addCleanup(self.box.close)
        self.backup = self.box.case.root / "backup"

    def test_the_procedure_has_each_check(self) -> None:
        self.assertEqual(sorted(snippets()), ["doctor", "history", "limits", "mark", "service", "snapshot"])

    def test_doctor_limits_and_history_read_two_separate_accounts(self) -> None:
        self.box.collector("once")
        self.assertEqual(self.box.snippet("snapshot", AC10_SNAPSHOT=str(self.backup)).returncode, 0)
        self.box.add_account()
        self.box.collector("once")

        doctor = self.box.snippet("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        lines = [line.split() for line in doctor.stdout.splitlines()]
        self.assertEqual([(line[0], line[1]) for line in lines], [("claude", "claude"), ("claude-az", "claude")])
        self.assertNotEqual(lines[0][2], lines[1][2], "separate transcript folders")
        self.assertTrue(all(int(line[3]) > 0 for line in lines), doctor.stdout)

        limits = self.box.snippet("limits")
        self.assertEqual(limits.returncode, 0, limits.stderr)
        self.assertEqual([line.split()[0] for line in limits.stdout.splitlines()], ["claude", "claude-az"])

        history = self.box.snippet("history", AC10_SNAPSHOT=str(self.backup))
        self.assertEqual(history.returncode, 0, history.stderr)
        # Each fixture message is its input tokens plus one output token.
        self.assertIn("tokens: {'claude': 101, 'claude-az': 201}", history.stdout)
        self.assertIn("earlier rows unchanged: True", history.stdout)
        self.assertIn("added accounts in state: ['claude-az']", history.stdout)

    def test_the_snapshot_finishes_an_interrupted_commit_first(self) -> None:
        self.box.collector("once")
        state_path = self.box.ai / "state.json"
        journal_state = {**json.loads(state_path.read_text(encoding="utf-8")), "journal": "committed"}
        row = {field: "" for field in SERVICE.CSV_FIELDS}
        row.update(collected_at="2026-10-21T00:00:00Z", provider="claude", category="usage", metric="input_tokens",
                   value="7", source="claude-session-log", status="ok")
        SERVICE.write_pending_transaction(state_path, "tx-interrupted",
                                          SERVICE.transaction_rows("tx-interrupted", [row]), journal_state)
        result = self.box.snippet("snapshot", AC10_SNAPSHOT=str(self.backup))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "", "the owner is told any other output means stop")
        self.assertIn("recovered pending collection transaction tx-interrupted",
                      (self.box.ai / "collector.log").read_text(encoding="utf-8"))
        self.assertFalse(SERVICE.pending_state_path(state_path).exists())
        self.assertEqual(json.loads((self.backup / "state.json").read_text(encoding="utf-8"))["journal"], "committed")
        with (self.backup / "usage.csv").open(encoding="utf-8") as handle:
            self.assertIn("tx-interrupted", {r["transaction_id"] for r in csv.DictReader(handle)})
        self.assertEqual((self.backup / "usage.csv").read_bytes(), (self.box.ai / "usage.csv").read_bytes())
        self.assertEqual((self.backup / "collector-version.txt").read_text(encoding="utf-8").strip(), SERVICE.VERSION)

    def test_the_snapshot_waits_for_a_running_collection(self) -> None:
        self.box.collector("once")
        state_path = self.box.ai / "state.json"
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, sys.argv[1]); import ai_usage_service as s; "
             "lock = s.state_transaction_lock(s.Path(sys.argv[2])); lock.__enter__(); "
             "print('held', flush=True); time.sleep(3)", str(REPO), str(state_path)],
            stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        started = time.monotonic()
        result = self.box.snippet("snapshot", AC10_SNAPSHOT=str(self.backup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(time.monotonic() - started, 2.0, "it waited for the lock")

    def test_the_service_step_ignores_a_foreground_collection_and_fails_at_its_timeout(self) -> None:
        self.box.add_account()
        mark = str(self.box.case.root / "mark.json")
        self.assertEqual(self.box.snippet("mark", AC10_MARK=mark).returncode, 0)
        self.box.collector("once")
        result = self.box.snippet("service", AC10_MARK=mark, AC10_TIMEOUT_SECONDS="2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: no service collection within the timeout", result.stderr)

    def service_run(self) -> subprocess.CompletedProcess[str]:
        mark = str(self.box.case.root / "mark.json")
        self.assertEqual(self.box.snippet("mark", AC10_MARK=mark).returncode, 0)
        daemon = subprocess.Popen([sys.executable, str(self.box.ai / "collector.py"), "daemon",
                                   "--config", str(self.box.ai / "config.json")],
                                  env=self.box.environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            return self.box.snippet("service", AC10_MARK=mark, AC10_TIMEOUT_SECONDS="60")
        finally:
            daemon.terminate()
            daemon.wait(timeout=30)

    def test_the_service_step_passes_on_a_service_collection_of_both_accounts(self) -> None:
        self.box.add_account()
        self.box.collector("once")
        result = self.service_run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"^PASS: \d+ new collection\(s\), the service's among them, each include")

    def test_the_service_step_fails_when_an_account_is_only_a_disabled_marker(self) -> None:
        self.box.add_account()
        self.box.collector("instance", "disable", "claude-az")
        result = self.service_run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no rows for ['claude-az']", result.stderr)

    def test_the_service_step_fails_when_a_later_collection_misses_an_account(self) -> None:
        # Another collection after the service's can't decide the result in its place.
        self.box.add_account()
        mark = str(self.box.case.root / "mark.json")
        self.assertEqual(self.box.snippet("mark", AC10_MARK=mark).returncode, 0)
        self.box.collector("instance", "disable", "claude-az")
        self.box.collector("once")
        with (self.box.ai / "collector.log").open("a", encoding="utf-8") as log:
            log.write("2026-10-21 00:00:00 INFO collection completed rows=1 detected_providers=1 errors=0\n")
        result = self.box.snippet("service", AC10_MARK=mark, AC10_TIMEOUT_SECONDS="5")
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no rows for ['claude-az']", result.stderr)

    def rotated_run(self, rotated_tail: str, new_log: str) -> subprocess.CompletedProcess[str]:
        """Mark, collect in the foreground, then rotate the log the way RotatingFileHandler does."""
        self.box.add_account()
        mark = str(self.box.case.root / "mark.json")
        self.assertEqual(self.box.snippet("mark", AC10_MARK=mark).returncode, 0)
        self.box.collector("once")
        log = self.box.ai / "collector.log"
        with log.open("a", encoding="utf-8") as handle:
            handle.write(rotated_tail)
        log.rename(self.box.ai / "collector.log.1")
        log.write_text(new_log, encoding="utf-8")
        return self.box.snippet("service", AC10_MARK=mark, AC10_TIMEOUT_SECONDS="3")

    SERVICE_LINE = "2026-10-21 00:00:00 INFO collection completed rows=9 detected_providers=2 errors=0\n"

    def test_the_service_step_reads_the_rotated_log_after_the_mark(self) -> None:
        # The service's line is only in collector.log.1, after the mark; the new log is empty.
        result = self.rotated_run(self.SERVICE_LINE, "")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS:", result.stdout)

    def test_the_service_step_reads_a_new_log_from_its_start(self) -> None:
        # The line sits early in a new log that has grown past the old offset: seeking to
        # the old offset would skip it.
        result = self.rotated_run("", self.SERVICE_LINE + "x" * 10_000_000 + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS:", result.stdout)

class PreviousCollectorSnapshotTests(unittest.TestCase):
    def test_the_snapshot_runs_against_the_previously_installed_collector(self) -> None:
        # The snapshot runs before the reinstall, so it imports the 2.2.0 collector.
        require_history(self, driver.phase1_sha())
        with tempfile.TemporaryDirectory(prefix="ai-usage-phase1-") as temporary:
            source = driver.extract_baseline(Path(temporary) / "src", driver.phase1_sha())
            box = ProcedureHome(source)
            self.addCleanup(box.close)
            box.collector("once")
            backup = box.case.root / "backup"
            result = box.snippet("snapshot", AC10_SNAPSHOT=str(backup))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("(version 2.2.0)", result.stdout)
            self.assertEqual(sorted(p.name for p in backup.iterdir()),
                             ["collector-version.txt", "config.json", "state.json", "usage.csv"])


if __name__ == "__main__":
    unittest.main()
