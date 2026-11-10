"""The Claude /usage refresh runs at normal priority while the daemon stays in the background band (#31).

launchd's Background process type pins a job and all its children in macOS's
background band. The LaunchAgent is now Standard, the daemon re-enters the band
itself, and only the `/usage` child leaves it ("normal", the default). An A/B
mode ("alternate") lets diagnostics compare the two arms on the scheduled path.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any
import unittest

CLAUDE = importlib.import_module("ai_usage_plugins.claude")
KEY = CLAUDE.REFRESH_DIAGNOSTICS_KEY
REPO = Path(__file__).resolve().parent.parent
DARWIN = sys.platform == "darwin"

# Run in a separate interpreter so the test process itself is never demoted.
PROBE = """import json, os, sys, tempfile
sys.path.insert(0, {repo!r})
import ai_usage_service
from ai_usage_plugins import claude
ai_usage_service.os.getppid = lambda: 1  # as if launchd had started it
band = ai_usage_service.enter_background_band()
out = {{"band": band, "self": claude.process_scheduling_priority(os.getpid())}}
for priority in ("normal", "background"):
    trace = {{"observe_priority": True}}
    try:
        claude.run_claude_usage({fake!r}, 5, None, trace, priority)
    except RuntimeError:
        pass
    out[priority] = {{"observed": trace.get("child_priority"), "reported": open({mark!r}).read().strip(),
                      "exit": trace.get("exit_status"), "run": trace.get("run_seconds")}}
print(json.dumps(out))
"""


class PrioritySelectionTests(unittest.TestCase):
    def test_only_a_daemon_launchd_started_enters_the_band(self) -> None:
        import ai_usage_service
        if DARWIN:
            self.assertTrue(ai_usage_service.enter_background_band().startswith("unchanged (not started"))
        else:
            self.assertEqual(ai_usage_service.enter_background_band(), "unchanged")

    def test_normal_is_the_default_and_the_two_fixed_arms_are_honored(self) -> None:
        self.assertEqual(CLAUDE.refresh_priority({}, None), "normal")
        self.assertEqual(CLAUDE.refresh_priority({"refresh_priority": "background"}, None), "background")
        self.assertEqual(CLAUDE.refresh_priority({"refresh_priority": "bogus"}, {}), "normal")

    def test_alternate_takes_the_arm_the_last_run_did_not(self) -> None:
        config = {"refresh_priority": "alternate"}
        self.assertEqual(CLAUDE.refresh_priority(config, {}), "normal")
        self.assertEqual(CLAUDE.refresh_priority(config, {KEY: [{"priority": "normal"}]}), "background")
        self.assertEqual(CLAUDE.refresh_priority(config, {KEY: [{"priority": "background"}]}), "normal")
        self.assertEqual(CLAUDE.refresh_priority(config, None), "normal", "no diagnostics, no experiment")

    def test_the_preexec_is_macos_only_and_known_arms_only(self) -> None:
        self.assertIsNone(CLAUDE.usage_priority_preexec("bogus"))
        self.assertEqual(CLAUDE.usage_priority_preexec("normal") is not None, DARWIN)


@unittest.skipUnless(DARWIN, "macOS scheduling bands")
class BandTests(unittest.TestCase):
    def test_a_background_daemon_runs_the_usage_child_at_normal_priority_only_when_asked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-usage-prio-") as temporary:
            root = Path(temporary)
            mark = root / "pri"
            fake = root / "claude"
            fake.write_text(f"#!/bin/sh\nsleep 1.5\nps -o pri= -p $$ > {mark}\n"
                            "echo '{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false}'\n",
                            encoding="utf-8")
            fake.chmod(0o755)
            probe = PROBE.format(repo=str(REPO), fake=str(fake), mark=str(mark))
            result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=900,
                                    check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads(result.stdout)
        self.assertEqual(out["band"], "background")
        self.assertEqual(out["self"], 4, "the daemon is in the background band")
        background, normal = out["background"], out["normal"]
        self.assertEqual(int(background["reported"]), 4, "the background arm stays in the band")
        self.assertGreater(int(normal["reported"]), 4, "the normal arm leaves it")
        self.assertEqual((normal["exit"], background["exit"]), (0, 0))
        self.assertEqual(background["observed"], 4)
        self.assertGreater(normal["observed"], 4)


class RecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ai-usage-prio-")
        self.root = Path(self._tmp.name)
        self.cache = self.root / ".claude.json"
        self.cache.write_text(json.dumps({
            "oauthAccount": {"accountUuid": "a"},
            "cachedUsageUtilization": {"accountUuid": "a", "fetchedAtMs": time.time() * 1000 - 3_600_000,
                                       "utilization": {"limits": [{"kind": "session", "percent": 8}]}},
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def collect(self, body: str, state: dict[str, Any], **settings: Any) -> None:
        fake = self.root / "claude"
        fake.write_text(f"#!{sys.executable}\nimport time, sys\n{body}", encoding="utf-8")
        fake.chmod(0o755)
        config = {"usage_cache_file": str(self.cache), "timeout_seconds": 5, **settings}
        CLAUDE.collect_claude_subscription_usage("2026-11-10T12:00:00Z", config, fake, state)

    def test_each_record_names_its_arm_load_and_run_time_and_a_timeout_says_if_the_child_exited(self) -> None:
        state: dict[str, Any] = {}
        self.collect("time.sleep(60)\n", state, refresh_priority="alternate")
        # Generous: under heavy load a background-band child can take long even to exit.
        self.collect("sys.exit(2)\n", state, refresh_priority="alternate", timeout_seconds=120)
        timeout, failed = state[KEY]
        self.assertEqual((timeout["priority"], failed["priority"]), ("normal", "background"))
        self.assertEqual((timeout["outcome"], timeout["child_exited"]), ("timeout", False))
        self.assertGreaterEqual(timeout["run_seconds"], 5)
        self.assertLess(timeout["run_seconds"], timeout["duration_seconds"] + 0.001)
        self.assertEqual("child_exited" in failed, failed["timed_out"])
        if not failed["timed_out"]:
            self.assertEqual(failed["exit_status"], 2)
        for record in (timeout, failed):
            self.assertEqual(len(record["load_average"]), 3)
            if DARWIN:
                self.assertIsInstance(record["child_priority"], int)


if __name__ == "__main__":
    unittest.main()
