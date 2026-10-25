"""Added provider instances: a second Claude account (phase 2, AC-1 to AC-6, AC-9).

Every test uses an isolated sandbox or HOME with a fake `claude` and a fake
launchctl. None reads the owner's ~/.claude, ~/.claude-az or ~/.ai-usage.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Optional
import unittest
from unittest import mock

import test_ai_usage_service as black_box
from tests.parity import driver
from tests.parity.test_parity import require_history

from tests import isolation  # noqa: E402

# Phase 3: in-process collection and reports never scan the developer's ~/.ai-usage/plugins.
isolation.module_fixtures(globals())

SERVICE = black_box.SERVICE
SCRIPT = black_box.SCRIPT
REPO = SCRIPT.parent
PLIST_NAME = "codes.porta.ai-usage.plist"

# A stand-in for Claude Code's `/usage`: it refreshes the cache Claude Code itself
# would use (inside CLAUDE_CONFIG_DIR when set, else ~/.claude.json) and records
# what it was run with in AI_USAGE_TEST_CLAUDE_RUNS.
FAKE_CLAUDE = f"""import json, os, sys
config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
cache = os.path.join(config_dir, ".claude.json") if config_dir else os.path.join(os.environ["HOME"], ".claude.json")
with open(os.environ["AI_USAGE_TEST_CLAUDE_RUNS"], "a") as log:
    log.write(json.dumps({{"CLAUDE_CONFIG_DIR": config_dir, "cache": os.path.realpath(cache)}}) + "\\n")
document = json.load(open(cache))
document["cachedUsageUtilization"]["fetchedAtMs"] = {driver.FIXED_TS * 1000!r}
json.dump(document, open(cache, "w"))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "usage"}}))
"""

AZ = {"id": "claude-az", "plugin": "claude", "label": "Claude (az)", "settings": {"config_dir": "~/claude-az"}}
OFF = {"enabled": False}


def usage_cache(session_percent: int) -> str:
    """A stale cache (two hours old), so the collector refreshes it once."""
    document = json.loads(driver.claude_usage_cache((driver.FIXED_TS - 2 * driver.HOUR) * 1000))
    document["cachedUsageUtilization"]["utilization"]["limits"][0]["percent"] = session_percent
    return json.dumps(document)


def message(message_id: str, input_tokens: int) -> str:
    return driver.claude_line(message_id, "s-1", "claude-opus-5", "2026-08-21T09:00:00.000Z",
                              {"input_tokens": input_tokens, "output_tokens": 1})


def installed_collector(box: driver.Sandbox, version: str) -> None:
    """A LaunchAgent whose collector script reports `version` (read as text, never run)."""
    script = box.write(f"installed-{version}/collector.py", f'VERSION = "{version}"\n')
    agents = box.home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    with (agents / PLIST_NAME).open("wb") as handle:
        plistlib.dump({"Label": "codes.porta.ai-usage", "ProgramArguments": [sys.executable, script]}, handle)


def two_accounts(box: driver.Sandbox, **az_settings: Any) -> None:
    """The default account in ~/.claude (with a stats cache), and claude-az in ~/claude-az (without one).

    Both transcripts hold a message with the same id, as two logins can't share."""
    box.write("home/.claude/projects/work/s1.jsonl", message("msg_shared", 10) + message("msg_default", 100))
    box.write("home/.claude/stats-cache.json", driver.CLAUDE_STATS, mtime=driver.FIXED_TS - driver.HOUR)
    box.write("home/.claude.json", usage_cache(8))
    box.write("home/claude-az/projects/work/s1.jsonl", message("msg_shared", 20) + message("msg_az", 200))
    box.write("home/claude-az/.claude.json", usage_cache(50))
    box.python_executable("claude", FAKE_CLAUDE)
    box.write_config(
        {"codex": OFF, "claude": {}, "antigravity": OFF, "gemini_cli": OFF, "grok": OFF},
        instances=[{**AZ, "settings": {**AZ["settings"], **az_settings}}],
    )
    installed_collector(box, "2.3.0")


def tokens(rows: list[dict[str, str]], provider: str) -> int:
    return sum(int(r["value"]) for r in rows if r["provider"] == provider
               and r["source"] == "claude-session-log" and r["metric"] == "input_tokens")


def session_percent(rows: list[dict[str, str]], provider: str) -> Optional[str]:
    return next((r["value"] for r in rows if r["provider"] == provider and r["metric"] == "used_percent"
                 and r["scope"] == "session"), None)


class Runs:
    """The fake claude's run log, outside the sandbox so it outlives it."""

    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="ai-usage-claude-runs-")
        self.path = Path(self._directory.name) / "runs.jsonl"
        self.box: Optional[driver.Sandbox] = None

    def environment(self, box: driver.Sandbox, hazard: bool = True) -> dict[str, str]:
        self.box = box
        environment = {"AI_USAGE_TEST_CLAUDE_RUNS": str(self.path)}
        if hazard:
            # The collector itself runs where CLAUDE_CONFIG_DIR names the other account.
            environment["CLAUDE_CONFIG_DIR"] = str(box.home / "claude-az")
        return environment

    def read(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def close(self) -> None:
        self._directory.cleanup()


def csv_rows(result: dict[str, Any]) -> list[dict[str, str]]:
    return result["csv"] or []


def poll_rows(result: dict[str, Any], previous: Optional[dict[str, Any]] = None) -> list[dict[str, str]]:
    """The rows one `once` step appended."""
    return csv_rows(result)[len(csv_rows(previous)) if previous else 0:]


# ---- AC-1: the config contract ------------------------------------------------

class InstanceConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ai-usage-instances-")
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        (self.home / "az").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temporary.cleanup)

    def config(self, instances: Any, **claude: Any) -> dict[str, Any]:
        data = self.root / "data"
        return {"poll_interval_seconds": 3600,
                "paths": {"usage_csv": str(data / "usage.csv"), "log_file": str(data / "collector.log"),
                          "state_file": str(data / "state.json"), "cache_dir": str(data / "cache")},
                "providers": {"claude": claude}, "instances": instances}

    def load(self, value: dict[str, Any]) -> dict[str, Any]:
        path = self.root / "config.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return SERVICE.load_config(path)

    def rejects(self, value: dict[str, Any], message: str) -> None:
        with self.assertRaises(ValueError) as caught:
            self.load(value)
        self.assertIn(message, str(caught.exception))

    def az(self, **changes: Any) -> dict[str, Any]:
        return {"id": "claude-az", "plugin": "claude", "label": "Claude (az)",
                "settings": {"config_dir": str(self.home / "az")}, **changes}

    def test_a_valid_instance_loads_with_settings_derived_from_its_config_dir(self) -> None:
        config = self.load(self.config([self.az()]))
        account = SERVICE.accounts(config)[-1]
        self.assertEqual((account.id, account.type, account.label, account.added), ("claude-az", "claude", "Claude (az)", True))
        settings = SERVICE.account_settings(config, account)
        az = self.home / "az"
        self.assertEqual((settings["config_dir"], settings["projects_dir"], settings["stats_file"], settings["usage_cache_file"]),
                         (str(az), str(az / "projects"), str(az / "stats-cache.json"), str(az / ".claude.json")))
        self.assertIsNone(settings["monthly_subscription_usd"])
        self.assertEqual([a.id for a in SERVICE.accounts(config)], [*SERVICE.ai_usage_plugins.BUNDLED, "claude-az"])

    def test_a_tilde_config_dir_is_expanded_to_an_absolute_path(self) -> None:
        config = self.load(self.config([self.az(settings={"config_dir": "~/az"})]))
        settings = SERVICE.account_settings(config, SERVICE.accounts(config)[-1])
        self.assertEqual(settings["config_dir"], str(self.home / "az"))
        self.assertTrue(Path(settings["config_dir"]).is_absolute())

    def test_explicit_settings_apply_after_the_derived_paths(self) -> None:
        custom = str(self.root / "elsewhere" / "projects")
        config = self.load(self.config([self.az(settings={"config_dir": str(self.home / "az"), "projects_dir": custom})]))
        settings = SERVICE.account_settings(config, SERVICE.accounts(config)[-1])
        self.assertEqual(settings["projects_dir"], custom)
        self.assertEqual(settings["usage_cache_file"], str(self.home / "az" / ".claude.json"))

    def test_every_invalid_entry_is_rejected(self) -> None:
        cases = {
            "instances must be a list": {"id": "x"},
            "instances[0] must be an object": ["claude-az"],
            "instances[0].id must be a lowercase slug": [self.az(id="Claude AZ")],
            "instances.claude: the id is a built-in provider's": [self.az(id="claude")],
            "instances.claude-az: the id is used twice": [self.az(), self.az(label="Again")],
            "instances.claude-az.plugin must be one of": [self.az(plugin="claude_code")],
            "instances.claude-az: codex does not support added accounts": [self.az(plugin="codex")],
            "instances.claude-az.label must be a non-empty string": [self.az(label=" ")],
            "instances.claude-az.settings must be an object": [self.az(settings=["x"])],
            "instances.claude-az has unknown keys: enabled": [self.az(enabled=True)],
            "instances.claude-az.settings: config_dir must be a non-empty string": [self.az(settings={})],
        }
        for message, instances in cases.items():
            with self.subTest(message):
                self.rejects(self.config(instances), message)
        self.rejects(self.config([self.az(settings={"config_dir": ""})]), "config_dir must be a non-empty string")

    def test_the_built_in_account_cannot_take_a_config_dir(self) -> None:
        self.rejects(self.config([], config_dir=str(self.home / "az")),
                     "providers.claude.config_dir is only for added accounts")

    def test_a_relative_config_dir_is_rejected(self) -> None:
        # The daemon has no working directory to resolve it against (review finding).
        self.rejects(self.config([self.az(settings={"config_dir": ".claude-az"})]),
                     "config_dir must be an absolute path or start with ~: .claude-az")

    def test_a_symlinked_config_dir_keeps_the_name_it_was_given(self) -> None:
        # Claude Code keys an account's login to its CLAUDE_CONFIG_DIR as named.
        (self.home / "link").symlink_to(self.home / "az")
        config = self.load(self.config([self.az(settings={"config_dir": "~/link"})]))
        settings = SERVICE.account_settings(config, SERVICE.accounts(config)[-1])
        self.assertEqual(settings["config_dir"], str(self.home / "link"))
        environment = sys.modules["ai_usage_plugins.claude"].claude_usage_environment(
            sys.modules["ai_usage_plugins.claude"].account_config_dir("~/link"))
        self.assertEqual(environment["CLAUDE_CONFIG_DIR"], str(self.home / "link"))

    def test_an_account_folder_inside_another_accounts_transcripts_is_rejected(self) -> None:
        nested = self.home / "default" / "projects" / "az"
        nested.mkdir(parents=True)
        self.rejects(self.config([self.az(settings={"config_dir": str(nested)})],
                                 projects_dir=str(self.home / "default" / "projects")),
                     "path role overlap: instances.claude-az.projects_dir")

    def test_two_accounts_reading_the_same_transcripts_are_rejected(self) -> None:
        self.rejects(self.config([self.az()], projects_dir=str(self.home / "az" / "projects")),
                     "path role collision: providers.claude.projects_dir and instances.claude-az.projects_dir")

    def test_a_usage_cache_reached_through_a_symlink_alias_is_rejected(self) -> None:
        shared = self.root / "shared.json"
        shared.write_text("{}", encoding="utf-8")
        (self.home / "az" / ".claude.json").symlink_to(shared)
        self.rejects(self.config([self.az()], usage_cache_file=str(shared)),
                     "path role collision: providers.claude.usage_cache_file and instances.claude-az.usage_cache_file")

    def second(self, config_dir: str) -> dict[str, Any]:
        """Another account whose read paths are all its own, so only the refreshed folder can collide."""
        own = self.root / "second"
        own.mkdir(exist_ok=True)
        return self.az(id="claude-two", label="Two", settings={
            "config_dir": config_dir, "projects_dir": str(own / "projects"),
            "stats_file": str(own / "stats-cache.json"), "usage_cache_file": str(own / ".claude.json")})

    def test_two_accounts_refreshing_the_same_folder_are_rejected_despite_separate_read_paths(self) -> None:
        # Review finding: the second account's `/usage` would refresh the first account's
        # login while it read its own, never-refreshed cache.
        self.rejects(self.config([self.az(), self.second(str(self.home / "az"))]),
                     f"refresh folder collision: instances.claude-az.config_dir and "
                     f"instances.claude-two.config_dir both resolve to {self.home / 'az'}")

    def test_an_account_refreshing_the_default_accounts_folder_is_rejected(self) -> None:
        (self.home / ".claude").mkdir()
        self.rejects(self.config([self.second("~/.claude")]),
                     f"refresh folder collision: providers.claude (Claude Code's default folder) and "
                     f"instances.claude-two.config_dir both resolve to {self.home / '.claude'}")
        self.rejects(self.config([self.second("~")]),
                     "refresh folder overlap: providers.claude (Claude Code's default folder)")

    def test_a_refresh_folder_reached_through_a_symlink_alias_is_rejected(self) -> None:
        (self.home / "link").symlink_to(self.home / "az")
        self.rejects(self.config([self.az(), self.second("~/link")]),
                     "refresh folder collision: instances.claude-az.config_dir and instances.claude-two.config_dir")
        (self.home / "default-link").symlink_to(self.home / ".claude", target_is_directory=True)
        (self.home / ".claude").mkdir()
        self.rejects(self.config([self.second("~/default-link")]),
                     "refresh folder collision: providers.claude (Claude Code's default folder)")

    def test_a_refresh_folder_inside_another_accounts_is_rejected(self) -> None:
        (self.home / "az" / "inner").mkdir()
        self.rejects(self.config([self.az(), self.second(str(self.home / "az" / "inner"))]),
                     "refresh folder overlap: instances.claude-two.config_dir")

    def test_a_refresh_cache_aliased_to_another_accounts_is_rejected_despite_a_read_override(self) -> None:
        # Review finding: distinct folders, but B's implicit .claude.json is a symlink to A's,
        # and B reads a separate file, so both refreshes reach A's cache while B reads a stale one.
        (self.home / "two").mkdir()
        (self.home / "az" / ".claude.json").write_text("{}", encoding="utf-8")
        (self.home / "two" / ".claude.json").symlink_to(self.home / "az" / ".claude.json")
        second = self.second("~/two")
        self.rejects(self.config([self.az(), second]),
                     f"refresh cache collision: instances.claude-az's refreshed .claude.json and "
                     f"instances.claude-two's refreshed .claude.json both resolve to {self.home / 'az' / '.claude.json'}")

    def test_a_refresh_cache_aliased_to_the_default_accounts_is_rejected(self) -> None:
        (self.home / "two").mkdir()
        (self.home / ".claude.json").write_text("{}", encoding="utf-8")
        (self.home / "two" / ".claude.json").symlink_to(self.home / ".claude.json")
        self.rejects(self.config([self.second("~/two")]),
                     f"refresh cache collision: providers.claude's refreshed .claude.json and "
                     f"instances.claude-two's refreshed .claude.json both resolve to {self.home / '.claude.json'}")

    def test_a_refresh_cache_aliased_to_another_accounts_read_cache_is_rejected(self) -> None:
        # The mirror case: B refreshes into the file A reads instead of its own.
        (self.home / "two").mkdir()
        read_by_az = self.root / "az-reads.json"
        read_by_az.write_text("{}", encoding="utf-8")
        (self.home / "two" / ".claude.json").symlink_to(read_by_az)
        az = self.az(settings={"config_dir": str(self.home / "az"), "usage_cache_file": str(read_by_az)})
        self.rejects(self.config([az, self.second("~/two")]),
                     "refresh cache collision: instances.claude-az.usage_cache_file and "
                     "instances.claude-two's refreshed .claude.json")

    def test_an_accounts_own_cache_may_be_read_through_another_name(self) -> None:
        # Within one account, reading its refreshed cache through a symlink is fine.
        (self.home / "az" / ".claude.json").write_text("{}", encoding="utf-8")
        (self.home / "az-cache-link.json").symlink_to(self.home / "az" / ".claude.json")
        self.load(self.config([self.az(settings={"config_dir": str(self.home / "az"),
                                                 "usage_cache_file": str(self.home / "az-cache-link.json")})]))
        (self.home / ".claude.json").write_text("{}", encoding="utf-8")
        self.load(self.config([], usage_cache_file="~/.claude.json"))

    def test_an_account_that_never_refreshes_may_share_a_folder(self) -> None:
        # Disabled, or with plan limits off, it never runs `claude`; its read paths are still checked.
        for off in ({"enabled": False}, {"subscription_usage": False}):
            with self.subTest(off):
                second = self.second(str(self.home / "az"))
                second["settings"].update(off)
                self.load(self.config([self.az(), second]))

    def test_separate_refresh_folders_and_an_accounts_own_paths_are_accepted(self) -> None:
        (self.home / "two").mkdir()
        (self.home / ".claude").mkdir()
        config = self.load(self.config([self.az(), self.az(id="claude-two", label="Two",
                                                            settings={"config_dir": "~/two"})]))
        self.assertEqual([a.id for a in SERVICE.accounts(config)][-2:], ["claude-az", "claude-two"])
        # Its read paths inside its own folder, or moved elsewhere, are its own business.
        self.load(self.config([self.az(settings={"config_dir": str(self.home / "az"),
                                                 "projects_dir": str(self.home / "az" / "projects")})]))
        self.load(self.config([self.second(str(self.home / "two"))]))

    def test_the_default_accounts_usage_cache_is_a_checked_path_role(self) -> None:
        (self.root / "a-directory").mkdir()
        self.rejects(self.config([], usage_cache_file=str(self.root / "a-directory")),
                     "providers.claude.usage_cache_file must be a regular file")

    def test_a_config_without_instances_has_only_the_built_ins(self) -> None:
        value = self.config([])
        del value["instances"]
        config = self.load(value)
        self.assertEqual([a.id for a in SERVICE.accounts(config)], list(SERVICE.ai_usage_plugins.BUNDLED))
        self.assertNotIn("instances", SERVICE.DEFAULT_CONFIG)

    def test_plugins_with_an_integration_do_not_allow_added_instances(self) -> None:
        for plugin in SERVICE.PLUGINS.values():
            with self.subTest(plugin.id):
                if plugin.integration is not None:
                    self.assertIsNone(plugin.instance_settings)
        self.assertEqual([p.id for p in SERVICE.PLUGINS.values() if p.instance_settings], ["claude"])


# ---- AC-3, AC-4, AC-5: collection, state and account isolation ----------------

class TwoAccountCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runs = Runs()
        self.addCleanup(self.runs.close)

    def run_plan(self, steps: list[dict[str, Any]], hazard: bool = True, **az_settings: Any) -> dict[str, Any]:
        def plan(box: driver.Sandbox) -> list[dict[str, Any]]:
            two_accounts(box, **az_settings)
            return [step(box) if callable(step) else step for step in steps]
        return driver.run_scenario(REPO, plan, environment_extra=lambda box: self.runs.environment(box, hazard))

    @staticmethod
    def once(progress: bool = False) -> Any:
        return lambda box: box.once(progress=progress)

    def test_each_account_refreshes_and_reads_only_its_own_usage_cache(self) -> None:
        document = self.run_plan([self.once()])
        home = Path(os.path.realpath(self.runs.box.home))
        self.assertEqual(self.runs.read(), [
            {"CLAUDE_CONFIG_DIR": None, "cache": str(home / ".claude.json")},
            {"CLAUDE_CONFIG_DIR": str(self.runs.box.home / "claude-az"), "cache": str(home / "claude-az" / ".claude.json")},
        ], "the default account never inherits CLAUDE_CONFIG_DIR; the added one gets its own, absolute, as named")
        rows = poll_rows(document["results"][0])
        self.assertEqual((session_percent(rows, "claude"), session_percent(rows, "claude-az")), ("8", "50"))
        self.assertTrue(all(r["status"] == "ok" for r in rows if r["metric"] == "used_percent"))

    def test_the_default_account_drops_an_inherited_config_dir_even_without_the_hazard(self) -> None:
        self.run_plan([self.once()], hazard=False)
        self.assertEqual([run["CLAUDE_CONFIG_DIR"] is None for run in self.runs.read()], [True, False])

    def test_shared_message_ids_are_counted_once_per_account(self) -> None:
        document = self.run_plan([self.once()])
        rows = poll_rows(document["results"][0])
        self.assertEqual((tokens(rows, "claude"), tokens(rows, "claude-az")), (110, 220))

    def test_an_account_without_a_stats_cache_still_collects_its_transcripts_and_quota(self) -> None:
        rows = poll_rows(self.run_plan([self.once()])["results"][0])
        az = [r for r in rows if r["provider"] == "claude-az"]
        self.assertFalse([r for r in az if r["source"] in ("claude-stats-cache", "local-file")],
                         "no stats rows: an absent stats cache is not an error for an added account")
        self.assertEqual(tokens(rows, "claude-az"), 220)
        self.assertEqual(session_percent(rows, "claude-az"), "50")
        self.assertTrue([r for r in rows if r["provider"] == "claude" and r["source"] == "claude-stats-cache"
                         and r["status"] == "ok"], "the default account's stats cache is read")

    def test_rows_state_doctor_and_progress_follow_the_account_order(self) -> None:
        document = self.run_plan([{"op": "doctor_json"}, {"op": "doctor_text"}, self.once(progress=True)])
        doctor = json.loads(document["results"][0]["stdout"])
        order = [*SERVICE.ai_usage_plugins.BUNDLED, "claude-az"]
        self.assertEqual(list(doctor), sorted(order), "doctor --json sorts its keys, as before")
        self.assertEqual(doctor["claude-az"]["type"], "claude")
        self.assertEqual(doctor["claude-az"]["label"], "Claude (az)")
        self.assertEqual(doctor["claude-az"]["projects_dir"], "/sandbox/home/claude-az/projects")
        self.assertEqual(doctor["claude"]["projects_dir"], "/sandbox/home/.claude/projects")
        text_order = [line.split()[0] for line in document["results"][1]["stdout"].splitlines()[1:]]
        self.assertEqual(text_order, order)
        once = document["results"][2]
        events = [json.loads(line) for line in once["stdout"].splitlines() if line.startswith("{")]
        self.assertEqual([(e["provider"], e["index"], e["total"]) for e in events],
                         [("claude", 1, 2), ("claude-az", 2, 2)])
        providers = []
        for row in poll_rows(once):
            if not providers or providers[-1] != row["provider"]:
                providers.append(row["provider"])
        self.assertEqual(providers, order)
        state = once["state"]
        self.assertEqual(sorted(state["instances"]), ["claude-az"])
        self.assertTrue(all("/home/.claude/" in key for key in state["claude_offsets"]))
        self.assertTrue(all("/home/claude-az/" in key for key in state["instances"]["claude-az"]["claude_offsets"]))
        self.assertNotIn("instances", state["instances"]["claude-az"])

    def test_a_disabled_account_keeps_its_data_and_resumes_without_recounting(self) -> None:
        def configure(enabled: bool) -> Any:
            return lambda box: {"op": "configure", "path": str(box.config),
                                "assignments": [f"instances.claude-az.enabled={json.dumps(enabled)}"]}

        def append(box: driver.Sandbox) -> dict[str, Any]:
            return {"op": "append", "files": {str(box.home / "claude-az/projects/work/s1.jsonl"): message("msg_az_2", 1000),
                                              str(box.home / ".claude/projects/work/s1.jsonl"): message("msg_default_2", 3)}}

        document = self.run_plan([self.once(), configure(False), append, self.once(), configure(True), self.once()])
        first, _, _, second, _, third = document["results"]
        self.assertEqual(tokens(poll_rows(first), "claude-az"), 220)
        disabled = [r for r in poll_rows(second, first) if r["provider"] == "claude-az"]
        self.assertEqual([(r["category"], r["metric"], r["status"]) for r in disabled], [("availability", "enabled", "disabled")])
        self.assertEqual(second["state"]["instances"]["claude-az"], first["state"]["instances"]["claude-az"],
                         "disabling leaves the account's checkpoints alone")
        self.assertEqual(tokens(poll_rows(second, first), "claude"), 3, "the default account keeps collecting")
        self.assertEqual(tokens(poll_rows(third, second), "claude-az"), 1000, "re-enabled: only the new message")
        config = json.loads(document["results"][4]["text"])
        self.assertEqual(config["instances"][0], {**AZ, "settings": {**AZ["settings"], "enabled": True}})

    def test_a_failing_account_is_one_error_row_and_the_other_is_unchanged(self) -> None:
        healthy = poll_rows(self.run_plan([self.once()])["results"][0])
        broken = poll_rows(self.run_plan([self.once()], usage_cache_max_age_seconds="not a number")["results"][0])
        errors = [r for r in broken if r["provider"] == "claude-az"]
        self.assertEqual([(r["metric"], r["status"]) for r in errors if r["category"] == "collection"],
                         [("unexpected_error", "error")])

        def others(rows: list[dict[str, str]]) -> list[dict[str, str]]:
            return [{k: v for k, v in r.items() if k not in ("transaction_id", "transaction_index")}
                    for r in rows if r["provider"] != "claude-az"]
        self.assertEqual(others(broken), others(healthy))


# ---- AC-4: data written by phase 1 --------------------------------------------

class Phase1CarryForwardTests(unittest.TestCase):
    def test_an_account_added_after_phase_1_data_counts_only_its_own(self) -> None:
        require_history(self, driver.phase1_sha())
        runs = Runs()
        self.addCleanup(runs.close)

        def plan(box: driver.Sandbox) -> list[dict[str, Any]]:
            two_accounts(box)
            with_az = box.config.read_text(encoding="utf-8")
            config = json.loads(with_az)
            del config["instances"]
            box.config.write_text(json.dumps(config), encoding="utf-8")  # phase 1 has only the default account
            transcript = str(box.home / ".claude/projects/work/s1.jsonl")
            return [box.once(),                                          # phase 1
                    {"op": "append", "files": {transcript: message("msg_default_2", 3)}},
                    {"op": "write_file", "path": str(box.config), "text": with_az},  # this tree from here
                    box.once()]

        with tempfile.TemporaryDirectory(prefix="ai-usage-phase1-") as temporary:
            phase1 = driver.extract_baseline(Path(temporary) / "src", driver.phase1_sha())
            document = driver.run_scenario(phase1, plan, handoff=(REPO, 2),
                                           environment_extra=lambda box: runs.environment(box, hazard=False))
        first, _, _, second = document["results"]
        rows = poll_rows(second, first)
        self.assertEqual(tokens(poll_rows(first), "claude"), 110)
        self.assertEqual(tokens(rows, "claude"), 3, "phase 1's checkpoints carry forward: no recount")
        self.assertEqual(tokens(rows, "claude-az"), 220)
        self.assertEqual(sorted(second["state"]["instances"]), ["claude-az"])
        self.assertEqual(second["state"]["claude_offsets"].keys(), first["state"]["claude_offsets"].keys())


# ---- AC-6, AC-9: the report ----------------------------------------------------

class TwoAccountReportTests(unittest.TestCase):
    def report(self, src: Path = REPO, installed: str = "2.3.0") -> dict[str, Any]:
        runs = Runs()
        self.addCleanup(runs.close)

        def plan(box: driver.Sandbox) -> list[dict[str, Any]]:
            two_accounts(box)
            steps: list[dict[str, Any]] = [box.once(),
                                           {"op": "configure", "path": str(box.config), "assignments": [
                                               "providers.claude.monthly_subscription_usd=100",
                                               "instances.claude-az.monthly_subscription_usd=20"]},
                                           box.once()]
            report: dict[str, Any] = {"op": "report", "days": 7}
            if installed != "2.3.0":
                report["installed_collector"] = str(box.root / f"installed-{installed}/collector.py")
                box.write(f"installed-{installed}/collector.py", f'VERSION = "{installed}"\n')
            return [*steps, report]
        document = driver.run_scenario(src, plan, environment_extra=runs.environment)
        return document["results"][-1]["report"]

    def test_each_account_is_its_own_provider_with_its_own_data(self) -> None:
        report = self.report()
        providers = {p["id"]: p for p in report["providers"]}
        self.assertEqual([p["id"] for p in report["providers"]],
                         ["codex", "claude", "antigravity", "grok", "gemini_cli", "claude-az"])
        self.assertEqual({p["id"]: p["type"] for p in report["providers"]},
                         {"codex": "codex", "claude": "claude", "antigravity": "antigravity", "grok": "grok",
                          "gemini_cli": "gemini_cli", "claude-az": "claude"})
        default, az = providers["claude"], providers["claude-az"]
        self.assertEqual((az["name"], az["product"], az["setup"], az["enabled"]), ("Claude (az)", "Claude Code", "ready", True))
        self.assertEqual((default["today"]["input"], az["today"]["input"]), (110, 220))

        def windows(provider: dict[str, Any]) -> dict[str, Any]:
            return {w["short"]: w["percent"] for w in provider["quota"]["windows"]}
        self.assertEqual(windows(default), {"5-hour": 8, "Weekly": 85, "Weekly · Fable": 4})
        self.assertEqual(windows(az), {"5-hour": 50, "Weekly": 85, "Weekly · Fable": 4},
                         "Claude's quota rules (used basis, per-model weekly names) apply by type")
        self.assertEqual((default["costs"]["subscription"]["monthly_usd"], az["costs"]["subscription"]["monthly_usd"]), (100, 20))
        self.assertEqual(report["collector"]["capabilities"]["instances"], True)
        self.assertEqual(report["collection"]["summary"]["providers_updated"], 2)
        self.assertEqual(report["collection"]["failures"], [], "claude-az's missing stats cache is not a failure")
        self.assertEqual({s["id"]: s["status"] for s in az["sources"]},
                         {"sessions": "ok", "stats-cache": "waiting", "subscription-usage": "ok"})

    def test_a_lookup_left_on_the_built_in_id_is_caught(self) -> None:
        # Negative control: the test above notices an account reading the built-in's data.
        with tempfile.TemporaryDirectory(prefix="ai-usage-lookup-control-") as temporary:
            src = Path(temporary) / "src"
            shutil.copytree(REPO, src, ignore=shutil.ignore_patterns(".git", "macos", "docs", "tests", "build"))
            path = src / "ai_usage_report.py"
            text = path.read_text(encoding="utf-8")
            needle = 'scan.monthly_rate.get(definition.id, [])'
            self.assertIn(needle, text)
            path.write_text(text.replace(needle, 'scan.monthly_rate.get(definition.type, [])'), encoding="utf-8")
            report = self.report(src)
        az = next(p for p in report["providers"] if p["id"] == "claude-az")
        self.assertEqual(az["costs"]["subscription"]["monthly_usd"], 100, "reads claude's price, not its own")

    def test_an_older_installed_collector_reports_the_account_as_not_collected(self) -> None:
        report = self.report(installed="2.2.0")
        az = next(p for p in report["providers"] if p["id"] == "claude-az")
        self.assertEqual(az["setup"], "not_configured")
        self.assertIn("installed collector (2.2.0) predates added accounts", az["setup_note"])
        self.assertIs(report["collector"]["capabilities"]["instances"], False)
        default = next(p for p in report["providers"] if p["id"] == "claude")
        self.assertEqual(default["setup"], "ready")


# ---- AC-2, AC-9: the instance command and the version boundary ------------------

class InstanceCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = black_box.IsolatedHome()
        self.addCleanup(self.case.close)
        self.account = self.case.home / "claude-az"
        self.account.mkdir()
        (self.account / ".claude.json").write_text("{}", encoding="utf-8")
        # Saved as the absolute folder, "~" expanded and symlinks kept (review finding).
        self.saved = {**AZ, "settings": {"config_dir": str(self.account)}}
        self.config = self.case.root / "config.json"
        self.case.run("install", "--no-start", "--config", str(self.config))
        os.chmod(self.config, 0o640)

    def cli(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.case.run(*arguments, "--config", str(self.config), check=check)

    def add(self, *extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.cli("instance", "add", "--plugin", "claude", "--id", "claude-az", "--label", "Claude (az)",
                        "--config-dir", "~/claude-az", *extra, check=check)

    def test_add_list_disable_and_enable(self) -> None:
        before = json.loads(self.config.read_text(encoding="utf-8"))
        added = json.loads(self.add().stdout)
        self.assertEqual(added, self.saved)
        after = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in after.items() if k != "instances"}, before, "other keys are kept")
        self.assertEqual(after["instances"], [self.saved], "the price is left unset")
        self.assertEqual(oct(self.config.stat().st_mode & 0o777), oct(0o640), "the mode is kept")
        listed = json.loads(self.cli("instance", "list").stdout)
        self.assertEqual(listed, [{"id": "claude-az", "plugin": "claude", "label": "Claude (az)", "enabled": True,
                                   "settings": {"config_dir": str(self.account)}}])
        self.assertEqual(json.loads(self.cli("instance", "disable", "claude-az").stdout), {"id": "claude-az", "enabled": False})
        self.assertFalse(json.loads(self.cli("instance", "list").stdout)[0]["enabled"])
        self.cli("instance", "enable", "claude-az")
        self.assertTrue(json.loads(self.cli("instance", "list").stdout)[0]["enabled"])

    def test_each_refusal_leaves_the_config_unchanged(self) -> None:
        self.add()
        before = self.config.read_bytes()
        refusals = {
            "the id is used twice": ["--id", "claude-az"],
            "the id is a built-in provider's": ["--id", "claude"],
            "must be a lowercase slug": ["--id", "Claude"],
            "codex does not support added accounts": ["--plugin", "codex", "--id", "codex-2"],
            "config folder not found": ["--id", "claude-b", "--config-dir", "~/missing"],
            "path role collision": ["--id", "claude-b"],
        }
        for message, override in refusals.items():
            with self.subTest(message):
                arguments = {"--plugin": "claude", "--id": "claude-az", "--label": "Claude (az)", "--config-dir": "~/claude-az"}
                arguments.update(dict(zip(override[::2], override[1::2])))
                result = self.cli("instance", "add", *[part for pair in arguments.items() for part in pair], check=False)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.config.read_bytes(), before)
        result = self.cli("instance", "enable", "claude-nope", check=False)
        self.assertIn("no added account with id 'claude-nope'", result.stderr)

    def installed_version(self, version: Optional[str]) -> None:
        plist = self.case.home / "Library" / "LaunchAgents" / PLIST_NAME
        if version is None:
            plist.unlink()
            return
        script = self.case.root / f"collector-{version}.py"
        script.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
        with plist.open("wb") as handle:
            plistlib.dump({"Label": "codes.porta.ai-usage", "ProgramArguments": [sys.executable, str(script)]}, handle)

    def test_an_older_or_missing_installed_collector_refuses_to_add_or_enable(self) -> None:
        self.add()
        self.cli("instance", "disable", "claude-az")
        before = self.config.read_bytes()
        for version, shown in (("2.2.0", "installed: 2.2.0"), (None, "installed: none found")):
            self.installed_version(version)
            with self.subTest(version):
                for arguments in (["instance", "enable", "claude-az"],
                                  ["configure", "--set", "instances.claude-az.enabled=true"]):
                    result = self.cli(*arguments, check=False)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(f"added accounts need the installed collector 2.3.0 or newer ({shown})", result.stderr)
                result = self.cli("instance", "add", "--plugin", "claude", "--id", "claude-b", "--label", "B",
                                  "--config-dir", "~/claude-az", check=False)
                self.assertIn("2.3.0 or newer", result.stderr)
                self.assertEqual(self.config.read_bytes(), before)
        # Turning an account off never needs the newer collector.
        self.cli("instance", "disable", "claude-az")

    def test_a_config_file_colliding_with_the_new_account_is_left_unchanged(self) -> None:
        # The config-file collision is checked before the write (review finding).
        account_config = self.account / "config.json"
        shutil.copy(self.config, account_config)
        (self.account / ".claude.json").unlink()
        account_config.rename(self.account / ".claude.json")
        target = self.account / ".claude.json"
        before = target.read_bytes()
        result = self.case.run("instance", "add", "--plugin", "claude", "--id", "claude-az", "--label", "AZ",
                               "--config-dir", "~/claude-az", "--config", str(target), check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("path role collision: config file and instances.claude-az.usage_cache_file", result.stderr)
        self.assertEqual(target.read_bytes(), before)

    def test_a_plugin_that_fails_to_load_cannot_take_an_account(self) -> None:
        before = self.config.read_bytes()
        with mock.patch.dict(SERVICE.PLUGINS, clear=False) as plugins, \
                mock.patch.dict(SERVICE.PLUGIN_LOAD_ERRORS, {"claude": "RuntimeError: broken"}):
            del plugins["claude"]
            with self.assertRaises(ValueError) as caught:
                SERVICE.add_instance(self.config, "claude", "claude-az", "AZ", str(self.account))
        self.assertIn("claude can't take an account while its module fails to load", str(caught.exception))
        self.assertEqual(self.config.read_bytes(), before)

    def test_a_symlinked_config_stays_linked(self) -> None:
        link = self.case.root / "config-link.json"
        link.symlink_to(self.config)
        self.case.run("instance", "add", "--plugin", "claude", "--id", "claude-az", "--label", "Claude (az)",
                      "--config-dir", "~/claude-az", "--config", str(link))
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["instances"], [self.saved])

    def test_configure_sets_an_instances_price_and_install_keeps_the_instances(self) -> None:
        self.add()
        self.cli("configure", "--set", "instances.claude-az.monthly_subscription_usd=20")
        config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(config["instances"][0]["settings"], {"config_dir": str(self.account), "monthly_subscription_usd": 20})
        result = self.cli("configure", "--set", "instances.claude-az.monthly_subscription_usd=-1", check=False)
        self.assertIn("must be a non-negative number or null", result.stderr)
        state = self.case.home / ".ai-usage" / "state.json"
        state.write_text(json.dumps({"instances": {"claude-az": {"claude_offsets": {"x": {}}}}}), encoding="utf-8")
        before = (self.config.read_bytes(), state.read_bytes())
        self.case.run("install", "--no-start", "--config", str(self.config))
        self.case.run("uninstall", "--config", str(self.config))
        self.assertEqual((self.config.read_bytes(), state.read_bytes()), before)

    def fake_claude(self) -> Path:
        """A `claude` that records that it ran and fails, so no real account is ever touched."""
        runs = self.case.root / "claude-runs.log"
        (self.case.bin / "claude").write_text(f"#!/bin/sh\necho ran >> '{runs}'\nexit 1\n", encoding="utf-8")
        os.chmod(self.case.bin / "claude", 0o755)
        return runs

    def run_with(self, environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *arguments, "--config", str(self.config)],
                              text=True, capture_output=True, env=environment, timeout=60, check=False)

    def test_a_folder_named_through_a_variable_is_saved_as_the_folder_itself(self) -> None:
        # Review finding: the expression was saved, and a process without the variable
        # (launchd's) could not load the config at all.
        self.fake_claude()
        caller = {**self.case.environment(), "AI_USAGE_TEST_ACCOUNT_DIR": str(self.account)}
        added = self.run_with(caller, "instance", "add", "--plugin", "claude", "--id", "claude-az",
                              "--label", "Claude (az)", "--config-dir", "$AI_USAGE_TEST_ACCOUNT_DIR")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["instances"], [self.saved])

        launchd = self.case.environment()
        self.assertNotIn("AI_USAGE_TEST_ACCOUNT_DIR", launchd)
        for arguments in (["instance", "list"], ["doctor", "--json"], ["once"],
                          ["instance", "disable", "claude-az"], ["once"]):
            with self.subTest(arguments):
                result = self.run_with(launchd, *arguments)
                # `once` exits 2 for the fake claude's failed refresh, which is a row, not a load failure.
                self.assertEqual(result.returncode, 2 if arguments == ["once"] else 0, result.stdout + result.stderr)
                self.assertNotIn("config", result.stderr.lower(), result.stderr)
        usage_csv = SERVICE.expand_path(json.loads(self.config.read_text(encoding="utf-8"))["paths"]["usage_csv"]
                                        .replace("~", str(self.case.home), 1))
        with usage_csv.open(encoding="utf-8") as handle:
            providers = {row["provider"] for row in csv.DictReader(handle)}
        self.assertIn("claude-az", providers, "the account was collected without the variable")

    def test_an_account_whose_refresh_cache_aliases_another_is_refused_before_collection(self) -> None:
        # Hand-written: distinct folders, B's .claude.json links to A's, and B reads elsewhere.
        runs = self.fake_claude()
        self.add()
        two = self.case.home / "claude-two"
        two.mkdir()
        (two / ".claude.json").symlink_to(self.account / ".claude.json")
        own = self.case.root / "two"
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["instances"].append({"id": "claude-two", "plugin": "claude", "label": "Two", "settings": {
            "config_dir": str(two), "usage_cache_file": str(own / ".claude.json")}})
        self.config.write_text(json.dumps(config), encoding="utf-8")
        before = self.config.read_bytes()
        for arguments in (["once"], ["doctor", "--json"]):
            with self.subTest(arguments):
                result = self.cli(*arguments, check=False)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("refresh cache collision: instances.claude-az's refreshed .claude.json and "
                              "instances.claude-two's refreshed .claude.json", result.stderr)
                self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(runs.exists(), "nothing was refreshed")

    def test_a_folder_naming_an_unset_variable_is_refused_unchanged(self) -> None:
        before = self.config.read_bytes()
        for folder in ("$AI_USAGE_TEST_UNSET_DIR", "${AI_USAGE_TEST_UNSET_DIR}/a", "~/claude-az/$AI_USAGE_TEST_UNSET_DIR"):
            with self.subTest(folder):
                result = self.cli("instance", "add", "--plugin", "claude", "--id", "claude-az", "--label", "AZ",
                                  "--config-dir", folder, check=False)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("config_dir names an environment variable that isn't set: $AI_USAGE_TEST_UNSET_DIR",
                              result.stderr)
                self.assertEqual(self.config.read_bytes(), before)

    def test_a_folder_with_a_literal_dollar_sign_is_accepted(self) -> None:
        folder = self.case.home / "acct$1"
        folder.mkdir()
        self.cli("instance", "add", "--plugin", "claude", "--id", "claude-az", "--label", "AZ",
                 "--config-dir", str(folder))
        saved = json.loads(self.config.read_text(encoding="utf-8"))["instances"][0]["settings"]["config_dir"]
        self.assertEqual(saved, str(folder))
        self.cli("instance", "list")

    def test_a_symlinked_folder_is_saved_by_the_name_it_was_given(self) -> None:
        link = self.case.home / "az-link"
        link.symlink_to(self.account)
        self.cli("instance", "add", "--plugin", "claude", "--id", "claude-az", "--label", "Claude (az)",
                 "--config-dir", "~/az-link")
        saved = json.loads(self.config.read_text(encoding="utf-8"))["instances"][0]["settings"]["config_dir"]
        self.assertEqual(saved, str(link))

    def test_an_account_refreshing_the_folder_of_another_is_refused_before_collection(self) -> None:
        # Hand-written: the second account moves every read path away, so only its
        # refreshed folder is shared (review finding).
        runs = self.fake_claude()
        self.add()
        config = json.loads(self.config.read_text(encoding="utf-8"))
        own = self.case.root / "two"
        config["instances"].append({"id": "claude-two", "plugin": "claude", "label": "Two", "settings": {
            "config_dir": str(self.account), "projects_dir": str(own / "projects"),
            "stats_file": str(own / "stats-cache.json"), "usage_cache_file": str(own / ".claude.json")}})
        self.config.write_text(json.dumps(config), encoding="utf-8")
        before = self.config.read_bytes()
        for arguments in (["once"], ["doctor", "--json"]):
            with self.subTest(arguments):
                result = self.cli(*arguments, check=False)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("refresh folder collision: instances.claude-az.config_dir and "
                              "instances.claude-two.config_dir", result.stderr)
                self.assertIn("give one its own folder or disable it", result.stderr)
                self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(runs.exists(), "nothing was refreshed")
        # A disabled account never refreshes, so disabling one resolves it; enabling it again is refused.
        self.cli("instance", "disable", "claude-two")
        self.assertEqual(self.cli("once", check=False).returncode, 2, "collects, with the fake claude's error row")
        self.assertEqual(runs.read_text(encoding="utf-8").count("ran"), 2, "the default account and claude-az, once each")
        disabled = self.config.read_bytes()
        result = self.cli("instance", "enable", "claude-two", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refresh folder collision", result.stderr)
        self.assertEqual(self.config.read_bytes(), disabled)


if __name__ == "__main__":
    unittest.main()
