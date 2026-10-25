"""Behavior parity with the merged pre-refactor revision (phase 1 AC-3/AC-4, phase 2 AC-1/AC-4).

The golden files were captured from BASELINE by `driver.py capture`; these
tests never rewrite them. Phase 2 compares with them after applying its
declared INTENTIONAL_CHANGES (driver.expected). Tests that need the BASELINE commit itself (the
ancestry check and the carry-forward run) skip when git history is not
available, unless AI_USAGE_REQUIRE_PARITY_HISTORY=1 makes them required.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.parity import driver

SERVICE_VERSION = next(line.split('"')[1] for line in (driver.REPO / "ai_usage_service.py").read_text(
    encoding="utf-8").splitlines() if line.startswith("VERSION = "))


# The golden files as captured from BASELINE; phase 2 must not touch them.
GOLDEN_SHA256 = {
    "all_missing": "612e96f2136c963b3864753f60e7aff579922b4ce95e04542f428e64e32ea9dc",
    "all_providers": "854fd1e707a67b69d44c8d99217a017c761c5e88e68ad97acc01a630ead65d08",
    "claude_usage_refresh": "12e9e2c3fafa132403657b07a562788d18c6c5a1a481c069f706e175fe78bdab",
    "config_errors": "7dae7cf316a2327e9879733be6089ac49834b64adb1edc11552c070254decd6c",
    "disabled_and_missing": "26ce2daeb3ec6c085e1033898fbad4e40f1c6a33ef7b195f781959cfa8a5e343",
    "grok_acp_billing": "6e303db6858ebd32c167ce7d229f1fe529ab2e38a435e6adf316dbd190c4234e",
    "provider_errors": "b076ff179ba250a04a285885c158fc6ef88340604acc4cb575800dff0819a694",
    "symlinked_config": "cd50174cb421bd4df6c149247090d01a67ec4dd52f422d461ca35da3211d3529",
}


def history_available(sha: str = "") -> bool:
    return subprocess.run(
        ["git", "-C", str(driver.REPO), "cat-file", "-e", f"{sha or driver.baseline_sha()}^{{commit}}"],
        capture_output=True, check=False,
    ).returncode == 0


def require_history(test: unittest.TestCase, sha: str = "") -> None:
    sha = sha or driver.baseline_sha()
    if history_available(sha):
        return
    if os.environ.get("AI_USAGE_REQUIRE_PARITY_HISTORY") == "1":
        test.fail(f"{sha} is not in this checkout's history")
    test.skipTest(f"{sha} not in this checkout (shallow clone)")


def first_poll_index() -> int:
    with tempfile.TemporaryDirectory(prefix="ai-usage-probe-") as temporary:
        steps = driver.SCENARIOS["all_providers"](driver.Sandbox(Path(temporary) / "probe"))
    return next(index for index, step in enumerate(steps) if step["op"] == "once")


class ParityTests(unittest.TestCase):
    def test_every_scenario_matches_its_golden_file(self) -> None:
        self.assertEqual(sorted(path.stem for path in driver.GOLDEN.glob("*.json")), sorted(driver.SCENARIOS))
        differences = {name: found for name, found in driver.compare(driver.REPO).items() if found}
        self.assertEqual(differences, {}, "behavior differs from the pre-refactor baseline")

    def test_the_golden_files_are_unchanged(self) -> None:
        found = {path.stem: hashlib.sha256(path.read_bytes()).hexdigest() for path in driver.GOLDEN.glob("*.json")}
        self.assertEqual(found, GOLDEN_SHA256)

    def test_the_intentional_changes_are_exactly_the_declared_ones(self) -> None:
        # Phase 2's whole output difference from the baseline: a new key needs a PRD change first.
        self.assertEqual(sorted(driver.INTENTIONAL_CHANGES), [
            "csv[claude].usage_source_age_seconds",
            "doctor_json.stdout[*].type",
            "once.stdout.appended_rows",
            "report.collector.capabilities.external_plugins",
            "report.collector.capabilities.instances",
            "report.collector.csv_rows",
            "report.collector.installed_version",
            "report.collector.version",
            "report.providers[*].type",
            "report.providers[claude] failed refresh",
            "report.providers[claude].quota.*measured_at",
            "report.providers[claude].quota.windows[*].becomes_stale_*",
        ])
        self.assertEqual(driver.VERSION, SERVICE_VERSION)
        expected = driver.expected("all_providers")
        report = next(r for r in expected["results"] if r["op"] == "report")["report"]
        self.assertEqual([p["type"] for p in report["providers"]], [p["id"] for p in report["providers"]])
        self.assertIs(report["collector"]["capabilities"]["instances"], True)
        self.assertIs(report["collector"]["capabilities"]["external_plugins"], True)
        doctor = json.loads(next(r for r in expected["results"] if r["op"] == "doctor_json")["stdout"])
        self.assertEqual({key: entry["type"] for key, entry in doctor.items()}, {key: key for key in doctor})

    def test_a_changed_behavior_is_reported_with_its_field(self) -> None:
        # Negative control: the same comparison must catch a one-message change.
        with tempfile.TemporaryDirectory(prefix="ai-usage-parity-control-") as temporary:
            src = Path(temporary) / "src"
            shutil.copytree(driver.REPO, src, ignore=shutil.ignore_patterns(".git", "macos", "tests", "docs"))
            needle = "must be an object"
            changed = [path for path in src.rglob("*.py") if needle in path.read_text(encoding="utf-8")]
            self.assertTrue(changed)
            for path in changed:
                path.write_text(path.read_text(encoding="utf-8").replace(needle, "must be a mapping"), encoding="utf-8")
            found = driver.compare(src, ["config_errors"])["config_errors"]
        self.assertIsNotNone(found)
        self.assertIn("/results/1/error", found)
        self.assertIn("must be a mapping", found)

    def test_normalization_is_limited_to_the_declared_fields(self) -> None:
        golden = json.loads(driver.golden_path("all_providers").read_text(encoding="utf-8"))
        text = json.dumps(golden)
        self.assertEqual(sorted(driver.NORMALIZE), ["csv_fields", "sandbox", "src", "state_keys"])
        self.assertEqual(driver.NORMALIZE["csv_fields"], ["transaction_id"])
        self.assertEqual(driver.NORMALIZE["state_keys"], ["inode"])
        self.assertNotIn("/var/folders", text)
        self.assertNotIn(tempfile.gettempdir(), text)

    def test_golden_files_come_from_an_ancestor_of_this_revision(self) -> None:
        require_history(self)
        result = subprocess.run(
            ["git", "-C", str(driver.REPO), "merge-base", "--is-ancestor", driver.baseline_sha(), "HEAD"],
            capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, "BASELINE is not an ancestor of HEAD")

    def test_state_and_csv_written_by_the_baseline_carry_forward(self) -> None:
        # Phase 1 AC-4: the baseline's config, state and CSV keep working with no migration.
        require_history(self)
        with tempfile.TemporaryDirectory(prefix="ai-usage-baseline-") as temporary:
            baseline = driver.extract_baseline(Path(temporary) / "src")
            handed_off = driver.run_scenario(baseline, "all_providers", handoff=(driver.REPO, first_poll_index() + 1))
        self.assertIsNone(driver.first_difference(driver.expected("all_providers", first_poll_index() + 1), handed_off))

    def test_state_and_csv_written_by_phase_1_carry_forward(self) -> None:
        # Phase 2 AC-4: the phase-1 squash commit's first poll, then this tree in the same sandbox.
        require_history(self, driver.phase1_sha())
        with tempfile.TemporaryDirectory(prefix="ai-usage-phase1-") as temporary:
            phase1 = driver.extract_baseline(Path(temporary) / "src", driver.phase1_sha())
            handed_off = driver.run_scenario(phase1, "all_providers", handoff=(driver.REPO, first_poll_index() + 1))
        self.assertIsNone(driver.first_difference(driver.expected("all_providers", first_poll_index() + 1), handed_off))


if __name__ == "__main__":
    unittest.main()
