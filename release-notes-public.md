# AI Usage

A native macOS menu-bar app over the AI Usage collector. It shows token usage,
quota and collector health for Codex, Claude Code, Antigravity, Gemini CLI and
Grok, from data the collector stores on your Mac.

- Requires macOS 14 or later on Apple silicon.
- The app is ad-hoc signed and not notarized; macOS will ask you to confirm the
  first launch.
- The collector bundled inside the app keeps its own version; run
  `python3 ai_usage_service.py install` from the repository to update the
  LaunchAgent.
