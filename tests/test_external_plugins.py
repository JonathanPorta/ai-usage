"""Local plugins in ~/.ai-usage/plugins/<id>/ (phase 3, AC-1 to AC-10).

Every test uses an isolated HOME and data folder with synthetic plugins written
at test time (their shebang is this Python). None reads the owner's ~/.ai-usage,
a real plugin, a credential or a log.
"""

from __future__ import annotations

import csv
import datetime as datetime_module
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import Any, Callable, Optional
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ai_usage_service.py"
REPORT = REPO / "ai_usage_report.py"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_usage_plugins import external  # noqa: E402

PLIST_NAME = "codes.porta.ai-usage.plist"
BUILT_INS_OFF = {name: {"enabled": False} for name in ("codex", "claude", "antigravity", "gemini_cli", "grok")}
SECRET = "AIU-TEST-SECRET-" + hashlib.sha256(os.urandom(16)).hexdigest()[:16]

# The plugin side of the protocol, shared by the fixtures. A plugin reads its
# request, records what it was run with (argv, cwd, environment, request) into the
# file its settings name, and answers with the response its settings carry.
RECORDING_PLUGIN = """\
import json, os, sys
request = json.load(sys.stdin)
settings = request["settings"]
with open(settings["record"], "a") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ),
                             "request": request}) + "\\n")
response = json.loads(settings.get("response", '{"protocol": 1, "rows": []}'))
if settings.get("count_polls"):
    response["state"] = {"polls": request["state"].get("polls", 0) + 1}
sys.stdout.write(json.dumps(response))
"""

# Writes the bytes of a file its settings name to stdout, then exits with a status.
RAW_PLUGIN = """\
import json, sys
request = json.load(sys.stdin)
settings = request["settings"]
sys.stdout.buffer.write(open(settings["stdout_file"], "rb").read())
sys.stdout.flush()
sys.exit(int(settings.get("exit", 0)))
"""

VALID = json.dumps({"protocol": 1, "rows": []})


def local_today() -> datetime_module.date:
    return datetime_module.datetime.now().date()


def utc_now_text(offset: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset))


class Box:
    """An isolated HOME with ~/.ai-usage/plugins, a data folder and a config."""

    def __init__(self, test: unittest.TestCase) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ai-usage-plugins-")
        test.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()
        self.home = self.root / "home"
        self.plugins = self.home / ".ai-usage" / "plugins"
        self.plugins.mkdir(parents=True)
        for directory in (self.home / ".ai-usage", self.plugins):
            directory.chmod(0o700)
        self.data = self.root / "data"
        self.data.mkdir()
        self.records = self.root / "records"
        self.records.mkdir()
        self.config_path = self.root / "config.json"
        self.paths = {
            "usage_csv": str(self.data / "usage.csv"),
            "log_file": str(self.data / "collector.log"),
            "state_file": str(self.data / "state.json"),
            "cache_dir": str(self.data / "cache"),
        }
        self.installed("2.4.0")
        self.config()

    # ---- set-up

    def installed(self, version: str) -> Path:
        """A LaunchAgent whose collector reports `version` (read as text, never run)."""
        script = self.root / f"installed-{version}" / "collector.py"
        script.parent.mkdir(exist_ok=True)
        script.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        with (agents / PLIST_NAME).open("wb") as handle:
            plistlib.dump({"Label": "codes.porta.ai-usage", "ProgramArguments": [sys.executable, str(script)]},
                          handle)
        self.installed_script = script
        return script

    def plugin(self, name: str, body: str = RECORDING_PLUGIN, *, usage: str = "daily_total", quota: str = "used",
               timeout: int = 10, defaults: Optional[dict[str, Any]] = None,
               edit: Optional[Callable[[dict[str, Any]], None]] = None, executable: str = "collect",
               shebang: Optional[str] = None) -> Path:
        directory = self.plugins / name
        directory.mkdir()
        directory.chmod(0o700)
        sources = []
        if usage != "none":
            sources.append({"id": "usage", "role": "usage", "label": "Daily usage", "kind": "Synthetic API",
                            "csv_source": f"{name}-usage"})
        if quota != "none":
            sources.append({"id": "limits", "role": "quota", "label": "Limits", "kind": "Synthetic API",
                            "csv_source": f"{name}-limits"})
        manifest: dict[str, Any] = {
            "manifest_version": 1, "protocol": 1, "id": name, "executable": executable, "timeout_seconds": timeout,
            "default_settings": {"record": str(self.records / f"{name}.jsonl"), **(defaults or {})},
            "report": {"name": name.replace("-", " ").title(), "product": "Synthetic Tool", "usage": usage,
                       "quota": quota, "sources": sources},
        }
        if edit is not None:
            edit(manifest)
        self.write_manifest(name, manifest)
        path = directory / executable
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{shebang or sys.executable}\n{body}", encoding="utf-8")
        path.chmod(0o700)
        return directory

    def write_manifest(self, name: str, manifest: Any) -> None:
        path = self.plugins / name / "plugin.json"
        path.write_text(json.dumps(manifest) if not isinstance(manifest, str) else manifest, encoding="utf-8")
        path.chmod(0o600)

    def config(self, providers: Optional[dict[str, Any]] = None, **extra: Any) -> None:
        value = {"poll_interval_seconds": 3600, "paths": self.paths,
                 "providers": {**BUILT_INS_OFF, **(providers or {})}, **extra}
        self.config_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        self.config_path.chmod(0o600)

    def raw_config(self) -> dict[str, Any]:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def enable(self, name: str, **settings: Any) -> None:
        """Turn a plugin on by writing the config directly (the owner's own edit)."""
        config = self.raw_config()
        config["providers"][name] = {"enabled": True, **settings}
        self.config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def response(self, name: str, rows: list[dict[str, Any]], **extra: Any) -> str:
        return json.dumps({"protocol": 1, "rows": rows, **extra})

    # ---- running

    def environment(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        return {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                "AI_USAGE_LAUNCHCTL": "", **(extra or {})}

    def cli(self, *arguments: str, extra_env: Optional[dict[str, str]] = None,
            timeout: float = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), *arguments, "--config", str(self.config_path)],
            capture_output=True, text=True, env=self.environment(extra_env), timeout=timeout, check=False)

    def once(self, extra_env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
        return self.cli("once", extra_env=extra_env)

    def report(self, installed: Optional[Path] = None) -> dict[str, Any]:
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(REPORT), "--config", str(self.config_path), "--service", "skip",
             "--installed-collector", str(installed or self.installed_script)],
            capture_output=True, text=True, env=self.environment(), timeout=120, check=False)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)

    def report_text(self) -> str:
        return json.dumps(self.report())

    def provider(self, report: dict[str, Any], name: str) -> dict[str, Any]:
        return next(p for p in report["providers"] if p["id"] == name)

    def rows(self, provider: Optional[str] = None) -> list[dict[str, str]]:
        path = Path(self.paths["usage_csv"])
        if not path.exists():
            return []
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return [row for row in rows if provider is None or row["provider"] == provider]

    def last_poll(self, provider: Optional[str] = None) -> list[dict[str, str]]:
        rows = self.rows()
        if not rows:
            return []
        last = rows[-1]["transaction_id"]
        return [row for row in rows if row["transaction_id"] == last and (provider is None or row["provider"] == provider)]

    def state(self) -> dict[str, Any]:
        path = Path(self.paths["state_file"])
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def log(self) -> str:
        path = Path(self.paths["log_file"])
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def runs(self, name: str) -> list[dict[str, Any]]:
        path = self.records / f"{name}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def snapshot(self) -> dict[str, tuple[Any, ...]]:
        """Every path under plugins/: lstat identity, mode and contents (for 'repairs nothing')."""
        found: dict[str, tuple[Any, ...]] = {}
        for path in sorted([self.plugins, *self.plugins.rglob("*")]):
            info = os.lstat(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else ""
            target = os.readlink(path) if path.is_symlink() else ""
            found[str(path.relative_to(self.root))] = (info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns,
                                                       digest, target)
        return found


def failure_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row["category"] == "collection" and row["metric"] == "unexpected_error"]


def daily(value: float, date: Optional[str] = None, source: str = "example-usage") -> dict[str, Any]:
    return {"category": "usage", "metric": "daily_tokens", "period_start": date or local_today().isoformat(),
            "value": value, "source": source}


def quota_row(value: float, *, metric: str = "used_percent", scope: str = "weekly", source: str = "example-limits",
              window: int = 604800, resets: Optional[str] = None) -> dict[str, Any]:
    row: dict[str, Any] = {"category": "quota", "metric": metric, "scope": scope, "value": value, "source": source,
                           "window_seconds": window}
    if resets:
        row["resets_at"] = resets
    return row


# ---- AC-1 and AC-2: discovery, the manifest, owner-only permissions ---------------

class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Box(self)

    def discover(self, checker: Optional[external.Checker] = None) -> external.Discovery:
        return external.discover(set(BUILT_INS_OFF), root=self.box.plugins, checker=checker)

    def test_a_valid_plugin_is_listed_everywhere_with_its_own_id_and_type(self) -> None:
        self.box.plugin("example")
        doctor = json.loads(self.box.cli("doctor", "--json").stdout)
        self.assertEqual(doctor["example"]["type"], "example")
        self.assertIs(doctor["example"]["external"], True)
        self.assertIs(doctor["example"]["detected"], True)
        listed = json.loads(self.box.cli("plugin", "list").stdout)
        self.assertEqual([(p["id"], p["status"], p["enabled"]) for p in listed["plugins"]],
                         [("example", "valid", False)])
        provider = self.box.provider(self.box.report(), "example")
        self.assertEqual((provider["id"], provider["type"], provider["external"], provider["name"]),
                         ("example", "example", True, "Example"))
        self.assertEqual([s["id"] for s in provider["sources"]], ["usage", "limits"])

    def test_discovery_never_runs_a_plugin(self) -> None:
        canary = self.box.root / "canary"
        self.box.plugin("example", f"open({str(canary)!r}, 'w').write('ran')\n")
        for arguments in (("doctor",), ("doctor", "--json"), ("plugin", "list")):
            self.assertEqual(self.box.cli(*arguments).returncode, 0)
        self.box.report()
        self.assertFalse(canary.exists())

    def invalid_reason(self, name: str) -> str:
        found = self.discover()
        self.assertNotIn(name, found.plugins)
        return found.invalid[name]

    def test_each_invalid_manifest_is_refused_with_a_reason_naming_the_field(self) -> None:
        def top(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
            return lambda manifest: manifest.__setitem__(key, value)

        def report(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
            return lambda manifest: manifest["report"].__setitem__(key, value)

        def source(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
            return lambda manifest: manifest["report"]["sources"][0].__setitem__(key, value)

        def duplicate_csv(manifest: dict[str, Any]) -> None:
            manifest["report"]["sources"][1]["csv_source"] = manifest["report"]["sources"][0]["csv_source"]

        def no_quota_source(manifest: dict[str, Any]) -> None:
            manifest["report"]["sources"] = manifest["report"]["sources"][:1]

        cases: dict[str, tuple[Callable[[dict[str, Any]], None], str]] = {
            "unknown-top": (top("extra", 1), "has an unknown key"),
            "unknown-nested": (source("extra", 1), "has an unknown key"),
            "manifest-version": (top("manifest_version", 2), "manifest_version must be 1"),
            "protocol": (top("protocol", 2), "protocol must be 1"),
            "wrong-id": (top("id", "other"), "id does not match the folder name"),
            "outside": (top("executable", "../collect"), "relative path inside the plugin folder"),
            "absolute": (top("executable", "/bin/sh"), "relative path inside the plugin folder"),
            "timeout-low": (top("timeout_seconds", 0), "timeout_seconds must be an integer from 1 to 120"),
            "timeout-high": (top("timeout_seconds", 121), "timeout_seconds must be an integer from 1 to 120"),
            "usage-profile": (report("usage", "tokens"), "report.usage is not an allowed value"),
            "quota-profile": (report("quota", "percent"), "report.quota is not an allowed value"),
            "missing-source": (no_quota_source, "needs a quota source"),
            "duplicate-csv": (duplicate_csv, "csv_source must be a unique"),
            "default-enabled": (lambda m: m["default_settings"].__setitem__("enabled", True),
                                "default_settings may not set enabled"),
            "default-timeout": (lambda m: m["default_settings"].__setitem__("timeout_seconds", 5),
                                "default_settings may not set enabled"),
            "default-price": (lambda m: m["default_settings"].__setitem__("monthly_subscription_usd", 1),
                              "default_settings may not set enabled"),
        }
        for name, (edit, reason) in cases.items():
            self.box.plugin(name, edit=edit)
        found = self.discover()
        for name, (_edit, reason) in cases.items():
            with self.subTest(name):
                self.assertNotIn(name, found.plugins)
                self.assertIn(reason, found.invalid[name])

    def test_malformed_files_and_names_are_refused(self) -> None:
        self.box.plugin("bad-json")
        self.box.write_manifest("bad-json", '{"manifest_version": 1,')
        self.box.plugin("too-big")
        self.box.write_manifest("too-big", json.dumps({"pad": "x" * (70 * 1024)}))
        self.box.plugin("not-executable")
        (self.box.plugins / "not-executable" / "collect").chmod(0o600)
        self.box.plugin("missing-executable")
        (self.box.plugins / "missing-executable" / "collect").unlink()
        self.box.plugin("codex")  # a built-in's id
        (self.box.plugins / "Bad_Name").mkdir(mode=0o700)
        self.box.plugin("escape")
        os.symlink("/bin/sh", self.box.plugins / "escape" / "linked")
        self.box.write_manifest("escape", {**json.loads((self.box.plugins / "escape" / "plugin.json").read_text()),
                                           "executable": "linked"})
        found = self.discover()
        self.assertIn("is not valid JSON at line 1 column", found.invalid["bad-json"])
        self.assertIn("is larger than 64 KiB", found.invalid["too-big"])
        self.assertIn("is not executable (it needs 0700)", found.invalid["not-executable"])
        self.assertIn("plugins/missing-executable/collect is missing", found.invalid["missing-executable"])
        self.assertIn("a built-in provider's or an added account's", found.invalid["codex"])
        self.assertIn("not a valid plugin id", found.invalid["Bad_Name"])
        # /bin/sh is root's, so the symlink target fails the owner check before the location check.
        self.assertIn("plugins/escape/linked (its symlink target)", found.invalid["escape"])

    def test_an_executable_linked_outside_the_folder_is_refused(self) -> None:
        outside = self.box.root / "outside"
        outside.mkdir(mode=0o700)
        target = outside / "collect"
        target.write_text(f"#!{sys.executable}\n", encoding="utf-8")
        target.chmod(0o700)
        self.box.plugin("escape")
        os.symlink(target, self.box.plugins / "escape" / "linked")
        manifest = json.loads((self.box.plugins / "escape" / "plugin.json").read_text())
        self.box.write_manifest("escape", {**manifest, "executable": "linked"})
        self.assertIn("resolves outside the plugin folder", self.invalid_reason("escape"))

    def test_an_added_accounts_id_is_refused(self) -> None:
        self.box.plugin("claude-az")
        found = external.discover({*BUILT_INS_OFF, "claude-az"}, root=self.box.plugins)
        self.assertIn("a built-in provider's or an added account's", found.invalid["claude-az"])

    def test_group_or_other_permissions_are_refused_and_never_repaired(self) -> None:
        cases = {
            "dir-0750": ("", 0o750), "dir-0705": ("", 0o705), "dir-0755": ("", 0o755),
            "manifest-0640": ("plugin.json", 0o640), "manifest-0604": ("plugin.json", 0o604),
            "manifest-0755": ("plugin.json", 0o755),
            "exe-0750": ("collect", 0o750), "exe-0705": ("collect", 0o705), "exe-0755": ("collect", 0o755),
        }
        for name, (relative, mode) in cases.items():
            directory = self.box.plugin(name)
            (directory / relative if relative else directory).chmod(mode)
        before = self.box.snapshot()
        found = self.discover()
        self.box.config({name: {"enabled": True} for name in cases})
        self.box.once()
        self.box.cli("doctor")
        self.box.cli("plugin", "list")
        self.assertEqual(self.box.snapshot(), before)  # nothing chmod-ed, rewritten or moved
        for name, (relative, _mode) in cases.items():
            with self.subTest(name):
                reason = found.invalid[name]
                label = f"plugins/{name}" + (f"/{relative}" if relative else "")
                needed = {"": "0700", "plugin.json": "0600", "collect": "0700"}[relative]
                self.assertEqual(reason, f"{label} must be owner-only ({needed}); it has group or other permissions")

    def test_the_plugins_folder_itself_must_be_owner_only(self) -> None:
        self.box.plugin("example")
        self.box.plugins.chmod(0o755)
        found = self.discover()
        self.assertEqual(found.plugins, {})
        self.assertIn("plugins must be owner-only (0700)", found.invalid[""])
        self.box.config({"example": {"enabled": True}})
        self.box.once()
        self.assertIn("plugin is invalid: plugins must be owner-only (0700)",
                      failure_rows(self.box.rows("example"))[0]["message"])

    def test_symlinks_pass_only_when_the_link_and_its_target_are_owner_only(self) -> None:
        elsewhere = self.box.root / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        real = self.box.plugin("real")
        shutil.move(str(real), str(elsewhere / "linked"))
        manifest = json.loads((elsewhere / "linked" / "plugin.json").read_text())
        manifest["id"] = "linked"
        (elsewhere / "linked" / "plugin.json").write_text(json.dumps(manifest))
        os.symlink(elsewhere / "linked", self.box.plugins / "linked")
        self.assertIn("linked", self.discover().plugins)
        (elsewhere / "linked").chmod(0o750)
        self.assertEqual(self.discover().invalid["linked"],
                         "plugins/linked (its symlink target) must be owner-only (0700); "
                         "it has group or other permissions")
        (elsewhere / "linked").chmod(0o700)
        loose = elsewhere / "loose.json"
        loose.write_text(json.dumps(manifest))
        loose.chmod(0o644)
        (elsewhere / "linked" / "plugin.json").unlink()
        os.symlink(loose, elsewhere / "linked" / "plugin.json")
        self.assertIn("plugins/linked/plugin.json (its symlink target) must be owner-only (0600)",
                      self.discover().invalid["linked"])

    def test_a_folder_others_can_write_above_a_linked_plugin_is_refused_and_a_swap_never_runs(self) -> None:
        # Review F1: a symlinked plugin under a non-sticky 0777 folder could be swapped for
        # another after discovery. Discovery refuses it, so neither copy ever runs.
        shared = self.box.root / "shared"
        shared.mkdir()
        shared.chmod(0o777)
        canary = self.box.root / "ran"
        self.box.plugin("linked", f"open({str(canary)!r}, 'a').write('original\\n')\n")
        shutil.move(str(self.box.plugins / "linked"), str(shared / "real"))
        os.symlink(shared / "real", self.box.plugins / "linked")
        found = self.discover()
        self.assertEqual(found.invalid["linked"],
                         f"plugins/linked/plugin.json: {shared} is writable by other users")
        replacement = self.box.root / "replacement"
        shutil.copytree(shared / "real", replacement)
        (replacement / "collect").write_text(f"#!{sys.executable}\nopen({str(canary)!r}, 'a').write('swapped\\n')\n")
        os.rename(shared / "real", self.box.root / "old")
        os.rename(replacement, shared / "real")
        self.box.enable("linked")
        self.box.once()
        self.assertFalse(canary.exists(), "neither the checked file nor its replacement may run")
        self.assertIn("is writable by other users", failure_rows(self.box.rows("linked"))[0]["message"])
        shared.chmod(0o1777)  # sticky, as /tmp: others can't move what they don't own
        self.assertIn("linked", self.discover().plugins)

    def test_a_file_vanishing_during_discovery_fails_only_that_plugin(self) -> None:
        # Review F2: an executable removed between its checks is that plugin's problem only.
        self.box.plugin("alpha", defaults={"response": json.dumps({"protocol": 1, "rows": [daily(1, source="alpha-usage")]})})
        self.box.plugin("middle")
        self.box.plugin("zulu", defaults={"response": json.dumps({"protocol": 1, "rows": [daily(2, source="zulu-usage")]})})
        self.box.config({"alpha": {"enabled": True}, "middle": {"enabled": True}, "zulu": {"enabled": True}})
        real_stat = os.stat
        target = self.box.plugins / "middle" / "collect"

        def vanishing(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
            # The owner check (lstat) passed; by the executable-bit check it is gone.
            if Path(path) == target:
                raise FileNotFoundError(2, "No such file or directory")
            return real_stat(path, *args, **kwargs)

        with mock.patch.dict(os.environ, {"HOME": str(self.box.home)}):
            import ai_usage_service as service

            config = service.load_config(self.box.config_path)
            with mock.patch.object(external.os, "stat", vanishing):
                rows = service.collect_snapshot(config, include_history=False, state={})
        by_provider = {row["provider"]: row for row in rows if row["category"] in ("usage", "collection")}
        self.assertEqual(by_provider["alpha"]["value"], 1)
        self.assertEqual(by_provider["zulu"]["value"], 2)
        self.assertEqual(by_provider["middle"]["message"],
                         "plugin is invalid: plugins/middle changed while it was checked (ENOENT)")

    def test_a_foreign_owner_is_refused_on_the_link_and_on_the_target(self) -> None:
        directory = self.box.plugin("example")
        link = self.box.plugins / "linked"
        os.symlink(directory, link)
        other = os.getuid() + 1

        def as_other(target: Path, real: Callable[[Any], os.stat_result]) -> Callable[[Path], os.stat_result]:
            def stat_function(path: Path) -> os.stat_result:
                result = real(path)
                if Path(path) == target:
                    values = list(result)
                    values[4] = other  # st_uid
                    return os.stat_result(values)
                return result
            return stat_function

        checker = external.Checker(lstat=as_other(link, os.lstat))
        manifest = directory / "plugin.json"
        self.assertEqual(self.discover(checker).invalid["linked"], "plugins/linked is a symlink owned by another user")
        checker = external.Checker(stat_=as_other(link, os.stat))
        self.assertEqual(self.discover(checker).invalid["linked"],
                         "plugins/linked (its symlink target) is owned by another user")
        checker = external.Checker(lstat=as_other(manifest, os.lstat))
        self.assertEqual(self.discover(checker).invalid["example"],
                         "plugins/example/plugin.json is owned by another user")

    def test_reasons_never_echo_a_manifest_value(self) -> None:
        self.box.plugin("secret-usage", edit=lambda m: m["report"].__setitem__("usage", SECRET))
        self.box.plugin("secret-key", edit=lambda m: m["report"].__setitem__(SECRET, 1))
        self.box.plugin("secret-exe", edit=lambda m: m.__setitem__("executable", f"../{SECRET}"))
        self.box.config({"secret-usage": {"enabled": True}, "secret-key": {"enabled": True},
                         "secret-exe": {"enabled": True}})
        self.box.once()
        outputs = [self.box.cli("doctor").stdout, self.box.cli("doctor", "--json").stdout,
                   self.box.cli("plugin", "list").stdout, self.box.report_text(), self.box.log(),
                   Path(self.box.paths["usage_csv"]).read_text(encoding="utf-8"),
                   json.dumps(self.box.state())]
        for text in outputs:
            self.assertNotIn(SECRET, text)
        self.assertIn("report.usage is not an allowed value", outputs[2])


# ---- AC-3: enablement on every path ------------------------------------------------

class EnablementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Box(self)

    def test_an_unconfigured_plugin_writes_nothing_and_is_reported_not_turned_on(self) -> None:
        self.box.plugin("example")
        self.assertEqual(self.box.once().returncode, 0)
        self.assertEqual(self.box.rows("example"), [])
        self.assertEqual(self.box.runs("example"), [])
        provider = self.box.provider(self.box.report(), "example")
        self.assertEqual((provider["enabled"], provider["setup"]), (False, "disabled"))
        self.assertIn("Not turned on", provider["setup_note"])
        self.assertIn("plugin enable example", provider["setup_note"])

    def test_plugin_enable_writes_the_config_under_the_lock_and_the_next_poll_collects_it(self) -> None:
        self.box.plugin("example")
        self.box.config(extra_key=1)
        self.box.config_path.chmod(0o640)
        result = self.box.cli("plugin", "enable", "example")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.box.raw_config()
        self.assertEqual(config["providers"]["example"], {"enabled": True})
        self.assertEqual(config["extra_key"], 1)
        self.assertEqual(self.box.config_path.stat().st_mode & 0o777, 0o640)
        self.box.once()
        self.assertEqual(len(self.box.runs("example")), 1)

    def test_enabling_is_refused_on_every_path_and_changes_nothing(self) -> None:
        self.box.plugin("example")
        self.box.plugin("broken", edit=lambda m: m.__setitem__("protocol", 9))
        self.box.config({"gone": {"enabled": False}})
        before = self.box.config_path.read_bytes()
        cases = {
            "unknown": ("nothing", "no local plugin with id 'nothing'", "is not a setting the menu app may change"),
            "invalid": ("broken", "can't be turned on: plugin is invalid", "can't be turned on: plugin is invalid"),
            "missing": ("gone", "can't be turned on: plugin folder not found",
                        "can't be turned on: plugin folder not found"),
        }
        for label, (name, cli_reason, configure_reason) in cases.items():
            with self.subTest(label):
                result = self.box.cli("plugin", "enable", name)
                self.assertEqual(result.returncode, 1)
                self.assertIn(cli_reason, result.stderr)
                result = self.box.cli("configure", "--set", f"providers.{name}.enabled=true")
                self.assertEqual(result.returncode, 1)
                self.assertIn(configure_reason, result.stderr)
                self.assertEqual(self.box.config_path.read_bytes(), before)
        self.box.installed("2.3.0")
        for arguments in (("plugin", "enable", "example"), ("configure", "--set", "providers.example.enabled=true")):
            with self.subTest(arguments):
                result = self.box.cli(*arguments)
                self.assertEqual(result.returncode, 1)
                self.assertIn("local plugins need the installed collector 2.4.0 or newer (installed: 2.3.0)",
                              result.stderr)
                self.assertEqual(self.box.config_path.read_bytes(), before)

    def test_disabling_works_for_valid_invalid_and_missing_plugins_with_an_older_collector(self) -> None:
        self.box.plugin("valid")
        self.box.plugin("broken", edit=lambda m: m.__setitem__("protocol", 9))
        self.box.config({"valid": {"enabled": True}, "broken": {"enabled": True}, "gone": {"enabled": True}})
        self.box.installed("2.3.0")
        self.assertEqual(self.box.cli("plugin", "disable", "valid").returncode, 0)
        self.assertEqual(self.box.cli("configure", "--set", "providers.broken.enabled=false").returncode, 0)
        result = self.box.cli("plugin", "disable", "gone")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("valid", "broken", "gone"):
            self.assertIs(self.box.raw_config()["providers"][name]["enabled"], False)
        self.box.once()
        self.box.once()
        for name in ("valid", "broken", "gone"):
            with self.subTest(name):
                rows = self.box.last_poll(name)
                self.assertEqual([(r["category"], r["metric"], r["status"]) for r in rows],
                                 [("availability", "enabled", "disabled")])
        self.assertEqual(self.box.runs("valid"), [])

    def test_each_row_of_the_rows_table(self) -> None:
        self.box.plugin("unconfigured")
        self.box.plugin("off-valid")
        self.box.plugin("off-invalid", edit=lambda m: m.__setitem__("protocol", 9))
        self.box.plugin("on-valid", defaults={"response": VALID})
        self.box.plugin("on-invalid", edit=lambda m: m.__setitem__("protocol", 9))
        self.box.config({
            "off-valid": {"monthly_subscription_usd": 5}, "off-invalid": {"enabled": False},
            "off-missing": {"enabled": False}, "on-valid": {"enabled": True}, "on-invalid": {"enabled": True},
            "on-missing": {"enabled": True},
        })
        self.box.once()
        poll = self.box.last_poll()
        by_provider: dict[str, list[tuple[str, str, str]]] = {}
        for row in poll:
            by_provider.setdefault(row["provider"], []).append((row["category"], row["metric"], row["status"]))
        disabled = [("availability", "enabled", "disabled")]
        self.assertNotIn("unconfigured", by_provider)
        self.assertEqual(by_provider["off-valid"], disabled)
        self.assertEqual(by_provider["off-invalid"], disabled)
        self.assertEqual(by_provider["off-missing"], disabled)
        self.assertEqual(by_provider["on-valid"], [("availability", "detected", "ok")])
        self.assertEqual(by_provider["on-invalid"], [("collection", "unexpected_error", "error")])
        self.assertEqual(by_provider["on-missing"], [("collection", "unexpected_error", "error")])
        messages = {row["provider"]: row["message"] for row in failure_rows(poll)}
        self.assertEqual(messages["on-invalid"], "plugin is invalid: plugins/on-invalid/plugin.json: protocol must be 1")
        self.assertEqual(messages["on-missing"], "plugin folder not found")

    def test_disable_then_enable_resumes_from_the_saved_state(self) -> None:
        self.box.plugin("counter", defaults={"count_polls": True})
        self.box.enable("counter")
        self.box.once()
        self.assertEqual(self.box.state()["instances"]["counter"], {"polls": 1})
        self.assertEqual(self.box.cli("plugin", "disable", "counter").returncode, 0)
        self.box.once()
        self.assertEqual(self.box.state()["instances"]["counter"], {"polls": 1})
        self.assertEqual(self.box.cli("plugin", "enable", "counter").returncode, 0)
        self.box.once()
        self.assertEqual(self.box.state()["instances"]["counter"], {"polls": 2})
        self.assertEqual([run["request"]["state"] for run in self.box.runs("counter")], [{}, {"polls": 1}])

    def test_configure_accepts_only_the_three_keys_and_never_raises_the_timeout(self) -> None:
        self.box.plugin("example", timeout=10)
        ok = self.box.cli("configure", "--set", "providers.example.timeout_seconds=4",
                          "--set", "providers.example.monthly_subscription_usd=12")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.box.raw_config()["providers"]["example"],
                         {"timeout_seconds": 4, "monthly_subscription_usd": 12})
        before = self.box.config_path.read_bytes()
        too_long = self.box.cli("configure", "--set", "providers.example.timeout_seconds=11")
        self.assertIn("can't exceed the plugin's own 10", too_long.stderr)
        other = self.box.cli("configure", "--set", "providers.example.region=\"eu\"")
        self.assertIn("is not a setting the menu app may change", other.stderr)
        self.assertEqual(self.box.config_path.read_bytes(), before)

    def test_external_providers_come_after_built_ins_and_added_accounts_in_id_order(self) -> None:
        for name in ("zeta", "alpha"):
            self.box.plugin(name, defaults={"response": VALID})
        claude_home = self.box.home / "claude-az"
        claude_home.mkdir()
        self.box.config({"zeta": {"enabled": True}, "alpha": {"enabled": True}}, instances=[
            {"id": "claude-az", "plugin": "claude", "label": "Claude (az)",
             "settings": {"config_dir": str(claude_home), "enabled": False}}])
        result = self.box.cli("once", "--progress")
        self.assertEqual(result.returncode, 0, result.stderr)
        doctor = [line.split()[0] for line in self.box.cli("doctor").stdout.splitlines()[1:]]
        self.assertEqual(doctor[-3:], ["claude-az", "alpha", "zeta"])
        providers = [p["id"] for p in self.box.report()["providers"]]
        self.assertEqual(providers[-3:], ["claude-az", "alpha", "zeta"])
        order = list(dict.fromkeys(row["provider"] for row in self.box.last_poll()))
        self.assertEqual(order[-3:], ["claude-az", "alpha", "zeta"])
        progress = [json.loads(line)["provider"] for line in result.stdout.splitlines() if line.startswith("{")]
        self.assertEqual(progress, ["alpha", "zeta"])


# ---- AC-4: protocol, collection and the reported values -----------------------------

class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Box(self)

    def test_the_plugin_gets_exactly_the_documented_request_and_environment(self) -> None:
        self.box.plugin("example", defaults={"region": "eu"})
        self.box.enable("example", monthly_subscription_usd=20)
        result = self.box.once(extra_env={"CLAUDE_CONFIG_DIR": "/elsewhere", "AIU_DUMMY_SECRET": SECRET,
                                          "LANG": "en_US.UTF-8"})
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.box.runs("example")[0]
        self.assertEqual(run["argv"], ["collect"])
        self.assertEqual(Path(run["cwd"]).resolve(), (self.box.plugins / "example").resolve())
        # macOS adds __CF_USER_TEXT_ENCODING to every process at start-up; the collector doesn't pass it.
        passed = sorted(key for key in run["env"] if key != "__CF_USER_TEXT_ENCODING")
        self.assertEqual(passed, ["AI_USAGE_PLUGIN_DIR", "AI_USAGE_PROTOCOL", "HOME", "LANG", "PATH"])
        self.assertEqual(run["env"]["AI_USAGE_PROTOCOL"], "1")
        self.assertEqual(run["env"]["HOME"], str(self.box.home))
        self.assertNotIn("/usr/bin:/bin", run["env"]["PATH"].split(":")[:1])  # not the collector's own PATH
        self.assertNotIn(SECRET, json.dumps(run))
        request = run["request"]
        self.assertEqual(sorted(request), ["collected_at", "collector_version", "include_history", "protocol",
                                           "provider", "settings", "state"])
        self.assertEqual((request["protocol"], request["collector_version"], request["provider"]),
                         (1, "2.4.0", "example"))
        self.assertEqual(request["settings"], {"record": str(self.box.records / "example.jsonl"), "region": "eu",
                                               "timeout_seconds": 10})
        self.assertEqual(request["state"], {})

    def test_rows_are_stamped_and_state_round_trips(self) -> None:
        today = local_today().isoformat()
        response = self.box.response("example", [daily(12345), quota_row(42.5)], state={"cursor": "abc"})
        self.box.plugin("example", defaults={"response": response})
        self.box.enable("example")
        self.box.once()
        self.box.once()
        rows = [row for row in self.box.last_poll("example") if row["category"] in ("usage", "quota")]
        transaction = self.box.last_poll()[0]["transaction_id"]
        usage, quota = rows
        self.assertEqual({k: usage[k] for k in ("provider", "category", "metric", "record_kind", "scope",
                                                 "period_start", "value", "unit", "source", "status")},
                         {"provider": "example", "category": "usage", "metric": "daily_tokens",
                          "record_kind": "period_total", "scope": today, "period_start": today, "value": "12345",
                          "unit": "tokens", "source": "example-usage", "status": "ok"})
        self.assertEqual((quota["record_kind"], quota["unit"], quota["value"], quota["window_seconds"]),
                         ("snapshot", "percent", "42.5", "604800"))
        self.assertTrue(all(row["transaction_id"] == transaction for row in rows))
        self.assertEqual(self.box.state()["instances"]["example"], {"cursor": "abc"})
        self.assertEqual(self.box.runs("example")[1]["request"]["state"], {"cursor": "abc"})

    def test_daily_totals_and_a_used_percent_window_reach_the_report(self) -> None:
        today = local_today()
        yesterday = (today - datetime_module.timedelta(days=1)).isoformat()
        resets = utc_now_text(3 * 86400)
        response = self.box.response("example", [daily(12345), daily(9000, yesterday),
                                                 quota_row(42.5, resets=resets)])
        self.box.plugin("example", defaults={"response": response})
        self.box.enable("example")
        self.box.once()
        provider = self.box.provider(self.box.report(), "example")
        self.assertEqual(provider["today"]["total"], 12345)
        days = {day["date"]: day for day in provider["days"]}
        self.assertEqual((days[today.isoformat()]["total"], days[today.isoformat()]["state"]), (12345, "partial"))
        self.assertEqual(days[yesterday]["total"], 9000)
        window = provider["quota"]["windows"][0]
        self.assertEqual((window["label"], window["basis"], window["percent"], window["resets_at"], window["status"]),
                         ("Weekly limit", "used", 42.5, resets, "current"))
        self.assertIsNone(window["limit"])
        # A later poll replaces today's total instead of adding to it, and 95% used is low.
        config = self.box.raw_config()
        config["providers"]["example"]["response"] = self.box.response("example", [daily(13000), quota_row(95)])
        self.box.config_path.write_text(json.dumps(config), encoding="utf-8")
        self.box.once()
        provider = self.box.provider(self.box.report(), "example")
        self.assertEqual(provider["today"]["total"], 13000)
        self.assertEqual(provider["quota"]["windows"][0]["limit"], "low")

    def test_events_with_a_checkpoint_and_a_remaining_percent_window_reach_the_report(self) -> None:
        body = textwrap.dedent("""\
            import json, sys, time
            request = json.load(sys.stdin)
            polls = request["state"].get("polls", 0)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
            batches = [(100, 50), (10, 5)]
            rows = []
            if polls < len(batches):
                tokens_in, tokens_out = batches[polls]
                rows += [{"category": "usage", "metric": "input_tokens", "at": now, "value": tokens_in,
                          "source": "events-usage", "scope": f"e{polls}:synthetic-model"},
                         {"category": "usage", "metric": "output_tokens", "at": now, "value": tokens_out,
                          "source": "events-usage", "scope": f"e{polls}:synthetic-model"}]
            rows.append({"category": "quota", "metric": "remaining_percent", "scope": "daily", "value": 30,
                         "window_seconds": 86400, "source": "events-limits"})
            print(json.dumps({"protocol": 1, "rows": rows, "state": {"polls": polls + 1}}))
            """)
        self.box.plugin("events", body, usage="events", quota="remaining")
        self.box.enable("events")
        for _ in range(3):
            self.assertEqual(self.box.once().returncode, 0)
        provider = self.box.provider(self.box.report(), "events")
        self.assertEqual((provider["today"]["input"], provider["today"]["output"]), (110, 55))
        self.assertEqual(provider["models_today"], [{"name": "synthetic-model", "tokens": 165}])
        window = provider["quota"]["windows"][0]
        self.assertEqual((window["basis"], window["percent"], window["label"]), ("remaining", 30, "24-hour limit"))

    def test_a_percent_of_the_other_basis_is_refused_and_never_shown(self) -> None:
        self.box.plugin("used", defaults={"response": json.dumps(
            {"protocol": 1, "rows": [quota_row(30, metric="remaining_percent", source="used-limits")]})})
        self.box.plugin("left", quota="remaining", defaults={"response": json.dumps(
            {"protocol": 1, "rows": [quota_row(30, metric="used_percent", source="left-limits")]})})
        self.box.config({"used": {"enabled": True}, "left": {"enabled": True}})
        self.box.once()
        for name in ("used", "left"):
            with self.subTest(name):
                self.assertEqual(failure_rows(self.box.rows(name))[0]["message"],
                                 "returned an invalid response at rows[0].metric")
                self.assertEqual(self.box.provider(self.box.report(), name)["quota"]["windows"], [])

    def test_a_plugins_own_source_error_fails_only_that_source(self) -> None:
        response = self.box.response("example", [
            daily(5), {"category": "collection", "metric": "plugin_error", "source": "example-limits",
                       "message": "sign in required (401)"}])
        self.box.plugin("example", defaults={"response": response})
        self.box.enable("example")
        self.box.once()
        rows = [r for r in self.box.last_poll("example") if r["category"] == "collection"]
        self.assertEqual([(r["metric"], r["status"], r["source"]) for r in rows],
                         [("limits_error", "error", "example-limits")])
        sources = {s["id"]: s["status"] for s in self.box.provider(self.box.report(), "example")["sources"]}
        self.assertEqual(sources, {"usage": "ok", "limits": "auth"})

    def test_a_monthly_price_is_recorded_as_for_a_built_in(self) -> None:
        self.box.plugin("example", defaults={"response": VALID})
        self.box.enable("example", monthly_subscription_usd=20)
        self.box.once()
        rates = [r for r in self.box.rows("example") if r["category"] == "cost"]
        self.assertTrue(rates)
        self.assertEqual(self.box.provider(self.box.report(), "example")["costs"]["subscription"]["monthly_usd"], 20)


# ---- AC-5 to AC-8: bounds, cleanup, malformed responses -----------------------------

def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_gone(pid: int, seconds: float = 1.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.02)
    return not process_alive(pid)


# A probe run in its own Python, so its peak memory is the runner's alone.
MEMORY_PROBE = """\
import json, resource, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ai_usage_plugins import external
plugin = external.discover(set(), root=Path(sys.argv[2])).plugins[sys.argv[3]]
info = external.RunInfo()
try:
    external.run_plugin(plugin, b"{}", int(sys.argv[4]), info)
    reason = None
except external.PluginFailure as error:
    reason = str(error)
print(json.dumps({"reason": reason, "stderr_bytes": info.stderr_bytes, "stdout_peak": info.stdout_peak,
                  "largest_read": info.largest_read,
                  "maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
"""


class BoundsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Box(self)

    def poll(self, name: str, body: str, timeout: int = 2, **plugin: Any) -> tuple[float, list[dict[str, str]]]:
        self.box.plugin(name, body, timeout=timeout, **plugin)
        self.box.enable(name)
        started = time.monotonic()
        self.box.once()
        return time.monotonic() - started, self.box.last_poll(name)

    def logged_duration(self, name: str) -> float:
        line = next(line for line in self.box.log().splitlines() if f"external plugin {name} " in line)
        return float(line.split("duration=")[1].split("s")[0])

    def probe(self, name: str, timeout: int) -> dict[str, Any]:
        result = subprocess.run([sys.executable, "-I", "-B", "-c", MEMORY_PROBE, str(REPO), str(self.box.plugins),
                                 name, str(timeout)], capture_output=True, text=True, timeout=60,
                                env=self.box.environment(), check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_hung_plugin_is_killed_at_the_deadline_and_the_others_are_unaffected(self) -> None:
        self.box.plugin("alpha", defaults={"response": self.box.response("alpha", [daily(1, source="alpha-usage")])})
        self.box.plugin("zulu", defaults={"response": self.box.response("zulu", [daily(2, source="zulu-usage")])})
        self.box.config({"alpha": {"enabled": True}, "zulu": {"enabled": True}})
        self.box.once()
        without = {name: [(r["metric"], r["value"]) for r in self.box.last_poll(name)] for name in ("alpha", "zulu")}
        elapsed, rows = self.poll("middle", "import time\ntime.sleep(600)\n")
        self.assertEqual([(r["metric"], r["message"]) for r in rows], [("unexpected_error", "timed out after 2s")])
        self.assertLessEqual(self.logged_duration("middle"), 2 + 3)
        self.assertLess(elapsed, 2 + 3 + 3)  # plus the collector's own start-up
        with_hang = {name: [(r["metric"], r["value"]) for r in self.box.last_poll(name)] for name in ("alpha", "zulu")}
        self.assertEqual(with_hang, without)

    def test_closing_stdout_without_exiting_still_hits_the_deadline(self) -> None:
        body = "import os, sys, time\nos.close(1)\nos.close(2)\ntime.sleep(600)\n"
        _elapsed, rows = self.poll("closer", body)
        self.assertEqual(rows[0]["message"], "timed out after 2s")
        self.assertLessEqual(self.logged_duration("closer"), 2 + 3)

    def test_endless_stderr_hits_the_deadline_with_bounded_memory(self) -> None:
        body = "import sys\nchunk = 'x' * 65536\nwhile True:\n    sys.stderr.write(chunk)\n"
        _elapsed, rows = self.poll("flood", body)
        self.assertEqual(rows[0]["message"], "timed out after 2s")
        self.assertLessEqual(self.logged_duration("flood"), 2 + 3)
        probe = self.probe("flood", 2)
        self.assertEqual(probe["reason"], "timed out after 2s")
        self.assertGreater(probe["stderr_bytes"], 16 * external.READ_CHUNK)  # it really flooded
        self.assertLessEqual(probe["largest_read"], external.READ_CHUNK)
        self.assertLess(probe["maxrss"], 96 * 1024 * 1024)  # bytes on macOS (KiB on Linux: smaller still)

    def test_a_configured_timeout_lowers_the_deadline_and_cannot_raise_it(self) -> None:
        self.box.plugin("slow", "import time\ntime.sleep(600)\n", timeout=3)
        self.box.enable("slow", timeout_seconds=1)
        self.box.once()
        self.assertEqual(failure_rows(self.box.rows("slow"))[-1]["message"], "timed out after 1s")
        self.box.enable("slow", timeout_seconds=100)
        self.box.once()
        self.assertEqual(failure_rows(self.box.rows("slow"))[-1]["message"], "timed out after 3s")

    def test_stdout_is_capped_at_one_mebibyte(self) -> None:
        body = "import sys\nchunk = 'x' * 65536\nfor _ in range(80):\n    sys.stdout.write(chunk)\n"
        _elapsed, rows = self.poll("chatty", body, timeout=20)
        self.assertEqual(rows[0]["message"], "wrote more than 1 MiB to stdout")
        probe = self.probe("chatty", 20)
        self.assertEqual(probe["reason"], "wrote more than 1 MiB to stdout")
        self.assertLessEqual(probe["stdout_peak"], external.STDOUT_MAX_BYTES + external.READ_CHUNK)

    def test_a_mebibyte_of_stderr_with_a_valid_response_succeeds_and_only_its_size_is_logged(self) -> None:
        response = self.box.response("noisy", [daily(7, source="noisy-usage")])
        body = textwrap.dedent(f"""\
            import json, sys
            json.load(sys.stdin)
            sys.stderr.write({SECRET!r} + "y" * (1048576 - {len(SECRET)}))
            sys.stderr.flush()
            sys.stdout.write({response!r})
            """)
        _elapsed, rows = self.poll("noisy", body, timeout=20)
        self.assertEqual([(r["metric"], r["value"]) for r in rows if r["category"] == "usage"], [("daily_tokens", "7")])
        self.assertIn("external plugin noisy collected rows=2 exit=status 0", self.box.log())
        self.assertIn("stderr_bytes=1048576", self.box.log())

    def test_a_secret_is_never_echoed_from_stderr_a_rejected_row_or_a_start_failure(self) -> None:
        stderr_failure = f"import sys\nsys.stderr.write({SECRET!r})\nsys.exit(3)\n"
        rejected_row = textwrap.dedent(f"""\
            import json, sys
            json.load(sys.stdin)
            print(json.dumps({{"protocol": 1, "rows": [{{"category": "usage", "metric": "daily_tokens",
                "period_start": {SECRET!r}, "value": 1, "source": "row-usage"}}]}}))
            """)
        self.box.plugin("stderr", stderr_failure)
        self.box.plugin("row", rejected_row)
        self.box.plugin("start", "", shebang=f"/nonexistent/{SECRET}")
        self.box.plugin("value", RAW_PLUGIN)
        raw = self.box.root / "raw.json"
        raw.write_text(json.dumps({"protocol": 1, "rows": [], SECRET: 1}), encoding="utf-8")
        self.box.config({"stderr": {"enabled": True}, "row": {"enabled": True}, "start": {"enabled": True},
                         "value": {"enabled": True, "stdout_file": str(raw)}})
        self.box.once()
        messages = {row["provider"]: row["message"] for row in failure_rows(self.box.rows())}
        self.assertEqual(messages, {
            "stderr": "exited with status 3",
            "row": "returned an invalid response at rows[0].period_start",
            "start": "could not be started (ENOENT)",
            "value": "returned an invalid response at the top level (unknown key)",
        })
        for text in (self.box.log(), Path(self.box.paths["usage_csv"]).read_text(encoding="utf-8"),
                     json.dumps(self.box.state()), self.box.report_text(), self.box.cli("doctor").stdout,
                     self.box.cli("doctor", "--json").stdout, self.box.cli("plugin", "list").stdout):
            self.assertNotIn(SECRET, text)

    def test_a_valid_message_and_state_are_stored_as_documented(self) -> None:
        response = self.box.response("example", [
            {"category": "collection", "metric": "plugin_error", "source": "example-usage",
             "message": "the plugin's own words"}], state={"kept": True})
        self.box.plugin("example", defaults={"response": response})
        self.box.enable("example")
        self.box.once()
        self.assertIn("the plugin's own words", [r["message"] for r in self.box.rows("example")])
        self.assertEqual(self.box.state()["instances"]["example"], {"kept": True})

    def test_too_many_rows_or_too_much_state_is_refused(self) -> None:
        rows = [daily(1, source="many-usage")] * 2001
        self.box.plugin("many")
        self.box.plugin("big")
        self.box.config({
            "many": {"enabled": True, "response": json.dumps({"protocol": 1, "rows": rows})},
            "big": {"enabled": True, "response": json.dumps({"protocol": 1, "rows": [],
                                                             "state": {"pad": "x" * (256 * 1024)}})},
        })
        self.box.once()
        self.assertEqual(failure_rows(self.box.rows("many"))[0]["message"],
                         "returned an invalid response at rows (more than 2000)")
        self.assertEqual(failure_rows(self.box.rows("big"))[0]["message"],
                         "returned an invalid response at state (larger than 256 KiB)")

    def test_a_background_child_in_the_group_is_stopped_and_the_response_kept(self) -> None:
        pid_file = self.box.root / "child.pid"
        response = self.box.response("forker", [daily(3, source="forker-usage")])
        body = textwrap.dedent(f"""\
            import json, subprocess, sys
            json.load(sys.stdin)
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            open({str(pid_file)!r}, "w").write(str(child.pid))
            sys.stdout.write({response!r})
            """)
        _elapsed, rows = self.poll("forker", body, timeout=10)
        self.assertEqual([r["value"] for r in rows if r["category"] == "usage"], ["3"])
        self.assertTrue(wait_gone(int(pid_file.read_text())))

    def test_a_child_holding_the_inherited_pipes_cannot_hold_the_poll(self) -> None:
        pid_file = self.box.root / "holder.pid"
        response = self.box.response("holder", [daily(4, source="holder-usage")])
        body = textwrap.dedent(f"""\
            import json, os, subprocess, sys
            json.load(sys.stdin)
            sys.stdout.write({response!r})
            sys.stdout.flush()
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # inherits 1 and 2
            open({str(pid_file)!r}, "w").write(str(child.pid))
            os._exit(0)
            """)
        _elapsed, rows = self.poll("holder", body, timeout=30)
        self.assertEqual([r["value"] for r in rows if r["category"] == "usage"], ["4"])
        self.assertLessEqual(self.logged_duration("holder"), 1 + 3 + 1)
        self.assertTrue(wait_gone(int(pid_file.read_text())))

    def test_children_are_stopped_after_a_timeout_and_after_an_output_kill(self) -> None:
        for name, tail, timeout in (("late", "import time\ntime.sleep(600)\n", 2),
                                    ("loud", "sys.stdout.write('x' * (2 * 1048576))\nimport time\ntime.sleep(600)\n", 10)):
            with self.subTest(name):
                pid_file = self.box.root / f"{name}.pid"
                body = textwrap.dedent(f"""\
                    import subprocess, sys
                    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    open({str(pid_file)!r}, "w").write(str(child.pid))
                    """) + tail
                self.poll(name, body, timeout=timeout)
                self.assertTrue(wait_gone(int(pid_file.read_text())))


class MalformedResponseTests(unittest.TestCase):
    """Each malformed response is one failure row; nothing of it is kept."""

    def setUp(self) -> None:
        self.box = Box(self)

    def test_each_malformed_response(self) -> None:
        far = (local_today() + datetime_module.timedelta(days=3)).isoformat()

        def response(*rows: dict[str, Any], **extra: Any) -> bytes:
            return json.dumps({"protocol": 1, "rows": list(rows), **extra}).encode("utf-8")

        good = daily(1, source="case-usage")
        cases: dict[str, tuple[bytes, int, str]] = {
            "empty": (b"", 0, "returned no output"),
            "not-json": (b"hello", 0, "returned invalid JSON at line 1 column 1"),
            "two-objects": (b'{"protocol": 1, "rows": []} {}', 0, "returned more than one JSON value"),
            "array": (b"[]", 0, "returned an invalid response at the top level (not an object)"),
            "trailing": (b'{"protocol": 1, "rows": []} x', 0, "returned more than one JSON value"),
            "nan": (b'{"protocol": 1, "rows": [], "state": {"x": NaN}}', 0, "returned invalid JSON"),
            "protocol": (json.dumps({"protocol": 2, "rows": []}).encode(), 0,
                         "returned an invalid response at protocol"),
            "rows-object": (json.dumps({"protocol": 1, "rows": {}}).encode(), 0,
                            "returned an invalid response at rows"),
            "unknown-key": (response({**good, "extra": 1}), 0, "returned an invalid response at rows[0] (unknown key)"),
            "provider-key": (response({**good, "provider": "codex"}), 0,
                             "returned an invalid response at rows[0] (unknown key)"),
            "stamped-kind": (response({**good, "record_kind": "event_total"}), 0,
                             "returned an invalid response at rows[0] (unknown key)"),
            "undeclared-source": (response({**good, "source": "other"}), 0,
                                  "returned an invalid response at rows[0].source"),
            "wrong-role-source": (response({**good, "source": "case-limits"}), 0,
                                  "returned an invalid response at rows[0].source"),
            "metric": (response({**good, "metric": "tokens"}), 0, "returned an invalid response at rows[0].metric"),
            "status": (response({"category": "collection", "metric": "plugin_error", "source": "case-usage",
                                 "status": "disabled"}), 0, "returned an invalid response at rows[0] (unknown key)"),
            "long-message": (response({"category": "collection", "metric": "plugin_error", "source": "case-usage",
                                       "message": "x" * 501}), 0, "returned an invalid response at rows[0].message"),
            "control-message": (response({"category": "collection", "metric": "plugin_error", "source": "case-usage",
                                          "message": "a\u0007b"}), 0,
                                "returned an invalid response at rows[0].message"),
            "negative": (response({**good, "value": -1}), 0, "returned an invalid response at rows[0].value"),
            "object-value": (response({**good, "value": {"a": 1}}), 0, "returned an invalid response at rows[0].value"),
            "bad-date": (response({**good, "period_start": "yesterday"}), 0,
                         "returned an invalid response at rows[0].period_start"),
            "far-date": (response({**good, "period_start": far}), 0,
                         "returned an invalid response at rows[0].period_start (too far ahead)"),
            "state-array": (response(good, state=[]), 0, "returned an invalid response at state"),
            "non-zero-exit": (response(good), 4, "exited with status 4"),
            "category": (response({**good, "category": "billing"}), 0,
                         "returned an invalid response at rows[0].category"),
            "quota-range": (response(quota_row(101, source="case-limits")), 0,
                            "returned an invalid response at rows[0].value"),
            "not-utf8": (b"\xff\xfe", 0, "returned output that is not UTF-8"),
        }
        providers: dict[str, Any] = {}
        for name, (data, status, _reason) in cases.items():
            raw = self.box.root / f"{name}.out"
            raw.write_bytes(data)
            self.box.plugin(name, RAW_PLUGIN, edit=lambda m, n=name: [
                source.__setitem__("csv_source", source["csv_source"].replace(n, "case"))
                for source in m["report"]["sources"]])
            providers[name] = {"enabled": True, "stdout_file": str(raw), "exit": status}
        self.box.config(providers)
        self.box.once()
        for name, (_data, _status, reason) in cases.items():
            with self.subTest(name):
                rows = self.box.last_poll(name)
                self.assertEqual([(r["category"], r["metric"], r["source"], r["message"]) for r in rows],
                                 [("collection", "unexpected_error", "plugin", reason)])

    def test_events_without_a_time_or_from_the_future_are_refused(self) -> None:
        def event(**changes: Any) -> dict[str, Any]:
            row = {"category": "usage", "metric": "input_tokens", "at": utc_now_text(-60), "value": 1,
                   "source": "case-usage"}
            row.update(changes)
            return {key: value for key, value in row.items() if value is not None}

        cases = {
            "no-at": (event(at=None), "returned an invalid response at rows[0].at"),
            "future": (event(at=utc_now_text(3600)), "returned an invalid response at rows[0].at (in the future)"),
            "offset": (event(at="2026-01-01T00:00:00+01:00"), "returned an invalid response at rows[0].at"),
        }
        providers: dict[str, Any] = {}
        for name, (row, _reason) in cases.items():
            raw = self.box.root / f"{name}.out"
            raw.write_text(json.dumps({"protocol": 1, "rows": [row]}), encoding="utf-8")
            self.box.plugin(name, RAW_PLUGIN, usage="events", edit=lambda m, n=name: [
                source.__setitem__("csv_source", source["csv_source"].replace(n, "case"))
                for source in m["report"]["sources"]])
            providers[name] = {"enabled": True, "stdout_file": str(raw)}
        self.box.config(providers)
        self.box.once()
        for name, (_row, reason) in cases.items():
            with self.subTest(name):
                self.assertEqual(failure_rows(self.box.rows(name))[0]["message"], reason)

    def test_a_failure_keeps_the_checkpoint_and_drops_every_row(self) -> None:
        body = textwrap.dedent("""\
            import json, sys
            request = json.load(sys.stdin)
            polls = request["state"].get("polls", 0)
            rows = [{"category": "usage", "metric": "daily_tokens", "period_start": "2026-01-01", "value": 1,
                     "source": "stateful-usage"}]
            if polls >= 1:
                rows.append({"category": "usage", "metric": "nonsense", "period_start": "2026-01-01", "value": 1,
                             "source": "stateful-usage"})
            print(json.dumps({"protocol": 1, "rows": rows, "state": {"polls": polls + 1}}))
            """)
        self.box.plugin("stateful", body)
        self.box.enable("stateful")
        self.box.once()
        self.box.once()
        self.assertEqual(self.box.state()["instances"]["stateful"], {"polls": 1})
        last = self.box.last_poll("stateful")
        self.assertEqual([(r["metric"], r["message"]) for r in last],
                         [("unexpected_error", "returned an invalid response at rows[1].metric")])

    def test_a_signal_death_is_its_own_failure(self) -> None:
        self.box.plugin("killed", "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        self.box.enable("killed")
        self.box.once()
        self.assertEqual(failure_rows(self.box.rows("killed"))[0]["message"], "was stopped by signal 9")


# ---- AC-9: lifecycle ----------------------------------------------------------------

class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Box(self)

    def test_a_removed_folder_is_an_error_and_restoring_it_resumes(self) -> None:
        self.box.plugin("example", defaults={"count_polls": True})
        self.box.enable("example")
        self.box.once()
        moved = self.box.root / "moved"
        shutil.move(str(self.box.plugins / "example"), str(moved))
        self.box.once()
        self.assertEqual(failure_rows(self.box.last_poll("example"))[0]["message"], "plugin folder not found")
        self.assertEqual(self.box.state()["instances"]["example"], {"polls": 1})
        self.assertIs(self.box.raw_config()["providers"]["example"]["enabled"], True)
        self.assertTrue(self.box.rows("example"))
        shutil.move(str(moved), str(self.box.plugins / "example"))
        self.box.once()
        self.assertEqual(self.box.state()["instances"]["example"], {"polls": 2})

    def test_an_upgraded_executable_keeps_the_state(self) -> None:
        self.box.plugin("example", defaults={"count_polls": True})
        self.box.enable("example")
        self.box.once()
        executable = self.box.plugins / "example" / "collect"
        executable.write_text(executable.read_text(encoding="utf-8") + "# upgraded\n", encoding="utf-8")
        self.box.once()
        self.assertEqual(self.box.state()["instances"]["example"], {"polls": 2})

    def test_the_app_bundle_check_refuses_a_bundled_plugins_folder(self) -> None:
        app = self.box.root / "AI Usage.app"
        collector = app / "Contents" / "Resources" / "collector"
        (collector / "plugins" / "example").mkdir(parents=True)
        (collector / "plugins" / "example" / "plugin.json").write_text("{}", encoding="utf-8")
        result = subprocess.run([sys.executable, "-I", "-B", str(REPO / "scripts" / "check_app_bundle.py"), str(app)],
                                capture_output=True, text=True, env=self.box.environment(), timeout=60, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("a local plugins folder is bundled", result.stderr)

    def test_a_new_plugin_is_picked_up_by_a_running_daemon_on_its_next_poll(self) -> None:
        self.box.config(poll_interval_seconds=60)
        daemon = subprocess.Popen(
            [sys.executable, "-I", "-B", str(SCRIPT), "daemon", "--config", str(self.box.config_path)],
            env=self.box.environment({"AI_USAGE_SCHEDULE_TICK_SECONDS": "0.2"}),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: daemon.poll() is None and daemon.kill())
        deadline = time.monotonic() + 30
        while "collection completed" not in self.box.log() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertIn("collection completed", self.box.log())
        self.box.plugin("example", defaults={"response": json.dumps({"protocol": 1, "rows": [daily(1)]})})
        self.assertEqual(self.box.cli("plugin", "enable", "example").returncode, 0)
        deadline = time.monotonic() + 90
        while not self.box.rows("example") and time.monotonic() < deadline:
            time.sleep(0.5)
        daemon.send_signal(signal.SIGTERM)
        daemon.wait(timeout=30)
        self.assertEqual([r["value"] for r in self.box.rows("example") if r["category"] == "usage"], ["1"])
        self.assertEqual(self.box.log().count("collector daemon started"), 1)  # never restarted


# ---- AC-11: an older installed collector ------------------------------------------

class VersionTests(unittest.TestCase):
    def test_an_older_installed_collector_reports_plugins_as_not_configured(self) -> None:
        box = Box(self)
        box.plugin("example")
        box.config({"example": {"enabled": True}})
        older = box.installed("2.3.0")
        report = box.report(installed=older)
        provider = box.provider(report, "example")
        self.assertEqual(provider["setup"], "not_configured")
        self.assertIn("predates local plugins", provider["setup_note"])
        self.assertIs(report["collector"]["capabilities"]["external_plugins"], False)
        self.assertIs(box.report(installed=box.installed("2.4.0"))["collector"]["capabilities"]["external_plugins"],
                      True)


class TrustStatementTests(unittest.TestCase):
    """AC-12: the "not a security sandbox" statement, word for word, wherever plugins are explained."""

    STATEMENT = ("None of this is a security sandbox. A plugin is not confined by file-system, network or "
                 "process restrictions. It can read files, open connections, spawn processes that escape its "
                 "process group (for example with `setsid`), or change `~/.ai-usage` directly. Install only "
                 "plugins you wrote or trust as much as the collector.")

    @staticmethod
    def plain(text: str) -> str:
        return " ".join(text.replace("**", "").replace("\n> ", "\n").split())

    def test_the_docs_and_the_plugin_help_say_it_word_for_word(self) -> None:
        help_text = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "plugin", "--help"],
                                   capture_output=True, text=True, timeout=60, check=True).stdout
        sources = {"docs/PLUGINS.md": (REPO / "docs" / "PLUGINS.md").read_text(encoding="utf-8"),
                   "docs/CLI.md": (REPO / "docs" / "CLI.md").read_text(encoding="utf-8"),
                   "plugin --help": help_text}
        for name, text in sources.items():
            with self.subTest(name):
                self.assertIn(self.STATEMENT, self.plain(text))


class InProcessDiscoveryTests(unittest.TestCase):
    """The collector scans on every poll: a folder added between polls is collected
    by the next one in the same process (what the daemon does each cycle)."""

    def test_collect_snapshot_rescans_every_poll(self) -> None:
        box = Box(self)
        with mock.patch.dict(os.environ, {"HOME": str(box.home)}):
            import ai_usage_service as service

            config = service.load_config(box.config_path)
            state: dict[str, Any] = {}
            first = service.collect_snapshot(config, include_history=False, state=state)
            self.assertFalse([row for row in first if row["provider"] == "example"])
            box.plugin("example", defaults={"response": json.dumps({"protocol": 1, "rows": [daily(9)]})})
            box.enable("example")
            config = service.load_config(box.config_path)
            second = service.collect_snapshot(config, include_history=False, state=state)
        self.assertEqual([row["value"] for row in second if row["provider"] == "example"
                          and row["category"] == "usage"], [9])


if __name__ == "__main__":
    unittest.main()
