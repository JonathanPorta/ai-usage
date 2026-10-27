# Procedure: AC-13 on the Owner's Work Laptop

**For the owner to run on the work laptop only.** The personal Mac already runs 2.4.0. Don't run this there, and never put the private plugin there.

AC-13 passes when the owner's private plugin is:
- installed on the work laptop and turned on;
- collected, both by a foreground run and by the installed service;
- shown correctly in the report and in the app.

**Privacy:** the plugin's code, its configuration and credentials, and every log, report, check output and screenshot stay on the laptop. Only `AC-13: pass` or `AC-13: fail` comes back.

**Each step is one block:**
- Paste the whole block. It runs as its own `bash -euo pipefail` script, so a failed command or check stops it with a message, and your shell stays open.
- Don't go on after a `FAIL:` line or a non-zero exit.
- The work folder is `~/ai-usage-ac13`.

**Tested in CI:** `tests/test_ac13_procedure.py` runs this procedure's checks against synthetic plugins in an isolated home. Keep each `# ac13:` first line, because the test finds the blocks and snippets by it.
- It runs every Python check (each starts `# ac13: <name>`) and the plugin-placement step.
- It runs an upgrade from 2.3.0 with the backup and install blocks, then the rollback's two blocks, then an uninstall and a reinstall.
- The stop and start blocks aren't run, because they act on the real LaunchAgent. There, a stand-in `launchctl` is used, and a stand-in `pgrep` reports that nothing is running.

## Prerequisites
- **The laptop:**
  - macOS 14 or newer, with you signed in at the desktop (the collector is a per-user LaunchAgent);
  - `git`, Python 3.9 or newer as `python3`, and the Xcode command-line tools with Swift (`xcode-select --install`);
  - network access to GitHub.
- **Your plugin:** a folder on the laptop holding its `plugin.json` and executable (contract: `docs/PLUGINS.md`, "Local plugins (phase 3)", in the clone).
  - The folder name you'll use is the plugin id: `^[a-z][a-z0-9_-]{0,31}$`, equal to the manifest's `id`, and not `codex`, `claude`, `antigravity`, `grok`, `gemini_cli` or an added account's id.
  - Keep credentials out of its rows, messages, scopes and `state`, because the collector stores those.
- **Fresh install or upgrade:** step 0 tells you which.
  - **Fresh:** neither `~/.ai-usage/collector.py` nor the LaunchAgent exists. Skip step 3.
  - **Upgrade:** a collector is already installed. Step 3 stops it and takes a locked backup before anything changes.

## 0. Before you start
In the terminal you'll use for every step:
```bash
export AC13_ID=<plugin-id> AC13_PLUGIN_SRC=<folder holding plugin.json and the executable>
```
Then:
```bash
bash -euo pipefail <<'STEP'
# ac13: preflight
echo "computer: $(scutil --get ComputerName)   (must be the work laptop)"
[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 14 ] || { echo "FAIL: macOS 14 or newer is needed"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "FAIL: Python 3.9 or newer is needed")'
xcode-select -p >/dev/null && swift --version >/dev/null 2>&1 || { echo "FAIL: install the Xcode command-line tools"; exit 1; }
git --version >/dev/null
[[ "${AC13_ID:?export AC13_ID first}" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || { echo "FAIL: $AC13_ID is not a valid plugin id"; exit 1; }
[ -f "${AC13_PLUGIN_SRC:?export AC13_PLUGIN_SRC first}/plugin.json" ] || { echo "FAIL: no plugin.json in $AC13_PLUGIN_SRC"; exit 1; }
if [ -e "$HOME/.ai-usage/collector.py" ] || [ -e "$HOME/Library/LaunchAgents/codes.porta.ai-usage.plist" ]; then
  echo "UPGRADE: a collector is installed ($("$HOME/.ai-usage/collector.py" version 2>/dev/null || echo 'version unknown')); run step 3"
else
  echo "FRESH: no collector is installed; skip step 3"
fi
STEP
```

## 1. A clean clone, pinned
```bash
bash -euo pipefail <<'STEP'
# ac13: clone
W="$HOME/ai-usage-ac13"; SHA=d9ea44543861bd18eaba5f1c86867d4ef2079d63
[ ! -e "$W/src" ] || { echo "FAIL: $W/src already exists; move $W aside to start over"; exit 1; }
mkdir -p -m 700 "$W"
git clone --quiet https://github.com/JonathanPorta/ai-usage.git "$W/src"
cd "$W/src"
git checkout --quiet --detach "$SHA"
[ "$(git rev-parse HEAD)" = "$SHA" ] || { echo "FAIL: the clone is not at $SHA"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "FAIL: the clone is not clean"; exit 1; }
echo "PASS: clean clone at $SHA"
STEP
```

## 2. Build and verify the app (nothing installed changes)
```bash
bash -euo pipefail <<'STEP'
# ac13: app
W="$HOME/ai-usage-ac13"; SHA=d9ea44543861bd18eaba5f1c86867d4ef2079d63
cd "$W/src"
[ "$(git rev-parse HEAD)" = "$SHA" ] && git diff --quiet HEAD || { echo "FAIL: the clone is not a clean $SHA"; exit 1; }
make app-build                        # also runs scripts/check_app_bundle.py
app="macos/build/AI Usage.app"
codesign --verify --deep --strict "$app"
cmp ai_usage_service.py "$app/Contents/Resources/collector/ai_usage_service.py"
cmp ai_usage_report.py "$app/Contents/Resources/collector/ai_usage_report.py"
diff -r -x __pycache__ ai_usage_plugins "$app/Contents/Resources/collector/ai_usage_plugins"
[ ! -e "$app/Contents/Resources/collector/plugins" ] || { echo "FAIL: the bundle carries a plugins folder"; exit 1; }
echo "$SHA" > "$W/app-verified"
echo "PASS: app built and verified at $W/src/$app"
STEP
```

## 3. Upgrade only: stop everything and back up
The first block quits every copy of AI Usage and stops the service. It waits for a running collection to finish, then confirms all three are stopped. Before it stops anything, it checks that the installed collector has an ownership record (`~/.ai-usage/install-ownership.json`). Without one, neither the upgrade nor its rollback is covered.
```bash
bash -euo pipefail <<'STEP'
# ac13: stop
[ -e "$HOME/.ai-usage/collector.py" ] || { echo "FAIL: no collector is installed; this is a fresh install, skip step 3"; exit 1; }
[ -f "$HOME/.ai-usage/install-ownership.json" ] || { echo "FAIL: the installed collector has no ownership record, so neither the upgrade nor its rollback is covered; stop here"; exit 1; }
pkill -x AIUsage 2>/dev/null || true
for _ in $(seq 1 30); do pgrep -x AIUsage >/dev/null || break; sleep 1; done
if pgrep -x AIUsage >/dev/null; then echo "FAIL: AI Usage is still running"; exit 1; fi
"$HOME/.ai-usage/collector.py" stop
for _ in $(seq 1 180); do pgrep -f "$HOME/.ai-usage/collector.py" >/dev/null || break; sleep 1; done
if pgrep -f "$HOME/.ai-usage/collector.py" >/dev/null; then echo "FAIL: a collection is still running"; exit 1; fi
"$HOME/.ai-usage/collector.py" service-status | python3 -c 'import json, sys; s = json.load(sys.stdin); sys.exit(0 if s["state"] != "running" else "FAIL: the service is still running")'
echo "PASS: the app, the service and every collection are stopped"
STEP
```
The second block takes phase 2's locked snapshot, which CI tests: it takes the collector's own locks and finishes an interrupted commit first. It also copies the installed code, its ownership record and the LaunchAgent. The rollback uses them.
```bash
bash -euo pipefail <<'STEP'
# ac13: backup
W="$HOME/ai-usage-ac13"
[ ! -e "$W/backup-path" ] || { echo "FAIL: a backup was already taken: $(cat "$W/backup-path")"; exit 1; }
if pgrep -x AIUsage >/dev/null || pgrep -f "$HOME/.ai-usage/collector.py" >/dev/null; then echo "FAIL: AI Usage or a collection is running; run the stop block first"; exit 1; fi
"$HOME/.ai-usage/collector.py" service-status | python3 -c 'import json, sys; s = json.load(sys.stdin); sys.exit(0 if s["state"] != "running" else "FAIL: the service is running; run the stop block first")'
backup="$W/backup-$(date +%Y%m%d%H%M%S)"
AC13_SNAPSHOT="$backup" python3 - <<'EOF'
# ac13: snapshot
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
target = os.environ["AC13_SNAPSHOT"]
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
cp -p "$HOME/.ai-usage/collector.py" "$HOME/.ai-usage/install-ownership.json" "$backup/"
[ ! -d "$HOME/.ai-usage/ai_usage_plugins" ] || cp -Rp "$HOME/.ai-usage/ai_usage_plugins" "$backup/"
cp -p "$HOME/Library/LaunchAgents/codes.porta.ai-usage.plist" "$backup/"
echo "$backup" > "$W/backup-path"
echo "PASS: backup at $backup"
STEP
```
If the snapshot fails on a much older collector, stop: the upgrade isn't covered. Start the old service again with `~/.ai-usage/collector.py start`.

## 4. Install the collector
For an upgrade, the first block refuses to run unless step 3's backup exists and the app and service are still stopped. It installs without starting the service. It checks the installed files against the clone, and for an upgrade checks that the data matches the backup. Only then does the second block start the service.
```bash
bash -euo pipefail <<'STEP'
# ac13: install
W="$HOME/ai-usage-ac13"; SHA=d9ea44543861bd18eaba5f1c86867d4ef2079d63
cd "$W/src"
[ "$(git rev-parse HEAD)" = "$SHA" ] && git diff --quiet HEAD || { echo "FAIL: the clone is not a clean $SHA"; exit 1; }
[ "$(cat "$W/app-verified" 2>/dev/null)" = "$SHA" ] || { echo "FAIL: run step 2 first"; exit 1; }
if pgrep -x AIUsage >/dev/null; then echo "FAIL: quit AI Usage first"; exit 1; fi
backup=""
if [ -e "$HOME/.ai-usage/collector.py" ] || [ -e "$HOME/Library/LaunchAgents/codes.porta.ai-usage.plist" ]; then
  [ -s "$W/backup-path" ] || { echo "FAIL: a collector is installed; run step 3 first"; exit 1; }
  backup="$(cat "$W/backup-path")"
  "$HOME/.ai-usage/collector.py" service-status | python3 -c 'import json, sys; s = json.load(sys.stdin); sys.exit(0 if s["state"] != "running" else "FAIL: the service is running again; rerun step 3 in a new work folder")'
fi
python3 ai_usage_service.py install --no-start
cmp ai_usage_service.py "$HOME/.ai-usage/collector.py"
diff -r -x __pycache__ ai_usage_plugins "$HOME/.ai-usage/ai_usage_plugins"
[ "$("$HOME/.ai-usage/collector.py" version)" = 2.4.0 ] || { echo "FAIL: the installed collector is not 2.4.0"; exit 1; }
if [ -n "$backup" ]; then
  for name in config.json state.json usage.csv; do cmp "$backup/$name" "$HOME/.ai-usage/$name"; done
  echo "kept: config, state and history match the backup"
fi
echo "PASS: collector 2.4.0 installed from $SHA; start it with the next block"
STEP
```
```bash
bash -euo pipefail <<'STEP'
# ac13: start
"$HOME/.ai-usage/collector.py" start
running=0
for _ in $(seq 1 30); do
  if "$HOME/.ai-usage/collector.py" service-status | python3 -c 'import json, sys; s = json.load(sys.stdin); sys.exit(0 if s["state"] == "running" and s["disabled"] is False else 1)'; then running=1; break; fi
  sleep 1
done
[ "$running" = 1 ] || { echo "FAIL: the service is not running; see collector.py service-status"; exit 1; }
echo "PASS: the service is running collector $("$HOME/.ai-usage/collector.py" version)"
STEP
```

## 5. Put the plugin in place
This copies `$AC13_PLUGIN_SRC` to `~/.ai-usage/plugins/$AC13_ID` and makes it owner-only: folders 0700, `plugin.json` 0600, the executable 0700, and no group or other access on any file.

It first checks that the manifest's `id` matches. It never changes a plugin folder that already exists. Later edits to your source folder need copying again.
```bash
bash -euo pipefail <<'STEP'
# ac13: place
id="${AC13_ID:?export AC13_ID first}"; src="${AC13_PLUGIN_SRC:?export AC13_PLUGIN_SRC first}"
dst="$HOME/.ai-usage/plugins/$id"
[[ "$id" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || { echo "FAIL: $id is not a valid plugin id"; exit 1; }
exe="$(python3 -c 'import json, sys; m = json.load(open(sys.argv[1], encoding="utf-8")); print(m["executable"]) if m.get("id") == sys.argv[2] else sys.exit("FAIL: the manifest id is %r, not %r" % (m.get("id"), sys.argv[2]))' "$src/plugin.json" "$id")"
if [ -e "$dst" ]; then
  echo "kept: $dst already exists and is left as it is"
else
  mkdir -p "$HOME/.ai-usage/plugins"
  chmod 700 "$HOME/.ai-usage/plugins"
  cp -R "$src" "$dst"
  chmod -R go-rwx "$dst"
  find "$dst" -type d -exec chmod 700 {} +
  chmod 600 "$dst/plugin.json"
  chmod 700 "$dst/$exe"
  echo "placed: $dst"
fi
STEP
```

## 6. Turn it on
The check runs before and after turning the plugin on. Each time it requires the plugins folder to have no problem and the plugin to be valid. After turning it on, the plugin must also be enabled.
```bash
bash -euo pipefail <<'STEP'
id="${AC13_ID:?export AC13_ID first}"
check() {
AC13_EXPECT_ENABLED="$1" python3 - <<'EOF'
# ac13: plugin
import json, os, subprocess, sys
pid, want = os.environ["AC13_ID"], os.environ.get("AC13_EXPECT_ENABLED", "")
out = subprocess.run([sys.executable, os.path.expanduser("~/.ai-usage/collector.py"), "plugin", "list"],
                     capture_output=True, text=True, check=True).stdout
listing = json.loads(out)
entry = next((p for p in listing["plugins"] if p["id"] == pid), None)
if listing["problem"] is not None:
    sys.exit(f"FAIL: the plugins folder: {listing['problem']}")
if pid in listing["other_folders"]:
    sys.exit(f"FAIL: {pid} can't be a local plugin: {listing['other_folders'][pid]}")
if entry is None:
    sys.exit(f"FAIL: there is no plugin {pid} in {listing['plugins_dir']}")
if entry["status"] != "valid":
    sys.exit(f"FAIL: {pid} is {entry['status']}: {entry.get('reason')}")
if want and entry["enabled"] is not (want == "true"):
    sys.exit(f"FAIL: {pid} is {'on' if entry['enabled'] else 'off'}")
print(f"PASS: {pid} is valid{', and on' if entry['enabled'] else ''}; usage {entry['usage']}, quota {entry['quota']}")
EOF
}
check ""
"$HOME/.ai-usage/collector.py" plugin enable "$id"
check true
STEP
```

## 7. A foreground collection
The check records where the CSV and the log end, runs `once`, and captures its exit code. It passes only if all of these hold:
- `once` exited 0. Exit 2 passes only when the error rows come from other providers.
- There's at least one collection after the mark.
- **Every** collection after the mark has the plugin's run marker (`availability`/`detected` = 1, `source` `plugin`, `status` `ok`). None of the plugin's rows is `error`, `missing` or `disabled`.

An older successful collection can't count, and a disabled, missing or failing plugin fails.
```bash
bash -euo pipefail <<'STEP'
W="$HOME/ai-usage-ac13"; : "${AC13_ID:?export AC13_ID first}"
AC13_MARK="$W/mark.json" python3 - <<'EOF'
# ac13: mark
import csv, importlib.util, json, os, sys
home = os.path.expanduser("~/.ai-usage")
sys.path.insert(0, home)
spec = importlib.util.spec_from_file_location("installed_collector", os.path.join(home, "collector.py"))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
usage, log = c.configured_paths(c.load_config(c.Path(home) / "config.json"))
if not usage.exists() or not log.exists():
    sys.exit("FAIL: nothing has been collected yet; wait for the service's first collection, then rerun")
with open(usage, encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle))
info = os.stat(log)
mark = {"usage": str(usage), "log": str(log), "log_offset": info.st_size, "log_inode": info.st_ino,
        "last_transaction": rows[-1]["transaction_id"] if rows else None}
with open(os.environ["AC13_MARK"], "w", encoding="utf-8") as handle:
    json.dump(mark, handle)
print(f"marked: {len(rows)} rows, log at {info.st_size} bytes")
EOF
set +e
"$HOME/.ai-usage/collector.py" once --progress
code=$?
set -e
echo "once exit=$code"
AC13_MARK="$W/mark.json" AC13_ONCE_EXIT="$code" python3 - <<'EOF'
# ac13: foreground
import csv, json, os, sys
pid, code = os.environ["AC13_ID"], os.environ["AC13_ONCE_EXIT"]
with open(os.environ["AC13_MARK"], encoding="utf-8") as handle:
    mark = json.load(handle)
def new_collections(rows):
    """Each collection appended after the mark, with its rows."""
    ids = [r["transaction_id"] for r in rows]
    if mark["last_transaction"] is not None and mark["last_transaction"] not in ids:
        sys.exit("FAIL: the marked collection is no longer in usage.csv")
    start = max((i for i, t in enumerate(ids) if t == mark["last_transaction"]), default=-1) + 1
    new = {}
    for r in rows[start:]:
        new.setdefault(r["transaction_id"], []).append(r)
    return new
def check(transaction, found):
    mine = [r for r in found if r["provider"] == pid]
    bad = sorted({f"{r['category']}/{r['metric']}: {r['status']}" for r in mine
                  if r["status"] in ("error", "missing", "disabled")})
    if bad:
        sys.exit(f"FAIL: collection {transaction}: {pid} wrote {bad}")
    if not any(r["category"] == "availability" and r["metric"] == "detected" and r["value"] == "1"
               and r["source"] == "plugin" and r["status"] == "ok" for r in mine):
        sys.exit(f"FAIL: collection {transaction} has no successful run of {pid}")
if code not in ("0", "2"):
    sys.exit(f"FAIL: once exited {code}")
with open(mark["usage"], encoding="utf-8", newline="") as handle:
    new = new_collections(list(csv.DictReader(handle)))
if not new:
    sys.exit("FAIL: there is no collection after the mark")
for transaction, found in new.items():
    check(transaction, found)
others = sorted({r["provider"] for found in new.values() for r in found if r["status"] == "error"} - {pid})
if code == "2" and not others:
    sys.exit("FAIL: once exited 2, but no other provider wrote an error row")
print(f"PASS: {len(new)} new collection(s), each with a successful run of {pid}; once exited {code}"
      + (f"; other providers with errors: {others}" if others else ""))
EOF
STEP
```

## 8. A collection by the installed service
This reuses phase 2's tested service check, adapted to the plugin:
- It marks the CSV and the log, restarts the LaunchAgent, and waits up to five minutes.
- A log rotation after the mark is followed into `collector.log.1`.
- The service logs `collection completed`, and a foreground run logs `one-shot collection completed`. The check passes only once the log holds a service completion **and** the CSV holds at least one new collection for every completion logged after the mark.
- So a foreground collection can't stand in for the service's, and **every** collection after the mark must pass step 7's plugin check.

Keep the app closed.
```bash
bash -euo pipefail <<'STEP'
W="$HOME/ai-usage-ac13"; : "${AC13_ID:?export AC13_ID first}"
if pgrep -x AIUsage >/dev/null; then echo "FAIL: quit AI Usage first"; exit 1; fi
AC13_MARK="$W/mark.json" python3 - <<'EOF'
# ac13: mark
import csv, importlib.util, json, os, sys
home = os.path.expanduser("~/.ai-usage")
sys.path.insert(0, home)
spec = importlib.util.spec_from_file_location("installed_collector", os.path.join(home, "collector.py"))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
usage, log = c.configured_paths(c.load_config(c.Path(home) / "config.json"))
if not usage.exists() or not log.exists():
    sys.exit("FAIL: nothing has been collected yet; wait for the service's first collection, then rerun")
with open(usage, encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle))
info = os.stat(log)
mark = {"usage": str(usage), "log": str(log), "log_offset": info.st_size, "log_inode": info.st_ino,
        "last_transaction": rows[-1]["transaction_id"] if rows else None}
with open(os.environ["AC13_MARK"], "w", encoding="utf-8") as handle:
    json.dump(mark, handle)
print(f"marked: {len(rows)} rows, log at {info.st_size} bytes")
EOF
launchctl kickstart -k "gui/$(id -u)/codes.porta.ai-usage"
AC13_MARK="$W/mark.json" AC13_TIMEOUT_SECONDS=300 python3 - <<'EOF'
# ac13: service
import csv, json, os, sys, time
pid = os.environ["AC13_ID"]
with open(os.environ["AC13_MARK"], encoding="utf-8") as handle:
    mark = json.load(handle)
deadline = time.monotonic() + float(os.environ.get("AC13_TIMEOUT_SECONDS", "300"))
def since_mark(path, inode):
    """The log's lines after the mark: the rest of the marked file, then any newer one."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as handle:
        handle.seek(mark["log_offset"] if os.fstat(handle.fileno()).st_ino == inode else 0)
        return handle.read().decode("utf-8", "replace").splitlines()
def new_collections(rows):
    """Each collection appended after the mark, with its rows."""
    ids = [r["transaction_id"] for r in rows]
    if mark["last_transaction"] is not None and mark["last_transaction"] not in ids:
        sys.exit("FAIL: the marked collection is no longer in usage.csv")
    start = max((i for i, t in enumerate(ids) if t == mark["last_transaction"]), default=-1) + 1
    new = {}
    for r in rows[start:]:
        new.setdefault(r["transaction_id"], []).append(r)
    return new
def check(transaction, found):
    mine = [r for r in found if r["provider"] == pid]
    bad = sorted({f"{r['category']}/{r['metric']}: {r['status']}" for r in mine
                  if r["status"] in ("error", "missing", "disabled")})
    if bad:
        sys.exit(f"FAIL: collection {transaction}: {pid} wrote {bad}")
    if not any(r["category"] == "availability" and r["metric"] == "detected" and r["value"] == "1"
               and r["source"] == "plugin" and r["status"] == "ok" for r in mine):
        sys.exit(f"FAIL: collection {transaction} has no successful run of {pid}")
waiting = "no service collection was logged"
while True:
    log = mark["log"]
    lines = since_mark(log, mark["log_inode"])
    try:
        rotated = os.stat(log).st_ino != mark["log_inode"]
    except FileNotFoundError:  # mid-rotation: the new log isn't there yet
        rotated = True
    if rotated:
        lines = since_mark(log + ".1", mark["log_inode"]) + lines
    service = sum("collection completed" in line and "one-shot" not in line for line in lines)
    foreground = sum("one-shot collection completed" in line for line in lines)
    if service:
        with open(mark["usage"], encoding="utf-8", newline="") as handle:
            new = new_collections(list(csv.DictReader(handle)))
        for transaction, found in new.items():
            check(transaction, found)
        if len(new) >= service + foreground:
            print(f"PASS: the service collected {pid}; {len(new)} new collection(s), each with a successful run of it")
            break
        waiting = (f"the log has {service} service and {foreground} foreground collection(s), "
                   f"but usage.csv has {len(new)} new")
    if time.monotonic() > deadline:
        sys.exit(f"FAIL: {waiting} within the timeout; see `collector.py service-status` and collector.log")
    time.sleep(5)
EOF
STEP
```

## 9. The report matches the manifest
Run from the clone. This checks that the plugin ran and is reported as it should be. It compares no numbers; step 10 does that.
- **Collector:** 2.4.0 or newer, with local plugins supported, and no plugin error for `plugins` or `plugins/<id>`.
- **The plugin:** `external`, enabled, setup `ready`, and every declared source `ok`.
- **Usage, by the manifest's `report.usage`:**
  - `daily_total` must be `current`;
  - `events` may be `current`, or `none` while no events have been reported;
  - `none` must be `none`.
- **Quota, by the manifest's `report.quota`:**
  - `none` must be `unsupported`;
  - `used` or `remaining` must be `current`.
```bash
bash -euo pipefail <<'STEP'
: "${AC13_ID:?export AC13_ID first}"
cd "$HOME/ai-usage-ac13/src"
python3 - <<'EOF'
# ac13: report
import json, os, subprocess, sys
pid = os.environ["AC13_ID"]
home = os.path.expanduser("~/.ai-usage")
manifest = os.path.join(home, "plugins", pid, "plugin.json")
if not os.path.exists(manifest):
    sys.exit(f"FAIL: there is no {manifest}")
with open(manifest, encoding="utf-8") as handle:
    profile = json.load(handle)["report"]
out = subprocess.run([sys.executable, "ai_usage_report.py", "--config", os.path.join(home, "config.json")],
                     capture_output=True, text=True, check=True).stdout
report = json.loads(out)
collector = report["collector"]
problems = []
version = collector.get("installed_version") or "none"
try:
    supported = tuple(int(part) for part in version.split(".")) >= (2, 4, 0)
except ValueError:
    supported = False
if not supported:
    problems.append(f"the installed collector is {version}, not 2.4.0 or newer")
if collector["capabilities"].get("external_plugins") is not True:
    problems.append("the installed collector doesn't support local plugins")
plugin_errors = collector.get("plugin_errors") or {}
problems += [f"{key}: {plugin_errors[key]}" for key in ("plugins", f"plugins/{pid}") if key in plugin_errors]
p = next((p for p in report["providers"] if p["id"] == pid), None)
if p is None:
    sys.exit(f"FAIL: {pid} is not in the report")
if p.get("external") is not True:
    problems.append("not reported as a local plugin")
if p["enabled"] is not True:
    problems.append("not enabled")
if p["setup"] != "ready":
    problems.append(f"setup {p['setup']}: {p['setup_note']}")
problems += [f"source {s['id']} {s['status']}" + (f": {s['error']}" if s["error"] else "")
             for s in p["sources"] if s["status"] != "ok"]
# What each profile can show: event usage with no events yet is "none"; no quota is "unsupported".
usage_ok = {"daily_total": ("current",), "events": ("current", "none"), "none": ("none",)}[profile["usage"]]
quota_ok = ("unsupported",) if profile["quota"] == "none" else ("current",)
if p["usage"]["status"] not in usage_ok:
    problems.append(f"usage is {p['usage']['status']}; usage {profile['usage']} needs {' or '.join(usage_ok)}")
if p["quota"]["status"] not in quota_ok:
    problems.append(f"quota is {p['quota']['status']}; quota {profile['quota']} needs {' or '.join(quota_ok)}")
if problems:
    sys.exit("FAIL: " + "; ".join(problems))
print(f"PASS: {pid} is reported ready with every source ok; usage {p['usage']['status']} "
      f"({profile['usage']}), quota {p['quota']['status']} ({profile['quota']})")
EOF
STEP
```

## 10. The reported values match what the plugin provided
This is separate from whether the plugin ran: it compares the report with the plugin's own rows in `usage.csv`, and only for what the manifest says the plugin provides.
- **`daily_total`:** each day the report shows, against the plugin's latest total for that date.
- **`events`:** today's input, output and cache totals, against the sum of the plugin's events for today.
- **Quota windows:** each window from the plugin's latest successful run, matched by id (its `scope`, or `<source id>-<scope>` when two sources share a scope). The percent and its basis (`used` or `remaining`) must match.
- **A profile of `none`** is printed as not provided and isn't compared.
```bash
bash -euo pipefail <<'STEP'
: "${AC13_ID:?export AC13_ID first}"
cd "$HOME/ai-usage-ac13/src"
AC13_MARK="$HOME/ai-usage-ac13/mark.json" python3 - <<'EOF'
# ac13: values
import csv, datetime, json, os, subprocess, sys
pid = os.environ["AC13_ID"]
home = os.path.expanduser("~/.ai-usage")
manifest = os.path.join(home, "plugins", pid, "plugin.json")
if not os.path.exists(manifest):
    sys.exit(f"FAIL: there is no {manifest}")
with open(manifest, encoding="utf-8") as handle:
    profile = json.load(handle)["report"]
with open(os.environ["AC13_MARK"], encoding="utf-8") as handle:
    usage_csv = json.load(handle)["usage"]
with open(usage_csv, encoding="utf-8", newline="") as handle:
    rows = [r for r in csv.DictReader(handle) if r["provider"] == pid]
out = subprocess.run([sys.executable, "ai_usage_report.py", "--config", os.path.join(home, "config.json")],
                     capture_output=True, text=True, check=True).stdout
p = next(p for p in json.loads(out)["providers"] if p["id"] == pid)
runs = [r["transaction_id"] for r in rows if r["category"] == "availability" and r["metric"] == "detected"
        and r["value"] == "1" and r["source"] == "plugin" and r["status"] == "ok"]
if not runs:
    sys.exit(f"FAIL: usage.csv has no successful run of {pid}")
latest = [r for r in rows if r["transaction_id"] == runs[-1]]
lines, mismatches = [], []
def compare(name, provided, shown):
    lines.append(f"{name}: plugin {provided:g}, report {shown}")
    if shown is None or abs(provided - float(shown)) > 1e-6:
        mismatches.append(name)
if profile["usage"] == "daily_total":
    totals = {}
    for r in rows:  # a later total for the same date replaces the earlier one
        if r["record_kind"] == "period_total" and r["metric"] == "daily_tokens":
            totals[r["scope"]] = float(r["value"])
    shown = [day for day in p["days"] if day["date"] in totals]
    for day in shown:
        compare(f"daily total {day['date']}", totals[day["date"]], day["total"])
    if not shown:
        lines.append("daily totals: none of the report's days has a total from the plugin")
elif profile["usage"] == "events":
    fields = {"input_tokens": "input", "output_tokens": "output",
              "cache_read_input_tokens": "cache_read", "cache_creation_input_tokens": "cache_write"}
    today, sums = datetime.date.today(), {}
    for r in rows:
        if r["record_kind"] == "event_total" and r["metric"] in fields:
            at = datetime.datetime.fromisoformat(r["collected_at"].replace("Z", "+00:00"))
            if at.astimezone().date() == today:
                sums[fields[r["metric"]]] = sums.get(fields[r["metric"]], 0.0) + float(r["value"])
    today_shown = p["today"] or {}
    for field in ("input", "output", "cache_read", "cache_write"):
        if field in sums or today_shown.get(field):
            compare(f"today's {field} events", sums.get(field, 0.0), today_shown.get(field) or 0)
    if not sums:
        lines.append("events: none today")
else:
    lines.append("usage: not provided (usage none)")
if profile["quota"] != "none":
    source_ids = {s["csv_source"]: s["id"] for s in profile["sources"]}
    readings = {(r["source"], r["scope"]): float(r["value"]) for r in latest if r["category"] == "quota"}
    scopes = [scope for _, scope in readings]
    windows = {w["id"]: w for w in p["quota"]["windows"] if not w["retired"]}
    for (source, scope), value in readings.items():
        wid = (f"{source_ids[source]}-{scope}" if scopes.count(scope) > 1 else scope) or "default"
        window = windows.get(wid)
        compare(f"quota {wid} ({profile['quota']})", round(max(0.0, min(100.0, value)), 1),
                window["percent"] if window else None)
        if window is not None:
            lines[-1] += f", basis {window['basis']}, resets {window['resets_at']}"
            if window["basis"] != profile["quota"]:
                mismatches.append(f"quota {wid} basis")
    if not readings:
        lines.append("quota: the latest run reported no windows")
else:
    lines.append("quota: not provided (quota none)")
print("\n".join(lines))
if mismatches:
    sys.exit(f"FAIL: the report differs from the plugin's rows for {mismatches}")
print(f"PASS: the report shows the values {pid} provided")
EOF
STEP
```
**Then compare the printed values with the work tool's own figures** for the same moment, by your own means. They must agree, allowing for usage since the plugin's last run.

## 11. Shown in the app
```bash
open "$HOME/ai-usage-ac13/src/macos/build/AI Usage.app"
```
**Pass:**
- the overview has the plugin's card, with step 10's values;
- Settings lists the plugin, with its switch on.

## Result
**AC-13 passes** only if all of these hold:
- every block of steps 1, 2 and 4 to 10 ends in `PASS`, and step 3's blocks also do for an upgrade;
- the values agree with the work tool;
- step 11 shows the plugin.

Otherwise it fails. Send back only `AC-13: pass` or `AC-13: fail`.

## Turning it off, and rollback
- **Turn the plugin off:** `~/.ai-usage/collector.py plugin disable "$AC13_ID"`. Its history is kept.
- **Fresh install, remove it:** quit the app, then from the clone run `python3 ai_usage_service.py uninstall`. This removes the service and keeps config and data.
- **Upgrade, return to the previous collector:** see the next section.

### Upgrade rollback
The rollback uses the installers' own ownership records. It doesn't copy program files back:
- 2.4.0's `uninstall` removes what its record lists. That includes files the upgrade added, such as `ai_usage_plugins/external.py`, and the integration settings it added.
  - It removes a setting only while the setting still has the installer's value, and leaves the rest of each settings file alone.
  - It keeps config, data, the plugins folder and any file that changed since it was installed.
- The previous collector's own `install`, run from the backup, puts its files back and writes a matching record. It sets up integrations as it would on a new install, so an integration the upgrade added stays only if that version adds it too.

To roll back:
1. Run step 3's stop block.
2. Run the two blocks below.
3. Run step 4's start block, then open the app you used before.

The first block saves a locked copy of the current config, state and history, which are newer than the backup, with 2.4.0's ownership record, in `$backup/at-rollback-<time>`. It then uninstalls 2.4.0 and checks that nothing 2.4.0 installed is left. If it lists a file that uninstall kept because it changed, move that file out of the way, then run the second block.
```bash
bash -euo pipefail <<'STEP'
# ac13: rollback
W="$HOME/ai-usage-ac13"
[ -s "$W/backup-path" ] || { echo "FAIL: there is no backup from step 3"; exit 1; }
backup="$(cat "$W/backup-path")"
for name in collector.py install-ownership.json collector-version.txt config.json state.json usage.csv; do
  [ -f "$backup/$name" ] || { echo "FAIL: the backup has no $name"; exit 1; }
done
[ "$("$HOME/.ai-usage/collector.py" version 2>/dev/null)" = 2.4.0 ] || { echo "FAIL: the installed collector is not 2.4.0"; exit 1; }
if pgrep -x AIUsage >/dev/null || pgrep -f "$HOME/.ai-usage/collector.py" >/dev/null; then echo "FAIL: AI Usage or a collection is running; run step 3's stop block"; exit 1; fi
"$HOME/.ai-usage/collector.py" service-status | python3 -c 'import json, sys; s = json.load(sys.stdin); sys.exit(0 if s["state"] != "running" else "FAIL: the service is running; run the stop block from step 3")'
rollback="$backup/at-rollback-$(date +%Y%m%d%H%M%S)"
AC13_SNAPSHOT="$rollback" python3 - <<'EOF'
# ac13: snapshot
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
target = os.environ["AC13_SNAPSHOT"]
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
cp -p "$HOME/.ai-usage/install-ownership.json" "$rollback/"
echo "$rollback" > "$W/rollback-path"
"$HOME/.ai-usage/collector.py" uninstall
AC13_RECORD="$rollback/install-ownership.json" python3 - <<'EOF'
# ac13: removed
import json, os, sys
with open(os.environ["AC13_RECORD"], encoding="utf-8") as handle:
    files = [entry for entry in json.load(handle)["files"].values() if entry["owned"]]
kept = [entry["path"] for entry in files if os.path.lexists(entry["path"])]
if kept:
    sys.exit("FAIL: uninstall kept these, because they changed after 2.4.0 installed them. Move each one out "
             "of the way, then run the second rollback block:\n" + "\n".join(f"  {path}" for path in kept))
print(f"removed: all {len(files)} files 2.4.0 installed")
EOF
echo "PASS: 2.4.0 is uninstalled; the newer data is kept in $rollback"
STEP
```
The second block puts back the config, state and history from the backup, under the previous collector's locks. It then installs the previous collector from the backup with that version's own installer, and checks:
- the installed code is the backup's, byte for byte, with no other modules;
- the version, config, state and history match the backup;
- the new ownership record matches the files on disk;
- everything managed before the upgrade is managed again. Two exceptions are noted, not failed:
  - an integration setting you changed after the upgrade keeps your value and is no longer managed;
  - an integration whose tool is gone isn't set up again.
```bash
bash -euo pipefail <<'STEP'
# ac13: previous
W="$HOME/ai-usage-ac13"
[ -s "$W/backup-path" ] && [ -s "$W/rollback-path" ] || { echo "FAIL: run the first rollback block first"; exit 1; }
backup="$(cat "$W/backup-path")"; rollback="$(cat "$W/rollback-path")"
[ ! -e "$HOME/.ai-usage/install-ownership.json" ] || { echo "FAIL: a collector is installed; run the first rollback block first"; exit 1; }
if pgrep -x AIUsage >/dev/null || pgrep -f "$HOME/.ai-usage/collector.py" >/dev/null; then echo "FAIL: AI Usage or a collection is running; run step 3's stop block"; exit 1; fi
AC13_RECORD="$rollback/install-ownership.json" python3 - <<'EOF'
# ac13: removed
import json, os, sys
with open(os.environ["AC13_RECORD"], encoding="utf-8") as handle:
    files = [entry for entry in json.load(handle)["files"].values() if entry["owned"]]
kept = [entry["path"] for entry in files if os.path.lexists(entry["path"])]
if kept:
    sys.exit("FAIL: uninstall kept these, because they changed after 2.4.0 installed them. Move each one out "
             "of the way, then run the second rollback block:\n" + "\n".join(f"  {path}" for path in kept))
print(f"removed: all {len(files)} files 2.4.0 installed")
EOF
AC13_RESTORE="$backup" python3 - <<'EOF'
# ac13: restore
import importlib.util, os, shutil, sys
backup = os.environ["AC13_RESTORE"]
sys.path.insert(0, backup)
spec = importlib.util.spec_from_file_location("previous_collector", os.path.join(backup, "collector.py"))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
config_path = c.Path(os.path.expanduser("~/.ai-usage")) / "config.json"
config = c.load_config(c.Path(backup) / "config.json")  # the settings from before the upgrade
usage, _ = c.configured_paths(config)
state = c.state_path_for(config)
with c.state_transaction_lock(state):  # the previous collector's own locks
    if c.pending_state_path(state).exists():
        sys.exit("FAIL: a pending transaction journal is present")
    with c.exclusive_file_lock(usage.with_name(f"{usage.name}.lock")):
        for path in (config_path, state, usage):
            target = os.path.realpath(path)  # a symlinked file stays linked
            staged = f"{target}.ac13-restore"
            shutil.copy2(os.path.join(backup, path.name), staged)
            os.replace(staged, target)
print(f"restored: config, state and history from {backup}")
EOF
python3 "$backup/collector.py" install --no-start
cmp "$backup/collector.py" "$HOME/.ai-usage/collector.py" || { echo "FAIL: collector.py is not the backup's"; exit 1; }
if [ -d "$backup/ai_usage_plugins" ]; then
  diff -r -x __pycache__ "$backup/ai_usage_plugins" "$HOME/.ai-usage/ai_usage_plugins" || { echo "FAIL: ai_usage_plugins is not the backup's"; exit 1; }
elif [ -e "$HOME/.ai-usage/ai_usage_plugins" ]; then
  echo "FAIL: the previous collector had no ai_usage_plugins folder"; exit 1
fi
[ "$("$HOME/.ai-usage/collector.py" version)" = "$(cat "$backup/collector-version.txt")" ] || { echo "FAIL: the installed version is not the backup's"; exit 1; }
for name in config.json state.json usage.csv; do cmp "$backup/$name" "$HOME/.ai-usage/$name" || { echo "FAIL: $name is not the backup's"; exit 1; }; done
AC13_BACKUP="$backup" python3 - <<'EOF'
# ac13: managed
import hashlib, json, os, stat, sys
home = os.path.expanduser("~/.ai-usage")
def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
def matches(entry):
    path = entry["path"]
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return digest == entry["sha256"] and stat.S_IMODE(os.stat(path).st_mode) == entry["mode"]
now = load(os.path.join(home, "install-ownership.json"))
before = load(os.path.join(os.environ["AC13_BACKUP"], "install-ownership.json"))
owned = {key: entry for key, entry in now["files"].items() if entry["owned"]}
problems = [f"{entry['path']} does not match the ownership record" for entry in owned.values() if not matches(entry)]
package = os.path.join(home, "ai_usage_plugins")
listed = {entry["path"] for entry in owned.values()}
for name in sorted(os.listdir(package)) if os.path.isdir(package) else []:
    if name.endswith(".py") and os.path.join(package, name) not in listed:
        problems.append(f"{os.path.join(package, name)} is not a file of the previous collector")
# What was managed before the upgrade is managed again: the same code and LaunchAgent path
# (the LaunchAgent is written afresh), and every integration setting still in place.
for key, entry in before["files"].items():
    current = owned.get(key)
    if entry["owned"] and not key.endswith("_wrapper") and not (
            current and current["path"] == entry["path"] and (key == "plist" or current["sha256"] == entry["sha256"])):
        problems.append(f"{entry['path']} is not managed as it was before the upgrade")
for name, entry in before["integrations"].items():
    current = now["integrations"].get(name, {})
    if not entry["owned"] or (current.get("owned") and current.get("value") == entry["value"]):
        continue
    settings = load(entry["settings_path"]) if os.path.isfile(entry["settings_path"]) else {}
    if settings.get(entry["field"]) == entry["value"]:
        problems.append(f"the {name} integration is in {entry['settings_path']}, but nothing manages it")
    elif entry["field"] in settings:
        print(f"kept: your own {entry['field']} in {entry['settings_path']}, which is no longer managed")
    else:
        print(f"note: the {name} integration wasn't set up again; its tool may be gone")
attached = {name: entry for name, entry in now["integrations"].items() if entry["owned"] and entry["attached"]}
for name, entry in attached.items():
    if load(entry["settings_path"]).get(entry["field"]) != entry["value"]:
        problems.append(f"the {name} integration is missing from {entry['settings_path']}")
if problems:
    sys.exit("FAIL: the installation doesn't match its ownership record:\n" + "\n".join(f"  {p}" for p in problems))
print(f"managed: {len(owned)} files and {len(attached)} integrations match the ownership record, "
      "and everything managed before the upgrade is managed again")
EOF
echo "PASS: collector $(cat "$backup/collector-version.txt") is installed again from $backup; now run step 4's start block"
STEP
```
