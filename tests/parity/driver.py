#!/usr/bin/env python3
"""Behavior parity for the provider-plugin refactor (phase 1, AC-3).

Each scenario builds a sandbox (fake provider executables, provider data files
and a config), then runs a plan of public entry points through child.py with
wall-clock time frozen at FIXED_NOW. The outputs are normalized only as
NORMALIZE describes and compared with golden files captured from the merged
pre-refactor revision recorded in BASELINE.

    python3 tests/parity/driver.py capture --src DIR   # write golden/ from DIR (the baseline only)
    python3 tests/parity/driver.py compare [--src DIR]  # compare DIR (default: this checkout)

Golden files are never regenerated from refactored code: `capture` refuses a
source tree that is not the BASELINE revision's.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GOLDEN = HERE / "golden"
BASELINE_FILE = HERE / "BASELINE"
CHILD = HERE / "child.py"

FIXED_NOW = "2026-08-21T12:00:00Z"
FIXED_TS = float(calendar.timegm(time.strptime(FIXED_NOW, "%Y-%m-%dT%H:%M:%SZ")))
HOUR = 3600
DAY = 86400

# The only normalization. Everything else must match exactly.
#   sandbox: every occurrence of the sandbox root (both as given and as
#     resolved, e.g. /var/... vs /private/var/...) becomes "/sandbox"; the
#     source tree under test becomes "/src". Both are temporary directories.
#   csv_fields: per-row fields that are random by design.
#   state_keys: state keys whose values are filesystem identities of the
#     sandbox files (inode numbers), which differ on every run.
NORMALIZE: dict[str, Any] = {
    "sandbox": "/sandbox",
    "src": "/src",
    "csv_fields": ["transaction_id"],
    "state_keys": ["inode"],
}

TRIPWIRE = "#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> \"$HOME/../tripwire.log\"\nexit 79\n"


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.bin = root / "bin"
        self.data = root / "data"
        for directory in (self.home, self.bin, self.data):
            directory.mkdir(parents=True)
        self.config = root / "config.json"
        self.paths = {
            "usage_csv": str(self.data / "usage.csv"),
            "log_file": str(self.data / "collector.log"),
            "state_file": str(self.data / "state.json"),
            "cache_dir": str(self.data / "cache"),
        }

    def executable(self, name: str, body: str = TRIPWIRE) -> str:
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o700)
        return str(path)

    def python_executable(self, name: str, body: str) -> str:
        return self.executable(name, f"#!{sys.executable}\n{body}")

    def write(self, relative: str, text: str, mtime: Optional[float] = None) -> str:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return str(path)

    def missing(self, relative: str) -> str:
        return str(self.root / relative)

    def write_config(self, providers: dict[str, Any], **extra: Any) -> None:
        value = {"poll_interval_seconds": 3600, "paths": self.paths, "providers": providers, **extra}
        self.config.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def once(self, progress: bool = False) -> dict[str, Any]:
        return {"op": "once", "paths": self.paths, "progress": progress}


# ---- provider fixtures ------------------------------------------------------

CODEX_RPC = """import json, sys
for line in sys.stdin:
    message = json.loads(line)
    ident = message.get("id")
    if ident == 100:
        result = {"id": 100, "result": {}}
    elif ident == 101:
        result = {"id": 101, "result": {"summary": {"lifetimeTokens": 12345, "peakDailyTokens": 678}, "dailyUsageBuckets": [{"startDate": "2026-08-01", "tokens": 111}, {"startDate": "2026-08-20", "tokens": 222}]}}
    elif ident == 102:
        result = {"id": 102, "result": {"rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": 14, "windowDurationMins": 10080, "resetsAt": 1787400000}, "secondary": {"usedPercent": 3, "windowDurationMins": 300, "resetsAt": 1787320000}, "planType": "pro"}}}}
    else:
        continue
    print(json.dumps(result), flush=True)
"""

CODEX_FAILS = "#!/bin/sh\necho 'codex: not signed in' >&2\nexit 3\n"

GROK_ACP = """import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == 1:
        response = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}
    elif message.get("id") == 2:
        response = {"jsonrpc": "2.0", "id": 2, "result": {"config": {"creditUsagePercent": 33, "monthlyLimit": {"val": 10000}, "used": {"val": 3300}, "currentPeriod": {"type": "monthly", "start": "2026-08-01T00:00:00Z", "end": "2026-09-01T00:00:00Z"}}, "subscriptionTier": "supergrok"}}
    else:
        continue
    print(json.dumps(response), flush=True)
"""

GROK_HANGS = "import time\ntime.sleep(30)\n"


def claude_usage_fake(cache: str, refresh: bool) -> str:
    return (
        "import json\n"
        f"cache, refresh = {cache!r}, {refresh!r}\n"
        "if refresh:\n"
        "    document = json.load(open(cache))\n"
        f"    document['cachedUsageUtilization']['fetchedAtMs'] = {FIXED_TS * 1000!r}\n"
        "    json.dump(document, open(cache, 'w'))\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'usage'}))\n"
    )


def claude_usage_cache(fetched_at_ms: float) -> str:
    limits = [
        {"kind": "session", "percent": 8, "resets_at": "2026-08-21T14:10:00.157608+00:00"},
        {"kind": "weekly_all", "percent": 85, "resets_at": "2026-08-24T13:00:00+00:00"},
        {"kind": "weekly_scoped", "percent": 4, "resets_at": "2026-08-24T13:00:00+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}}},
        {"kind": "some_future_kind", "percent": 1},
    ]
    return json.dumps({
        "oauthAccount": {"accountUuid": "acct-1"},
        "cachedUsageUtilization": {"accountUuid": "acct-1", "fetchedAtMs": fetched_at_ms,
                                   "utilization": {"limits": limits}},
    })


def claude_line(message_id: str, session: str, model: str, timestamp: str, usage: dict[str, int]) -> str:
    return json.dumps({
        "type": "assistant",
        "timestamp": timestamp,
        "sessionId": session,
        "message": {"id": message_id, "model": model, "role": "assistant",
                    "content": [{"type": "text", "text": "PRIVATE_TEXT"}], "usage": usage},
    }) + "\n"


CLAUDE_STATS = json.dumps({
    "lastComputedDate": "2026-08-20",
    "totalSessions": 7,
    "totalMessages": 89,
    "modelUsage": {"claude-opus-5": {"inputTokens": 101, "outputTokens": 202,
                                     "cacheReadInputTokens": 303, "costUSD": 1.25}},
    "dailyModelTokens": [{"date": "2026-08-20", "tokensByModel": {"claude-opus-5": 404}}],
})


def gemini_event(input_tokens: int, output_tokens: int, model: str = "gemini-2.5-pro") -> str:
    return json.dumps({"name": "gemini_cli.api_response", "model": model,
                       "input_token_count": input_tokens, "output_token_count": output_tokens}) + "\n"


def grok_event(event_id: str, input_tokens: int, timestamp: str) -> str:
    return json.dumps({
        "method": "_x.ai/session/update",
        "timestamp": timestamp,
        "params": {
            "sessionId": "private-session",
            "_meta": {"eventId": event_id},
            "update": {"sessionUpdate": "turn_completed", "promptId": f"prompt-{event_id}",
                       "usage": {"inputTokens": input_tokens, "outputTokens": 25, "totalTokens": input_tokens + 25,
                                 "costUsdTicks": 12_300_000_000, "costIsPartial": False,
                                 "usageIsIncomplete": False}},
        },
    }) + "\n"


def grok_billing(ts: float) -> str:
    return json.dumps({
        "ts": ts,
        "msg": "billing: fetched credits config",
        "ctx": {"config": {"creditUsagePercent": 21, "monthlyLimit": {"val": 4500}, "used": {"val": 945},
                           "currentPeriod": {"type": "monthly", "start": "2026-08-01T00:00:00Z",
                                             "end": "2026-09-01T00:00:00Z"}},
                "subscriptionTier": "supergrok", "onDemandEnabled": True},
    }) + "\n"


def antigravity_payload(input_tokens: int, remaining_fraction: float) -> dict[str, Any]:
    return {
        "email": "private@example.test",
        "session_id": "secret-session",
        "transcript_path": "/private/transcript",
        "plan_tier": "pro",
        "model": {"id": "gemini-pro", "display_name": "Gemini Pro"},
        "context_window": {"total_input_tokens": input_tokens, "total_output_tokens": 20,
                           "context_window_size": 1000, "used_percentage": 42, "remaining_percentage": 58,
                           "current_usage": {"input_tokens": 33, "output_tokens": 4}},
        "quota": {"weekly": {"remaining_fraction": remaining_fraction, "reset_time": "2026-08-24T00:00:00Z",
                             "reset_in_seconds": 100}},
    }


# ---- scenarios --------------------------------------------------------------

def scenario_all_providers(box: Sandbox) -> list[dict[str, Any]]:
    """All five enabled with data; a second poll after new data (incremental state)."""
    projects = box.root / "claude-projects"
    transcript = box.write(
        "claude-projects/workspace/session-one.jsonl",
        claude_line("msg_a", "s-1", "claude-opus-5", "2026-08-20T12:00:00.000Z",
                    {"input_tokens": 3, "output_tokens": 11, "cache_read_input_tokens": 100,
                     "cache_creation_input_tokens": 7})
        + claude_line("msg_synthetic", "s-1", "<synthetic>", "2026-08-20T12:01:00.000Z",
                      {"input_tokens": 9, "output_tokens": 9}),
    )
    box.write("claude-projects/workspace/session-one/subagents/agent-a.jsonl",
              claude_line("msg_child", "s-1", "claude-opus-5-5", "2026-08-20T12:02:00.000Z",
                          {"input_tokens": 5, "output_tokens": 17}))
    stats = box.write("claude-stats.json", CLAUDE_STATS, mtime=FIXED_TS - HOUR)
    usage_cache = box.write("home/.claude.json", claude_usage_cache((FIXED_TS - 60) * 1000))
    claude = box.python_executable("claude", claude_usage_fake(usage_cache, refresh=True))

    cache_dir = box.data / "cache"
    agy_cache = str(cache_dir / "antigravity.json")
    agy_events = str(cache_dir / "antigravity-events.jsonl")

    telemetry = box.write("gemini/telemetry.log", gemini_event(10, 5) + "{not json\n")

    grok_home = box.root / "grok-home"
    session = box.write("grok-home/sessions/one/updates.jsonl", grok_event("event-1", 100, "2026-08-20T00:00:00Z"))
    box.write("grok-home/sessions/one/subagents/child/updates.jsonl", grok_event("event-child", 9999, "2026-08-20T00:00:00Z"))
    auth = box.write("grok-home/auth.json", "{}\n")
    billing = box.write("grok-home/logs/unified.jsonl", grok_billing(FIXED_TS - 600))

    box.write_config({
        "codex": {"executable": box.python_executable("codex", CODEX_RPC), "timeout_seconds": 10,
                  "monthly_subscription_usd": 20},
        "claude": {"executable": claude, "stats_file": stats, "projects_dir": str(projects),
                   "usage_cache_file": usage_cache, "monthly_subscription_usd": 100},
        "antigravity": {"executable": box.executable("agy"), "cache_file": agy_cache, "events_file": agy_events,
                        "settings_file": box.missing("home/.gemini/antigravity-cli/settings.json")},
        "gemini_cli": {"executable": box.executable("gemini"), "telemetry_file": telemetry,
                       "settings_file": box.missing("home/.gemini/settings.json")},
        "grok": {"executable": box.executable("grok"), "grok_home": str(grok_home), "auth_file": auth,
                 "billing_cache_file": billing, "monthly_subscription_usd": 30},
    })
    statusline = {"op": "statusline", "cache_file": agy_cache, "events_file": agy_events}
    return [
        {"op": "default_config"},
        {"op": "doctor_json"},
        {"op": "doctor_text"},
        {**statusline, "payload": antigravity_payload(400, 0.75)},
        box.once(progress=True),
        {"op": "append", "files": {
            transcript: claude_line("msg_b", "s-2", "claude-opus-5", "2026-08-21T09:00:00.000Z",
                                    {"input_tokens": 4, "output_tokens": 40}),
            telemetry: gemini_event(7, 3, "gemini-2.5-flash"),
            session: grok_event("event-2", 50, "2026-08-21T08:00:00Z"),
        }},
        {**statusline, "payload": antigravity_payload(450, 0.5)},
        box.once(),
        {"op": "doctor_json"},
        {"op": "report", "days": 30},
    ]


def scenario_disabled_and_missing(box: Sandbox) -> list[dict[str, Any]]:
    """Disabled providers leave one marker row; missing executables leave none."""
    box.write_config({
        "codex": {"executable_names": ["definitely-missing-codex"]},
        "claude": {"enabled": False},
        "antigravity": {"executable_names": ["definitely-missing-agy"],
                        "cache_file": box.missing("agy.json"), "events_file": box.missing("agy-events.jsonl"),
                        "settings_file": box.missing("agy-settings.json")},
        "gemini_cli": {"executable_names": ["definitely-missing-gemini"],
                       "telemetry_file": box.missing("gemini.log"), "settings_file": box.missing("gemini-settings.json")},
        "grok": {"enabled": False},
    })
    return [{"op": "doctor_json"}, {"op": "doctor_text"}, box.once(progress=True), {"op": "report", "days": 7}]


def scenario_all_missing(box: Sandbox) -> list[dict[str, Any]]:
    """Every provider enabled, nothing installed and no data anywhere."""
    box.write_config({
        "codex": {"executable_names": ["definitely-missing-codex"]},
        "claude": {"executable_names": ["definitely-missing-claude"], "stats_file": box.missing("claude.json"),
                   "projects_dir": box.missing("claude-projects"), "usage_cache_file": box.missing("home/.claude.json")},
        "antigravity": {"executable_names": ["definitely-missing-agy"], "cache_file": box.missing("agy.json"),
                        "events_file": box.missing("agy-events.jsonl"), "settings_file": box.missing("agy-settings.json")},
        "gemini_cli": {"executable_names": ["definitely-missing-gemini"], "telemetry_file": box.missing("gemini.log"),
                       "settings_file": box.missing("gemini-settings.json")},
        "grok": {"executable_names": ["definitely-missing-grok"], "grok_home": box.missing("grok"),
                 "auth_file": box.missing("grok/auth.json"), "billing_cache_file": box.missing("grok/unified.jsonl")},
    })
    return [{"op": "doctor_json"}, {"op": "doctor_text"}, box.once(), {"op": "report", "days": 7}]


def scenario_provider_errors(box: Sandbox) -> list[dict[str, Any]]:
    """Failing commands, a timeout, stale sources and an unrefreshed usage cache."""
    stats = box.write("claude-stats.json", CLAUDE_STATS, mtime=FIXED_TS - 10 * DAY)
    usage_cache = box.write("home/.claude.json", claude_usage_cache((FIXED_TS - 2 * HOUR) * 1000))
    claude = box.python_executable("claude", claude_usage_fake(usage_cache, refresh=False))
    agy_cache = box.write("data/cache/antigravity.json", json.dumps({
        "captured_at": iso(FIXED_TS - DAY), "plan_tier": "pro",
        "quota": {"weekly": {"remaining_fraction": 0.4, "reset_time": "2026-08-24T00:00:00Z"}},
    }), mtime=FIXED_TS - DAY)
    grok_home = box.root / "grok-home"
    auth = box.write("grok-home/auth.json", "{}\n")
    box.write_config({
        "codex": {"executable": box.executable("codex", CODEX_FAILS), "timeout_seconds": 5},
        "claude": {"executable": claude, "stats_file": stats, "projects_dir": box.missing("claude-projects"),
                   "usage_cache_file": usage_cache},
        "antigravity": {"executable": box.executable("agy"), "cache_file": agy_cache,
                        "events_file": box.missing("data/cache/antigravity-events.jsonl"),
                        "settings_file": box.missing("agy-settings.json")},
        "gemini_cli": {"executable": box.executable("gemini"),
                       "telemetry_file": box.write("gemini.log", "{broken\n"),
                       "settings_file": box.missing("gemini-settings.json")},
        "grok": {"executable": box.python_executable("grok", GROK_HANGS), "grok_home": str(grok_home),
                 "auth_file": auth, "billing_cache_file": box.missing("grok-home/logs/unified.jsonl"),
                 "timeout_seconds": 1},
    })
    return [
        {"op": "doctor_json"},
        # A payload that is not an object: logged, and the bare name is shown.
        {"op": "statusline", "payload": [1, 2], "cache_file": agy_cache,
         "events_file": box.missing("data/cache/antigravity-events.jsonl")},
        box.once(),
        {"op": "report", "days": 7},
    ]


def scenario_claude_usage_refresh(box: Sandbox) -> list[dict[str, Any]]:
    """A stale Claude usage cache refreshed by one /usage run."""
    usage_cache = box.write("home/.claude.json", claude_usage_cache((FIXED_TS - 2 * HOUR) * 1000))
    box.write_config({
        "codex": {"enabled": False},
        "claude": {"executable": box.python_executable("claude", claude_usage_fake(usage_cache, refresh=True)),
                   "stats_file": box.missing("claude.json"), "projects_dir": box.missing("claude-projects"),
                   "usage_cache_file": usage_cache},
        "antigravity": {"enabled": False},
        "gemini_cli": {"enabled": False},
        "grok": {"enabled": False},
    })
    return [box.once(), {"op": "report", "days": 7}]


def scenario_grok_acp_billing(box: Sandbox) -> list[dict[str, Any]]:
    """Grok billing read over ACP when no fresh billing log exists."""
    grok_home = box.root / "grok-home"
    box.write("grok-home/sessions/one/updates.jsonl", grok_event("event-1", 100, "2026-08-20T00:00:00Z"))
    auth = box.write("grok-home/auth.json", "{}\n")
    stale_billing = box.write("grok-home/logs/unified.jsonl", grok_billing(FIXED_TS - 5 * HOUR))
    box.write_config({
        "codex": {"enabled": False},
        "claude": {"enabled": False},
        "antigravity": {"enabled": False},
        "gemini_cli": {"enabled": False},
        "grok": {"executable": box.python_executable("grok", GROK_ACP), "grok_home": str(grok_home),
                 "auth_file": auth, "billing_cache_file": stale_billing, "timeout_seconds": 10},
    })
    return [{"op": "doctor_json"}, box.once(), {"op": "report", "days": 7}]


def scenario_symlinked_config(box: Sandbox) -> list[dict[str, Any]]:
    """Settings changes through a symlinked config keep the link and update the target."""
    providers: dict[str, Any] = {
        name: {"enabled": False} for name in ("codex", "claude", "antigravity", "gemini_cli", "grok")
    }
    # Enabled below by configure; never discover a real grok on this machine.
    providers["grok"].update(executable_names=["definitely-missing-grok"], grok_home=box.missing("grok"),
                             auth_file=box.missing("grok/auth.json"),
                             billing_cache_file=box.missing("grok/unified.jsonl"))
    box.write_config(providers)
    real = box.root / "real-config.json"
    box.config.rename(real)
    box.config.symlink_to(real)
    link = str(box.config)
    return [
        {"op": "configure", "path": link,
         "assignments": ["providers.codex.monthly_subscription_usd=25", "poll_paused=true"]},
        {"op": "configure", "path": link, "assignments": ["providers.grok.enabled=true"]},
        {"op": "configure", "path": link, "assignments": ["providers.unknown.enabled=true"]},
        {"op": "configure", "path": link, "assignments": ["providers.codex.executable=\"/bin/sh\""]},
        {"op": "configure", "path": link, "assignments": ["providers.claude.enabled=\"yes\""]},
        {"op": "configure", "path": link, "assignments": ["providers.claude.monthly_subscription_usd=-1"]},
        {"op": "load_config", "path": link},
        box.once(),
    ]


def scenario_config_errors(box: Sandbox) -> list[dict[str, Any]]:
    """Config validation errors name the same roles in the same order."""
    shared = box.missing("shared.json")
    box.write("collision.json", json.dumps({"paths": box.paths, "providers": {
        "antigravity": {"cache_file": shared}, "grok": {"auth_file": shared}}}))
    box.write("not-object.json", json.dumps({"paths": box.paths, "providers": {"gemini_cli": 5}}))
    box.write("empty-path.json", json.dumps({"paths": box.paths, "providers": {"claude": {"projects_dir": " "}}}))
    box.write("extra-provider.json", json.dumps({"paths": box.paths, "providers": {"someday": {"enabled": True}}}))
    box.write_config({})
    return [
        {"op": "load_config", "path": str(box.root / name)}
        for name in ("collision.json", "not-object.json", "empty-path.json", "extra-provider.json")
    ]


SCENARIOS: dict[str, Callable[[Sandbox], list[dict[str, Any]]]] = {
    "all_providers": scenario_all_providers,
    "disabled_and_missing": scenario_disabled_and_missing,
    "all_missing": scenario_all_missing,
    "provider_errors": scenario_provider_errors,
    "claude_usage_refresh": scenario_claude_usage_refresh,
    "grok_acp_billing": scenario_grok_acp_billing,
    "symlinked_config": scenario_symlinked_config,
    "config_errors": scenario_config_errors,
}


# ---- running and normalizing -----------------------------------------------

def replace_all(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [replace_all(item, replacements) for item in value]
    if isinstance(value, dict):
        return {replace_all(key, replacements): replace_all(item, replacements) for key, item in value.items()}
    return value


def normalize_state(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_state(item) for item in value]
    if isinstance(value, dict):
        return {key: ("<normalized>" if key in NORMALIZE["state_keys"] else normalize_state(item))
                for key, item in value.items()}
    return value


def normalize(document: dict[str, Any], root: Path, src: Path) -> dict[str, Any]:
    pairs = {str(root.resolve()): NORMALIZE["sandbox"], str(root): NORMALIZE["sandbox"],
             str(src.resolve()): NORMALIZE["src"], str(src): NORMALIZE["src"]}
    replacements = sorted(pairs.items(), key=lambda item: -len(item[0]))
    document = replace_all(document, replacements)
    for result in document["results"]:
        for row in result.get("csv") or []:
            for field in NORMALIZE["csv_fields"]:
                if row.get(field):
                    row[field] = "<normalized>"
        if "state" in result:
            result["state"] = normalize_state(result["state"])
    return document


def run_scenario(src: Path, name: str, handoff: Optional[tuple[Path, int]] = None) -> dict[str, Any]:
    """Run a scenario's plan with `src`. With `handoff` (other_src, index), the
    steps from `index` on run with other_src in the same sandbox: data written
    by one revision is read by the other."""
    root = Path(tempfile.mkdtemp(prefix="ai-usage-parity-"))
    try:
        box = Sandbox(root)
        steps = SCENARIOS[name](box)
        environment = {
            "HOME": str(box.home),
            "PATH": os.pathsep.join([str(box.bin), "/usr/bin", "/bin"]),
            "TZ": "UTC",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Lifecycle commands are not part of a plan; a set-but-empty override means "no launchctl".
            "AI_USAGE_LAUNCHCTL": "",
        }
        segments = [(src, steps)]
        if handoff is not None:
            segments = [(src, steps[: handoff[1]]), (handoff[0], steps[handoff[1]:])]
        document: dict[str, Any] = {"results": []}
        for segment_src, segment_steps in segments:
            plan = {"src": str(segment_src), "config": str(box.config), "now": FIXED_TS,
                    "now_iso": FIXED_NOW, "steps": segment_steps}
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(CHILD)], input=json.dumps(plan), text=True,
                capture_output=True, env=environment, timeout=120, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"parity child failed for {name} ({result.returncode}):\n{result.stderr}")
            document["results"].extend(json.loads(result.stdout)["results"])
        tripwire = root / "tripwire.log"
        document["tripwire"] = tripwire.read_text(encoding="utf-8") if tripwire.exists() else ""
        for segment_src, _ in segments[1:]:
            document = replace_all(document, [(str(segment_src.resolve()), NORMALIZE["src"]),
                                              (str(segment_src), NORMALIZE["src"])])
        return normalize(document, root, src)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def first_difference(expected: Any, actual: Any, path: str = "") -> Optional[str]:
    if type(expected) is not type(actual):
        return f"{path or '/'}: expected {expected!r}, got {actual!r}"
    if isinstance(expected, dict):
        for key in list(expected) + [key for key in actual if key not in expected]:
            if key not in actual:
                return f"{path}/{key}: missing (expected {expected[key]!r})"
            if key not in expected:
                return f"{path}/{key}: unexpected {actual[key]!r}"
            found = first_difference(expected[key], actual[key], f"{path}/{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        for index, (left, right) in enumerate(zip(expected, actual)):
            found = first_difference(left, right, f"{path}/{index}")
            if found:
                return found
        if len(expected) != len(actual):
            return f"{path}: expected {len(expected)} items, got {len(actual)}"
        return None
    if expected != actual:
        return f"{path or '/'}: expected {expected!r}, got {actual!r}"
    return None


def golden_path(name: str) -> Path:
    return GOLDEN / f"{name}.json"


def compare(src: Path, names: Optional[list[str]] = None) -> dict[str, Optional[str]]:
    differences: dict[str, Optional[str]] = {}
    for name in names or list(SCENARIOS):
        expected = json.loads(golden_path(name).read_text(encoding="utf-8"))
        differences[name] = first_difference(expected, run_scenario(src, name))
    return differences


def baseline_sha() -> str:
    return BASELINE_FILE.read_text(encoding="utf-8").split()[0]


def capture(src: Path) -> None:
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], text=True,
                          capture_output=True, check=False).stdout.strip()
    marker = src / ".parity-baseline"
    recorded = marker.read_text(encoding="utf-8").strip() if marker.is_file() else head
    if recorded != baseline_sha():
        raise SystemExit(f"capture refused: {src} is {recorded or 'unknown'}, BASELINE is {baseline_sha()}")
    GOLDEN.mkdir(exist_ok=True)
    for name in SCENARIOS:
        first, second = run_scenario(src, name), run_scenario(src, name)
        found = first_difference(first, second)
        if found:
            raise SystemExit(f"capture refused: {name} is not deterministic: {found}")
        golden_path(name).write_text(json.dumps(first, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"captured {name}")


def extract_baseline(destination: Path) -> Path:
    """The BASELINE revision's tree, from git (needs the commit in local history)."""
    sha = baseline_sha()
    archive = subprocess.run(["git", "-C", str(REPO), "archive", sha], capture_output=True, check=True).stdout
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive, check=True)
    (destination / ".parity-baseline").write_text(sha + "\n", encoding="utf-8")
    return destination


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture", help="write golden/ from the BASELINE revision")
    capture_parser.add_argument("--src", type=Path, help="baseline tree (default: extracted from git)")
    compare_parser = sub.add_parser("compare", help="compare a tree with golden/")
    compare_parser.add_argument("--src", type=Path, default=REPO)
    compare_parser.add_argument("scenario", nargs="*")
    arguments = parser.parse_args(argv)
    if arguments.command == "capture":
        if arguments.src is not None:
            capture(arguments.src.resolve())
        else:
            with tempfile.TemporaryDirectory(prefix="ai-usage-baseline-") as temporary:
                capture(extract_baseline(Path(temporary) / "src"))
        return 0
    failures = 0
    for name, difference in compare(arguments.src.resolve(), arguments.scenario or None).items():
        print(f"{name}: {'OK' if difference is None else 'DIFFERS: ' + difference}")
        failures += difference is not None
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
