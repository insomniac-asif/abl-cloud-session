# abl1 Bug Fixes: exact changes for the local session to apply

**Written:** 2026-11-03 (cloud session, no SSH to abl1). **Applies to:** abl1 (`rescue@192.168.1.208`).
Each fix has: symptom → cause → **script** (dry run first) → **exact manual change** (when
you'd rather not run the script) → verify → rollback.

All scripts live in `fixes/`. They are stdlib-only Python 3, **dry-run by default**, and back up every file
to `<file>.bak-cloudcc-20261103` before writing (an existing backup is never
overwritten). They validate their own edits (JSON re-parse, `py_compile`) and restore the
backup on failure. They refuse to touch `~/.dsh/.credentials.yaml` and
`node_modules/@deepseek-ai/`. 27 tests: `python3 -m pytest fixes/tests -q`.

```bash
cd ~/abl-cloud-session          # wherever this repo is cloned on abl1
python3 -m pytest fixes/tests -q   # 27 passed
```

> **Confidence key.** ✅ cause verified in code. 🔎 cause is the most likely one, given the code snapshot plus PROMPT.md; confirm on the box. ❓ unknown until the diagnostic runs.

---

## Bug 1: Fleet registry has the desktop at the old tailnet IP

**Symptom:** fleet registry (:8900) lists the desktop at `100.127.203.4`; it is now `100.117.30.114`.
**Cause:** ❓ where the registry persists nodes (JSON / YAML / SQLite / memory) wasn't visible from the cloud.

### Script
```bash
python3 fixes/fix_fleet_ip.py                       # dry run: lists every hit, grouped
# find the registry's unit name first:
systemctl --user list-units --type=service | grep -i fleet
python3 fixes/fix_fleet_ip.py --apply --restart-unit <fleet-unit>.service
```
What it does, in order:
- Scans `~/abl ~/abl-fused ~/.config/abl ~/.abl ~/fleet ~/abl/fleet ~/.config/systemd/user` for the **whole token** `100.127.203.4` (so `100.127.203.40` is not a match).
- **Rewrites** config/data files only (`.json .yaml .yml .toml .env .ini .conf .cfg .service .txt .jsonl`).
- **Reports but does not rewrite** code, tests and docs. That's deliberate: `core/egress_gateway.py` ✅ and two tests ✅ use `100.127.203.4` as a correct CGNAT example, so changing them would be wrong. `shorts_pipeline.py:38` ✅ has the old IP in a hint string; fix that one by hand if it still exists.
- For SQLite files containing the IP, it prints a ready-made `UPDATE … REPLACE(…)` (data only, no schema change) for you to run with the registry stopped.
- `--restart-unit` stops the registry **before** the edit and starts it **after**. That ordering matters: a registry that saves in-memory state on shutdown would otherwise write the old IP straight back.
- After `--apply`, it GETs `http://127.0.0.1:8900/` and reports whether the new IP is present and the old one gone.

### Manual equivalent
```bash
grep -rIln --include='*.json' --include='*.y*ml' --include='*.toml' --include='*.env' \
  --exclude-dir=tests --exclude-dir=node_modules --exclude-dir=.git '100\.127\.203\.4\b' ~/abl ~/abl-fused
# for each file listed (registry stopped):
cp FILE FILE.bak-cloudcc-20261103
sed -i -E 's/(^|[^0-9.])100\.127\.203\.4([^0-9]|$)/\1100.117.30.114\2/g' FILE
python3 -m json.tool FILE >/dev/null && echo valid       # for .json
```
If nothing on disk matches, the registry keeps state in memory only. Re-register the desktop through the registry's API (or restart its heartbeat agent on the desktop with the new IP).

**Verify:** `curl -s http://127.0.0.1:8900/ | grep -c 100.117.30.114` ≥ 1, and `grep -c 100.127.203.4` = 0.
**Rollback:** `for f in $(find ~/abl ~/abl-fused -name '*.bak-cloudcc-20261103'); do echo "$f"; done`, then copy back the ones for the registry.

---

## Bug 2: Errands scanner looks for `/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands`

**Symptom:** on abl1, every errands scan misses a WSL-only Windows path.
**Cause:** ✅ pattern. The Odysseus snapshot hard-codes `/mnt/c/Users/Asif/...` in many modules (`fileorg.py`, `music_catalog.py`, `inventory_catalog.py`, `photo_catalog.py`, …). The errands scanner itself postdates the snapshot, so the script locates it by content at run time.

### Script
```bash
python3 fixes/fix_errands_path.py           # dry run: file:line, old literal → new expression
python3 fixes/fix_errands_path.py --apply   # rewrites, py_compiles, creates ~/abl/inbox/errands
```
It edits **only Python string tokens** (via `tokenize`), so comments and docstrings that
mention the path are left alone. f-strings and triple-quoted literals are **reported, not
rewritten**. `import os` is added after the docstring and `__future__` imports if it's missing.
Tests are skipped.

### Exact code change (what the script produces)
```diff
+import os  # added by fix_errands_path (cloudcc 2026-11-03)
 from pathlib import Path
-ERRANDS_DIR = Path("/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands")
+ERRANDS_DIR = Path(os.path.join(os.environ.get("ABL_INBOX_DIR", os.path.expanduser("~/abl/inbox")), 'errands'))
```
- abl1: reads `~/abl/inbox/errands` (created by `--apply`).
- WSL desktop: set `ABL_INBOX_DIR=/mnt/c/Users/Asif/Desktop/ABL_INBOX` to keep the exact old behaviour.

To set it for the backend explicitly:
```bash
systemctl --user edit abl-backend.service     # add:
# [Service]
# Environment=ABL_INBOX_DIR=%h/abl/inbox
```

**Verify:** `python3 -m py_compile <file>`; restart the backend (see the testing rule below); `journalctl --user -u abl-backend -n 200 | grep -i errand` shows no `/mnt/c` miss.
**Rollback:** `cp <file>.bak-cloudcc-20261103 <file>`.

---

## Bug 3: `one-project-context-v1` fails on dsh-web restart (stale "ready" marker)

**Symptom:** after `systemctl --user restart abl-dsh-web`, the plugin fails: a "ready" file from the previous run is still there.
**Cause:** 🔎 the plugin writes a ready marker and never removes it on stop.

### Script
```bash
python3 fixes/fix_one_project_context.py            # dry run: plugin dir, markers, source lines that write them
python3 fixes/fix_one_project_context.py --apply    # writes a systemd drop-in + daemon-reload (NO restart)
```
Discovery:
- Files named `*ready*` inside `~/abl/tools/dsh/app/local-plugins/one-project-context*/`.
- `*ready*` files whose path mentions `one-project-context` under `~/.dsh` (credentials excluded), `~/.local/share/dsh`, `~/.cache/dsh`, `$XDG_RUNTIME_DIR` (depth 3) and `/tmp` (depth 2, `pytest-of-*` and `systemd-private-*` skipped).
- Lines in the plugin's own source that write or check a `ready` literal, so you can see the marker's origin even when it isn't on disk right now.

If no marker exists at the moment, reproduce the failure once, or pass `--path /abs/path/ready`.

### Exact change: drop-in file
`~/.config/systemd/user/abl-dsh-web.service.d/30-one-project-context-ready.conf`
```ini
# Added by fix_one_project_context.py (cloud session 2026-11-03).
[Service]
ExecStartPre=-/bin/rm -f /home/rescue/abl/tools/dsh/app/local-plugins/one-project-context-v1/<marker>
ExecStopPost=-/bin/rm -f /home/rescue/abl/tools/dsh/app/local-plugins/one-project-context-v1/<marker>
```
`ExecStartPre` covers crashes and SIGKILL, where `ExecStopPost` of the old process never ran. The leading `-` means a missing file never blocks startup. Paths are validated: absolute, basename contains `ready`, no shell metacharacters.

No plugin fork, no dsh core change. A cleaner long-term fix is to have the plugin unlink its marker in its own stop hook. The script prints the exact source lines to change.

**Verify (dsh-web boundary: test before restarting):**
```bash
systemd-analyze --user verify abl-dsh-web.service
systemctl --user restart abl-dsh-web
journalctl --user -u abl-dsh-web -n 120 --no-pager | grep -iE 'one-project-context|plugin.*(fail|error)'   # expect nothing
systemctl --user restart abl-dsh-web   # second restart is the real test
```
**Rollback:** `rm ~/.config/systemd/user/abl-dsh-web.service.d/30-one-project-context-ready.conf && systemctl --user daemon-reload`.

---

## Bug 4: `abl-market-bridge.service` crash loop (activating / auto-restart)

**Cause:** ❓. The bridge isn't in either code snapshot. 🔎 **Leading hypothesis:** the options engine was stopped on 2026-11-02 to free 6.8 GB 📄. If the bridge connects to the options desk at start-up and exits on `ConnectionRefused`, `Restart=` turns that into exactly this loop. Second candidate: a `/mnt/c/...` path, the same class as Bug 2.

### Step 1: diagnose (read-only)
```bash
python3 fixes/diagnose_unit.py --unit abl-market-bridge --lines 400 --report ~/abl/reports/market_bridge_diag.md
```
It collects `systemctl show` (state, NRestarts, ExecStart, WorkingDirectory, EnvironmentFiles), the unit plus drop-ins, and the journal tail. It checks that ExecStart's paths and WorkingDirectory exist, and lists listeners on every port the logs mention. Then it ranks findings against 17 signatures, each with the exact next command:

| Finding key | Typical fix |
|---|---|
| `conn_refused :809x` | Upstream desk is down (options?). Make the bridge tolerate it (below), or restart the desk if RAM allows |
| `module_missing` | `<venv>/bin/pip install X`, or point ExecStart at the right venv |
| `addr_in_use` | `ss -ltnp \| grep :PORT`; stop the duplicate |
| `wsl_path` | Env override in a drop-in (Bug 2 pattern) |
| `exec_203` / `chdir_200` / `exec_path` / `wd_missing` | Fix the unit's ExecStart / WorkingDirectory |
| `env_missing` | `Environment=`/`EnvironmentFile=` in a drop-in |
| `oom` | `MemoryMax=`; check `free -h` (27B + desks on 16 GB) |
| `clean_exit_loop` | It's a one-shot job: `Type=oneshot` + a `.timer`, not `Restart=always` |

### Step 2: immediate mitigation (safe, reversible, independent of the cause)
Stop the loop from hammering CPU and the journal while you fix the cause:
`~/.config/systemd/user/abl-market-bridge.service.d/20-cloudcc-backoff.conf`
```ini
[Unit]
StartLimitIntervalSec=600
StartLimitBurst=5
[Service]
Restart=on-failure
RestartSec=30
```
```bash
systemctl --user daemon-reload && systemctl --user reset-failed abl-market-bridge
```
Or, if it serves nothing right now: `python3 fixes/diagnose_unit.py --quarantine` (stop + disable; prints the re-enable command). This is never run implicitly.

### Step 3: code fix if the cause is a down desk (pattern)
```python
# before: one unreachable desk kills the whole bridge
for port in DESK_PORTS:
    data[port] = fetch(port)              # raises ConnectionRefusedError -> exit -> restart loop

# after: degrade per desk, keep serving the rest
for port in DESK_PORTS:
    try:
        data[port] = fetch(port)
        down.discard(port)
    except OSError as e:                  # ConnectionRefusedError, timeouts
        data[port] = {"ok": False, "error": type(e).__name__, "stale": cache.get(port)}
        if port not in down:
            log.warning("desk :%s unreachable (%s); continuing without it", port, e)
            down.add(port)
```
And remove the stopped options desk from the bridge's desk list (config), not just from systemd.

**Verify:** `systemctl --user show abl-market-bridge -p NRestarts` stays constant for 10 minutes; `ActiveState=active`.
**Rollback:** delete the drop-in, `daemon-reload`, and `cp <file>.bak-cloudcc-20261103 <file>` for any code change.

---

## Bonus fixes (other known issues from PROMPT.md)

### B5: `OllamaEmbedder` warm-up "Connection refused" on every backend start: 🔎 likely cause found
✅ `config/system.toml` in the snapshot:
```toml
embedding_base_url = "http://192.168.1.160:11434"  # ABL satellite: embeds -> Mac M1
```
✅ `src/abl/memory/embedding.py` `warm_up()` probes the **primary** `embedding_base_url`
first, and only falls back to `base_url` afterwards. On abl1, if the Mac's Ollama is
asleep or not LAN-bound, every start logs `Connection refused` before the fallback.
**Fix (config only):**
```diff
-embedding_base_url = "http://192.168.1.160:11434"  # ABL satellite: embeds -> Mac M1
+embedding_base_url = ""  # abl1: embed on local ollama (base_url); Mac satellite retired for embeds 2026-11-03
```
Also confirm the model exists locally: `ollama list | grep qwen3-embedding || ollama pull qwen3-embedding:0.6b`.
Check that it is the same file on abl1 first: `grep -rn embedding_base_url ~/abl/config/`.

### B6: Desktop 35B llama-server on `0.0.0.0:8095`: SECURITY
Windows launch line (wherever it's started: a `.bat`, scheduled task or shortcut):
```diff
-llama-server.exe ... --host 0.0.0.0 --port 8095
+llama-server.exe ... --host 127.0.0.1 --port 8095
```
Verify on Windows: `netstat -ano | findstr :8095` shows `127.0.0.1:8095` only. If the spec's
`review` role is approved later, bind the tailnet IP `100.117.30.114`, never `0.0.0.0`.

### B7: ears.py posts to the dead `localhost:7800`
Desktop `~/ops/speaker/ears.py`:
```python
# before
ABL_URL = "http://localhost:7800/api/abl/voice/heard"

# after: desktop initiates; target + token from env/config, never hard-coded
import os, pathlib
ABL_URL = os.environ.get("ABL_EARS_URL", "http://100.x.y.z:7800/api/abl/voice/heard")   # abl1 tailnet IP (interim, P0)
_TOKEN_FILE = pathlib.Path(os.environ.get("ABL_TOKEN_FILE", "~/.config/abl/token")).expanduser()
HEADERS = {"Authorization": f"Bearer {_TOKEN_FILE.read_text().strip()}"} if _TOKEN_FILE.exists() else {}
# requests.post(ABL_URL, files={"audio": ...}, headers=HEADERS, timeout=15)
```
P1 target (spec §7): `https://abl.<tailnet>.ts.net/machine/voice/heard`. `/api/abl/voice/heard` calls `_require_owner` ✅, which **already accepts an Asif-owned bearer token** (corrected 2026-11-03; an earlier draft said a token branch was needed). Create one on abl1 (`POST /tokens`, owner session) and put it in `~/.config/abl/token` on the desktop.
**Superseded by the full implementation:** `src/voice_bridge.py` (failover, health, backoff, buffer) + `patches/ears_voice_target.patch`.

---

## Testing rules (from the session brief)
- After any Odysseus change: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7800/api/health` → `200`.
- After any plugin change: restart `abl-dsh-web` and check the journal for plugin failures.
- After model-related changes: `nvidia-smi; free -h`.
- The 27B unit `abl-llama-27b.service` is **not touched** by anything here.
