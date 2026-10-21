#!/usr/bin/env python3
"""Check the app bundle's collector files after `make app-build` (phase 1, AC-7).

- The bundle carries the report, the service and the whole plugin package,
  byte for byte the same as this checkout.
- The bundled reporter and collector run on their own: `python -I` from an
  empty working directory, no PYTHONPATH, nothing from the checkout. The
  report they print for the synthetic sandbox at a fixed time is identical to
  the checkout's.
- Running them writes nothing into the bundle (no __pycache__), so its
  signature still verifies.

    python3 scripts/check_app_bundle.py "macos/build/AI Usage.app"
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import ai_usage_fixtures  # noqa: E402
import ai_usage_plugins  # noqa: E402


def fail(message: str) -> None:
    print(f"check_app_bundle: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(app: Path) -> int:
    collector = app / "Contents" / "Resources" / "collector"
    expected = {"ai_usage_report.py", "ai_usage_service.py"} | {
        f"ai_usage_plugins/{name}.py" for name in ai_usage_plugins.MODULES
    }
    bundled = {str(path.relative_to(collector)) for path in collector.rglob("*") if path.is_file()}
    if bundled != expected:
        fail(f"bundled collector files differ: missing {sorted(expected - bundled)}, extra {sorted(bundled - expected)}")
    for relative in sorted(expected):
        if (collector / relative).read_bytes() != (REPO / relative).read_bytes():
            fail(f"{relative} in the bundle differs from the checkout")

    with tempfile.TemporaryDirectory(prefix="ai-usage-bundle-check-") as temporary:
        root = Path(temporary)
        sandbox, home, cwd = root / "sandbox", root / "home", root / "cwd"
        home.mkdir()
        cwd.mkdir()
        subprocess.run([sys.executable, str(REPO / "ai_usage_fixtures.py"), "sandbox", str(sandbox),
                        "--now", ai_usage_fixtures.FIXTURE_NOW], check=True, stdout=subprocess.DEVNULL)
        environment = {"HOME": str(home), "PATH": "/usr/bin:/bin", "TZ": ai_usage_fixtures.FIXTURE_TZ,
                       "AI_USAGE_LAUNCHCTL": ""}
        arguments = ["--config", str(sandbox / "config.json"), "--service", "skip",
                     "--now", ai_usage_fixtures.FIXTURE_NOW]

        def run(script: Path, *extra: str) -> str:
            result = subprocess.run([sys.executable, "-I", str(script), *extra], cwd=cwd, env=environment,
                                    capture_output=True, text=True, check=False)
            if result.returncode != 0:
                fail(f"{script} {' '.join(extra[:1])} failed ({result.returncode}): {result.stderr.strip()}")
            return result.stdout

        if run(collector / "ai_usage_report.py", *arguments) != run(REPO / "ai_usage_report.py", *arguments):
            fail("the bundled reporter's report differs from the checkout's")
        run(collector / "ai_usage_service.py", "doctor", "--json", "--config", str(sandbox / "config.json"))
        if any(cwd.iterdir()):
            fail("running from the bundle wrote into the working directory")

    if list(collector.rglob("__pycache__")):
        fail("running from the bundle wrote bytecode into it")
    if sys.platform == "darwin":
        subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
    print(f"check_app_bundle: {len(expected)} collector files match; the bundled reporter and collector run on their own")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_app_bundle.py APP")
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
