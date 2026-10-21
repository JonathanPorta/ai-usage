# Procedure: Reinstall and AC-10 (Owner's Mac)

This procedure is **prepared, not run.** It changes the owner's installed collector, so it needs two separate approvals from the owner:
1. merging the corrective PR;
2. performing the reinstall.

Nothing here publishes a release. Every command runs on the owner's Mac, typed by the owner (or run with their approval) from the repository checkout. No command reads, prints or moves a credential.

AC-10 passes when `claude` and `claude-az` appear separately, each with its own usage, its own plan limits and its own history, and both are collected by the installed LaunchAgent.

## 0. Preconditions

- The corrective PR is merged, and main CI is green on its merge commit, including Proofglass macOS.
- `~/.claude-az` is a Claude Code config folder the owner has already signed in to with `CLAUDE_CONFIG_DIR=~/.claude-az`. It holds `.claude.json`.
- The default account is signed in as usual, with `CLAUDE_CONFIG_DIR` unset.

## 1. Back up the collector's own files (read-only for the providers)

```bash
backup=~/.ai-usage/pre-2.3.0-$(date +%Y%m%d%H%M%S)
mkdir -m 700 "$backup"
cp -p ~/.ai-usage/config.json ~/.ai-usage/state.json ~/.ai-usage/usage.csv "$backup"/
~/.ai-usage/collector.py version > "$backup/installed-version.txt"
```

## 2. Reinstall from main

```bash
cd ~/devel/portaj/ai-usage
git switch main && git pull --ff-only && git log --oneline -1   # the corrective PR's merge commit
python3 ai_usage_service.py install
~/.ai-usage/collector.py version                                # expect 2.3.0
~/.ai-usage/collector.py status
```

- **Expect:** `install` keeps `config.json`, `state.json` and `usage.csv`.
- **Check:** `cmp "$backup/config.json" ~/.ai-usage/config.json` reports no difference.

## 3. Add the second account

Use either the app (Settings › Providers › "Add Claude account…": name "Claude (az)", id `claude-az`, folder `~/.claude-az`) or the CLI:

```bash
~/.ai-usage/collector.py instance add --plugin claude --id claude-az --label "Claude (az)" --config-dir ~/.claude-az
~/.ai-usage/collector.py instance list
```

**Expect:** the entry's `config_dir` is the absolute folder (`/Users/<you>/.claude-az`), not `~` or a variable.

## 4. Check the sources (runs no provider command)

```bash
~/.ai-usage/collector.py doctor --json | python3 -c '
import json, sys
d = json.load(sys.stdin)["providers"]
for k in ("claude", "claude-az"):
    p = d[k]
    print(k, p.get("type"), p.get("projects_dir"), p.get("session_log_count"), p.get("stats_file_readable"))'
```

**Expect:**
- two lines, each of type `claude`;
- different `projects_dir`: `~/.claude/projects` and `~/.claude-az/projects`;
- each with a non-zero `session_log_count`.

`claude-az` may show `stats_file_readable False`; its daily-stats source then reads "waiting".

## 5. One collection, in the foreground

```bash
~/.ai-usage/collector.py once --progress
```

**Expect:**
- progress lines for `claude` and later `claude-az`;
- exit 0, or 2 with a named row error, never 1.

## 6. Each account's own plan limits

```bash
make report | python3 -c '
import json, sys
r = json.load(sys.stdin)
for p in r["providers"]:
    if p.get("type") == "claude":
        print(p["id"], p["name"], p["quota"]["status"],
              [(w["short"], w["percent"], w["status"]) for w in p["quota"]["windows"]])'
```

Compare each line with Claude Code's own `/usage` in that account:
- `env -u CLAUDE_CONFIG_DIR claude`, then `/usage`, for `claude`;
- `CLAUDE_CONFIG_DIR=~/.claude-az claude`, then `/usage`, for `claude-az`.

**Expect:** each account's session and weekly percentages match its own `/usage`, not the other's.

## 7. Separate history

```bash
python3 - <<'EOF'
import csv, os, collections
totals = collections.Counter()
for row in csv.DictReader(open(os.path.expanduser("~/.ai-usage/usage.csv"))):
    if row["source"] == "claude-session-log" and row["metric"] in ("input_tokens", "output_tokens"):
        totals[row["provider"]] += int(row["value"] or 0)
print(dict(totals))
EOF
python3 -c 'import json, os; s = json.load(open(os.path.expanduser("~/.ai-usage/state.json"))); print(sorted(s.get("instances", {})))'
```

**Expect:**
- separate `claude` and `claude-az` totals;
- `claude`'s rows from before the reinstall are unchanged (no recount: the earlier rows in `$backup/usage.csv` are a prefix of the new file);
- `state.json` has `instances: ["claude-az"]`.

## 8. Collection through the installed service

```bash
launchctl kickstart -k "gui/$(id -u)/codes.porta.ai-usage"   # or wait for the next scheduled check
tail -n 20 ~/.ai-usage/collector.log
python3 - <<'EOF'
import csv, os
rows = list(csv.DictReader(open(os.path.expanduser("~/.ai-usage/usage.csv"))))
last = rows[-1]["transaction_id"]
print(sorted({r["provider"] for r in rows if r["transaction_id"] == last}))
EOF
```

**Expect:** the LaunchAgent's latest transaction includes both `claude` and `claude-az`.

Then open the app with `make app-run`:
- Settings lists "Claude (az)" with "Claude Code account · claude-az";
- the overview shows both accounts, each with its own limits.

## 9. Record the result

Record pass or fail for each of steps 4 to 8 in `tasks/phase-2/session-state-provider-instances.md`. Usage percentages and counts are fine to record; don't record credentials or account emails. Only then is AC-10 accepted.

## Rollback

- **Quickest, keeps everything:** `~/.ai-usage/collector.py instance disable claude-az`.
- **Full:**
  1. `~/.ai-usage/collector.py stop`;
  2. restore `config.json`, `state.json` and `usage.csv` from `$backup`;
  3. reinstall the previously installed revision from a checkout of it;
  4. `~/.ai-usage/collector.py start`.
