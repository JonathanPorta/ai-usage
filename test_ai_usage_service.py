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


_GUARD_DIR: Optional[tempfile.TemporaryDirectory] = None
_PREVIOUS_LAUNCHCTL: Optional[str] = None


def setUpModule() -> None:
    """Defense in depth for in-process tests: any unmocked launchctl call
    reaches a refusing fake, never the developer's real launchd domain."""
    global _GUARD_DIR, _PREVIOUS_LAUNCHCTL
    _GUARD_DIR = tempfile.TemporaryDirectory(prefix="ai-usage-launchctl-guard-")
    guard = Path(_GUARD_DIR.name) / "refusing-launchctl"
    guard.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{Path(_GUARD_DIR.name) / 'calls.log'}'\n"
        'case "$1" in print) exit 113 ;; *) exit 97 ;; esac\n',
        encoding="utf-8",
    )
    guard.chmod(0o755)
    _PREVIOUS_LAUNCHCTL = os.environ.get(SERVICE.LAUNCHCTL_OVERRIDE_ENV)
    os.environ[SERVICE.LAUNCHCTL_OVERRIDE_ENV] = str(guard)


def tearDownModule() -> None:
    if _PREVIOUS_LAUNCHCTL is None:
        os.environ.pop(SERVICE.LAUNCHCTL_OVERRIDE_ENV, None)
    else:
        os.environ[SERVICE.LAUNCHCTL_OVERRIDE_ENV] = _PREVIOUS_LAUNCHCTL
    if _GUARD_DIR is not None:
        _GUARD_DIR.cleanup()


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
        self.child_pid = self.root / "child.pid"
        self.child_ready = self.root / "child.ready"

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
        environment["TEST_CHILD_PID"] = str(self.child_pid)
        environment["TEST_CHILD_READY"] = str(self.child_ready)
        environment["AI_USAGE_LAUNCHCTL"] = str(self.fake_launchctl())
        return environment

    def fake_launchctl(self) -> Path:
        """A launchctl stand-in: records calls and reports the service as not loaded.

        Without it, black-box install/uninstall runs reach the real per-user
        launchd domain and boot out the developer's installed collector.
        """
        path = self.root / "fake-launchctl"
        if not path.exists():
            path.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >> '{self.root / 'launchctl-calls.log'}'\n"
                'case "$1" in print) exit 113 ;; *) exit 0 ;; esac\n',
                encoding="utf-8",
            )
            path.chmod(0o755)
        return path

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
            error_details = ""
            if self.csv.is_file():
                error_rows = [
                    row for row in self.rows() if row.get("status") == "error"
                ]
                if error_rows:
                    error_details = (
                        "\nerror rows:\n"
                        + json.dumps(error_rows, indent=2, sort_keys=True)
                        + "\n"
                    )
            raise AssertionError(
                f"command failed ({result.returncode}): {result.args}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                f"{error_details}"
            )
        return result

    def rows(self) -> list[dict[str, str]]:
        if not self.csv.is_file():
            return []
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
        child_pid = int(self.case.child_pid.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        child_exists = True
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                child_exists = False
                break
            time.sleep(0.02)
        self.assertFalse(child_exists, "provider descendant survived cleanup")
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
        providers["claude"]["projects_dir"] = str(self.case.root / "missing-claude-projects")
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
        # Fully absent providers must not pollute the CSV with missing/availability
        # rows every hour. Only an explicit config disable should leave a marker.
        providers = {"codex", "claude", "antigravity", "gemini_cli", "grok"}
        self.assertEqual(
            {row["provider"] for row in rows} & providers,
            set(),
            f"expected no rows for absent providers, got: {rows}",
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

    def _write_claude_assistant_line(
        self,
        path: Path,
        *,
        message_id: str,
        session_id: str,
        model: str,
        timestamp: str,
        usage: dict[str, int],
        text: str = "hello",
        extra: Optional[dict] = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "type": "assistant",
            "timestamp": timestamp,
            "sessionId": session_id,
            "message": {
                "id": message_id,
                "model": model,
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "usage": usage,
            },
        }
        if extra:
            record.update(extra)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def test_claude_session_transcripts_backfill_without_launching_or_duplicating(self) -> None:
        fake = self.case.write_executable("claude")
        stats = self.case.root / "claude-stats.json"
        stats.write_text(
            json.dumps(
                {
                    "lastComputedDate": "2026-08-01",
                    "totalSessions": 7,
                    "totalMessages": 89,
                    "modelUsage": {"claude-opus-5": {"inputTokens": 101, "outputTokens": 202}},
                }
            ),
            encoding="utf-8",
        )
        old = time.time() - (10 * 24 * 3600)
        os.utime(stats, (old, old))
        projects = self.case.root / "claude-projects"
        parent = projects / "workspace" / "session-one.jsonl"
        subagent = projects / "workspace" / "session-one" / "subagents" / "agent-a.jsonl"
        usage = {
            "input_tokens": 3,
            "output_tokens": 11,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 7,
        }
        self._write_claude_assistant_line(
            parent,
            message_id="msg_parent",
            session_id="secret-session",
            model="claude-opus-5",
            timestamp="2026-08-20T12:00:00.000Z",
            usage=usage,
            text="SECRET_PROMPT_TEXT",
        )
        self._write_claude_assistant_line(
            parent,
            message_id="msg_synthetic",
            session_id="secret-session",
            model="<synthetic>",
            timestamp="2026-08-20T12:01:00.000Z",
            usage={"input_tokens": 9, "output_tokens": 9},
        )
        self._write_claude_assistant_line(
            subagent,
            message_id="msg_parent",
            session_id="secret-session",
            model="claude-opus-5",
            timestamp="2026-08-20T12:00:00.000Z",
            usage={"input_tokens": 999, "output_tokens": 999},
            text="should-not-double-count",
        )
        self._write_claude_assistant_line(
            subagent,
            message_id="msg_child",
            session_id="secret-session",
            model="claude-opus-5-5",
            timestamp="2026-08-20T12:02:00.000Z",
            usage={"input_tokens": 5, "output_tokens": 17},
        )
        grok_home = self.case.root / "grok-home"
        (grok_home / "sessions" / "one").mkdir(parents=True)
        self.case.write_config(
            {
                "poll_interval_seconds": 3600,
                "paths": {
                    "usage_csv": str(self.case.csv),
                    "log_file": str(self.case.log),
                    "state_file": str(self.case.state),
                    "cache_dir": str(self.case.cache),
                },
                "providers": {
                    "codex": {"enabled": False},
                    "claude": {
                        "enabled": True,
                        "executable": str(fake),
                        "stats_file": str(stats),
                        "projects_dir": str(projects),
                    },
                    "antigravity": {"enabled": False},
                    "gemini_cli": {"enabled": False},
                    "grok": {
                        "enabled": True,
                        "executable_names": ["definitely-missing-grok"],
                        "grok_home": str(grok_home),
                        "auth_file": str(grok_home / "missing-auth.json"),
                        "billing_cache_file": str(grok_home / "missing-billing.jsonl"),
                    },
                },
            }
        )

        self.case.run("once", "--config", str(self.case.config))
        first = self.case.rows()
        header = first[0].keys() if first else []
        self.assertEqual(list(header), SERVICE.CSV_FIELDS)
        self.case.run("once", "--config", str(self.case.config))
        all_rows = self.case.rows()

        self.assertFalse(self.case.tripwire.exists())
        session_first = [row for row in first if row["source"] == "claude-session-log"]
        session_all = [row for row in all_rows if row["source"] == "claude-session-log"]
        self.assertEqual(len(session_first), len(session_all), "Claude turns were counted twice")
        self.assertTrue(session_first)
        self.assertEqual(
            {row["record_kind"] for row in session_first},
            {"event_total"},
        )
        self.assertEqual(
            self.rows_matching(
                provider="claude",
                source="claude-session-log",
                metric="output_tokens",
                scope=SERVICE.stable_scope("secret-session") + ":claude-opus-5",
            )[0]["value"],
            "11",
        )
        self.assertEqual(
            self.rows_matching(
                provider="claude",
                source="claude-session-log",
                metric="output_tokens",
                scope=SERVICE.stable_scope("secret-session") + ":claude-opus-5-5",
            )[0]["value"],
            "17",
        )
        self.assertFalse(
            any(row["value"] == "999" for row in session_all),
            "overlapping subagent message was double-counted",
        )
        self.assertFalse(any(row["value"] == "9" for row in session_all))
        csv_text = self.case.csv.read_text(encoding="utf-8")
        self.assertNotIn("SECRET_PROMPT_TEXT", csv_text)
        self.assertNotIn("secret-session", csv_text)
        self.assertNotIn("msg_parent", csv_text)
        self.assertEqual(
            self.rows_matching(provider="claude", source="claude-stats-cache", metric="total_sessions")[0]["value"],
            "7",
        )
        age = self.rows_matching(provider="claude", metric="source_age_seconds")
        self.assertTrue(age)
        self.assertEqual(age[0]["status"], "stale")
        self.assertEqual(
            self.rows_matching(provider="claude", source="claude-session-log", metric="output_tokens")[0]["collected_at"],
            "2026-08-20T12:00:00.000Z",
        )
        self.assertEqual(
            self.rows_matching(provider="claude", source="claude-session-log", metric="output_tokens")[0]["period_start"],
            "2026-08-20",
        )
        grok_rows = [row for row in all_rows if row["provider"] == "grok"]
        self.assertEqual(grok_rows, [])

    def test_claude_session_byte_budget_defers_without_dropping_later_files(self) -> None:
        fake = self.case.write_executable("claude")
        projects = self.case.root / "claude-projects"
        first = projects / "a" / "one.jsonl"
        second = projects / "b" / "two.jsonl"
        usage = {"input_tokens": 1, "output_tokens": 2}
        padding = "x" * 8000
        for index in range(200):
            self._write_claude_assistant_line(
                first,
                message_id=f"msg_a_{index}",
                session_id="session-a",
                model="claude-opus-5",
                timestamp="2026-08-21T00:00:00.000Z",
                usage=usage,
                text=padding,
            )
        self._write_claude_assistant_line(
            second,
            message_id="msg_b",
            session_id="session-b",
            model="claude-opus-5",
            timestamp="2026-08-22T00:00:00.000Z",
            usage={"input_tokens": 4, "output_tokens": 8},
        )
        self.assertGreater(first.stat().st_size, 1024 * 1024)
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
                max_session_bytes_per_poll=1024 * 1024,
            )
        )

        self.case.run("once", "--config", str(self.case.config))
        first_rows = self.case.rows()
        deferred = self.rows_matching(provider="claude", metric="session_files_deferred")
        self.assertTrue(deferred)
        self.assertFalse(
            any(
                row["source"] == "claude-session-log" and row["period_start"] == "2026-08-22"
                for row in first_rows
            )
        )
        self.case.run("once", "--config", str(self.case.config))
        self.case.run("once", "--config", str(self.case.config))
        later = self.case.rows()
        self.assertTrue(
            any(
                row["source"] == "claude-session-log"
                and row["metric"] == "output_tokens"
                and row["value"] == "8"
                for row in later
            )
        )
        self.assertFalse(self.case.tripwire.exists())

    def test_claude_streamed_message_keeps_final_usage_across_polls(self) -> None:
        fake = self.case.write_executable("claude")
        projects = self.case.root / "claude-projects"
        session = projects / "workspace" / "session-stream.jsonl"
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:00.000Z",
            usage={"input_tokens": 10, "output_tokens": 1},
            extra={"stop_reason": None},
        )
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
            )
        )

        self.case.run("once", "--config", str(self.case.config))
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:01.000Z",
            usage={"input_tokens": 10, "output_tokens": 34},
            extra={"stop_reason": "tool_use"},
        )
        self.case.run("once", "--config", str(self.case.config))

        output_rows = self.rows_matching(
            provider="claude",
            source="claude-session-log",
            metric="output_tokens",
            scope=SERVICE.stable_scope("stream-session") + ":claude-opus-5",
        )
        self.assertEqual(sum(int(row["value"]) for row in output_rows), 34)
        self.assertFalse(self.case.tripwire.exists())

    def _claude_state(self) -> dict:
        return json.loads(self.case.state.read_text(encoding="utf-8"))

    def _write_claude_state(self, state: dict) -> None:
        self.case.state.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _output_token_total(self, session_id: str, model: str) -> int:
        scope = SERVICE.stable_scope(session_id) + ":" + model
        return sum(
            int(row["value"])
            for row in self.rows_matching(
                provider="claude",
                source="claude-session-log",
                metric="output_tokens",
                scope=scope,
            )
        )

    def test_claude_upgrade_from_first_seen_state_keeps_final_usage(self) -> None:
        fake = self.case.write_executable("claude")
        projects = self.case.root / "claude-projects"
        session = projects / "workspace" / "session-stream.jsonl"
        parent = projects / "workspace" / "session-one.jsonl"
        subagent = projects / "workspace" / "session-one" / "subagents" / "agent-a.jsonl"
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:00.000Z",
            usage={"input_tokens": 10, "output_tokens": 1},
            extra={"stop_reason": None},
        )
        self._write_claude_assistant_line(
            parent,
            message_id="msg_parent",
            session_id="overlap-session",
            model="claude-opus-5",
            timestamp="2026-08-20T12:00:00.000Z",
            usage={"input_tokens": 3, "output_tokens": 11},
        )
        self._write_claude_assistant_line(
            subagent,
            message_id="msg_parent",
            session_id="overlap-session",
            model="claude-opus-5",
            timestamp="2026-08-20T12:00:00.000Z",
            usage={"input_tokens": 999, "output_tokens": 999},
        )
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
            )
        )

        self.case.run("once", "--config", str(self.case.config))
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 1)
        self.assertEqual(self._output_token_total("overlap-session", "claude-opus-5"), 11)
        state = self._claude_state()
        self.assertIn("msg_stream", state.get("claude_recent_message_ids", []))
        state.pop("claude_accounted_usage", None)
        self._write_claude_state(state)

        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:01.000Z",
            usage={"input_tokens": 10, "output_tokens": 34},
            extra={"stop_reason": "tool_use"},
        )
        self.case.run("once", "--config", str(self.case.config))

        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 34)
        self.assertEqual(self._output_token_total("overlap-session", "claude-opus-5"), 11)
        self.assertFalse(
            any(
                row["source"] == "claude-session-log" and row["value"] == "999"
                for row in self.case.rows()
            ),
            "overlapping subagent message was double-counted after upgrade",
        )
        self.assertFalse(self.case.tripwire.exists())

    def test_claude_upgrade_recovers_final_usage_already_behind_offset(self) -> None:
        fake = self.case.write_executable("claude")
        projects = self.case.root / "claude-projects"
        session = projects / "workspace" / "session-stream.jsonl"
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:00.000Z",
            usage={"input_tokens": 10, "output_tokens": 1},
        )
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
            )
        )
        self.case.run("once", "--config", str(self.case.config))
        state = self._claude_state()
        state.pop("claude_accounted_usage", None)
        self._write_claude_state(state)
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:01.000Z",
            usage={"input_tokens": 10, "output_tokens": 34},
        )
        state = self._claude_state()
        size = session.stat().st_size
        inode = session.stat().st_ino
        for cursor in state.setdefault("claude_offsets", {}).values():
            if isinstance(cursor, dict):
                cursor["offset"] = size
                cursor["inode"] = inode
        self._write_claude_state(state)

        self.case.run("once", "--config", str(self.case.config))
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 34)
        self.assertFalse(self.case.tripwire.exists())

    def _unrecoverable_legacy_rows(self) -> list[dict[str, str]]:
        return self.rows_matching(
            provider="claude",
            metric="legacy_message_baselines_unrecovered",
        )

    def test_claude_upgrade_does_not_replay_ids_evicted_from_recent_window(self) -> None:
        fake = self.case.write_executable("claude")
        projects = self.case.root / "claude-projects"
        session = projects / "workspace" / "session-stream.jsonl"
        self._write_claude_assistant_line(
            session,
            message_id="msg_evicted",
            session_id="evicted-session",
            model="claude-opus-5",
            timestamp="2026-09-01T11:00:00.000Z",
            usage={"input_tokens": 4, "output_tokens": 7},
        )
        self._write_claude_assistant_line(
            session,
            message_id="msg_recent",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:00.000Z",
            usage={"input_tokens": 10, "output_tokens": 1},
        )
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
            )
        )
        self.case.run("once", "--config", str(self.case.config))
        self.assertEqual(self._output_token_total("evicted-session", "claude-opus-5"), 7)
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 1)
        state = self._claude_state()
        state.pop("claude_accounted_usage", None)
        state["claude_recent_message_ids"] = ["msg_recent"]
        self._write_claude_state(state)
        self._write_claude_assistant_line(
            session,
            message_id="msg_recent",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:01.000Z",
            usage={"input_tokens": 10, "output_tokens": 34},
        )

        self.case.run("once", "--config", str(self.case.config))
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 34)
        self.assertEqual(self._output_token_total("evicted-session", "claude-opus-5"), 7)
        self.assertFalse(self._unrecoverable_legacy_rows())
        self.assertFalse(self.case.tripwire.exists())

    def test_claude_upgrade_recovers_final_usage_after_inode_rotation(self) -> None:
        fake = self.case.write_executable("claude")
        projects = self.case.root / "claude-projects"
        session = projects / "workspace" / "session-stream.jsonl"
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:00.000Z",
            usage={"input_tokens": 10, "output_tokens": 1},
        )
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
            )
        )
        self.case.run("once", "--config", str(self.case.config))
        state = self._claude_state()
        state.pop("claude_accounted_usage", None)
        self._write_claude_state(state)
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:01.000Z",
            usage={"input_tokens": 10, "output_tokens": 34},
        )
        state = self._claude_state()
        old_inode = session.stat().st_ino
        size = session.stat().st_size
        for cursor in state.setdefault("claude_offsets", {}).values():
            if isinstance(cursor, dict):
                cursor["offset"] = size
                cursor["inode"] = old_inode
        self._write_claude_state(state)
        rotated = session.with_name(session.name + ".rotated")
        session.rename(rotated)
        session.write_text("", encoding="utf-8")
        self.assertNotEqual(session.stat().st_ino, old_inode)
        self.assertEqual(rotated.stat().st_ino, old_inode)

        self.case.run("once", "--config", str(self.case.config))
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 34)
        self.assertFalse(self._unrecoverable_legacy_rows())
        self.assertFalse(self.case.tripwire.exists())

    def test_claude_upgrade_prefix_replay_stays_within_poll_byte_budget(self) -> None:
        fake = self.case.write_executable("claude")
        projects = self.case.root / "claude-projects"
        session = projects / "workspace" / "session-stream.jsonl"
        padding = "x" * 8000
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:00.000Z",
            usage={"input_tokens": 10, "output_tokens": 1},
        )
        for index in range(200):
            self._write_claude_assistant_line(
                session,
                message_id=f"msg_pad_{index}",
                session_id="pad-session",
                model="claude-opus-5",
                timestamp="2026-09-01T12:00:00.000Z",
                usage={"input_tokens": 1, "output_tokens": 2},
                text=padding,
            )
        self.assertGreater(session.stat().st_size, 1024 * 1024)
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
            )
        )
        self.case.run("once", "--config", str(self.case.config), timeout_seconds=40)
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 1)
        self.assertEqual(self._output_token_total("pad-session", "claude-opus-5"), 400)
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
                max_session_bytes_per_poll=1024 * 1024,
            )
        )
        state = self._claude_state()
        state.pop("claude_accounted_usage", None)
        state["claude_recent_message_ids"] = ["msg_stream"]
        self._write_claude_state(state)
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:01.000Z",
            usage={"input_tokens": 10, "output_tokens": 34},
        )
        state = self._claude_state()
        size = session.stat().st_size
        inode = session.stat().st_ino
        for cursor in state.setdefault("claude_offsets", {}).values():
            if isinstance(cursor, dict):
                cursor["offset"] = size
                cursor["inode"] = inode
        self._write_claude_state(state)

        self.case.run("once", "--config", str(self.case.config), timeout_seconds=40)
        after_first = self._claude_state()
        scans = [
            entry
            for entry in (after_first.get("claude_legacy_scan") or {}).values()
            if isinstance(entry, dict)
        ]
        self.assertTrue(scans)
        scan = scans[0]
        self.assertLess(int(scan.get("offset", 0)), size)
        self.assertLessEqual(int(scan.get("offset", 0)), 1024 * 1024 + 65536)
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 1)
        self.assertEqual(self._output_token_total("pad-session", "claude-opus-5"), 400)
        self.assertFalse(self._unrecoverable_legacy_rows())

        for _ in range(8):
            if self._output_token_total("stream-session", "claude-opus-5") == 34:
                break
            self.case.run("once", "--config", str(self.case.config), timeout_seconds=40)
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 34)
        self.assertEqual(self._output_token_total("pad-session", "claude-opus-5"), 400)
        self.assertFalse(self._unrecoverable_legacy_rows())
        self.assertFalse(self.case.tripwire.exists())

    def test_claude_upgrade_prefix_replay_bounds_a_line_larger_than_poll_budget(self) -> None:
        fake = self.case.write_executable("claude")
        projects = self.case.root / "claude-projects"
        session = projects / "workspace" / "session-stream.jsonl"
        self._write_claude_assistant_line(
            session,
            message_id="msg_stream",
            session_id="stream-session",
            model="claude-opus-5",
            timestamp="2026-09-01T12:00:00.000Z",
            usage={"input_tokens": 10, "output_tokens": 34},
            text="x" * 2_200_000,
        )
        self.assertGreater(session.stat().st_size, 2_200_000)
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
            )
        )
        self.case.run("once", "--config", str(self.case.config), timeout_seconds=40)
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 34)
        self.case.write_config(
            self.case.provider_config(
                "claude",
                executable=str(fake),
                stats_file=str(self.case.root / "missing-stats.json"),
                projects_dir=str(projects),
                max_session_bytes_per_poll=1024 * 1024,
            )
        )
        state = self._claude_state()
        state.pop("claude_accounted_usage", None)
        state["claude_recent_message_ids"] = ["msg_stream"]
        size = session.stat().st_size
        inode = session.stat().st_ino
        for cursor in state.setdefault("claude_offsets", {}).values():
            if isinstance(cursor, dict):
                cursor["offset"] = size
                cursor["inode"] = inode
        self._write_claude_state(state)

        previous_offset = 0
        budget = 1024 * 1024
        finished = False
        for _ in range(8):
            self.case.run("once", "--config", str(self.case.config), timeout_seconds=40)
            after = self._claude_state()
            scans = [
                entry
                for entry in (after.get("claude_legacy_scan") or {}).values()
                if isinstance(entry, dict)
            ]
            self.assertTrue(scans)
            scan_offset = int(scans[0].get("offset", 0))
            self.assertLessEqual(scan_offset - previous_offset, budget)
            previous_offset = scan_offset
            self.assertEqual(
                self._output_token_total("stream-session", "claude-opus-5"), 34
            )
            self.assertFalse(self._unrecoverable_legacy_rows())
            if scan_offset >= size:
                finished = True
                break
        self.assertTrue(finished, "prefix replay never finished the oversized line")
        self.assertEqual(self._output_token_total("stream-session", "claude-opus-5"), 34)
        self.assertFalse(self.case.tripwire.exists())

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
    with open(os.environ["TEST_CHILD_PID"], "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    with open(os.environ["TEST_CHILD_READY"], "w", encoding="utf-8"):
        pass
    while True:
        time.sleep(1)
deadline = time.monotonic() + 2
while not os.path.exists(os.environ["TEST_CHILD_READY"]):
    if time.monotonic() >= deadline:
        raise RuntimeError("codex child did not become ready")
    time.sleep(0.01)
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
    with open(os.environ["TEST_CHILD_PID"], "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    with open(os.environ["TEST_CHILD_READY"], "w", encoding="utf-8"):
        pass
    while True:
        time.sleep(1)
deadline = time.monotonic() + 2
while not os.path.exists(os.environ["TEST_CHILD_READY"]):
    if time.monotonic() >= deadline:
        raise RuntimeError("grok child did not become ready")
    time.sleep(0.01)
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
            ["initialize", "_x.ai/billing"],
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

    def test_every_collector_subprocess_carries_the_isolated_launcher(self) -> None:
        environment = self.case.environment()
        self.assertEqual(environment[SERVICE.LAUNCHCTL_OVERRIDE_ENV], str(self.case.fake_launchctl()))
        self.assertTrue(str(self.case.fake_launchctl()).startswith(str(self.case.root)))
        # In-process code is guarded too (setUpModule), never the system binary.
        self.assertNotIn(os.environ[SERVICE.LAUNCHCTL_OVERRIDE_ENV], ("/bin/launchctl", "/usr/bin/launchctl", ""))

    def test_explicit_launcher_that_is_missing_or_invalid_fails_closed(self) -> None:
        directory = self.case.root / "launcher-dir"
        directory.mkdir()
        not_executable = self.case.root / "launcher.txt"
        not_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for value in ("", str(self.case.root / "missing-launchctl"), str(directory), str(not_executable)):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {SERVICE.LAUNCHCTL_OVERRIDE_ENV: value}):
                    self.assertIsNone(SERVICE.find_launchctl(), "must not fall back to /bin/launchctl")
                    with self.assertRaisesRegex(FileNotFoundError, "AI_USAGE_LAUNCHCTL is set"):
                        SERVICE.run_launchctl(["print", "gui/0/none"])
        with mock.patch.dict(os.environ, {SERVICE.LAUNCHCTL_OVERRIDE_ENV: str(self.case.fake_launchctl())}):
            self.assertEqual(SERVICE.find_launchctl(), self.case.fake_launchctl())

    def test_relative_launcher_override_runs_the_validated_file_not_a_path_lookup(self) -> None:
        workdir = self.case.root / "relative-launcher"
        trap_dir = self.case.root / "path-trap"
        workdir.mkdir()
        trap_dir.mkdir()
        marker = self.case.root / "which-launcher-ran"
        for directory, label in ((workdir, "validated"), (trap_dir, "path-trap")):
            fake = directory / "launchctl"
            fake.write_text(f"#!/bin/sh\necho {label} >> '{marker}'\n", encoding="utf-8")
            fake.chmod(0o755)
        previous = os.getcwd()
        os.chdir(workdir)
        self.addCleanup(os.chdir, previous)
        overrides = {SERVICE.LAUNCHCTL_OVERRIDE_ENV: "./launchctl", "PATH": f"{trap_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"}
        with mock.patch.dict(os.environ, overrides):
            found = SERVICE.find_launchctl()
            self.assertIsNotNone(found)
            self.assertTrue(found.is_absolute(), found)
            self.assertEqual(found.resolve(), (workdir / "launchctl").resolve())
            SERVICE.run_launchctl(["print", "gui/0/none"])
        self.assertEqual(marker.read_text(encoding="utf-8").split(), ["validated"],
                         "the PATH executable named launchctl must never run")
        # Relative values that don't name an executable still fail closed.
        with mock.patch.dict(os.environ, {SERVICE.LAUNCHCTL_OVERRIDE_ENV: "./missing-launchctl"}):
            self.assertIsNone(SERVICE.find_launchctl())

    def test_invalid_explicit_launcher_blocks_lifecycle_commands_without_changes(self) -> None:
        root = self.case.home / ".ai-usage"
        config = root / "config.json"
        self.case.run("install", "--config", str(config), "--no-start")
        before = sorted(str(p.relative_to(self.case.home)) for p in self.case.home.rglob("*"))
        missing = str(self.case.root / "missing-launchctl")
        environment = dict(self.case.environment(), **{SERVICE.LAUNCHCTL_OVERRIDE_ENV: missing})
        commands = [["status"]]
        if sys.platform == "darwin":
            commands += [["uninstall", "--config", str(config)], ["install", "--config", str(config)]]
        for command in commands:
            with self.subTest(command=command[0]):
                result = subprocess.run([sys.executable, str(SCRIPT), *command], env=environment,
                                        capture_output=True, text=True, timeout=30, check=False)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("unavailable", result.stdout + result.stderr)
        after = sorted(str(p.relative_to(self.case.home)) for p in self.case.home.rglob("*"))
        self.assertEqual(before, after, "a failed-closed lifecycle command changed files")
        self.assertFalse((self.case.root / "launchctl-calls.log").exists()
                         and "bootout" in (self.case.root / "launchctl-calls.log").read_text(encoding="utf-8"))

    def test_black_box_uninstall_never_reaches_the_real_launchd_domain(self) -> None:
        root = self.case.home / ".ai-usage"
        config = root / "config.json"
        self.case.run("install", "--config", str(config), "--no-start")
        self.case.run("uninstall", "--config", str(config))

        calls_log = self.case.root / "launchctl-calls.log"
        if sys.platform != "darwin":
            # Off macOS, uninstall never consults launchctl at all.
            self.assertFalse(calls_log.exists())
            return
        calls = calls_log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(calls, "uninstall did not consult the isolated launchctl")
        self.assertTrue(all(call.startswith("print ") for call in calls), calls)

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
                "bootstrap": {"bootstrap"},
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

            calls: list[str] = []

            def recording(arguments: list[str], check: bool = False):
                calls.append(arguments[0])
                return subprocess.CompletedProcess(["launchctl", *arguments], 0, "", "")

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(SERVICE.sys, "platform", "darwin"))
                stack.enter_context(
                    mock.patch.object(
                        SERVICE, "find_launchctl", return_value=Path("/bin/true")
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        SERVICE, "run_launchctl", side_effect=recording
                    )
                )
                SERVICE.install_service(self.case.config, no_start=False)
            self.assertEqual({path: path.read_bytes() for path in tracked}, baseline)
            # Explicit start order, with no legacy `load -w`.
            self.assertEqual(calls[-3:], ["enable", "bootstrap", "kickstart"])
            self.assertNotIn("load", calls)

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


class ScheduleDecisionTests(unittest.TestCase):
    def test_pause_resume_and_due_decisions(self) -> None:
        decide = SERVICE.schedule_decision
        self.assertEqual(decide(paused=True, was_paused=False, next_due=5.0, now=1.0, interval=60), ("pause", 5.0))
        self.assertEqual(decide(paused=True, was_paused=True, next_due=5.0, now=9.0, interval=60), ("paused", 5.0))
        # Resume clears the pause and schedules the next check one interval later,
        # even if the old due time already passed.
        self.assertEqual(decide(paused=False, was_paused=True, next_due=5.0, now=100.0, interval=60), ("resume", 160.0))
        self.assertEqual(decide(paused=False, was_paused=False, next_due=160.0, now=120.0, interval=60), ("wait", 160.0))
        self.assertEqual(decide(paused=False, was_paused=False, next_due=160.0, now=160.0, interval=60), ("collect", 160.0))
        self.assertEqual(decide(paused=False, was_paused=False, next_due=None, now=0.0, interval=60), ("collect", None))


class MenuAppCommandTests(unittest.TestCase):
    """Collector commands used by the menu app, exercised against isolated homes."""

    def setUp(self) -> None:
        self.case = IsolatedHome()
        self.addCleanup(self.case.close)
        self.uid = os.getuid()

    def wait_for_log(self, text: str, timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            content = self.case.log.read_text(encoding="utf-8") if self.case.log.exists() else ""
            if text in content:
                return content
            time.sleep(0.05)
        self.fail(f"log never contained {text!r}:\n{content}")

    def disabled_config(self, **extra: object) -> dict[str, object]:
        """Every provider off: a fast, provider-free check."""
        config = self.case.provider_config("codex", executable_names=["definitely-missing-codex"])
        config["providers"]["codex"]["enabled"] = False
        config.update(extra)
        return config

    def test_configure_changes_only_named_settings_atomically(self) -> None:
        config = self.disabled_config()
        config["x_custom"] = {"kept": [1, 2, 3]}
        config["providers"]["codex"]["executable_names"] = ["my-codex"]
        self.case.write_config(config)
        os.chmod(self.case.config, 0o600)
        result = self.case.run("configure", "--config", str(self.case.config),
                               "--set", "poll_paused=true",
                               "--set", "providers.codex.monthly_subscription_usd=25",
                               "--set", "providers.grok.enabled=false")
        self.assertEqual(json.loads(result.stdout)["poll_paused"], True)
        written = json.loads(self.case.config.read_text(encoding="utf-8"))
        self.assertIs(written["poll_paused"], True)
        self.assertEqual(written["providers"]["codex"]["monthly_subscription_usd"], 25)
        self.assertIs(written["providers"]["grok"]["enabled"], False)
        self.assertEqual(written["x_custom"], {"kept": [1, 2, 3]}, "unrelated keys must survive")
        self.assertEqual(written["providers"]["codex"]["executable_names"], ["my-codex"])
        self.assertEqual(written["paths"], config["paths"])
        self.assertEqual(stat.S_IMODE(self.case.config.stat().st_mode), 0o600)

        before = self.case.config.read_bytes()
        for bad in ("poll_interval_seconds=10", "providers.codex.enabled=\"yes\"", "paths.usage_csv=\"/tmp/x\"",
                    "providers.codex.monthly_subscription_usd=-1", "poll_paused=1", "nonsense"):
            with self.subTest(bad=bad):
                failed = self.case.run("configure", "--config", str(self.case.config), "--set", bad, check=False)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(self.case.config.read_bytes(), before, "a rejected change must not touch the file")

    def test_once_progress_reports_each_enabled_provider(self) -> None:
        config = self.disabled_config()
        for name in ("codex", "grok"):
            config["providers"][name]["enabled"] = True
            config["providers"][name]["executable_names"] = [f"definitely-missing-{name}"]
        config["providers"]["grok"]["collect_billing_via_acp"] = False
        self.case.write_config(config)
        result = self.case.run("once", "--config", str(self.case.config), "--progress")
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        self.assertEqual([(e["provider"], e["index"], e["total"]) for e in events], [("codex", 1, 2), ("grok", 2, 2)])
        self.assertIn("Appended", result.stdout)

    def test_paused_daemon_skips_scheduled_checks_while_once_still_collects(self) -> None:
        self.case.write_config(self.disabled_config(poll_paused=True, poll_on_start=True))
        environment = dict(self.case.environment(), **{SERVICE.SCHEDULE_TICK_ENV: "0.1"})
        daemon = subprocess.Popen([sys.executable, str(SCRIPT), "daemon", "--config", str(self.case.config)],
                                  env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for_log("scheduled checks paused; collector keeps running")
            time.sleep(0.6)
            log = self.case.log.read_text(encoding="utf-8")
            self.assertNotIn("collection completed", log.replace("one-shot collection completed", ""),
                             "a paused daemon must not run its start-up or scheduled check")
            self.assertTrue(daemon.poll() is None, "pause must keep the daemon running")

            once = self.case.run("once", "--config", str(self.case.config))
            self.assertIn("Appended", once.stdout, "Collect now must stay available while paused")

            self.case.run("configure", "--config", str(self.case.config), "--set", "poll_paused=false")
            self.wait_for_log("scheduled checks resumed next_poll_seconds=3600")
            time.sleep(0.6)
            log = self.case.log.read_text(encoding="utf-8")
            self.assertNotIn("INFO collection completed", log,
                             "resume schedules the next check one interval later, not immediately")
        finally:
            daemon.terminate()
            daemon.wait(timeout=15)
        self.assertIn("collector daemon stopped", self.case.log.read_text(encoding="utf-8"))

    def test_pausing_a_running_daemon_takes_effect_after_its_current_check(self) -> None:
        self.case.write_config(self.disabled_config(poll_on_start=True))
        environment = dict(self.case.environment(), **{SERVICE.SCHEDULE_TICK_ENV: "0.1"})
        daemon = subprocess.Popen([sys.executable, str(SCRIPT), "daemon", "--config", str(self.case.config)],
                                  env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for_log("INFO collection completed")
            self.case.run("configure", "--config", str(self.case.config), "--set", "poll_paused=true")
            log = self.wait_for_log("scheduled checks paused")
            self.assertLess(log.index("INFO collection completed"), log.index("scheduled checks paused"))
            self.assertIsNone(daemon.poll())
        finally:
            daemon.terminate()
            daemon.wait(timeout=15)

    def stateful_launchctl(self, *, loaded: bool, running: bool, disabled: bool, linger_prints: int = 0) -> Path:
        state = self.case.root / "launchd-state.json"
        state.write_text(json.dumps({"loaded": loaded, "running": running, "disabled": disabled,
                                     "linger_prints": linger_prints}), encoding="utf-8")
        calls = self.case.root / "launchctl-calls.log"
        script = self.case.root / "stateful-launchctl"
        script.write_text(f"""#!{sys.executable}
import json, sys
state_path = {str(state)!r}
s = json.load(open(state_path))
args = sys.argv[1:]
open({str(calls)!r}, "a").write(" ".join(args) + "\\n")
cmd = args[0]
code = 0
if cmd == "print" and s.get("linger"):
    s["linger"] -= 1
    if not s["linger"]:
        s["loaded"] = False; s["running"] = False
    print("state = running")
    print("pid = 4242")
elif cmd == "print":
    if not s["loaded"]:
        code = 113
    else:
        print("state = running" if s["running"] else "state = waiting")
        if s["running"]:
            print("pid = 4242")
elif cmd == "print-disabled":
    print('disabled services = {{\\n\\t"codes.porta.ai-usage" => ' + ("disabled" if s["disabled"] else "enabled") + "\\n}}")
elif cmd == "disable":
    s["disabled"] = True
elif cmd == "enable":
    s["disabled"] = False
elif cmd == "bootout":
    # Like launchd, bootout may return while the job is still exiting.
    s["linger"] = s.get("linger_prints", 0)
    if not s["linger"]:
        s["loaded"] = False; s["running"] = False
elif cmd == "bootstrap":
    if s["disabled"]:
        code = 5
    else:
        s["loaded"] = True; s["running"] = True
elif cmd == "kickstart":
    if s["loaded"]:
        s["running"] = True
    else:
        code = 3
elif cmd == "load":
    # Legacy `load -w` also clears the persistent disable flag.
    if "-w" in args:
        s["disabled"] = False
    if s["disabled"]:
        code = 5
    else:
        s["loaded"] = True; s["running"] = True
json.dump(s, open(state_path, "w"))
sys.exit(code)
""", encoding="utf-8")
        script.chmod(0o755)
        return script

    @unittest.skipUnless(sys.platform == "darwin", "LaunchAgent control is macOS-only")
    def test_stop_disables_then_boots_out_and_start_reenables_then_bootstraps(self) -> None:
        root = self.case.home / ".ai-usage"
        self.case.run("install", "--config", str(root / "config.json"), "--no-start")
        launcher = self.stateful_launchctl(loaded=True, running=True, disabled=False)
        environment = dict(self.case.environment(), **{SERVICE.LAUNCHCTL_OVERRIDE_ENV: str(launcher)})
        target = f"gui/{self.uid}/codes.porta.ai-usage"

        def run(command: str) -> dict[str, object]:
            result = subprocess.run([sys.executable, str(SCRIPT), command], env=environment,
                                    capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        files_before = sorted(str(p) for p in root.rglob("*"))
        stopped = run("stop")
        self.assertEqual((stopped["state"], stopped["disabled"]), ("stopped", True))
        calls = (self.case.root / "launchctl-calls.log").read_text(encoding="utf-8").splitlines()
        mutations = [c for c in calls if not c.startswith("print")]
        self.assertEqual(mutations, [f"disable {target}", f"bootout {target}"],
                         "stop must disable (stays stopped at login) before booting out")
        self.assertEqual(sorted(str(p) for p in root.rglob("*")), files_before, "stop must keep installation and data")

        (self.case.root / "launchctl-calls.log").unlink()
        started = run("start")
        self.assertEqual((started["state"], started["disabled"], started["pid"]), ("running", False, 4242))
        calls = (self.case.root / "launchctl-calls.log").read_text(encoding="utf-8").splitlines()
        mutations = [c for c in calls if not c.startswith("print")]
        plist = self.case.home / "Library" / "LaunchAgents" / "codes.porta.ai-usage.plist"
        self.assertEqual(mutations, [f"enable {target}", f"bootstrap gui/{self.uid} {plist}"])

    @unittest.skipUnless(sys.platform == "darwin", "LaunchAgent control is macOS-only")
    def test_stop_reports_stopped_only_after_a_slow_job_has_exited(self) -> None:
        root = self.case.home / ".ai-usage"
        self.case.run("install", "--config", str(root / "config.json"), "--no-start")
        launcher = self.stateful_launchctl(loaded=True, running=True, disabled=False, linger_prints=3)
        environment = dict(self.case.environment(), **{SERVICE.LAUNCHCTL_OVERRIDE_ENV: str(launcher)})
        result = subprocess.run([sys.executable, str(SCRIPT), "stop"], env=environment,
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        stopped = json.loads(result.stdout)
        self.assertEqual((stopped["state"], stopped["pid"], stopped["disabled"]), ("stopped", None, True),
                         "stop must not report a job that is still exiting as its final state")

    @unittest.skipUnless(sys.platform == "darwin", "LaunchAgent control is macOS-only")
    def test_reinstall_after_stop_clears_the_disable_flag_and_runs(self) -> None:
        root = self.case.home / ".ai-usage"
        config = root / "config.json"
        self.case.run("install", "--config", str(config), "--no-start")
        launcher = self.stateful_launchctl(loaded=True, running=True, disabled=False)
        environment = dict(self.case.environment(), **{SERVICE.LAUNCHCTL_OVERRIDE_ENV: str(launcher)})

        def run(*command: str) -> subprocess.CompletedProcess[str]:
            result = subprocess.run([sys.executable, str(SCRIPT), *command], env=environment,
                                    capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

        run("stop")
        run("install", "--config", str(config))
        status = json.loads(run("service-status").stdout)
        self.assertEqual((status["state"], status["disabled"]), ("running", False),
                         "reinstalling after Stop must leave the collector enabled and running")

    @unittest.skipUnless(sys.platform == "darwin", "LaunchAgent control is macOS-only")
    def test_start_without_installation_changes_nothing(self) -> None:
        launcher = self.stateful_launchctl(loaded=False, running=False, disabled=True)
        environment = dict(self.case.environment(), **{SERVICE.LAUNCHCTL_OVERRIDE_ENV: str(launcher)})
        result = subprocess.run([sys.executable, str(SCRIPT), "start"], env=environment,
                                capture_output=True, text=True, timeout=30, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not installed", result.stderr)
        self.assertFalse((self.case.root / "launchctl-calls.log").exists())


class PausedSchedulerLoopTests(unittest.TestCase):
    """Drive the real run_daemon loop with a virtual clock; no real waiting."""

    def setUp(self) -> None:
        self.case = IsolatedHome()
        self.addCleanup(self.case.close)

    def test_pause_after_an_established_deadline_waits_positively_and_never_collects(self) -> None:
        config = self.case.provider_config("codex", executable_names=["definitely-missing-codex"])
        config["providers"]["codex"]["enabled"] = False
        config.update(poll_on_start=True, poll_interval_seconds=3600)
        self.case.write_config(config)
        clock = {"now": 1000.0}
        waits: list[tuple[float, float]] = []  # (virtual time, requested wait)
        collections: list[float] = []
        real_collect = SERVICE.collect_snapshot
        case = self.case

        def counting_collect(*args, **kwargs):
            collections.append(clock["now"])
            return real_collect(*args, **kwargs)

        class VirtualEvent:
            def __init__(self) -> None:
                self.stopped = False

            def is_set(self) -> bool:
                return self.stopped

            def set(self) -> None:
                self.stopped = True

            def wait(self, seconds: float) -> bool:
                waits.append((clock["now"], seconds))
                if len(waits) == 1:
                    # The first collection set a deadline one interval ahead; pause now.
                    paused = json.loads(case.config.read_text(encoding="utf-8"))
                    paused["poll_paused"] = True
                    case.write_config(paused)
                clock["now"] += max(seconds, 0.0)
                # Run well past the original deadline (1000 + 3600), then stop.
                if clock["now"] > 1000.0 + 3 * 3600 or len(waits) > 2000:
                    self.stopped = True
                return self.stopped

        with mock.patch.object(SERVICE.threading, "Event", VirtualEvent), \
                mock.patch.object(SERVICE.time, "monotonic", lambda: clock["now"]), \
                mock.patch.object(SERVICE, "collect_snapshot", counting_collect), \
                mock.patch.object(SERVICE.signal, "signal"), \
                mock.patch.dict(os.environ, {SERVICE.SCHEDULE_TICK_ENV: "15"}):
            SERVICE.run_daemon(self.case.config)

        self.assertEqual(collections, [1000.0], "only the start-up check; no scheduled check while paused")
        self.assertLess(len(waits), 2000, "a zero-wait loop would never advance virtual time")
        past_deadline = [seconds for at, seconds in waits if at > 1000.0 + 3600]
        self.assertTrue(past_deadline, "the scenario must run beyond the expired deadline")
        self.assertTrue(all(0 < seconds <= 15 for _, seconds in waits[1:]), waits[:5])
        self.assertEqual(set(past_deadline), {15.0}, "paused waits use the bounded config-check tick")

    def test_wait_function_bounds(self) -> None:
        wait = SERVICE.schedule_wait_seconds
        self.assertEqual(wait("paused", next_due=10.0, now=500.0, tick=15.0), 15.0)
        self.assertEqual(wait("pause", next_due=10.0, now=500.0, tick=15.0), 15.0)
        self.assertEqual(wait("wait", next_due=505.0, now=500.0, tick=15.0), 5.0)
        self.assertEqual(wait("resume", next_due=4100.0, now=500.0, tick=15.0), 15.0)
        self.assertGreater(wait("wait", next_due=500.0, now=500.0, tick=15.0), 0)
        self.assertEqual(wait("wait", next_due=None, now=0.0, tick=0.1), 0.1)


class InstallerRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = IsolatedHome()
        self.addCleanup(self.case.close)

    def test_rollback_leaves_unchanged_files_untouched(self) -> None:
        target = self.case.root / "kept.txt"
        target.write_text("original\n", encoding="utf-8")
        target.chmod(0o600)
        inode = target.stat().st_ino
        changed = self.case.root / "changed.txt"
        changed.write_text("before\n", encoding="utf-8")
        tx = SERVICE.FilesystemTransaction(self.case.root)
        tx.snapshot(target)
        tx.snapshot(changed)
        changed.write_text("after\n", encoding="utf-8")
        tx.rollback()
        self.assertEqual(target.stat().st_ino, inode, "an unchanged file must not be rewritten")
        self.assertEqual(changed.read_text(encoding="utf-8"), "before\n")

    def test_incomplete_rollback_says_the_collector_was_not_restarted(self) -> None:
        self.case.write_config(self.case.provider_config("codex", executable_names=["definitely-missing-codex"]))
        harness = CollectorBlackBoxTests(methodName="test_missing_binaries_are_skipped")
        harness.case = self.case
        calls: list[str] = []

        def launchctl(arguments: list[str], check: bool = False):
            calls.append(arguments[0])
            failed = arguments[0] == "enable"
            if check and failed:
                raise RuntimeError("enable failed")
            return subprocess.CompletedProcess(["launchctl", *arguments], int(failed), "", "")

        with harness.patched_service_install_paths():
            SERVICE.install_service(self.case.config, no_start=True)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(SERVICE.sys, "platform", "darwin"))
                stack.enter_context(mock.patch.object(SERVICE, "find_launchctl", return_value=Path("/bin/true")))
                stack.enter_context(mock.patch.object(SERVICE, "run_launchctl", side_effect=launchctl))
                stack.enter_context(mock.patch.object(
                    SERVICE.FilesystemTransaction, "rollback",
                    side_effect=RuntimeError("rollback incomplete; recovery artifacts preserved at /x")))
                with self.assertRaisesRegex(RuntimeError, "has NOT been restarted"):
                    SERVICE.install_service(self.case.config, no_start=False)
        self.assertEqual(calls.count("bootstrap"), 0, "no restart after an incomplete rollback")

    def test_service_label_override_never_names_the_real_agent(self) -> None:
        for label, ok in (("codes.porta.ai-usage", False), ("other.label", False),
                          ("codes.porta.ai-usage.test.", False), ("codes.porta.ai-usage.test.ab12cd34", True)):
            with self.subTest(label=label):
                environment = dict(self.case.environment(), AI_USAGE_SERVICE_LABEL=label)
                result = subprocess.run([sys.executable, str(SCRIPT), "version"], env=environment,
                                        capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(result.returncode == 0, ok, result.stderr)
