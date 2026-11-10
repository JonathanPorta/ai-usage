"""Claude /usage refresh diagnostics (issue #31): bounded, sanitized, per account, off by default.

With `"refresh_diagnostics": true` in an account's settings, each `/usage` run
leaves one record in that account's own state: a failure with its duration,
exit status or timeout and a short redacted stderr tail; a success as timing
only. Nothing raw is kept, and records are bounded by count and age.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Optional
import unittest

from tests.parity import driver
from tests.test_provider_instances import AZ, OFF, REPO, installed_collector
from tests.test_stale_quota_reporting import FAKE_CLAUDE, cache

CLAUDE = importlib.import_module("ai_usage_plugins.claude")
KEY = CLAUDE.REFRESH_DIAGNOSTICS_KEY

SECRET_STDERR = "\n".join([
    "starting /usage for jonathan@example.com",
    "Authorization: Bearer sk-ant-oat01-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "token=abcdefghijklmnopqrstuvwxyz0123456789 api_key: 'hunter2'",
    "reading /Users/someone/.claude-az/.claude.json",
    "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    "GET https://api.example.com/oauth/usage?code=SECRETCODE&state=xyz",
    "waiting for the network",
])
SECRETS = ("jonathan@example.com", "sk-ant-", "AAAAAAAAAAAAAAAAAAAA", "abcdefghijklmnop", "hunter2",
           "/Users/someone", "eyJhbGci", "SECRETCODE")


class RedactionTests(unittest.TestCase):
    def test_secrets_emails_and_home_paths_never_survive(self) -> None:
        tail = CLAUDE.refresh_output_tail(SECRET_STDERR, home="/Users/someone")
        text = json.dumps(tail)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertIn("~/.claude-az/.claude.json", text)
        self.assertIn("waiting for the network", text, "structural lines are kept")
        self.assertIn("https://api.example.com/oauth/usage?[redacted]", text)

    def test_another_users_home_is_shortened_too(self) -> None:
        self.assertEqual(CLAUDE.redact_refresh_text("open /home/bob/x", home="/Users/me"), "open ~/x")

    def test_stdout_is_kept_as_structure_only(self) -> None:
        answer = json.dumps({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 812,
                             "result": "Current session 8% used; private words", "session_id": "abc"})
        shape = CLAUDE.refresh_answer_shape(answer)
        self.assertEqual(shape, {"bytes": len(answer), "json": True, "type": "result", "subtype": "success",
                                 "is_error": False, "duration_ms": 812})
        self.assertEqual(CLAUDE.refresh_answer_shape(b"not json at all"), {"bytes": 15, "json": False})
        self.assertEqual(CLAUDE.refresh_answer_shape(None), {"bytes": 0})


class BoundsTests(unittest.TestCase):
    def test_the_tail_is_bounded_by_lines_line_length_and_bytes(self) -> None:
        output = "\n".join(f"line {index} " + "x y " * 300 for index in range(50))
        tail = CLAUDE.refresh_output_tail(output)
        self.assertLessEqual(len(tail), CLAUDE.REFRESH_DIAGNOSTICS_MAX_LINES)
        self.assertTrue(all(len(line) <= CLAUDE.REFRESH_DIAGNOSTICS_LINE_CHARS for line in tail))
        self.assertLessEqual(len(json.dumps(tail).encode("utf-8")), CLAUDE.REFRESH_DIAGNOSTICS_DETAIL_BYTES)
        self.assertTrue(tail[-1].startswith("line 49 "), "the newest lines are the ones kept")

    def test_undecodable_and_control_bytes_are_harmless(self) -> None:
        tail = CLAUDE.refresh_output_tail(b"\xff\xfe bad \x1b[31mred\x1b[0m\n")
        self.assertEqual(len(tail), 1)
        self.assertTrue(all(character.isprintable() for character in tail[0]))


class RetentionTests(unittest.TestCase):
    def test_records_are_bounded_by_count_and_age(self) -> None:
        now = time.time()
        state: dict[str, Any] = {KEY: [{"at": CLAUDE.timestamp_to_iso(now - 15 * 86400), "outcome": "old"}]}
        for index in range(CLAUDE.REFRESH_DIAGNOSTICS_MAX_RECORDS + 5):
            CLAUDE.keep_refresh_diagnostic(state, {"at": CLAUDE.timestamp_to_iso(now), "n": index}, now)
        records = state[KEY]
        self.assertEqual(len(records), CLAUDE.REFRESH_DIAGNOSTICS_MAX_RECORDS)
        self.assertNotIn("old", [r.get("outcome") for r in records], "records past the age limit are dropped")
        self.assertEqual(records[-1]["n"], CLAUDE.REFRESH_DIAGNOSTICS_MAX_RECORDS + 4)

    def test_switching_diagnostics_off_drops_what_was_kept(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-usage-diag-") as temporary:
            state: dict[str, Any] = {KEY: [{"at": "2026-11-09T00:00:00Z"}]}
            settings = {**CLAUDE.DEFAULT_SETTINGS, "executable": str(Path(temporary) / "missing"),
                        "stats_file": str(Path(temporary) / "stats.json"),
                        "projects_dir": str(Path(temporary) / "projects"),
                        "usage_cache_file": str(Path(temporary) / ".claude.json")}
            CLAUDE.collect_claude("2026-11-09T00:00:00Z", settings, False, state)
            self.assertNotIn(KEY, state)


class RunTests(unittest.TestCase):
    """A real subprocess per case: timeout, non-zero exit, stale answer, success."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ai-usage-diag-")
        self.root = Path(self._tmp.name)
        self.cache = self.root / ".claude.json"
        self.now_ms = time.time() * 1000

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_cache(self, fetched_at_ms: float) -> None:
        self.cache.write_text(json.dumps({
            "oauthAccount": {"accountUuid": "acct-1"},
            "cachedUsageUtilization": {"accountUuid": "acct-1", "fetchedAtMs": fetched_at_ms,
                                       "utilization": {"limits": [{"kind": "session", "percent": 8}]}},
        }), encoding="utf-8")

    def fake(self, body: str) -> Path:
        path = self.root / "claude"
        path.write_text(f"#!{sys.executable}\nimport json, sys, time\n"
                        f"cache = {str(self.cache)!r}\n{body}", encoding="utf-8")
        path.chmod(0o755)
        return path

    def collect(self, executable: Path, state: Optional[dict[str, Any]], **settings: Any) -> list[dict[str, Any]]:
        config = {"usage_cache_file": str(self.cache), "usage_cache_max_age_seconds": 900,
                  "config_dir": str(Path.home() / ".claude-az"), "timeout_seconds": 5, **settings}
        return CLAUDE.collect_claude_subscription_usage("2026-11-09T12:00:00Z", config, executable, state)

    def test_a_timeout_records_duration_the_flag_processes_and_a_redacted_tail(self) -> None:
        self.write_cache(self.now_ms - 3_600_000)
        executable = self.fake(f"sys.stderr.write({SECRET_STDERR!r} + '\\n'); sys.stderr.flush()\n"
                               "time.sleep(60)\n")
        state: dict[str, Any] = {}
        rows = self.collect(executable, state)
        self.assertEqual(rows[0]["message"], "claude /usage did not finish within 5s",
                         "the collection's own rows are unchanged by diagnostics")
        [record] = state[KEY]
        self.assertEqual((record["outcome"], record["timed_out"], record["exit_status"], record["timeout_seconds"]),
                         ("timeout", True, None, 5))
        self.assertEqual((record["collected_at"], record["profile"]), ("2026-11-09T12:00:00Z", "~/.claude-az"))
        self.assertGreaterEqual(record["duration_seconds"], 5)
        self.assertLess(record["duration_seconds"], 15)
        self.assertIsInstance(record["processes_at_timeout"], list)
        self.assertLessEqual(len(record["processes_at_timeout"]), CLAUDE.REFRESH_DIAGNOSTICS_MAX_PROCESSES)
        self.assertEqual(record["stdout"]["bytes"], 0)
        self.assertIn("waiting for the network", record["stderr_tail"])
        self.assertFalse(record["cache_fetched_seconds_after_start"] > 0, "the cache was not refreshed")
        text = json.dumps(record)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertNotIn(str(Path.home()), text)

    def test_a_nonzero_exit_records_its_status(self) -> None:
        self.write_cache(self.now_ms - 3_600_000)
        state: dict[str, Any] = {}
        self.collect(self.fake("sys.stderr.write('rate limited\\n'); sys.exit(3)\n"), state)
        [record] = state[KEY]
        self.assertEqual((record["outcome"], record["exit_status"], record["timed_out"]), ("exit_status", 3, False))
        self.assertEqual(record["stderr_tail"], ["rate limited"])
        self.assertEqual(record["message"], "claude /usage failed: rate limited")

    def test_an_answer_from_an_old_cache_is_recorded_as_stale(self) -> None:
        self.write_cache(self.now_ms - 2 * 3_600_000)
        state: dict[str, Any] = {}
        self.collect(self.fake("print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,"
                               " 'result': 'private'}))\n"), state)
        [record] = state[KEY]
        self.assertEqual((record["outcome"], record["exit_status"]), ("stale_answer", 0))
        self.assertNotIn("private", json.dumps(record))
        self.assertTrue(record["stdout"]["json"])

    def test_a_success_records_timing_only(self) -> None:
        self.write_cache(self.now_ms - 3_600_000)
        state: dict[str, Any] = {}
        self.collect(self.fake(
            "sys.stderr.write('noise jonathan@example.com\\n')\n"
            "d = json.load(open(cache)); d['cachedUsageUtilization']['fetchedAtMs'] = time.time() * 1000\n"
            "json.dump(d, open(cache, 'w'))\n"
            "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'x'}))\n"),
            state)
        [record] = state[KEY]
        self.assertEqual(set(record), {"at", "collected_at", "profile", "outcome", "duration_seconds",
                                       "timeout_seconds", "timed_out", "exit_status", "seconds_since_wake"})
        self.assertEqual((record["outcome"], record["exit_status"]), ("ok", 0))

    def test_without_a_state_nothing_is_recorded_and_a_fresh_cache_runs_nothing(self) -> None:
        self.write_cache(self.now_ms - 3_600_000)
        self.collect(self.fake("sys.exit(3)\n"), None)  # diagnostics off: no state to write to
        self.write_cache(self.now_ms - 60_000)
        state: dict[str, Any] = {}
        self.collect(self.fake("sys.exit(3)\n"), state)
        self.assertEqual(state, {}, "a cache read without a run leaves no record")


class CollectorTests(unittest.TestCase):
    """Through the real `once` command: only the account that opts in keeps records, in its own state."""

    def test_only_the_opted_in_account_records_its_timeout(self) -> None:
        def plan(box: driver.Sandbox) -> list[dict[str, Any]]:
            box.write("home/.claude.json", cache(driver.FIXED_TS - 2 * driver.HOUR, 8))
            box.write("home/hang", "")
            box.write("home/.claude/stats-cache.json", driver.CLAUDE_STATS, mtime=driver.FIXED_TS - driver.HOUR)
            box.write("home/claude-az/.claude.json", cache(driver.FIXED_TS - 2 * driver.HOUR, 50))
            box.write("home/claude-az/hang", "")
            executable = box.python_executable("claude", FAKE_CLAUDE)
            claude = {"executable": executable, "timeout_seconds": 5}
            az = {**AZ, "settings": {**AZ["settings"], "executable": executable, "timeout_seconds": 5,
                                     "refresh_diagnostics": True}}
            box.write_config({"codex": OFF, "claude": claude, "antigravity": OFF, "gemini_cli": OFF, "grok": OFF},
                             instances=[az])
            installed_collector(box, "2.4.0")
            return [box.once()]

        state = driver.run_scenario(REPO, plan)["results"][0]["state"]
        self.assertNotIn(KEY, state, "the default account did not opt in")
        [record] = state["instances"]["claude-az"][KEY]
        self.assertEqual((record["outcome"], record["timed_out"], record["profile"]),
                         ("timeout", True, "~/claude-az"))


if __name__ == "__main__":
    unittest.main()
