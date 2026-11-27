"""macos/scripts/install_app.sh against a temporary destination (never ~/Applications)."""

from __future__ import annotations

import os
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "macos" / "scripts" / "install_app.sh"


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("codesign") and shutil.which("ditto"),
                     "install_app.sh needs macOS codesign and ditto")
class InstallAppTests(unittest.TestCase):
    def make_bundle(self, root: Path, marker: str, where: str = "src", sleeper: bool = False) -> Path:
        bundle = root / where / "AI Usage.app"
        if bundle.exists():
            shutil.rmtree(bundle)
        macos = bundle / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        (bundle / "Contents" / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
            "<key>CFBundleIdentifier</key><string>codes.porta.ai-usage.install-test</string>"
            "<key>CFBundleExecutable</key><string>AIUsage</string></dict></plist>\n")
        exe = macos / "AIUsage"
        if sleeper:
            shutil.copyfile("/bin/sleep", exe)
        else:
            exe.write_text(f"#!/bin/sh\n# {marker}\n")
        exe.chmod(0o755)
        subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True, capture_output=True)
        return bundle

    def install(self, src: Path, dest: Path, **extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, APP_INSTALL_DEST=str(dest), APP_INSTALL_NO_LAUNCH="1", APP_INSTALL_STOP_TIMEOUT="1")
        env.pop("APP_INSTALL_PREVIOUS", None)
        env.update(extra)
        return subprocess.run(["bash", str(SCRIPT), str(src)], env=env, capture_output=True, text=True)

    def run_install(self, src: Path, dest: Path) -> str:
        result = self.install(src, dest)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def start_ignoring_term(self, bundle: Path) -> subprocess.Popen:
        """Run the bundle's sleep executable with SIGTERM ignored (inherited across exec)."""
        proc = subprocess.Popen([str(bundle / "Contents/MacOS/AIUsage"), "60"],
                                preexec_fn=lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN))
        self.addCleanup(lambda: (proc.kill(), proc.wait()))  # SIGKILL on this exact PID only
        time.sleep(0.3)
        return proc

    def snapshot(self, *paths: Path) -> list:
        return [sorted(str(f.relative_to(p)) + ":" + (f.read_bytes().hex() if f.is_file() else "")
                       for f in p.rglob("*")) if p.exists() else None for p in paths]

    def test_fresh_install_then_reinstall_keeps_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "Applications" / "AI Usage.app"
            previous = Path(f"{dest}.previous")
            exe = Path("Contents/MacOS/AIUsage")

            out = self.run_install(self.make_bundle(root, "one"), dest)
            self.assertIn(f"Installed: {dest}", out)
            self.assertIn("one", (dest / exe).read_text())
            self.assertFalse(previous.exists())

            out = self.run_install(self.make_bundle(root, "two"), dest)
            self.assertIn("two", (dest / exe).read_text())
            self.assertIn("one", (previous / exe).read_text())
            self.assertIn("Rollback:", out)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(dest)], check=True)

    def installed_twice(self, root: Path) -> tuple:
        dest = root / "Applications" / "AI Usage.app"
        self.run_install(self.make_bundle(root, "one"), dest)
        self.run_install(self.make_bundle(root, "two"), dest)
        return dest, Path(f"{dest}.previous")

    def test_unsigned_source_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest, previous = self.installed_twice(root)
            before = self.snapshot(dest, previous)
            bad = self.make_bundle(root, "bad", where="bad")
            (bad / "Contents/MacOS/AIUsage").write_text("#!/bin/sh\n# tampered\n")
            result = self.install(bad, dest)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.snapshot(dest, previous), before)
            self.assertFalse(Path(f"{dest}.staging").exists())

    def test_source_equal_to_destination_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest, previous = self.installed_twice(root)
            before = self.snapshot(dest, previous)
            result = self.install(dest, dest)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("same bundle", result.stderr)
            self.assertEqual(self.snapshot(dest, previous), before)

    def test_destination_that_survives_sigterm_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "Applications" / "AI Usage.app"
            self.run_install(self.make_bundle(root, "x", where="sleeper", sleeper=True), dest)
            before = self.snapshot(dest)
            proc = self.start_ignoring_term(dest)
            result = self.install(self.make_bundle(root, "new"), dest)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("did not exit", result.stderr)
            self.assertIsNone(proc.poll())
            self.assertEqual(self.snapshot(dest), before)
            self.assertFalse(Path(f"{dest}.previous").exists())
            self.assertFalse(Path(f"{dest}.staging").exists())

    def test_unidentified_other_running_copy_is_refused_before_any_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "Applications" / "AI Usage.app"
            other = self.make_bundle(root, "x", where="other", sleeper=True)
            proc = self.start_ignoring_term(other)
            result = self.install(self.make_bundle(root, "new"), dest, APP_INSTALL_NO_LAUNCH="")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("other copies of AI Usage are running", result.stderr)
            self.assertIn(str(other.resolve() / "Contents/MacOS/AIUsage"), result.stderr)
            self.assertFalse(dest.exists())
            self.assertFalse(Path(f"{dest}.staging").exists())
            self.assertIsNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
