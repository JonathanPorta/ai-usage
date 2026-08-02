#!/usr/bin/env python3
"""Black-box fixture tests for ai_usage_service.py.

These tests deliberately put executable "tripwires" in provider locations.  A
tripwire writes a marker if it is ever launched, which proves that doctor and
local-file collectors do not merely *claim* to avoid executing provider CLIs.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).with_name("ai_usage_service.py").resolve()


class IsolatedHome:
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ai-usage-test-")
        self.root = Path(self._temporary.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.data = self.root / "data"
        self.home.mkdir()
        self.bin.mkdir()
        self.data.mkdir()
        self.config = self.root / "config.json"
        self.csv = self.data / "usage.csv"
        self.log = self.data / "collector.log"
        self.state = self.data / "state.json"
        self.cache = self.data / "cache"
        self.tripwire = self.root / "tripwire.log"
        self.trace = self.root / "rpc-trace.jsonl"

    def close(self) -> None:
        self._temporary.cleanup()

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        environment["PATH"] = os.pathsep.join(
            [str(self.bin), "/usr/local/bin", "/usr/bin", "/bin"]
        )
        environment["TEST_TRIPWIRE"] = str(self.tripwire)
        environment["TEST_TRACE"] = str(self.trace)
        return environment

    def provider_config(self, enabled: str, **settings: object) -> dict[str, object]:
        providers: dict[str, dict[str, object]] = {
            name: {"enabled": name == enabled}
            for name in ("codex", "claude", "antigravity", "gemini_cli", "grok")
        }
        providers[enabled].update(settings)
        return {
            "poll_interval_seconds": 3600,
            "paths": {
                "usage_csv": str(self.csv),
                "log_file": str(self.log),
                "state_file": str(self.state),
                "cache_dir": str(self.cache),
            },
            "providers": providers,
        }

    def write_config(self, value: dict[str, object]) -> None:
        self.config.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def write_executable(self, name: str, body: str | None = None) -> Path:
        path = self.bin / name
        if body is None:
            body = (
                "#!/bin/sh\n"
                "printf '%s\\n' \"$0 $*\" >> \"$TEST_TRIPWIRE\"\n"
                "exit 79\n"
            )
        path.write_text(body, encoding="utf-8")
        path.chmod(0o700)
        return path

    def run(
        self,
        *arguments: str,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            input=input_text,
            text=True,
            capture_output=True,
            env=self.environment(),
            timeout=20,
            check=False,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"command failed ({result.returncode}): {result.args}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def rows(self) -> list[dict[str, str]]:
        with self.csv.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))


class CollectorBlackBoxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = IsolatedHome()

    def tearDown(self) -> None:
        self.case.close()

    def rows_matching(self, **expected: str) -> list[dict[str, str]]:
        return [
            row
            for row in self.case.rows()
            if all(row.get(key) == value for key, value in expected.items())
        ]

    def test_missing_binaries_are_skipped(self) -> None:
        providers = {
            name: {
                "enabled": True,
                "executable_names": [f"definitely-missing-{name}"],
            }
            for name in ("codex", "claude", "antigravity", "gemini_cli", "grok")
        }
        providers["claude"]["stats_file"] = str(self.case.root / "missing-claude.json")
        providers["antigravity"]["cache_file"] = str(self.case.root / "missing-agy.json")
        providers["gemini_cli"]["telemetry_file"] = str(self.case.root / "missing-gemini.jsonl")
        providers["grok"].update(
            {
                "grok_home": str(self.case.root / "missing-grok"),
                "auth_file": str(self.case.root / "missing-auth.json"),
                "billing_cache_file": str(self.case.root / "missing-billing.jsonl"),
            }
        )
        config = self.case.provider_config("codex")
        config["providers"] = providers
        self.case.write_config(config)

        self.case.run("once", "--config", str(self.case.config))

        rows = self.case.rows()
        missing = {
            row["provider"]
            for row in rows
            if row["category"] == "availability"
            and row["metric"] == "detected"
            and row["status"] == "missing"
        }
        self.assertEqual(
            missing,
            {"codex", "claude", "antigravity", "gemini_cli", "grok"},
        )
        self.assertFalse(self.case.tripwire.exists())

    def test_doctor_detects_but_executes_no_provider_binary(self) -> None:
        providers: dict[str, dict[str, object]] = {}
        names = {
            "codex": "codex",
            "claude": "claude",
            "antigravity": "agy",
            "gemini_cli": "gemini",
            "grok": "grok",
        }
        for provider, executable_name in names.items():
            executable = self.case.write_executable(executable_name)
            providers[provider] = {"enabled": True, "executable": str(executable)}
        config = self.case.provider_config("codex")
        config["providers"] = providers
        self.case.write_config(config)

        result = self.case.run(
            "doctor", "--config", str(self.case.config), "--json"
        )
        report = json.loads(result.stdout)

        self.assertTrue(all(report[name]["detected"] for name in names))
        self.assertFalse(
            self.case.tripwire.exists(),
            "doctor launched at least one provider tripwire",
        )

    def test_codex_json_rpc_is_metadata_only(self) -> None:
        fake = self.case.write_executable(
            "codex",
            """#!/usr/bin/python3
import json, os, sys
with open(os.environ["TEST_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps({"argv": sys.argv[1:]}) + "\\n")
for line in sys.stdin:
    message = json.loads(line)
    with open(os.environ["TEST_TRACE"], "a", encoding="utf-8") as trace:
        trace.write(json.dumps({"method": message.get("method")}) + "\\n")
    ident = message.get("id")
    if ident == 100:
        result = {"id": 100, "result": {}}
    elif ident == 101:
        result = {"id": 101, "result": {"summary": {"lifetimeTokens": 12345, "peakDailyTokens": 678}, "dailyUsageBuckets": [{"startDate": "2026-08-01", "tokens": 111}]}}
    elif ident == 102:
        result = {"id": 102, "result": {"rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": 14, "windowDurationMins": 10080, "resetsAt": 1785900000}, "planType": "pro"}}}}
    else:
        continue
    print(json.dumps(result), flush=True)
""",
        )
        self.case.write_config(
            self.case.provider_config("codex", executable=str(fake), timeout_seconds=5)
        )

        self.case.run("once", "--config", str(self.case.config))

        trace = [json.loads(line) for line in self.case.trace.read_text().splitlines()]
        self.assertEqual(trace[0], {"argv": ["app-server"]})
        methods = [item["method"] for item in trace[1:]]
        self.assertEqual(
            methods,
            [
                "initialize",
                "initialized",
                "account/usage/read",
                "account/rateLimits/read",
            ],
        )
        self.assertFalse(any("thread" in method or "turn" in method for method in methods))
        lifetime = self.rows_matching(
            provider="codex", category="usage", metric="lifetime_tokens"
        )
        limit = self.rows_matching(
            provider="codex", category="quota", metric="used_percent"
        )
        self.assertEqual(lifetime[0]["value"], "12345")
        self.assertEqual(limit[0]["value"], "14")

    def test_claude_stats_are_read_without_launching_claude(self) -> None:
        fake = self.case.write_executable("claude")
        stats = self.case.root / "claude-stats.json"
        stats.write_text(
            json.dumps(
                {
                    "totalSessions": 7,
                    "totalMessages": 89,
                    "modelUsage": {
                        "claude-opus-5": {
                            "inputTokens": 101,
                            "outputTokens": 202,
                            "cacheReadInputTokens": 303,
                            "costUSD": 1.25,
                        }
                    },
                    "dailyModelTokens": [
                        {"date": "2026-08-01", "tokensByModel": {"claude-opus-5": 404}}
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.case.write_config(
            self.case.provider_config(
                "claude", executable=str(fake), stats_file=str(stats)
            )
        )

        self.case.run("once", "--config", str(self.case.config))

        self.assertFalse(self.case.tripwire.exists())
        self.assertEqual(
            self.rows_matching(provider="claude", metric="total_sessions")[0]["value"],
            "7",
        )
        self.assertEqual(
            self.rows_matching(provider="claude", metric="output_tokens")[0]["value"],
            "202",
        )
        self.assertEqual(
            self.rows_matching(provider="claude", metric="estimated_api_cost")[0]["value"],
            "1.25",
        )

    def test_antigravity_callback_sanitizes_then_collector_reads_cache(self) -> None:
        fake = self.case.write_executable("agy")
        cache = self.case.cache / "antigravity.json"
        self.case.write_config(
            self.case.provider_config(
                "antigravity", executable=str(fake), cache_file=str(cache)
            )
        )
        payload = {
            "email": "private@example.test",
            "session_id": "secret-session",
            "transcript_path": "/private/transcript",
            "plan_tier": "pro",
            "model": {"id": "gemini-pro", "display_name": "Gemini Pro"},
            "context_window": {
                "total_input_tokens": 400,
                "total_output_tokens": 20,
                "context_window_size": 1000,
                "used_percentage": 42,
                "remaining_percentage": 58,
                "current_usage": {"input_tokens": 33, "output_tokens": 4},
            },
            "quota": {
                "weekly": {
                    "remaining_fraction": 0.75,
                    "reset_time": "2026-08-08T00:00:00Z",
                    "reset_in_seconds": 100,
                }
            },
        }

        result = self.case.run(
            "antigravity-statusline",
            "--config",
            str(self.case.config),
            input_text=json.dumps(payload),
        )
        sanitized_text = cache.read_text(encoding="utf-8")
        sanitized = json.loads(sanitized_text)
        self.assertIn("context 58% left", result.stdout)
        self.assertNotIn("private@example.test", sanitized_text)
        self.assertNotIn("secret-session", sanitized_text)
        self.assertNotIn("/private/transcript", sanitized_text)
        self.assertNotIn("email", sanitized)

        self.case.run("once", "--config", str(self.case.config))

        self.assertFalse(self.case.tripwire.exists())
        used = self.rows_matching(
            provider="antigravity", category="quota", metric="used_percent"
        )
        self.assertEqual(used[0]["value"], "25.0")
        self.assertEqual(
            self.rows_matching(
                provider="antigravity",
                metric="input_tokens",
                source="antigravity-status-line-events",
            )[0]["value"],
            "400",
        )

    def test_gemini_telemetry_is_incremental_and_never_launches_cli(self) -> None:
        fake = self.case.write_executable("gemini")
        telemetry = self.case.cache / "gemini.jsonl"
        telemetry.parent.mkdir(parents=True)
        events = [
            {
                "event_name": "gemini_cli.token.usage",
                "timestamp": "2026-08-01T12:00:00Z",
                "model": "gemini-2.5-pro",
                "token_type": "input",
                "value": 123,
            },
            {
                "name": "gemini_cli.api_response",
                "model": "gemini-2.5-pro",
                "input_token_count": 10,
                "output_token_count": 5,
            },
        ]
        telemetry.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        self.case.write_config(
            self.case.provider_config(
                "gemini_cli", executable=str(fake), telemetry_file=str(telemetry)
            )
        )

        self.case.run("once", "--config", str(self.case.config))
        first_rows = self.case.rows()
        self.case.run("once", "--config", str(self.case.config))
        all_rows = self.case.rows()

        self.assertFalse(self.case.tripwire.exists())
        usage_first = [row for row in first_rows if row["category"] == "usage"]
        usage_all = [row for row in all_rows if row["category"] == "usage"]
        self.assertEqual(len(usage_all), len(usage_first), "telemetry was counted twice")
        metrics = {(row["metric"], row["value"]) for row in usage_first}
        # token.usage is intentionally ignored: it overlaps api_response and
        # would double-count.  The api_response event is the canonical source.
        self.assertEqual(metrics, {("input_tokens", "10"), ("output_tokens", "5")})

    def test_gemini_preserves_partial_lines_and_detects_rotation(self) -> None:
        telemetry = self.case.cache / "gemini.jsonl"
        telemetry.parent.mkdir(parents=True)
        events = [
            {
                "name": "gemini_cli.api_response",
                "model": "gemini-test",
                "input_token_count": value,
            }
            for value in (1, 2, 3)
        ]
        second_line = json.dumps(events[1])
        split_at = len(second_line) // 2
        telemetry.write_text(
            json.dumps(events[0]) + "\n" + second_line[:split_at],
            encoding="utf-8",
        )
        self.case.write_config(
            self.case.provider_config(
                "gemini_cli", telemetry_file=str(telemetry)
            )
        )

        self.case.run("once", "--config", str(self.case.config))
        with telemetry.open("a", encoding="utf-8") as handle:
            handle.write(second_line[split_at:] + "\n")
        self.case.run("once", "--config", str(self.case.config))

        rotated = telemetry.with_suffix(".jsonl.1")
        telemetry.replace(rotated)
        telemetry.write_text(json.dumps(events[2]) + "\n", encoding="utf-8")
        self.case.run("once", "--config", str(self.case.config))

        values = [
            row["value"]
            for row in self.rows_matching(
                provider="gemini_cli",
                category="usage",
                metric="input_tokens",
            )
        ]
        self.assertEqual(values, ["1", "2", "3"])

    def test_grok_session_logs_and_fresh_billing_cache_avoid_cli(self) -> None:
        fake = self.case.write_executable("grok")
        grok_home = self.case.root / "grok-home"
        top = grok_home / "sessions" / "one" / "updates.jsonl"
        subagent = grok_home / "sessions" / "one" / "subagents" / "child" / "updates.jsonl"
        top.parent.mkdir(parents=True)
        subagent.parent.mkdir(parents=True)
        record = {
            "method": "_x.ai/session/update",
            "timestamp": "2026-08-01T00:00:00Z",
            "params": {
                "sessionId": "private-session",
                "_meta": {"eventId": "event-1"},
                "update": {
                    "sessionUpdate": "turn_completed",
                    "promptId": "prompt-1",
                    "usage": {
                        "inputTokens": 100,
                        "outputTokens": 25,
                        "totalTokens": 125,
                        "costUsdTicks": 12_300_000_000,
                        "costIsPartial": False,
                        "usageIsIncomplete": False,
                    },
                },
            },
        }
        top.write_text(json.dumps(record) + "\n", encoding="utf-8")
        child_record = json.loads(json.dumps(record))
        child_record["params"]["_meta"]["eventId"] = "event-child"
        child_record["params"]["update"]["usage"]["inputTokens"] = 9999
        subagent.write_text(json.dumps(child_record) + "\n", encoding="utf-8")
        auth = grok_home / "auth.json"
        auth.write_text("{}\n", encoding="utf-8")
        billing = grok_home / "logs" / "unified.jsonl"
        billing.parent.mkdir(parents=True)
        billing.write_text(
            json.dumps(
                {
                    "ts": time.time(),
                    "msg": "billing: fetched credits config",
                    "ctx": {
                        "config": {
                            "creditUsagePercent": 21,
                            "monthlyLimit": {"val": 4500},
                            "used": {"val": 945},
                            "currentPeriod": {
                                "type": "monthly",
                                "start": "2026-08-01T00:00:00Z",
                                "end": "2026-09-01T00:00:00Z",
                            },
                        },
                        "subscriptionTier": "supergrok",
                        "onDemandEnabled": True,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.case.write_config(
            self.case.provider_config(
                "grok",
                executable=str(fake),
                grok_home=str(grok_home),
                auth_file=str(auth),
                billing_cache_file=str(billing),
            )
        )

        self.case.run("once", "--config", str(self.case.config))
        first_rows = self.case.rows()
        self.case.run("once", "--config", str(self.case.config))
        all_rows = self.case.rows()

        self.assertFalse(self.case.tripwire.exists())
        session_first = [row for row in first_rows if row["source"] == "grok-session-log"]
        session_all = [row for row in all_rows if row["source"] == "grok-session-log"]
        self.assertEqual(len(session_first), len(session_all), "Grok events were counted twice")
        self.assertFalse(any(row["value"] == "9999" for row in all_rows))
        self.assertEqual(
            self.rows_matching(provider="grok", category="cost", metric="api_cost")[0]["value"],
            "1.23",
        )
        billing_rows = [row for row in all_rows if row["source"] == "grok-unified-log"]
        self.assertTrue(any(row["metric"] == "used_percent" for row in billing_rows))
        self.assertTrue(
            any(row["metric"] == "included_credit_limit" and row["value"] == "4500" for row in billing_rows)
        )
        csv_text = self.case.csv.read_text(encoding="utf-8")
        self.assertNotIn("private-session", csv_text)

    def test_grok_acp_sends_billing_metadata_method_only(self) -> None:
        fake = self.case.write_executable(
            "grok",
            """#!/usr/bin/python3
import json, os, sys
with open(os.environ["TEST_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps({"argv": sys.argv[1:]}) + "\\n")
for line in sys.stdin:
    message = json.loads(line)
    with open(os.environ["TEST_TRACE"], "a", encoding="utf-8") as trace:
        trace.write(json.dumps({"method": message.get("method")}) + "\\n")
    if message.get("id") == 1:
        response = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}
    elif message.get("id") == 2:
        response = {"jsonrpc": "2.0", "id": 2, "result": {"config": {"creditUsagePercent": 33, "monthlyLimit": {"val": 10000}, "used": {"val": 3300}}, "subscriptionTier": "supergrok"}}
    else:
        continue
    print(json.dumps(response), flush=True)
""",
        )
        grok_home = self.case.root / "grok-home"
        grok_home.mkdir()
        auth = grok_home / "auth.json"
        auth.write_text("{}\n", encoding="utf-8")
        self.case.write_config(
            self.case.provider_config(
                "grok",
                executable=str(fake),
                grok_home=str(grok_home),
                auth_file=str(auth),
                billing_cache_file=str(grok_home / "missing-billing.jsonl"),
                timeout_seconds=5,
            )
        )

        self.case.run("once", "--config", str(self.case.config))

        trace = [json.loads(line) for line in self.case.trace.read_text().splitlines()]
        self.assertEqual(trace[0], {"argv": ["agent", "--no-leader", "stdio"]})
        self.assertEqual(
            [item["method"] for item in trace[1:]],
            ["initialize", "x.ai/billing"],
        )
        self.assertEqual(
            self.rows_matching(provider="grok", metric="used_percent")[0]["value"],
            "33.0",
        )

    def test_install_no_start_creates_private_launch_agent_and_integrations(self) -> None:
        self.case.write_executable("agy")
        self.case.write_executable("gemini")
        config = self.case.home / ".ai-usage" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "providers": {
                        "gemini_cli": {"configure_telemetry": True}
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        config.chmod(0o600)

        result = self.case.run("install", "--config", str(config), "--no-start")

        root = self.case.home / ".ai-usage"
        installed = root / "collector.py"
        usage = root / "usage.csv"
        log = root / "collector.log"
        plist_path = (
            self.case.home
            / "Library"
            / "LaunchAgents"
            / "codes.porta.ai-usage.plist"
        )
        self.assertIn("Installed without starting", result.stdout)
        for path in (config, installed, usage, log, plist_path):
            self.assertTrue(path.exists(), f"installer did not create {path}")
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(usage.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(plist_path.stat().st_mode), 0o600)
        self.assertFalse(self.case.tripwire.exists())

        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertEqual(plist["Label"], "codes.porta.ai-usage")
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["ProcessType"], "Background")
        self.assertEqual(
            plist["ProgramArguments"][1:],
            [str(installed), "daemon", "--config", str(config)],
        )
        self.assertEqual(plist["StandardOutPath"], str(log))
        self.assertEqual(plist["StandardErrorPath"], str(log))

        antigravity_settings = json.loads(
            (self.case.home / ".gemini" / "antigravity-cli" / "settings.json").read_text()
        )
        wrapper = root / "antigravity-statusline"
        self.assertEqual(
            antigravity_settings["statusLine"],
            {"type": "command", "command": str(wrapper)},
        )
        self.assertEqual(stat.S_IMODE(wrapper.stat().st_mode), 0o700)

        gemini_settings = json.loads(
            (self.case.home / ".gemini" / "settings.json").read_text()
        )
        telemetry = gemini_settings["telemetry"]
        self.assertTrue(telemetry["enabled"])
        self.assertEqual(telemetry["target"], "local")
        self.assertFalse(telemetry["logPrompts"])
        self.assertFalse(telemetry["traces"])
        self.assertEqual(telemetry["outfile"], str(root / "cache" / "gemini-telemetry.log"))

    def test_uninstall_detaches_only_managed_hooks_and_preserves_data(self) -> None:
        self.case.write_executable("agy")
        self.case.write_executable("gemini")
        root = self.case.home / ".ai-usage"
        config = root / "config.json"
        root.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "providers": {
                        "gemini_cli": {"configure_telemetry": True}
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )

        self.case.run("install", "--config", str(config), "--no-start")
        self.case.run("uninstall", "--config", str(config))

        plist_path = (
            self.case.home
            / "Library"
            / "LaunchAgents"
            / "codes.porta.ai-usage.plist"
        )
        self.assertFalse(plist_path.exists())
        self.assertFalse((root / "collector.py").exists())
        self.assertFalse((root / "antigravity-statusline").exists())
        for preserved in (config, root / "usage.csv", root / "collector.log"):
            self.assertTrue(preserved.exists(), f"uninstall removed {preserved}")

        antigravity_settings = json.loads(
            (self.case.home / ".gemini" / "antigravity-cli" / "settings.json").read_text()
        )
        gemini_settings = json.loads(
            (self.case.home / ".gemini" / "settings.json").read_text()
        )
        self.assertNotIn("statusLine", antigravity_settings)
        self.assertNotIn("telemetry", gemini_settings)

    def test_incompatible_csv_is_preserved_before_schema_upgrade(self) -> None:
        self.case.csv.write_text(
            "timestamp,provider,tokens\n2026-07-01,codex,123\n",
            encoding="utf-8",
        )
        self.case.write_config(
            self.case.provider_config(
                "codex", executable_names=["definitely-missing-codex"]
            )
        )

        self.case.run("once", "--config", str(self.case.config))

        backups = list(self.case.data.glob("usage.legacy-*.csv"))
        self.assertEqual(len(backups), 1)
        self.assertIn("2026-07-01,codex,123", backups[0].read_text(encoding="utf-8"))
        with self.case.csv.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        self.assertIn("record_kind", header)
        self.assertIn("period_start", header)

    def test_monthly_subscription_is_recorded_once_and_revised_on_change(self) -> None:
        config = self.case.provider_config(
            "codex",
            executable_names=["definitely-missing-codex"],
            monthly_subscription_usd=20,
        )
        self.case.write_config(config)

        self.case.run("once", "--config", str(self.case.config))
        self.case.run("once", "--config", str(self.case.config))
        config["providers"]["codex"]["monthly_subscription_usd"] = 30
        self.case.write_config(config)
        self.case.run("once", "--config", str(self.case.config))

        rows = self.rows_matching(
            provider="codex",
            category="cost",
            metric="monthly_subscription",
        )
        self.assertEqual([row["value"] for row in rows], ["20.0", "30.0"])
        self.assertTrue(all(row["record_kind"] == "monthly_rate" for row in rows))
        self.assertTrue(all(row["period_start"] for row in rows))
        self.assertTrue(all(row["period_end"] for row in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
