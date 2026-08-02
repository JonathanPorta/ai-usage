#!/usr/bin/env python3
"""Black-box fixture tests for ai_usage_service.py.

These tests deliberately put executable "tripwires" in provider locations.  A
tripwire writes a marker if it is ever launched, which proves that doctor and
local-file collectors do not merely *claim* to avoid executing provider CLIs.
"""

from __future__ import annotations

import csv
from contextlib import contextmanager, ExitStack
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Optional
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("ai_usage_service.py").resolve()
WORKFLOW = SCRIPT.parent / ".github" / "workflows" / "tests.yml"

SPEC = importlib.util.spec_from_file_location("ai_usage_service_under_test", SCRIPT)
assert SPEC and SPEC.loader
SERVICE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVICE
SPEC.loader.exec_module(SERVICE)


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
        self.child_exit = self.root / "child-exit.log"

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
        environment["TEST_CHILD_EXIT"] = str(self.child_exit)
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

    def write_executable(self, name: str, body: Optional[str] = None) -> Path:
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

    def write_python_executable(self, name: str, body: str) -> Path:
        """Write a fixture using the same interpreter that runs the tests."""

        return self.write_executable(name, f"#!{sys.executable}\n{body}")

    def run(
        self,
        *arguments: str,
        input_text: Optional[str] = None,
        check: bool = True,
        timeout_seconds: float = 20,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            input=input_text,
            text=True,
            capture_output=True,
            env=self.environment(),
            timeout=timeout_seconds,
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

    @contextmanager
    def patched_service_install_paths(self):
        root = self.case.home / ".ai-usage"
        plist_path = (
            self.case.home
            / "Library"
            / "LaunchAgents"
            / "codes.porta.ai-usage.plist"
        )
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(SERVICE, "DEFAULT_ROOT", root))
            stack.enter_context(
                mock.patch.object(SERVICE, "DEFAULT_CONFIG_PATH", root / "config.json")
            )
            stack.enter_context(
                mock.patch.object(SERVICE, "DEFAULT_INSTALLED_SCRIPT", root / "collector.py")
            )
            stack.enter_context(mock.patch.object(SERVICE, "DEFAULT_PLIST_PATH", plist_path))
            stack.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.case.home)}))
            yield root, plist_path

    def assert_child_was_terminated(self, elapsed: float) -> None:
        self.assertLess(elapsed, 4.0, "provider timeout cleanup exceeded its bound")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.case.child_exit.exists():
            time.sleep(0.02)
        self.assertTrue(
            self.case.child_exit.exists(),
            "provider descendant did not receive process-group termination",
        )

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
        fake = self.case.write_python_executable(
            "codex",
            """import json, os, sys
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

    def test_rotation_drains_an_unread_tail_from_the_previous_inode(self) -> None:
        telemetry = self.case.cache / "gemini.jsonl"
        telemetry.parent.mkdir(parents=True)
        events = [
            {
                "name": "gemini_cli.api_response",
                "model": "gemini-rotation",
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
            self.case.provider_config("gemini_cli", telemetry_file=str(telemetry))
        )
        self.case.run("once", "--config", str(self.case.config))

        rotated = telemetry.with_suffix(".jsonl.1")
        telemetry.replace(rotated)
        with rotated.open("a", encoding="utf-8") as handle:
            handle.write(second_line[split_at:] + "\n")
        telemetry.write_text(json.dumps(events[2]) + "\n", encoding="utf-8")

        self.case.run("once", "--config", str(self.case.config))

        values = [
            row["value"]
            for row in self.rows_matching(
                provider="gemini_cli", category="usage", metric="input_tokens"
            )
        ]
        self.assertEqual(values, ["1", "2", "3"])

    def test_missing_rotated_inode_emits_detectable_data_loss(self) -> None:
        telemetry = self.case.cache / "gemini.jsonl"
        telemetry.parent.mkdir(parents=True)
        partial = json.dumps(
            {
                "name": "gemini_cli.api_response",
                "model": "gemini-loss",
                "input_token_count": 1,
            }
        )
        telemetry.write_text(partial[: len(partial) // 2], encoding="utf-8")
        self.case.write_config(
            self.case.provider_config("gemini_cli", telemetry_file=str(telemetry))
        )
        self.case.run("once", "--config", str(self.case.config))

        rotated = telemetry.with_suffix(".jsonl.1")
        telemetry.replace(rotated)
        telemetry.write_text(
            json.dumps(
                {
                    "name": "gemini_cli.api_response",
                    "model": "gemini-loss",
                    "input_token_count": 2,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        rotated.unlink()
        self.case.run("once", "--config", str(self.case.config), check=False)

        loss_rows = self.rows_matching(
            provider="gemini_cli",
            category="collection",
            metric="rotation_data_loss",
        )
        self.assertEqual(len(loss_rows), 1)
        self.assertEqual(loss_rows[0]["status"], "error")

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX process groups")
    def test_codex_cleanup_terminates_descendants_that_inherit_stdout(self) -> None:
        fake = self.case.write_python_executable(
            "codex",
            """import json, os, signal, sys, time
child = os.fork()
if child == 0:
    def stop(_signal, _frame):
        with open(os.environ["TEST_CHILD_EXIT"], "a", encoding="utf-8") as handle:
            handle.write("codex-child-terminated\\n")
        os._exit(0)
    signal.signal(signal.SIGTERM, stop)
    while True:
        time.sleep(1)
for line in sys.stdin:
    message = json.loads(line)
    ident = message.get("id")
    if ident == 100:
        response = {"id": 100, "result": {}}
    elif ident == 101:
        response = {"id": 101, "result": {"summary": {}}}
    elif ident == 102:
        response = {"id": 102, "result": {"rateLimitsByLimitId": {}}}
    else:
        continue
    print(json.dumps(response), flush=True)
""",
        )
        self.case.write_config(
            self.case.provider_config("codex", executable=str(fake), timeout_seconds=5)
        )

        started = time.monotonic()
        self.case.run(
            "once",
            "--config",
            str(self.case.config),
            timeout_seconds=8,
        )
        self.assert_child_was_terminated(time.monotonic() - started)

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX process groups")
    def test_grok_cleanup_terminates_descendants_that_inherit_stdout(self) -> None:
        fake = self.case.write_python_executable(
            "grok",
            """import json, os, signal, sys, time
child = os.fork()
if child == 0:
    def stop(_signal, _frame):
        with open(os.environ["TEST_CHILD_EXIT"], "a", encoding="utf-8") as handle:
            handle.write("grok-child-terminated\\n")
        os._exit(0)
    signal.signal(signal.SIGTERM, stop)
    while True:
        time.sleep(1)
for line in sys.stdin:
    message = json.loads(line)
    ident = message.get("id")
    if ident == 1:
        response = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}
    elif ident == 2:
        response = {"jsonrpc": "2.0", "id": 2, "result": {"creditUsagePercent": 1}}
    else:
        continue
    print(json.dumps(response), flush=True)
""",
        )
        grok_home = self.case.root / "grok-timeout-home"
        grok_home.mkdir()
        (grok_home / "auth.json").write_text("{}\n", encoding="utf-8")
        self.case.write_config(
            self.case.provider_config(
                "grok",
                executable=str(fake),
                grok_home=str(grok_home),
                auth_file=str(grok_home / "auth.json"),
                billing_cache_file=str(grok_home / "missing.jsonl"),
                timeout_seconds=5,
            )
        )

        started = time.monotonic()
        self.case.run(
            "once",
            "--config",
            str(self.case.config),
            timeout_seconds=8,
        )
        self.assert_child_was_terminated(time.monotonic() - started)

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
        fake = self.case.write_python_executable(
            "grok",
            """import json, os, sys
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
            [str(Path(value).resolve()) if index in {0, 3} else value for index, value in enumerate(plist["ProgramArguments"][1:])],
            [str(installed.resolve()), "daemon", "--config", str(config.resolve())],
        )
        self.assertEqual(Path(plist["StandardOutPath"]).resolve(), log.resolve())
        self.assertEqual(Path(plist["StandardErrorPath"]).resolve(), log.resolve())

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
        self.assertEqual(
            Path(telemetry["outfile"]).resolve(),
            (root / "cache" / "gemini-telemetry.log").resolve(),
        )

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

    def test_preexisting_identical_integrations_remain_unowned(self) -> None:
        self.case.write_executable("agy")
        self.case.write_executable("gemini")
        root = self.case.home / ".ai-usage"
        config = root / "config.json"
        root.mkdir(parents=True)
        config.write_text(
            json.dumps({"providers": {"gemini_cli": {"configure_telemetry": True}}})
            + "\n",
            encoding="utf-8",
        )
        wrapper = root / "antigravity-statusline"
        antigravity_settings = self.case.home / ".gemini" / "antigravity-cli" / "settings.json"
        antigravity_settings.parent.mkdir(parents=True)
        original_status = {"type": "command", "command": str(wrapper)}
        antigravity_settings.write_text(
            json.dumps({"statusLine": original_status}) + "\n", encoding="utf-8"
        )
        gemini_settings = self.case.home / ".gemini" / "settings.json"
        gemini_settings.parent.mkdir(parents=True, exist_ok=True)
        original_telemetry = {
            "enabled": True,
            "target": "local",
            "outfile": str(root / "cache" / "gemini-telemetry.log"),
            "logPrompts": False,
            "traces": False,
        }
        gemini_settings.write_text(
            json.dumps({"telemetry": original_telemetry}) + "\n", encoding="utf-8"
        )

        self.case.run("install", "--config", str(config), "--no-start")
        ownership = json.loads((root / "install-ownership.json").read_text(encoding="utf-8"))
        self.assertFalse(ownership["integrations"]["antigravity"]["owned"])
        self.assertFalse(ownership["integrations"]["gemini_cli"]["owned"])
        self.assertFalse(wrapper.exists(), "installer created a wrapper for an unowned hook")

        self.case.run("uninstall", "--config", str(config))

        self.assertEqual(
            json.loads(antigravity_settings.read_text(encoding="utf-8"))["statusLine"],
            original_status,
        )
        self.assertEqual(
            json.loads(gemini_settings.read_text(encoding="utf-8"))["telemetry"],
            original_telemetry,
        )

    def test_preexisting_nonidentical_integrations_are_never_replaced_or_removed(self) -> None:
        self.case.write_executable("agy")
        self.case.write_executable("gemini")
        root = self.case.home / ".ai-usage"
        config = root / "config.json"
        root.mkdir(parents=True)
        config.write_text(
            json.dumps({"providers": {"gemini_cli": {"configure_telemetry": True}}})
            + "\n",
            encoding="utf-8",
        )
        antigravity_settings = self.case.home / ".gemini" / "antigravity-cli" / "settings.json"
        antigravity_settings.parent.mkdir(parents=True)
        custom_status = {"type": "command", "command": "/usr/local/bin/custom-status"}
        antigravity_settings.write_text(
            json.dumps({"statusLine": custom_status}) + "\n", encoding="utf-8"
        )
        gemini_settings = self.case.home / ".gemini" / "settings.json"
        gemini_settings.parent.mkdir(parents=True, exist_ok=True)
        custom_telemetry = {
            "enabled": True,
            "target": "local",
            "outfile": "/tmp/custom-gemini.log",
            "logPrompts": True,
        }
        gemini_settings.write_text(
            json.dumps({"telemetry": custom_telemetry}) + "\n", encoding="utf-8"
        )

        self.case.run("install", "--config", str(config), "--no-start")
        self.case.run("uninstall", "--config", str(config))

        self.assertEqual(
            json.loads(antigravity_settings.read_text(encoding="utf-8"))["statusLine"],
            custom_status,
        )
        self.assertEqual(
            json.loads(gemini_settings.read_text(encoding="utf-8"))["telemetry"],
            custom_telemetry,
        )

    def test_launchctl_failures_roll_back_install_transaction(self) -> None:
        config = self.case.provider_config(
            "codex", executable_names=["definitely-missing-codex"]
        )
        self.case.write_config(config)

        with self.patched_service_install_paths() as (root, plist_path):
            SERVICE.install_service(self.case.config, no_start=True)
            tracked = [
                root / "collector.py",
                root / "install-ownership.json",
                plist_path,
            ]
            baseline = {path: path.read_bytes() for path in tracked}

            failure_sets = {
                "bootout": {"bootout"},
                "bootstrap-and-load": {"bootstrap", "load"},
                "enable": {"enable"},
                "kickstart": {"kickstart"},
            }
            for label, failures in failure_sets.items():
                with self.subTest(stage=label):
                    def fake_launchctl(arguments: list[str], check: bool = False):
                        command = arguments[0]
                        returncode = 0 if command == "print" else int(command in failures)
                        result = subprocess.CompletedProcess(
                            ["launchctl", *arguments], returncode, "", f"{command} failed"
                        )
                        if check and returncode:
                            raise RuntimeError(f"{command} failed")
                        return result

                    with ExitStack() as stack:
                        stack.enter_context(mock.patch.object(SERVICE.sys, "platform", "darwin"))
                        stack.enter_context(
                            mock.patch.object(
                                SERVICE, "find_launchctl", return_value=Path("/bin/true")
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                SERVICE, "run_launchctl", side_effect=fake_launchctl
                            )
                        )
                        with self.assertRaises(RuntimeError):
                            SERVICE.install_service(self.case.config, no_start=False)
                    self.assertEqual({path: path.read_bytes() for path in tracked}, baseline)

            def bootstrap_fallback(arguments: list[str], check: bool = False):
                command = arguments[0]
                returncode = int(command == "bootstrap")
                result = subprocess.CompletedProcess(
                    ["launchctl", *arguments], returncode, "", "bootstrap unavailable"
                )
                if check and returncode:
                    raise RuntimeError("bootstrap unavailable")
                return result

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(SERVICE.sys, "platform", "darwin"))
                stack.enter_context(
                    mock.patch.object(
                        SERVICE, "find_launchctl", return_value=Path("/bin/true")
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        SERVICE, "run_launchctl", side_effect=bootstrap_fallback
                    )
                )
                SERVICE.install_service(self.case.config, no_start=False)
            self.assertEqual({path: path.read_bytes() for path in tracked}, baseline)

    def test_bootout_failure_aborts_uninstall_before_deleting_files(self) -> None:
        config = self.case.provider_config(
            "codex", executable_names=["definitely-missing-codex"]
        )
        self.case.write_config(config)
        with self.patched_service_install_paths() as (root, plist_path):
            SERVICE.install_service(self.case.config, no_start=True)
            installed = root / "collector.py"

            def fake_launchctl(arguments: list[str], check: bool = False):
                command = arguments[0]
                returncode = 1 if command == "bootout" else 0
                result = subprocess.CompletedProcess(
                    ["launchctl", *arguments], returncode, "", "bootout failed"
                )
                if check and returncode:
                    raise RuntimeError("bootout failed")
                return result

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(SERVICE.sys, "platform", "darwin"))
                stack.enter_context(
                    mock.patch.object(
                        SERVICE, "find_launchctl", return_value=Path("/bin/true")
                    )
                )
                stack.enter_context(
                    mock.patch.object(SERVICE, "run_launchctl", side_effect=fake_launchctl)
                )
                with self.assertRaisesRegex(RuntimeError, "bootout failed"):
                    SERVICE.uninstall_service(self.case.config)

            self.assertTrue(installed.exists())
            self.assertTrue(plist_path.exists())
            self.assertTrue((root / "install-ownership.json").exists())

    def test_install_start_preflight_mutates_nothing_off_macos(self) -> None:
        if sys.platform == "darwin":
            self.skipTest("non-macOS preflight control")
        config = self.case.home / ".ai-usage" / "config.json"
        result = self.case.run("install", "--config", str(config), check=False)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.case.home / ".ai-usage").exists())
        self.assertFalse(
            (
                self.case.home
                / "Library"
                / "LaunchAgents"
                / "codes.porta.ai-usage.plist"
            ).exists()
        )

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

    def test_malformed_state_fails_closed_without_overwriting_or_appending(self) -> None:
        self.case.state.write_text("{ definitely not json\n", encoding="utf-8")
        original = self.case.state.read_bytes()
        self.case.write_config(
            self.case.provider_config(
                "codex", executable_names=["definitely-missing-codex"]
            )
        )

        result = self.case.run(
            "once", "--config", str(self.case.config), check=False
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("state file", result.stderr)
        self.assertEqual(self.case.state.read_bytes(), original)
        self.assertFalse(self.case.csv.exists())

    def test_csv_commit_recovers_exactly_once_after_state_save_failure(self) -> None:
        self.case.write_config(
            self.case.provider_config(
                "codex", executable_names=["definitely-missing-codex"]
            )
        )
        known_row = SERVICE.metric_row(
            "2026-08-02T00:00:00Z",
            "test-provider",
            "usage",
            "source_event",
            record_kind="event_total",
            value=1,
            unit="events",
            source="test-fixture",
        )

        def first_collection(
            _config: object, *, include_history: bool, state: dict[str, object]
        ) -> list[dict[str, object]]:
            del include_history
            state["fixture_offset"] = 1
            return [known_row]

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(SERVICE, "collect_snapshot", side_effect=first_collection)
            )
            stack.enter_context(
                mock.patch.object(
                    SERVICE, "save_state", side_effect=OSError("state unavailable")
                )
            )
            with self.assertRaisesRegex(OSError, "state unavailable"):
                SERVICE.run_once(self.case.config)

        pending = self.case.state.with_name(f"{self.case.state.name}.pending")
        self.assertTrue(pending.is_file())
        persisted = self.case.csv.read_bytes()
        self.case.csv.write_bytes(persisted[:-5])
        with mock.patch.object(SERVICE, "collect_snapshot", return_value=[]):
            SERVICE.run_once(self.case.config)

        rows = [
            row
            for row in self.case.rows()
            if row["provider"] == "test-provider" and row["metric"] == "source_event"
        ]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["transaction_id"])
        self.assertEqual(rows[0]["transaction_index"], "0")
        self.assertFalse(pending.exists())
        self.assertEqual(json.loads(self.case.state.read_text())["fixture_offset"], 1)

    def test_path_role_collisions_and_symlink_aliases_fail_before_output(self) -> None:
        collisions: list[tuple[str, str]] = []
        collisions.append((str(self.case.csv), str(self.case.csv)))
        collisions.append((str(self.case.csv), f"{self.case.csv}.lock"))
        alias_target = self.case.data / "shared.json"
        alias_target.write_text("{}\n", encoding="utf-8")
        alias = self.case.data / "state-alias.json"
        alias.symlink_to(alias_target)
        collisions.append((str(alias_target), str(alias)))

        for usage_path, state_path in collisions:
            with self.subTest(usage_path=usage_path, state_path=state_path):
                config = self.case.provider_config(
                    "codex", executable_names=["definitely-missing-codex"]
                )
                config["paths"]["usage_csv"] = usage_path
                config["paths"]["state_file"] = state_path
                self.case.write_config(config)
                started = time.monotonic()
                result = self.case.run(
                    "once",
                    "--config",
                    str(self.case.config),
                    check=False,
                    timeout_seconds=4,
                )
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(result.returncode, 1)
                self.assertIn("path role collision", result.stderr)
                self.assertFalse(self.case.log.exists())

        config = self.case.provider_config(
            "gemini_cli", telemetry_file=str(self.case.csv)
        )
        self.case.write_config(config)
        result = self.case.run(
            "once", "--config", str(self.case.config), check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("path role collision", result.stderr)
        self.assertFalse(self.case.log.exists())

    def test_concurrent_valid_collectors_record_each_source_event_once(self) -> None:
        telemetry = self.case.cache / "gemini.jsonl"
        telemetry.parent.mkdir(parents=True)
        telemetry.write_text(
            json.dumps(
                {
                    "name": "gemini_cli.api_response",
                    "model": "concurrency",
                    "input_token_count": 7,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.case.write_config(
            self.case.provider_config("gemini_cli", telemetry_file=str(telemetry))
        )
        command = [
            sys.executable,
            str(SCRIPT),
            "once",
            "--config",
            str(self.case.config),
        ]
        processes = [
            subprocess.Popen(command, env=self.case.environment(), text=True) for _ in range(2)
        ]
        self.assertEqual([process.wait(timeout=20) for process in processes], [0, 0])
        usage_rows = self.rows_matching(
            provider="gemini_cli", category="usage", metric="input_tokens"
        )
        self.assertEqual([row["value"] for row in usage_rows], ["7"])

    def test_workflow_and_make_surface_are_immutable_and_canonical(self) -> None:
        makefile = SCRIPT.with_name("Makefile").read_text(encoding="utf-8")
        self.assertRegex(makefile, r"(?m)^test:")
        self.assertRegex(makefile, r"(?m)^check:")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        references = re.findall(r"(?m)^\s*uses:\s*[^@\s]+@([^\s#]+)", workflow)
        self.assertTrue(references)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in references))
        self.assertIn("macos-14", workflow)
        self.assertIn('python: ["3.9", "3.14"]', workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
