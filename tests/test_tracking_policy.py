"""Auto / On / Off tracking (issue #8), detection evidence, and the collection summary's
fresh/stale/failed outcomes (UX pass slice 1, contract sections A, B and C).

Every test uses a temporary sandbox from ai_usage_fixtures.write_sandbox. Provider
collection is replaced by a stub, so no provider command runs and nothing outside
the sandbox is read or written.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Optional
import unittest
from unittest import mock

import ai_usage_fixtures as fixtures
import ai_usage_report as report
import ai_usage_service as collector
from ai_usage_plugins import external
from ai_usage_plugins.base import tracking_policy

from tests import isolation  # noqa: E402

isolation.module_fixtures(globals())

R = fixtures.CsvBuilder.row
NOW = report.parse_ts(fixtures.FIXTURE_NOW)
BUILT_INS = ("codex", "claude", "antigravity", "gemini_cli", "grok")


class Sandbox:
    """A sandbox config whose providers are all Off unless a test sets them."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ai-usage-tracking-")
        self.root = Path(self._tmp.name)
        self.config_path = fixtures.write_sandbox(self.root, NOW)
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        for name in BUILT_INS:
            raw["providers"][name].pop("enabled", None)
            raw["providers"][name]["tracking"] = "off"
        self.raw = raw
        (self.root / "bin").mkdir(exist_ok=True)
        self.save()
        self.csv = Path(raw["paths"]["usage_csv"])
        self.csv.unlink()
        Path(raw["paths"]["log_file"]).write_text("", encoding="utf-8")
        os.chmod(self.config_path, 0o600)

    def close(self) -> None:
        self._tmp.cleanup()

    def save(self) -> None:
        self.config_path.write_text(json.dumps(self.raw, indent=2) + "\n", encoding="utf-8")

    def install(self, name: str) -> None:
        """A stand-in executable at the sandbox path the provider looks for (never run)."""
        path = self.root / "bin" / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o700)

    def set(self, name: str, **settings: Any) -> None:
        entry = self.raw["providers"][name]
        entry.pop("tracking", None)
        entry.pop("enabled", None)
        entry.update(settings)
        self.save()

    def add_az(self, **settings: Any) -> None:
        (self.root / "claude-az").mkdir(exist_ok=True)
        self.raw.setdefault("instances", []).append({
            **fixtures.SECOND_ACCOUNT,
            "settings": {"config_dir": str(self.root / "claude-az"),
                         "executable": str(self.root / "bin" / "claude-az"), **settings}})
        self.save()

    def config(self) -> dict[str, Any]:
        return collector.load_config(self.config_path)

    def write_csv(self, transactions: list[list[dict[str, Any]]]) -> None:
        builder = fixtures.CsvBuilder()
        for rows in transactions:
            builder.transaction(rows)
        builder.write(self.csv)

    def build(self, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        config = self.config()
        if state is not None:
            collector.save_state(collector.state_path_for(config), state)
        if not self.csv.exists():
            self.write_csv([])
        return report.build_report(config, config_path=self.config_path, now=NOW,
                                   service={"state": "running", "pid": 1, "disabled": False, "detail": ""},
                                   installed_version=tuple(int(p) for p in collector.VERSION.split(".")))


def provider(built: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in built["providers"] if p["id"] == pid)


def stub_collect(calls: list[str]):
    """Replace every bundled plugin's collect with one that records the account."""
    def collect(context: Any) -> list[dict[str, Any]]:
        calls.append(context.settings.get("config_dir") or "default")
        return [collector.metric_row(context.collected_at, "claude", "availability", "detected", value=True,
                                     unit="boolean", source="filesystem-discovery", status="ok")]
    patched = {name: dataclasses.replace(plugin, collect=collect) for name, plugin in collector.PLUGINS.items()}
    return mock.patch.dict(collector.PLUGINS, patched)


class PolicyResolutionTests(unittest.TestCase):
    def test_tracking_wins_then_legacy_enabled_then_auto(self) -> None:
        cases = [
            ({"tracking": "off", "enabled": True}, "off"),
            ({"tracking": "on", "enabled": False}, "on"),
            ({"tracking": "auto"}, "auto"),
            ({"enabled": True}, "on"),
            ({"enabled": False}, "off"),
            ({"enabled": "yes"}, "off"),  # as before: only `true` ever turned one on
            ({}, "auto"),
        ]
        for settings, expected in cases:
            with self.subTest(settings=settings):
                self.assertEqual(tracking_policy(settings), expected)
                self.assertEqual(tracking_policy(settings), expected, "resolution is repeatable")

    def test_external_plugins_without_a_choice_stay_off(self) -> None:
        account = collector.Account("acme", "acme", external=True)
        self.assertEqual(collector.account_tracking(account, {}), "off")
        self.assertEqual(collector.account_tracking(account, {"tracking": "auto"}), "auto")
        detection = collector.detect_installation(account, {})
        self.assertEqual(detection, {"installed": None, "requirement": "none"})
        self.assertEqual(collector.effective_tracking("auto", detection), "active")

    def test_effective_state_for_each_policy_and_observation(self) -> None:
        exe = {"requirement": "executable"}
        cases = [
            ("off", {**exe, "installed": True}, None, "inactive"),
            ("on", {**exe, "installed": False}, None, "active"),
            ("on", {**exe, "installed": None}, None, "active"),
            ("auto", {**exe, "installed": True}, "inactive", "active"),
            ("auto", {**exe, "installed": False}, "active", "inactive"),
            ("auto", {**exe, "installed": None}, "active", "active"),
            ("auto", {**exe, "installed": None}, "inactive", "inactive"),
            ("auto", {**exe, "installed": None}, None, "pending"),
            ("auto", {"requirement": "none", "installed": None}, None, "active"),
        ]
        for policy, detection, last, expected in cases:
            with self.subTest(policy=policy, detection=detection, last=last):
                self.assertEqual(collector.effective_tracking(policy, detection, last), expected)

    def test_default_config_has_no_enabled_so_absent_means_auto(self) -> None:
        for name in BUILT_INS:
            self.assertNotIn("enabled", collector.DEFAULT_CONFIG["providers"][name])
            self.assertNotIn("tracking", collector.DEFAULT_CONFIG["providers"][name])


class ConfigWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Sandbox()
        self.addCleanup(self.box.close)

    def written(self) -> dict[str, Any]:
        return json.loads(self.box.config_path.read_text(encoding="utf-8"))

    def test_validation_rejects_unknown_policies(self) -> None:
        self.box.set("codex", tracking="sometimes")
        with self.assertRaisesRegex(ValueError, "providers.codex.tracking must be one of"):
            self.box.config()
        self.box.set("codex", tracking="off")
        self.box.add_az(tracking="maybe")
        with self.assertRaisesRegex(ValueError, "instances.claude-az.settings.tracking must be one of"):
            self.box.config()

    def test_writer_stores_tracking_with_an_enabled_mirror(self) -> None:
        self.box.set("codex", enabled=True)
        for policy, mirror in (("on", True), ("off", False), ("auto", None)):
            with self.subTest(policy=policy):
                collector.configure(self.box.config_path, [f"providers.codex.tracking=\"{policy}\""])
                entry = self.written()["providers"]["codex"]
                self.assertEqual(entry["tracking"], policy)
                self.assertEqual(entry.get("enabled"), mirror, "an older collector reads only `enabled`")

        echoed = collector.configure(self.box.config_path, ["providers.codex.enabled=false"])
        self.assertEqual(echoed, {"providers.codex.enabled": False}, "an older app reads back what it sent")
        self.assertEqual(self.written()["providers"]["codex"]["tracking"], "off")
        self.assertIs(self.written()["providers"]["codex"]["enabled"], False)

        before = self.box.config_path.read_bytes()
        collector.configure(self.box.config_path, ["providers.codex.enabled=false"])
        self.assertEqual(self.box.config_path.read_bytes(), before, "migration is idempotent")
        self.assertEqual(stat.S_IMODE(self.box.config_path.stat().st_mode), 0o600)

        for bad in ("providers.codex.tracking=\"yes\"", "providers.codex.tracking=true"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                collector.configure(self.box.config_path, [bad])
        self.assertEqual(self.box.config_path.read_bytes(), before)

    def test_instance_writer_mirrors_enabled(self) -> None:
        self.box.add_az(enabled=False)
        with mock.patch.object(collector, "require_instance_support"):
            collector.configure(self.box.config_path, ["instances.claude-az.tracking=\"on\""])
        settings = self.written()["instances"][0]["settings"]
        self.assertEqual(settings["tracking"], "on")
        self.assertIs(settings["enabled"], True)
        listed = {entry["id"]: entry for entry in collector.list_instances(self.box.config_path)}
        self.assertEqual(listed["claude-az"]["tracking"], "on")
        self.assertIs(listed["claude-az"]["enabled"], True)

    def test_loading_never_rewrites_a_legacy_config(self) -> None:
        self.box.set("codex", enabled=False)
        before = self.box.config_path.read_bytes()
        config = self.box.config()
        account = collector.Account("codex", "codex")
        self.assertEqual(collector.account_tracking(account, collector.account_settings(config, account)), "off")
        self.assertEqual(self.box.config_path.read_bytes(), before)


class CollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Sandbox()
        self.addCleanup(self.box.close)
        self.calls: list[str] = []
        patcher = stub_collect(self.calls)
        patcher.start()
        self.addCleanup(patcher.stop)

    def collect(self, state: Optional[dict[str, Any]] = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        state = {} if state is None else state
        rows = collector.collect_snapshot(self.box.config(), include_history=False, state=state)
        return rows, state

    def claude_rows(self, rows: list[dict[str, Any]], pid: str = "claude") -> list[dict[str, Any]]:
        return [row for row in rows if row["provider"] == pid and row["category"] != "cost"]

    def test_legacy_enabled_true_still_collects_without_the_cli(self) -> None:
        self.box.set("claude", enabled=True)
        rows, state = self.collect()
        self.assertEqual(self.calls, ["default"])
        self.assertEqual(state["tracking"]["claude"]["effective"], "active")

    def test_auto_installed_collects(self) -> None:
        self.box.install("claude")
        self.box.set("claude", tracking="auto")
        rows, state = self.collect()
        self.assertEqual(self.calls, ["default"])
        self.assertEqual(state["tracking"]["claude"]["installed"], True)

    def test_auto_absent_collects_nothing_and_writes_no_rows(self) -> None:
        self.box.set("claude")  # neither key: Auto
        rows, state = self.collect()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.claude_rows(rows), [], "no disabled row and no discovery row")
        self.assertEqual(state["tracking"]["claude"]["effective"], "inactive")
        self.assertIs(state["tracking"]["claude"]["saved_data"], False)

    def test_auto_absent_with_saved_local_data_is_recorded(self) -> None:
        stats = Path(self.box.raw["providers"]["claude"]["stats_file"])
        stats.parent.mkdir(parents=True, exist_ok=True)
        stats.write_text("{}", encoding="utf-8")
        self.box.set("claude", tracking="auto")
        _rows, state = self.collect()
        self.assertEqual(self.calls, [])
        self.assertIs(state["tracking"]["claude"]["saved_data"], True)
        self.assertEqual(stats.read_text(encoding="utf-8"), "{}", "saved data is never touched")

    def test_on_absent_still_collects(self) -> None:
        self.box.set("claude", tracking="on")
        self.collect()
        self.assertEqual(self.calls, ["default"])

    def test_off_writes_the_disabled_row_as_before(self) -> None:
        self.box.set("claude", tracking="off")
        rows, _state = self.collect()
        self.assertEqual(self.calls, [])
        self.assertEqual([r["status"] for r in self.claude_rows(rows)], ["disabled"])

    def test_unknown_detection_keeps_the_last_known_state(self) -> None:
        self.box.set("claude", tracking="auto")
        with mock.patch.object(collector, "find_executable", side_effect=OSError("unreadable PATH")):
            _rows, state = self.collect({"tracking": {"claude": {"last_observed": "active"}}})
            self.assertEqual(self.calls, ["default"])
            self.assertEqual(state["tracking"]["claude"]["effective"], "active")
            self.assertEqual(state["tracking"]["claude"]["last_observed"], "active", "carried, not re-observed")
            self.assertIsNone(state["tracking"]["claude"]["installed"])

            self.calls.clear()
            _rows, state = self.collect({"tracking": {"claude": {"last_observed": "inactive"}}})
            self.assertEqual(self.calls, [])

            self.calls.clear()
            _rows, state = self.collect()
            self.assertEqual(self.calls, [], "first-run unknown is pending: not collected")
            self.assertEqual(state["tracking"]["claude"]["effective"], "pending")
            self.assertNotIn("last_observed", state["tracking"]["claude"])

    def test_policy_forced_states_never_seed_last_known(self) -> None:
        self.box.set("claude", tracking="off")
        _rows, state = self.collect()
        self.assertNotIn("last_observed", state["tracking"]["claude"], "Off is forced, not observed")
        self.box.set("claude", tracking="on")
        _rows, state = self.collect(state)
        self.assertNotIn("last_observed", state["tracking"]["claude"], "On without a probe is forced")
        self.box.set("claude", tracking="auto")
        self.calls.clear()
        with mock.patch.object(collector, "find_executable", side_effect=OSError("unreadable PATH")):
            _rows, state = self.collect(state)
        self.assertEqual(self.calls, [], "Off/On then Auto with an unknown probe is pending")
        self.assertEqual(state["tracking"]["claude"]["effective"], "pending")

        # An earlier Auto observation survives an Off spell.
        self.box.install("claude")
        _rows, state = self.collect(state)
        self.assertEqual(state["tracking"]["claude"]["last_observed"], "active")
        self.box.set("claude", tracking="off")
        _rows, state = self.collect(state)
        self.assertEqual(state["tracking"]["claude"]["last_observed"], "active")
        self.box.set("claude", tracking="auto")
        self.calls.clear()
        with mock.patch.object(collector, "find_executable", side_effect=OSError("unreadable PATH")):
            _rows, state = self.collect(state)
        self.assertEqual(self.calls, ["default"])

    def test_entries_for_removed_instances_are_dropped(self) -> None:
        _rows, state = self.collect({"tracking": {"gone": {"last_observed": "active"}}})
        self.assertNotIn("gone", state["tracking"])
        self.assertEqual(set(state["tracking"]), set(BUILT_INS))

    def test_two_claude_instances_resolve_independently(self) -> None:
        self.box.install("claude")
        self.box.set("claude", tracking="auto")
        self.box.add_az()  # Auto; its own executable path is absent
        rows, state = self.collect()
        self.assertEqual(self.calls, ["default"])
        self.assertEqual(state["tracking"]["claude"]["effective"], "active")
        self.assertEqual(state["tracking"]["claude-az"]["effective"], "inactive")

        self.box.install("claude-az")
        self.calls.clear()
        self.collect()
        self.assertEqual(self.calls, ["default", str(self.box.root / "claude-az")])


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Sandbox()
        self.addCleanup(self.box.close)

    def history(self) -> None:
        check = NOW - 600
        event = NOW - 5 * 3600
        self.box.write_csv([
            [fixtures.detected(check - 3600, "claude"), *fixtures.claude_event(event, "claude-opus-5", 10, 5)],
            [fixtures.detected(check, "claude"),
             fixtures.claude_quota(check, "five_hour", 20, 18000, NOW + 3600)],
        ])
        self.event = event

    def record(self, **entries: dict[str, Any]) -> dict[str, Any]:
        return {"tracking": {pid: {"policy": "auto", "requirement": "executable", "saved_data": False,
                                   "observed_at": "2026-09-29T16:00:00Z", **entry}
                             for pid, entry in entries.items()}}

    def test_report_keys_for_auto_absent_with_saved_history(self) -> None:
        self.history()
        self.box.set("claude", tracking="auto")
        built = self.box.build(self.record(claude={"effective": "inactive", "installed": False,
                                                   "last_observed": "inactive"}))
        claude = provider(built, "claude")
        self.assertEqual(claude["tracking"], "auto")
        self.assertEqual(claude["tracking_effective"], "inactive")
        self.assertIs(claude["enabled"], False)
        self.assertEqual(claude["detection"], {"installed": False, "requirement": "executable", "saved_data": True})
        self.assertIsNotNone(claude["today"]["total"], "hidden providers keep their recorded history")
        self.assertTrue(built["collector"]["capabilities"]["tracking_policy"])

        self.box.set("claude", tracking="on")
        on = provider(self.box.build(), "claude")
        self.assertEqual(on["tracking_effective"], "active", "a saved On shows at once")
        self.assertIs(on["enabled"], True)
        self.assertEqual(on["days"], claude["days"], "inactivity never changes historical accounting")

    def test_the_report_never_probes_and_follows_the_collectors_record(self) -> None:
        self.box.set("claude", tracking="auto")
        recorded = self.record(claude={"effective": "active", "installed": True, "last_observed": "active"})
        # The reporter's PATH lacks the CLI (the sandbox path doesn't exist); the record wins.
        with mock.patch.object(collector, "find_executable", side_effect=AssertionError("probed")):
            claude = provider(self.box.build(recorded), "claude")
            self.assertEqual(claude["tracking_effective"], "active")
            self.assertIs(claude["enabled"], True)
            self.assertIs(claude["detection"]["installed"], True)

            self.box.install("claude")  # installed now, but the collector hasn't seen it yet
            claude = provider(self.box.build(self.record(claude={"effective": "inactive", "installed": False})),
                              "claude")
            self.assertEqual(claude["tracking_effective"], "inactive")

    def test_without_a_record(self) -> None:
        self.box.set("claude", tracking="auto")
        self.box.set("codex", tracking="off")
        older = self.box.build({"history_date": "2026-09-29"})  # an older collector: no records at all
        self.assertEqual(provider(older, "claude")["tracking_effective"], "active")
        self.assertIsNone(provider(older, "claude")["detection"]["installed"])
        self.assertEqual(provider(older, "codex")["tracking_effective"], "inactive")
        newer = self.box.build(self.record(codex={"policy": "off", "effective": "inactive", "installed": None}))
        self.assertEqual(provider(newer, "claude")["tracking_effective"], "pending")
        self.assertIs(provider(newer, "claude")["enabled"], False)

    def test_tracking_policy_capability_follows_the_collectors_records(self) -> None:
        # A collector that predates the policy (also VERSION 2.4.0) writes no tracking
        # records: its Auto collects as On and detection is never known, so the app must
        # not offer Auto/On/Off or say "Detection unknown" on every row.
        self.box.set("claude", tracking="auto")
        older = self.box.build({"history_date": "2026-09-29"})
        self.assertIs(older["collector"]["capabilities"]["tracking_policy"], False)
        self.assertEqual(provider(older, "claude")["tracking_effective"], "active")
        empty = self.box.build({"history_date": "2026-09-29", "tracking": {}})
        self.assertIs(empty["collector"]["capabilities"]["tracking_policy"], True,
                      "a collector that records tracking, even with no accounts yet")
        newer = self.box.build(self.record(claude={"effective": "active", "installed": True}))
        self.assertIs(newer["collector"]["capabilities"]["tracking_policy"], True)
        config = self.box.config()
        for version in (None, (2, 3, 0)):
            built = report.build_report(config, config_path=self.box.config_path, now=NOW,
                                        service={"state": "running", "pid": 1, "disabled": False, "detail": ""},
                                        installed_version=version)
            self.assertIs(built["collector"]["capabilities"]["tracking_policy"], False,
                          f"records left by a newer collector don't count for {version}")

    def test_policy_changed_since_the_record_uses_the_last_observation(self) -> None:
        self.box.set("claude", tracking="auto")
        built = self.box.build(self.record(claude={"policy": "off", "effective": "inactive",
                                                   "last_observed": "active"}))
        self.assertEqual(provider(built, "claude")["tracking_effective"], "active")
        built = self.box.build(self.record(claude={"policy": "off", "effective": "inactive"}))
        self.assertEqual(provider(built, "claude")["tracking_effective"], "pending")

    def test_last_activity_is_the_newest_event_not_a_quota_or_collection_time(self) -> None:
        self.history()
        self.box.set("claude", tracking="on")
        claude = provider(self.box.build(), "claude")
        self.assertEqual(claude["last_activity_at"], fixtures.iso(self.event))
        self.assertIsNotNone(claude["quota"]["measured_at"])
        self.assertNotEqual(claude["last_activity_at"], claude["quota"]["measured_at"])

    def test_summary_counts_expected_providers_and_outcomes(self) -> None:
        check = NOW - 600
        for name in ("codex", "grok", "gemini_cli"):
            self.box.set(name, tracking="on")
        self.box.set("claude", tracking="off")
        self.box.write_csv([[
            fixtures.detected(check, "codex"),
            fixtures.codex_quota(check, "primary", 10, 18000, NOW + 3600),
            fixtures.codex_usage_summary(check),
            fixtures.disabled(check, "claude"),
            # Grok failed before returning any data rows.
            fixtures.error(check, "grok", "unexpected_error", "collector", "boom"),
        ]])
        summary = self.box.build()["collection"]["summary"]
        outcomes = {o["id"]: o["outcome"] for o in summary["outcomes"]}
        self.assertNotIn("claude", outcomes, "off is never expected or failed")
        self.assertEqual(outcomes["codex"], "fresh")
        self.assertEqual(outcomes["grok"], "failed")
        self.assertEqual(outcomes["gemini_cli"], "stale", "expected but no rows this check")
        self.assertEqual(summary["providers_expected"], 3)
        self.assertEqual(summary["providers_fresh"], 1)
        self.assertEqual(summary["providers_stale"], 1)
        self.assertEqual(len(summary["skipped"]), 1, "the Off provider is still listed as skipped")
        self.assertEqual(summary["providers_failed"], 1, "legacy counters keep their meaning")

    def test_zero_expected_providers_is_its_own_state(self) -> None:
        check = NOW - 600
        self.box.write_csv([[fixtures.disabled(check, name) for name in BUILT_INS]])
        summary = self.box.build()["collection"]["summary"]
        self.assertEqual(summary["providers_expected"], 0)
        self.assertEqual(summary["outcomes"], [])
        self.assertEqual(summary["providers_attempted"], 0)

    def test_fixture_separates_no_hard_failure_from_fresh(self) -> None:
        summary = fixtures.fixture_report()["collection"]["summary"]
        outcomes = {o["id"]: o["outcome"] for o in summary["outcomes"]}
        self.assertEqual(summary["providers_updated"], 3)
        self.assertEqual(outcomes["claude"], "stale", "a stale stats cache is not fully updated")
        self.assertEqual(summary["providers_fresh"], 2)
        self.assertEqual(summary["providers_expected"], len(outcomes))


if __name__ == "__main__":
    unittest.main()
