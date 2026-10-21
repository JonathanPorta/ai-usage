"""Behavior parity with the merged pre-refactor revision (phase 1, AC-3 and AC-4).

The golden files were captured from BASELINE by `driver.py capture`; these
tests never rewrite them. Tests that need the BASELINE commit itself (the
ancestry check and the carry-forward run) skip when git history is not
available, unless AI_USAGE_REQUIRE_PARITY_HISTORY=1 makes them required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.parity import driver


def history_available() -> bool:
    return subprocess.run(
        ["git", "-C", str(driver.REPO), "cat-file", "-e", f"{driver.baseline_sha()}^{{commit}}"],
        capture_output=True, check=False,
    ).returncode == 0


def require_history(test: unittest.TestCase) -> None:
    if history_available():
        return
    if os.environ.get("AI_USAGE_REQUIRE_PARITY_HISTORY") == "1":
        test.fail(f"BASELINE {driver.baseline_sha()} is not in this checkout's history")
    test.skipTest("BASELINE commit not in this checkout (shallow clone)")


class ParityTests(unittest.TestCase):
    def test_every_scenario_matches_its_golden_file(self) -> None:
        self.assertEqual(sorted(path.stem for path in driver.GOLDEN.glob("*.json")), sorted(driver.SCENARIOS))
        differences = {name: found for name, found in driver.compare(driver.REPO).items() if found}
        self.assertEqual(differences, {}, "behavior differs from the pre-refactor baseline")

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
        # AC-4: the baseline's config, state and CSV keep working with no migration.
        require_history(self)
        with tempfile.TemporaryDirectory(prefix="ai-usage-baseline-") as temporary:
            baseline = driver.extract_baseline(Path(temporary) / "src")
            first_poll = next(index for index, step in enumerate(driver.SCENARIOS["all_providers"](
                driver.Sandbox(Path(temporary) / "probe"))) if step["op"] == "once")
            handed_off = driver.run_scenario(baseline, "all_providers", handoff=(driver.REPO, first_poll + 1))
        expected = json.loads(driver.golden_path("all_providers").read_text(encoding="utf-8"))
        self.assertIsNone(driver.first_difference(expected, handed_off))


if __name__ == "__main__":
    unittest.main()
