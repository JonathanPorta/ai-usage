"""macos/scripts/install_app.sh against a temporary destination (never ~/Applications)."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "macos" / "scripts" / "install_app.sh"


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("codesign") and shutil.which("ditto"),
                     "install_app.sh needs macOS codesign and ditto")
class InstallAppTests(unittest.TestCase):
    def make_bundle(self, root: Path, marker: str) -> Path:
        bundle = root / "src" / "AI Usage.app"
        if bundle.exists():
            shutil.rmtree(bundle)
        macos = bundle / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        (bundle / "Contents" / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
            "<key>CFBundleIdentifier</key><string>codes.porta.ai-usage.install-test</string>"
            "<key>CFBundleExecutable</key><string>AIUsage</string></dict></plist>\n")
        exe = macos / "AIUsage"
        exe.write_text(f"#!/bin/sh\n# {marker}\n")
        exe.chmod(0o755)
        subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True, capture_output=True)
        return bundle

    def run_install(self, src: Path, dest: Path) -> str:
        env = dict(os.environ, APP_INSTALL_DEST=str(dest), APP_INSTALL_NO_LAUNCH="1")
        result = subprocess.run(["bash", str(SCRIPT), str(src)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

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


if __name__ == "__main__":
    unittest.main()
