#!/usr/bin/env python3
"""Real-launchd lifecycle check with a disposable LaunchAgent (macOS only).

Explicitly invoked (`make launchd-integration-check`); ordinary test runs use
fakes. Exercises the collector's own install/start/stop/service-status code
against the real per-user launchd domain, using:

- a unique label `codes.porta.ai-usage.test.<random>` (the collector refuses
  any other override, so the real `codes.porta.ai-usage` can't be addressed);
- a temporary HOME holding the plist, collector copy, config, and data;
- a harmless process: the collector daemon with every provider off and no
  start-up poll, so it only idles.

Cleanup boots out and re-enables only the disposable label and deletes only
its temporary directory. launchd keeps a per-label enable/disable record in
its override database that launchctl offers no way to delete; for the
disposable label it is left as "enabled".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ai_usage_service.py"
REAL_LABEL = "codes.porta.ai-usage"
LAUNCHCTL = "/bin/launchctl"


class CheckFailed(AssertionError):
    pass


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, check=False)


def real_collector_pid() -> str:
    result = launchctl("print", f"gui/{os.getuid()}/{REAL_LABEL}")
    for line in result.stdout.splitlines():
        if line.strip().startswith("pid ="):
            return line.strip()
    return "not loaded" if result.returncode else "no pid"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)
    print(f"  ✓ {message}")


def main() -> int:
    if sys.platform != "darwin":
        print("launchd-integration-check: macOS only; skipped")
        return 0
    domain = f"gui/{os.getuid()}"
    if launchctl("print", domain).returncode != 0:
        print(f"launchd-integration-check: no launchd GUI session ({domain}) available here; "
              "run it from a logged-in desktop session")
        return 2
    # One fixed disposable label, so repeated runs reuse a single launchd
    # override entry instead of accumulating one per run.
    label = f"{REAL_LABEL}.test.integration"
    target = f"{domain}/{label}"
    launchctl("bootout", target)  # leftovers from an interrupted earlier run
    home = Path(tempfile.mkdtemp(prefix="ai-usage-launchd-check-"))
    root = home / ".ai-usage"
    config_path = root / "config.json"
    plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
    environment = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "AI_USAGE_SERVICE_LABEL": label,
        "PYTHONDONTWRITEBYTECODE": "1",
    }  # deliberately no AI_USAGE_LAUNCHCTL: this check uses the real launchctl
    real_before = real_collector_pid()
    outcome = 1
    print(f"launchd-integration-check: disposable agent {label}")
    print(f"  temp HOME {home}")

    def run(*command: str, ok: bool = True, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(script), *command], env=environment,
                                capture_output=True, text=True, timeout=120, check=False)
        if ok and result.returncode != 0:
            raise CheckFailed(f"{' '.join(command)} failed ({result.returncode}): {result.stderr.strip()}")
        return result

    def status() -> dict[str, object]:
        return json.loads(run("service-status").stdout)

    def wait_running(timeout: float = 15.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        current = status()
        while time.monotonic() < deadline and current["state"] != "running":
            time.sleep(0.2)
            current = status()
        return current

    def process_alive(pid: object) -> bool:
        return isinstance(pid, int) and subprocess.run(["/bin/ps", "-p", str(pid)], capture_output=True).returncode == 0

    try:
        root.mkdir(parents=True)
        config_path.write_text(json.dumps({
            "poll_interval_seconds": 3600,
            "poll_on_start": False,
            "paths": {
                "usage_csv": str(root / "usage.csv"),
                "log_file": str(root / "collector.log"),
                "state_file": str(root / "state.json"),
                "cache_dir": str(root / "cache"),
            },
            "providers": {
                "codex": {"enabled": False}, "claude": {"enabled": False},
                "antigravity": {"enabled": False, "configure_status_line": False},
                "gemini_cli": {"enabled": False, "configure_telemetry": False},
                "grok": {"enabled": False},
            },
        }, indent=2) + "\n", encoding="utf-8")

        print("1. install --no-start writes files but starts nothing")
        run("install", "--config", str(config_path), "--no-start")
        expect(plist.is_file() and (root / "collector.py").is_file(), "plist and collector copy written")
        expect(launchctl("print", target).returncode != 0, "--no-start leaves the agent unloaded")

        print("2. start: enable + bootstrap")
        running = wait_running() if run("start") else {}
        expect(running["state"] == "running" and running["disabled"] is False, f"running and enabled ({running})")
        expect(process_alive(running["pid"]), f"daemon process {running['pid']} is alive")
        first_pid = running["pid"]

        print("3. stop: disable + bootout (stays stopped at login)")
        stopped = json.loads(run("stop").stdout)
        expect(stopped["state"] == "stopped" and stopped["disabled"] is True, f"stopped and disabled ({stopped})")
        disabled_db = launchctl("print-disabled", domain).stdout
        expect(f'"{label}" => disabled' in disabled_db or f'"{label}" => true' in disabled_db,
               "launchd's override database records the agent as disabled")
        time.sleep(0.5)
        expect(not process_alive(first_pid), "daemon process exited")
        expect(plist.is_file() and config_path.is_file(), "installation and config preserved")

        print("4. start again: re-enables and runs")
        running = wait_running() if run("start") else {}
        expect(running["state"] == "running" and running["disabled"] is False, f"running and enabled ({running})")

        print("5. reinstall after stop: explicit enable → bootstrap → kickstart")
        run("stop")
        run("install", "--config", str(config_path))
        running = wait_running()
        expect(running["state"] == "running" and running["disabled"] is False,
               f"reinstall after stop leaves it running and enabled ({running})")

        print("6. upgrade while running: install a newer collector over the running agent")
        upgrade = home / "ai_usage_service_upgrade.py"
        upgrade.write_bytes(SCRIPT.read_bytes() + b"\n# integration-check upgrade\n")
        prior_pid = status()["pid"]
        run("install", "--config", str(config_path), script=upgrade)
        upgraded = wait_running()
        expect((root / "collector.py").read_bytes() == upgrade.read_bytes(), "the newer collector.py is installed")
        expect(upgraded["state"] == "running" and upgraded["disabled"] is False,
               f"running and enabled after the upgrade ({upgraded})")
        expect(upgraded["pid"] != prior_pid, "the old daemon was replaced by a new process")
        expect(config_path.is_file() and json.loads(config_path.read_text())["poll_on_start"] is False,
               "config preserved across the upgrade")

        print("7. failed install recovers the prior service")
        # A newer collector copy must replace collector.py after the running agent
        # is booted out; making the installed file user-immutable makes that
        # replacement fail, which exercises the installer's real rollback.
        variant = home / "ai_usage_service_variant.py"
        variant.write_bytes(SCRIPT.read_bytes() + b"\n# integration-check variant\n")
        before_script = (root / "collector.py").read_bytes()
        prior_pid = status()["pid"]
        subprocess.run(["/usr/bin/chflags", "uchg", str(root / "collector.py")], check=True)
        try:
            failed = run("install", "--config", str(config_path), ok=False, script=variant)
        finally:
            subprocess.run(["/usr/bin/chflags", "nouchg", str(root / "collector.py")], check=False)
        expect(failed.returncode != 0, f"install fails when collector.py can't be replaced ({failed.stderr.strip()[:120]})")
        recovered = wait_running()
        expect(recovered["state"] == "running" and recovered["disabled"] is False,
               f"rollback restarted the prior service ({recovered})")
        expect(recovered["pid"] != prior_pid, "the prior service was booted out and started again")
        expect((root / "collector.py").read_bytes() == before_script, "installed collector left unchanged")

        print("8. failed install keeps a stopped, disabled agent stopped and disabled")
        stopped = json.loads(run("stop").stdout)
        expect(stopped["state"] == "stopped" and stopped["disabled"] is True, f"stopped and disabled ({stopped})")
        subprocess.run(["/usr/bin/chflags", "uchg", str(root / "collector.py")], check=True)
        try:
            failed = run("install", "--config", str(config_path), ok=False, script=variant)
        finally:
            subprocess.run(["/usr/bin/chflags", "nouchg", str(root / "collector.py")], check=False)
        expect(failed.returncode != 0, "install fails")
        after = status()
        expect(after["state"] == "stopped" and after["disabled"] is True,
               f"still stopped and disabled on real launchd ({after})")
        expect((root / "collector.py").read_bytes() == before_script, "installed collector left unchanged")
    except CheckFailed as error:
        print(f"  ✗ {error}")
        outcome = 1
    else:
        outcome = 0
    finally:
        launchctl("bootout", target)
        launchctl("enable", target)
        # Step 6's immutable flag propagates into installer backups (copy2 keeps
        # macOS file flags); clear flags so the temporary HOME can be removed.
        subprocess.run(["/usr/bin/chflags", "-R", "nouchg", str(home)], capture_output=True, check=False)
        shutil.rmtree(home, ignore_errors=True)
        if home.exists():
            print(f"  ✗ temporary HOME could not be removed: {home}")
            outcome = 1
        leftover = launchctl("print", target).returncode == 0
        print(f"cleanup: disposable agent {'STILL LOADED' if leftover else 'unloaded'}; temp HOME removed")
        real_after = real_collector_pid()
        print(f"real {REAL_LABEL}: before [{real_before}] after [{real_after}]")
        real_changed = real_after != real_before
        if real_changed:
            print("  ✗ the real collector changed during the check")
    if real_changed:
        return 1
    if outcome == 0:
        print("launchd-integration-check: all lifecycle checks passed against real launchd")
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
