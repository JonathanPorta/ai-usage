"""Phase 4's All tools: the report's `groups` (AC-1 to AC-3) and the series tokens (AC-9).

Synthetic data only. The rules are checked on hand-built parts; the report on the
fixture sandbox, which has its own HOME and absent provider executables. Nothing
reads the owner's files or starts a provider tool.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import ai_usage_fixtures as fixtures
import ai_usage_report as report

REPO = Path(__file__).resolve().parent.parent
TOKENS = REPO / "macos" / "design" / "tokens.json"

VALUES = {"measured": 100, "zero": 0, "missing": None, "not_collected": None, "partial": 40}

# Every unordered pair of two accounts' day states: the day's state and total. Written
# out, not computed, so the table is the specification (PRD rules 3 and 4).
PAIRS = {
    ("measured", "measured"): ("measured", 200),
    ("measured", "zero"): ("measured", 100),
    ("measured", "missing"): ("incomplete", 100),
    ("measured", "not_collected"): ("measured", 100),
    ("measured", "partial"): ("partial", 140),
    ("zero", "zero"): ("zero", 0),
    ("zero", "missing"): ("incomplete", 0),
    ("zero", "not_collected"): ("zero", 0),
    ("zero", "partial"): ("partial", 40),
    ("missing", "missing"): ("missing", None),
    ("missing", "not_collected"): ("missing", None),
    ("missing", "partial"): ("incomplete", 40),
    ("not_collected", "not_collected"): ("not_collected", None),
    ("not_collected", "partial"): ("partial", 40),
    ("partial", "partial"): ("partial", 80),
}

GROUP_KEYS = {"type", "name", "members", "excluded", "coverage_start", "days"}
DAY_KEYS = {"date", "state", "total", "incomplete_events", "parts"}
PART_KEYS = {"id", "state", "total", "as_of", "incomplete_events"}


def part(index: int, state: str, as_of: str | None = None, incomplete: int = 0) -> dict:
    return {"id": f"a{index}", "state": state, "total": VALUES[state], "as_of": as_of,
            "incomplete_events": incomplete}


class DayRuleTests(unittest.TestCase):
    def test_every_pair_of_states_in_both_orders(self) -> None:
        self.assertEqual(len(PAIRS), 15)
        for (first, second), (state, total) in PAIRS.items():
            for order in ((first, second), (second, first)):
                with self.subTest(order=order):
                    day = report.group_day("2026-10-04", [part(0, order[0]), part(1, order[1])])
                    self.assertEqual((day["state"], day["total"]), (state, total))

    def test_three_accounts(self) -> None:
        cases = [
            (("measured", "partial", "missing"), ("incomplete", 140)),
            (("not_collected", "not_collected", "zero"), ("zero", 0)),
            (("missing", "missing", "not_collected"), ("missing", None)),
            (("measured", "zero", "measured"), ("measured", 200)),
        ]
        for states, expected in cases:
            with self.subTest(states=states):
                day = report.group_day("2026-10-04", [part(i, s) for i, s in enumerate(states)])
                self.assertEqual((day["state"], day["total"]), expected)

    def test_no_known_value_is_null_and_known_zeros_are_zero(self) -> None:
        for states in (("missing", "missing"), ("missing", "not_collected"), ("not_collected", "not_collected")):
            day = report.group_day("2026-10-04", [part(i, s) for i, s in enumerate(states)])
            self.assertIsNone(day["total"], states)
        for states in (("zero", "zero"), ("zero", "missing"), ("zero", "not_collected")):
            day = report.group_day("2026-10-04", [part(i, s) for i, s in enumerate(states)])
            self.assertEqual(day["total"], 0, states)
            self.assertIsNotNone(day["total"], states)

    def test_no_members_is_not_collected_with_no_total(self) -> None:
        self.assertEqual(report.group_day("2026-10-04", []),
                         {"date": "2026-10-04", "state": "not_collected", "total": None,
                          "incomplete_events": 0, "parts": []})

    def test_parts_keep_their_own_times_and_incomplete_events(self) -> None:
        day = report.group_day("2026-10-04", [part(0, "partial", "2026-10-04T16:17:00Z", 2),
                                              part(1, "partial", None, 1)])
        self.assertEqual([p["as_of"] for p in day["parts"]], ["2026-10-04T16:17:00Z", None])
        self.assertEqual([p["incomplete_events"] for p in day["parts"]], [2, 1])
        self.assertEqual(day["incomplete_events"], 3)
        self.assertNotIn("as_of", day)

    def test_day_and_part_keys_are_exactly_the_documented_ones(self) -> None:
        day = report.group_day("2026-10-04", [{**part(0, "measured"), "cache_read": 5, "input": 60}])
        self.assertEqual(set(day), DAY_KEYS)
        self.assertEqual(set(day["parts"][0]), PART_KEYS)


class ReportGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.full = fixtures.fixture_report(second_account=True, local_plugin=True)
        cls.week = fixtures.fixture_report(second_account=True, local_plugin=True, days=7)
        cls.off = fixtures.fixture_report(second_account=True, scenario="all-tools", days=30)

    def reports(self) -> list[dict]:
        return [self.full, self.week, self.off]

    def test_one_group_per_type_in_the_order_of_its_first_provider(self) -> None:
        for built in self.reports():
            order: list[str] = []
            for provider in built["providers"]:
                if provider["type"] not in order:
                    order.append(provider["type"])
            self.assertEqual([g["type"] for g in built["groups"]], order)
        self.assertEqual([g["type"] for g in self.full["groups"]],
                         ["codex", "claude", "antigravity", "grok", "gemini_cli", "example"])

    def test_members_share_the_type_and_keep_display_order(self) -> None:
        for built in self.reports():
            providers = {p["id"]: p for p in built["providers"]}
            for group in built["groups"]:
                ids = group["members"] + [entry["id"] for entry in group["excluded"]]
                self.assertEqual({providers[i]["type"] for i in ids}, {group["type"]})
                shown = [p["id"] for p in built["providers"] if p["id"] in group["members"]]
                self.assertEqual(group["members"], shown)
        claude = next(g for g in self.full["groups"] if g["type"] == "claude")
        self.assertEqual((claude["name"], claude["members"]), ("Claude Code", ["claude", "claude-az"]))
        local = next(g for g in self.full["groups"] if g["type"] == "example")
        self.assertEqual(local["name"], next(p["name"] for p in self.full["providers"] if p["id"] == "example"))

    def test_days_match_the_providers_dates_and_count(self) -> None:
        for built, count in ((self.full, 90), (self.week, 7), (self.off, 30)):
            dates = [d["date"] for d in built["providers"][0]["days"]]
            self.assertEqual(len(dates), count)
            for group in built["groups"]:
                self.assertEqual([d["date"] for d in group["days"]], dates)

    def test_parts_copy_each_members_own_day_and_totals_add_known_values(self) -> None:
        for built in self.reports():
            providers = {p["id"]: p for p in built["providers"]}
            for group in built["groups"]:
                for index, day in enumerate(group["days"]):
                    own = [providers[i]["days"][index] for i in group["members"]]
                    self.assertEqual(day["parts"], [{"id": i, **{k: d[k] for k in sorted(PART_KEYS - {"id"})}}
                                                    for i, d in zip(group["members"], own)])
                    known = [d["total"] for d in own if d["total"] is not None]
                    self.assertEqual(day["total"], sum(known) if known else None)
                    self.assertEqual(day["incomplete_events"], sum(d["incomplete_events"] for d in own))

    def test_cache_tokens_stay_out(self) -> None:
        claude = next(g for g in self.full["groups"] if g["type"] == "claude")
        own = {p["id"]: p for p in self.full["providers"]}
        cached = [i for i, d in enumerate(own["claude"]["days"]) if d["cache_read"]]
        self.assertTrue(cached)
        for index in cached:
            day = claude["days"][index]
            self.assertEqual(day["total"], sum(own[i]["days"][index]["total"] or 0 for i in claude["members"]))
            self.assertLess(day["total"], day["total"] + own["claude"]["days"][index]["cache_read"])

    def test_a_disabled_account_and_its_history_are_left_out(self) -> None:
        claude = next(g for g in self.off["groups"] if g["type"] == "claude")
        self.assertEqual(claude["members"], ["claude", "claude-az"])
        self.assertEqual(claude["excluded"], [{"id": "claude-old", "reason": "disabled"}])
        old = next(p for p in self.off["providers"] if p["id"] == "claude-old")
        self.assertTrue(any(d["total"] for d in old["days"]), "the off account has history to leave out")
        self.assertFalse(any(p["id"] == "claude-old" for d in claude["days"] for p in d["parts"]))

    def test_a_missing_reading_makes_an_incomplete_day(self) -> None:
        claude = next(g for g in self.off["groups"] if g["type"] == "claude")
        incomplete = [d for d in claude["days"] if d["state"] == "incomplete"]
        self.assertEqual(len(incomplete), 1)
        parts = {p["id"]: p for p in incomplete[0]["parts"]}
        self.assertEqual(parts["claude-az"]["state"], "missing")
        self.assertEqual(incomplete[0]["total"], parts["claude"]["total"])

    def test_a_group_with_no_members(self) -> None:
        providers = copy.deepcopy(self.full["providers"])
        for provider in providers:
            if provider["type"] == "antigravity":
                provider.update(enabled=False, setup="disabled")
        group = next(g for g in report.build_groups(providers) if g["type"] == "antigravity")
        self.assertEqual((group["members"], group["excluded"], group["coverage_start"]),
                         ([], [{"id": "antigravity", "reason": "disabled"}], None))
        self.assertEqual({(d["state"], d["total"], len(d["parts"])) for d in group["days"]},
                         {("not_collected", None, 0)})

    def test_groups_carry_only_the_documented_keys(self) -> None:
        for built in self.reports():
            self.assertEqual(list(built)[-1], "groups")
            for group in built["groups"]:
                self.assertEqual(set(group), GROUP_KEYS)
                for day in group["days"]:
                    self.assertEqual(set(day), DAY_KEYS)
                    for item in day["parts"]:
                        self.assertEqual(set(item), PART_KEYS)
                for entry in group["excluded"]:
                    self.assertEqual(set(entry), {"id", "reason"})

    def test_groups_are_a_function_of_the_providers(self) -> None:
        for built in self.reports():
            self.assertEqual(built["groups"], report.build_groups(built["providers"]))


def luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class SeriesTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        document = json.loads(TOKENS.read_text(encoding="utf-8"))
        self.semantic = document["color"]["semantic"]
        self.chart = document["color"]["component"]["chart"]

    def value(self, name: str, dark: bool) -> str:
        token = self.semantic[name]
        return token["$extensions"]["codes.porta.appearance"]["dark"] if dark else token["$value"]

    def test_each_account_token_aliases_its_series(self) -> None:
        for index in range(1, 5):
            self.assertEqual(self.chart[f"account{index}"]["$value"], f"{{color.semantic.series{index}}}")

    def test_series_reach_three_to_one_on_every_surface_they_touch(self) -> None:
        # The background, the panel (cards), the selected day and the today fill.
        surfaces = ("background", "panel", "hover", "accentBackground")
        for dark in (False, True):
            for index in range(1, 5):
                color = self.value(f"series{index}", dark)
                for surface in surfaces:
                    with self.subTest(series=index, surface=surface, dark=dark):
                        self.assertGreaterEqual(contrast(color, self.value(surface, dark)), 3.0)

    def test_neighbouring_series_differ_in_lightness(self) -> None:
        for dark in (False, True):
            for index in range(1, 4):
                with self.subTest(pair=(index, index + 1), dark=dark):
                    self.assertGreaterEqual(contrast(self.value(f"series{index}", dark),
                                                     self.value(f"series{index + 1}", dark)), 1.5)


if __name__ == "__main__":
    unittest.main()
