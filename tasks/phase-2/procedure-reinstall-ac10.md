# Procedure: Reinstall and AC-10 (Owner's Mac)

This procedure is **prepared, not run.** It changes the owner's installed collector, so it needs two separate approvals from the owner:
1. merging #13;
2. performing the reinstall.

Nothing here publishes a release. Every command runs on the owner's Mac, typed by the owner (or run with their approval) in one terminal, from the repository checkout. No command reads, prints or moves a credential. It assumes the default collector paths under `~/.ai-usage`.

AC-10 passes when `claude` and `claude-az` appear separately, each with its own usage, its own plan limits and its own history, and both are collected by the installed LaunchAgent.

The Python snippets are read-only checks, except the snapshot in step 1, which also finishes an interrupted commit. `tests/test_ac10_procedure.py` runs each one against an isolated installed collector, so a change to the collector's output that breaks one fails CI. Keep each snippet's first line, `# ac10: <name>`, because the test finds the snippets by it.

## 0. Preconditions

- #13 is merged, and main CI is green on its merge commit, including Proofglass macOS.
- `~/.claude-az` is a Claude Code config folder the owner has already signed in to with `CLAUDE_CONFIG_DIR=~/.claude-az`. It holds `.claude.json`.
- The default account is signed in as usual, with `CLAUDE_CONFIG_DIR` unset.

## 1. A coherent snapshot of the collector's files

The snapshot uses the installed collector's own locks:
1. It waits for any running collection to finish, by taking the state transaction lock.
2. It finishes an interrupted commit, if a pending journal exists, the way the next collection would.
3. It copies the files while still holding both locks.

If it finished an interrupted commit, `collector.log` records `recovered pending collection transaction …`, as the collector itself would.

```bash
cd ~/devel/portaj/ai-usage
backup=~/.ai-usage/pre-2.3.0-$(date +%Y%m%d%H%M%S)
AC10_SNAPSHOT="$backup" python3 - <<'EOF'
# ac10: snapshot
import importlib.util, os, shutil, sys
home = os.path.expanduser("~/.ai-usage")
sys.path.insert(0, home)
spec = importlib.util.spec_from_file_location("installed_collector", os.path.join(home, "collector.py"))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
config_path = c.Path(home) / "config.json"
config = c.load_config(config_path)
usage, log = c.configured_paths(config)
state = c.state_path_for(config)
c.configure_logging(log)  # a recovery is logged to collector.log, as the collector would
target = os.environ["AC10_SNAPSHOT"]
with c.state_transaction_lock(state):  # no collection runs until the copy is done
    c.recover_pending_transaction(state, usage, c.load_state(state))
    if c.pending_state_path(state).exists():
        sys.exit("FAIL: a pending transaction journal is still present")
    with c.exclusive_file_lock(usage.with_name(f"{usage.name}.lock")):
        os.makedirs(target, mode=0o700)
        for path in (config_path, state, usage):
            shutil.copy2(path, target)
        with open(os.path.join(target, "collector-version.txt"), "w") as handle:
            handle.write(c.VERSION + "\n")
print(f"snapshot: {target} (version {c.VERSION})")
EOF
```

**Expect:** `snapshot: …`. Any other output, or a non-zero exit, means stop here.

## 2. Reinstall from main

```bash
git switch main && git pull --ff-only && git log --oneline -1   # #13's merge commit
python3 ai_usage_service.py install
~/.ai-usage/collector.py version                                # expect 2.3.0
~/.ai-usage/collector.py status
cmp "$backup/config.json" ~/.ai-usage/config.json && echo "config kept"
```

**Expect:** `install` keeps `config.json`, `state.json` and `usage.csv`.

## 3. Add the second account

Use either the app (Settings › Providers › "Add Claude account…": name "Claude (az)", id `claude-az`, folder `~/.claude-az`) or the CLI:

```bash
~/.ai-usage/collector.py instance add --plugin claude --id claude-az --label "Claude (az)" --config-dir ~/.claude-az
~/.ai-usage/collector.py instance list
```

**Expect:** the entry's `config_dir` is the absolute folder (`/Users/<you>/.claude-az`), not `~` or a variable.

## 4. Check the sources (runs no provider command)

```bash
python3 - <<'EOF'
# ac10: doctor
import json, os, subprocess, sys
out = subprocess.run([sys.executable, os.path.expanduser("~/.ai-usage/collector.py"), "doctor", "--json"],
                     capture_output=True, text=True, check=True).stdout
providers = json.loads(out)  # the provider map itself, keyed by id
for key in ("claude", "claude-az"):
    p = providers[key]
    print(key, p.get("type"), p.get("projects_dir"), p.get("session_log_count"), p.get("stats_file_readable"))
EOF
```

**Expect:**
- two lines, each of type `claude`;
- different `projects_dir`, as absolute paths: `/Users/<you>/.claude/projects` and `/Users/<you>/.claude-az/projects`;
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
python3 - <<'EOF'
# ac10: limits
import json, os, subprocess, sys
out = subprocess.run([sys.executable, "ai_usage_report.py", "--config", os.path.expanduser("~/.ai-usage/config.json")],
                     capture_output=True, text=True, check=True).stdout
for p in json.loads(out)["providers"]:
    if p.get("type") == "claude":
        print(p["id"], p["name"], p["quota"]["status"],
              [(w["short"], w["percent"], w["status"]) for w in p["quota"]["windows"]])
EOF
```

Compare each line with Claude Code's own `/usage` in that account:
- `env -u CLAUDE_CONFIG_DIR claude`, then `/usage`, for `claude`;
- `CLAUDE_CONFIG_DIR=~/.claude-az claude`, then `/usage`, for `claude-az`.

**Expect:** each account's session and weekly percentages match its own `/usage`, not the other's.

## 7. Separate history

```bash
AC10_SNAPSHOT="$backup" python3 - <<'EOF'
# ac10: history
import collections, csv, json, os
home = os.path.expanduser("~/.ai-usage")
rows = list(csv.DictReader(open(os.path.join(home, "usage.csv"), encoding="utf-8")))
before = list(csv.DictReader(open(os.path.join(os.environ["AC10_SNAPSHOT"], "usage.csv"), encoding="utf-8")))
totals = collections.Counter()
for row in rows:
    if row["source"] == "claude-session-log" and row["metric"] in ("input_tokens", "output_tokens"):
        totals[row["provider"]] += int(row["value"] or 0)
state = json.load(open(os.path.join(home, "state.json"), encoding="utf-8"))
print("tokens:", dict(totals))
print("earlier rows unchanged:", rows[:len(before)] == before)
print("added accounts in state:", sorted(state.get("instances", {})))
EOF
```

**Expect:**
- separate `claude` and `claude-az` totals;
- `earlier rows unchanged: True`, meaning nothing was recounted;
- `added accounts in state: ['claude-az']`.

## 8. Collection through the installed service

Quit the AI Usage app first, so its "Collect now" can't add a collection meanwhile.

Record where the log and the CSV end, restart the LaunchAgent, then wait up to five minutes for a new collection *by the service*:
- The service's collection logs `collection completed`; a foreground `once` logs `one-shot collection completed`, so it can't pass this step on its own.
- Every transaction added after the mark must include both accounts, so another collection can't pass in the service's place or hide one that's missing an account.
- A log rotation after the mark is followed into `collector.log.1`.

```bash
mark="$backup/ac10-mark.json"
AC10_MARK="$mark" python3 - <<'EOF'
# ac10: mark
import csv, json, os
home = os.path.expanduser("~/.ai-usage")
rows = list(csv.DictReader(open(os.path.join(home, "usage.csv"), encoding="utf-8")))
log = os.stat(os.path.join(home, "collector.log"))
mark = {"log_offset": log.st_size, "log_inode": log.st_ino,
        "last_transaction": rows[-1]["transaction_id"] if rows else None}
json.dump(mark, open(os.environ["AC10_MARK"], "w"))
print("marked:", mark)
EOF
launchctl kickstart -k "gui/$(id -u)/codes.porta.ai-usage"
AC10_MARK="$mark" AC10_TIMEOUT_SECONDS=300 python3 - <<'EOF'
# ac10: service
import csv, json, os, sys, time
home = os.path.expanduser("~/.ai-usage")
mark = json.load(open(os.environ["AC10_MARK"]))
deadline = time.monotonic() + float(os.environ.get("AC10_TIMEOUT_SECONDS", "300"))
def since_mark(path, inode):
    """The log's lines after the mark: the rest of the marked file, then any newer one."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as handle:
        handle.seek(mark["log_offset"] if os.fstat(handle.fileno()).st_ino == inode else 0)
        return handle.read().decode("utf-8", "replace").splitlines()
while True:
    log = os.path.join(home, "collector.log")
    lines = since_mark(log, mark["log_inode"])
    try:
        rotated = os.stat(log).st_ino != mark["log_inode"]
    except FileNotFoundError:  # mid-rotation: the new log isn't there yet
        rotated = True
    if rotated:
        lines = since_mark(log + ".1", mark["log_inode"]) + lines
    if any("collection completed" in line and "one-shot" not in line for line in lines):
        rows = list(csv.DictReader(open(os.path.join(home, "usage.csv"), encoding="utf-8")))
        ids = [r["transaction_id"] for r in rows]
        start = max((i for i, t in enumerate(ids) if t == mark["last_transaction"]), default=-1) + 1
        new = list(dict.fromkeys(ids[start:]))
        if not new:
            sys.exit("FAIL: the service logged a collection but appended no transaction")
        for transaction in new:
            # A disabled account writes only a marker row; that isn't a collection of it.
            providers = {r["provider"] for r in rows[start:]
                         if r["transaction_id"] == transaction and r["status"] != "disabled"}
            missing = {"claude", "claude-az"} - providers
            if missing:
                sys.exit(f"FAIL: collection {transaction} has no rows for {sorted(missing)}")
        print(f"PASS: {len(new)} new collection(s), the service's among them, each include claude and claude-az")
        break
    if time.monotonic() > deadline:
        sys.exit("FAIL: no service collection within the timeout; see `collector.py status` and collector.log "
                 "(with poll_on_start false, rerun this step after the next scheduled check)")
    time.sleep(5)
EOF
```

**Expect:** `PASS: …`. A `FAIL:` line means AC-10 does not pass.

Then open the app with `make app-run`:
- Settings lists "Claude (az)" with "Claude Code account · claude-az";
- the overview shows both accounts, each with its own limits.

## 9. Record the result

Record pass or fail for each of steps 4 to 8 in `tasks/phase-2/session-state-provider-instances.md`. Usage percentages and counts are fine to record; don't record credentials or account emails. Only then is AC-10 accepted.

## Rollback

- **Quickest, keeps everything:** `~/.ai-usage/collector.py instance disable claude-az`.
- **Full:**
  1. `~/.ai-usage/collector.py stop`.
  2. Keep a side copy of the newer data first. In the step 1 terminal, so `$backup` is still set (otherwise set it again to the snapshot folder), rerun the step 1 snippet with `AC10_SNAPSHOT="$backup/at-rollback"`.
     - It takes the same locks, finishes any interrupted commit, and copies today's `config.json`, `state.json` and `usage.csv`.
     - Rows collected since the reinstall then stay in `$backup/at-rollback/usage.csv`. Step 3 puts the older files back live, so this copy is how those rows are kept.
  3. Restore `config.json`, `state.json` and `usage.csv` from `$backup`.
  4. Reinstall the previously installed revision from a checkout of it. Its version is in `$backup/collector-version.txt`.
  5. `~/.ai-usage/collector.py start`.
