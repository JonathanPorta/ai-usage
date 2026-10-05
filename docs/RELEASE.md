---
status: draft
owner: Jonathan
updated: 2026-10-05
---

# RELEASE — ai-usage

## Components

| Component | Artifact | Version source |
| --- | --- | --- |
| Collector | `ai_usage_service.py`, installed by `python3 ai_usage_service.py install` to `~/.ai-usage/collector.py` | `VERSION` in `ai_usage_service.py` |
| Reporting layer | `ai_usage_report.py` (bundled inside the app; also runnable from the repo) | `REPORT_SCHEMA` (`ai-usage/report/v1`) |
| macOS app | `macos/build/AI Usage.app`, built by `make app-build` | `CFBundleShortVersionString` in `macos/Support/Info.plist` |

## Current release model

- **Local builds only.** `make app-build` produces an ad-hoc-signed bundle (`codesign --sign -`) for the owner's own Mac.
- **Not done yet:** no notarization, no Developer ID signing, and no distribution channel. `docs/PUBLISHING.md` will be added when distribution becomes real.
- **Collector updates stay independent:** re-run `install` from the repo. The app talks to whatever collector the LaunchAgent plist names.

## Compatibility

- The app requires report schema `ai-usage/report/v1`. It refuses unknown major versions and shows a "report format not supported" state.
- The app bundles its own copy of the reporting module. It needs only the collector's data files and the LaunchAgent plist, not a particular collector version, as long as the CSV header is unchanged.

## Release checklist (local)

1. `make check` (collector and report tests) and `make app-test`.
2. `make design-check ARGS=--strict` and `make spec-check ARGS=--strict`.
3. `make app-build`, then launch and confirm the verification items in `docs/design/app/design.md#verification`.
4. Copy `macos/build/AI Usage.app` to `/Applications` (manually; nothing installs automatically).
