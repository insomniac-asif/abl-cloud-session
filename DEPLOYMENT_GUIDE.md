# ABL Deployment Guide: everything from PRs #1, #2 and #3

**Audience:** the local Claude Code session (or Asif) with shell access to **abl1** (`rescue@192.168.1.208`)
and the **desktop** (192.168.1.204 / Windows + WSL).
**Rule for every step:** run it, run its **Verify**, and only move on when Verify passes.
Each step lists its **Rollback**. Nothing here touches `abl-llama-27b.service`, `~/.dsh/.credentials.yaml`,
`node_modules/@deepseek-ai/`, or the Odysseus DB schema.

| Where | What gets deployed |
|---|---|
| **abl1** | `src/autoresponder.py`, `src/abl_settings.py`, `src/alert_rules.py`, `src/morning_briefing_abl1.py`, `src/finance_voice_abl1.py`, `routes/autoresponder_routes.py`, the `dsh-abl-settings-r1` plugin, the bug fixes, the systemd timers, ufw |
| **desktop (WSL)** | `src/voice_bridge.py` and the `ears.py` patch (`~/ops/speaker/`), the SSH tunnel, the `pm2 delete` cleanup |
| **desktop (Windows)** | 35B llama-server bind |

Order: **0 → 1 → 2** are safety-critical. After that, the steps are independent and can be done one per sitting.

---

## Step 0: Get the code onto abl1 and take a snapshot

```bash
ssh rescue@192.168.1.208
git clone https://github.com/insomniac-asif/abl-cloud-session ~/abl-cloud-session 2>/dev/null \
  || git -C ~/abl-cloud-session pull --ff-only
cd ~/abl-cloud-session && git log --oneline -1

# Snapshot everything this guide can touch (one tarball, about 1 minute)
SNAP=~/abl/backups/pre-cloudcc-$(date +%Y%m%d-%H%M); mkdir -p "$SNAP"
tar -czf "$SNAP/odysseus-code.tgz" -C ~/abl/odysseus src routes tests app.py config 2>/dev/null
tar -czf "$SNAP/dsh-plugins.tgz"   -C ~/abl/tools/dsh/app local-plugins 2>/dev/null
tar -czf "$SNAP/systemd-user.tgz"  -C ~/.config systemd 2>/dev/null
cp ~/abl/tools/dsh/app/package.json "$SNAP/dsh-profile-package.json" 2>/dev/null
pm2 save 2>/dev/null; cp ~/.pm2/dump.pm2 "$SNAP/" 2>/dev/null
ls -la "$SNAP"
```
**Verify:** the three `.tgz` files are non-empty.
**Rollback for anything below:** restore from `$SNAP`, or use the per-step rollback.

### 0b. Orientation docs (CLAUDE.md + PROJECT_CONTEXT.md)
The existing `~/abl/CLAUDE.md` is the v0.1–v0.16 sprint log, written for the old WSL desktop. Keep it as history, don't overwrite it blindly:
```bash
cd ~/abl && mkdir -p docs
git mv CLAUDE.md docs/CLAUDE_HISTORY_v0.16.md 2>/dev/null || mv CLAUDE.md docs/CLAUDE_HISTORY_v0.16.md
cp ~/abl-cloud-session/abl1/CLAUDE.md ~/abl/CLAUDE.md
[ -f PROJECT_CONTEXT.md ] && cp PROJECT_CONTEXT.md PROJECT_CONTEXT.md.bak-cloudcc-20261103
cat ~/abl-cloud-session/abl1/PROJECT_CONTEXT.md >> ~/abl/PROJECT_CONTEXT.md     # append: it's a living log
```
**Verify:** a fresh `claude` session in `~/abl` starts with the abl1 orientation (§1 commands).

---

## Step 1: Desktop hygiene (do this FIRST)

### 1a. Stop the desktop gamemode watcher from resurrecting the old Odysseus
`gamemode_watch._reconcile()` runs `pm2 start` on any of these four every **10 s** if they're merely *stopped*.
That brings back the duplicate desktop Odysseus (750 MB, and it holds `:7800`) and possibly a second fleet registry.
Deleting them from pm2 makes the reconciler ignore them; gamemode's GPU eviction for games keeps working.
```bash
# on the DESKTOP (WSL)
pm2 ls | grep -E 'abl-backend|abl-watch|abl-fleet-registry|abl-node-health|gamemode|abl-self-train'
pm2 delete abl-backend abl-watch abl-fleet-registry abl-node-health
pm2 delete abl-self-train 2>/dev/null     # nightly cron that would train against a dead backend
pm2 save
```
**Verify (wait 60 s, then):**
```bash
pm2 ls | grep -cE 'abl-backend|abl-watch|abl-fleet-registry|abl-node-health'   # → 0
ss -ltnp | grep ':7800'                                                      # → nothing on the desktop
```
**Rollback:** `pm2 resurrect` (from the pre-change `pm2 save`) or `pm2 start <ecosystem file>`.

### 1b. Bind the Windows 35B llama-server to localhost (security)
Edit whatever launches it (`.bat`, scheduled task or shortcut): `--host 0.0.0.0` → `--host 127.0.0.1`. Then restart it.
**Verify (Windows PowerShell):** `netstat -ano | findstr :8095` shows `127.0.0.1:8095` only.
From abl1: `timeout 3 bash -c 'echo > /dev/tcp/192.168.1.204/8095' && echo STILL-OPEN || echo closed` → `closed`.
**Rollback:** revert the flag.

---

## Step 2: Close the LAN side of abl1's `:7800`
`run_abl.sh` binds `0.0.0.0:7800`, which the tailnet voice path needs for now. But that also exposes the backend on the LAN.
```bash
sudo ufw status | head -3                      # if inactive, allow ssh FIRST:
sudo ufw allow OpenSSH
sudo ufw allow in on tailscale0 to any port 7800 proto tcp
sudo ufw allow in on lo to any port 7800 proto tcp
sudo ufw deny 7800/tcp
sudo ufw enable                                # only if it was inactive
sudo ufw status numbered
```
**Verify:** from the desktop, `curl -m 3 http://192.168.1.208:7800/api/health` → timeout, and
`curl -m 3 http://abl1.tailda08f9.ts.net:7800/api/health` → `200`. From abl1: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7800/api/health` → `200`.
**Rollback:** `sudo ufw delete deny 7800/tcp`.

---

## Step 3: Install the abl1 Python modules (autoresponder, settings reader, alert rules, briefing, finance)
```bash
cd ~/abl-cloud-session
bash deploy/deploy_abl1.sh             # DRY RUN: health, 225+ repo tests, what would change
bash deploy/deploy_abl1.sh --apply     # backs up, installs, runs the in-tree tests with Odysseus's own conftest
```
**Verify:**
```bash
cd ~/abl/odysseus
venv/bin/python -m src.autoresponder --self-test        # → self-test: OK
venv/bin/python -m src.autoresponder --status | jq '.mode, .egress_gateway, .model_router, .alert_rules'
#   mode "shadow"; egress_gateway true; alert_rules.module ends with src/alert_rules.py; has_fn true
venv/bin/python -m src.abl_settings | jq '.quiet_now, .effective["alerts.tiers.bills"]'   # → false/true, "YELLOW"
venv/bin/python -m src.model_router --self-test 2>/dev/null || venv/bin/python src/model_router.py --self-test
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7800/api/health        # → 200 (nothing restarted yet)
```
**Rollback:** each installed file has `<file>.bak-cloudcc-20261103` beside it (new files: just delete them).

---

## Step 4: Wire the autoresponder routes (shadow mode, no sends)
```bash
cd ~/abl/odysseus && cp app.py app.py.bak-cloudcc-20261103
grep -n "include_router(setup_abl_routes" app.py            # find the include block
```
Add these two lines right after that `include_router` line (same indentation):
```python
from routes.autoresponder_routes import setup_autoresponder_routes
app.include_router(setup_autoresponder_routes())
```
```bash
venv/bin/python -m py_compile app.py && echo compiles
pm2 restart abl-backend 2>/dev/null || systemctl --user restart abl-backend
sleep 8
```
**Verify:**
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7800/api/health          # 200
curl -s -H "Authorization: Bearer $(cat ~/abl/odysseus/data/abl/dev.token)" \
     http://127.0.0.1:7800/api/abl/autoresponder/status | jq '.mode, .chain, .pending'   # "shadow", [...], 0
curl -s -X POST -H "Authorization: Bearer $(cat ~/abl/odysseus/data/abl/dev.token)" -H 'content-type: application/json' \
     -d '{"message":"Your verification code is 123456","sender":"x","channel":"sms"}' \
     http://127.0.0.1:7800/api/abl/autoresponder/classify | jq '.verdict.tier'          # "RED"
nvidia-smi --query-gpu=memory.used --format=csv; free -h | head -2
```
**Rollback:** `cp app.py.bak-cloudcc-20261103 app.py` and restart the backend.

### 4b. Dashboard endpoint (`GET /api/abl/dashboard`)
Add to `app.py` next to the autoresponder lines:
```python
from routes.dashboard_routes import setup_dashboard_routes
app.include_router(setup_dashboard_routes())
```
Optionally point it at the router's decision log: add `Environment=ABL_ROUTER_LOG=%h/abl/odysseus/data/abl/<router log>.jsonl`
to the backend unit, or leave it unset and the collector auto-discovers `*router*.jsonl` under `data/` and `logs/`.
**Verify:** `curl -s -H "Authorization: Bearer $(cat ~/abl/odysseus/data/abl/dev.token)" http://127.0.0.1:7800/api/abl/dashboard | jq '.status, .collect_ms, (.services|length), .router.status'`
→ a status, under ~2000 ms, 16 services, and the router `ok` (or `no-log` with a hint).

---

## Step 5: Settings panel plugin (`dsh-abl-settings-r1`)
```bash
ls ~/abl/tools/dsh/app/local-plugins/dsh-abl-console-r1/      # how is the console installed? mirror it
cp -r ~/abl-cloud-session/plugins/dsh-abl-settings-r1 ~/abl/tools/dsh/app/local-plugins/
cd ~/abl/tools/dsh/app
dsh plugin --profile web add ./local-plugins/dsh-abl-settings-r1
dsh --profile web --dump-config | grep -B1 -A10 'dsh-abl-settings-r1'   # the layer appears (no boot)
```
If the console is enabled by a path `insert` row in the profile's `cordis.patch.yml` instead, add the same kind of row:
`- insert: [{id: abl-settings, name: /home/rescue/abl/tools/dsh/app/local-plugins/dsh-abl-settings-r1/index.mjs}]`.
**Test before restarting dsh-web (boundary):** `node --test ~/abl/tools/dsh/app/local-plugins/dsh-abl-settings-r1/tests/server.test.mjs` → all pass.
```bash
systemctl --user restart abl-dsh-web
journalctl --user -u abl-dsh-web -n 120 --no-pager | grep -iE 'abl-settings|plugin.*(fail|error)'   # expect nothing bad
```
**Verify:** open ABL on the phone. The **Settings** gear is at the bottom of the sidebar. Open Quiet hours, change *End* to 08:30, then Save. You should see the toast with Undo, and **Recent changes** lists the edit. Then:
```bash
jq '.version, .values' ~/abl/odysseus/data/abl/settings.json
cd ~/abl/odysseus && venv/bin/python -c "from src import abl_settings as s; print(s.get('quiet.end'))"    # 08:30 within 5 s
```
Undo it in the UI (or set it back). **Rollback:** `dsh plugin --profile web remove dsh-abl-settings-r1`, restart dsh-web,
and optionally `mv settings.json settings.json.off` (everything reverts to the defaults, which equal the old behaviour).

### 5b. Console r2 (replaces r1 after you've checked it)
```bash
cp -r ~/abl-cloud-session/plugins/dsh-abl-console-r2 ~/abl/tools/dsh/app/local-plugins/
cd ~/abl/tools/dsh/app && dsh plugin --profile web add ./local-plugins/dsh-abl-console-r2
node --test local-plugins/dsh-abl-console-r2/tests/server.test.mjs
systemctl --user restart abl-dsh-web
```
**Verify:** sidebar → **Console**. All 5 tabs show cards with an "updated Xs ago" stamp. Stop Ollama briefly: the Models
card says "Service unreachable" with the reason, and recovers within 30 s. Then `dsh plugin --profile web remove dsh-abl-console-r1`.
**Rollback:** remove r2; r1 is untouched until then.

---

## Step 6: Bug fixes (one at a time; each is dry-run first)
All scripts are in `~/abl-cloud-session/fixes/`, back up to `*.bak-cloudcc-20261103`, and never restart dsh-web.

**6a. Fleet registry desktop IP.** Do Step 1a first: a desktop-side registry may be the thing re-publishing the old IP.
```bash
python3 fixes/fix_fleet_ip.py                                   # dry run: what it would rewrite / SQLite hints
systemctl --user list-units --type=service | grep -i fleet       # unit name
python3 fixes/fix_fleet_ip.py --apply --restart-unit <fleet-unit>.service
```
Verify: `curl -s http://127.0.0.1:8900/ | grep -c 100.117.30.114` ≥ 1 and `grep -c 100.127.203.4` = 0.

**6b. Errands scanner `/mnt/c` path**
```bash
python3 fixes/fix_errands_path.py && python3 fixes/fix_errands_path.py --apply
```
Verify: `ls -d ~/abl/inbox/errands`; after the next backend restart, `journalctl --user -u abl-backend -n 200 | grep -c /mnt/c` = 0.

**6c. one-project-context stale ready marker**
```bash
python3 fixes/fix_one_project_context.py && python3 fixes/fix_one_project_context.py --apply
systemd-analyze --user verify abl-dsh-web.service
systemctl --user restart abl-dsh-web; sleep 5; systemctl --user restart abl-dsh-web   # the 2nd restart is the real test
journalctl --user -u abl-dsh-web -n 150 --no-pager | grep -i one-project-context     # no failure
```

**6d. market-bridge crash loop**
```bash
python3 fixes/diagnose_unit.py --unit abl-market-bridge --report ~/abl/reports/market_bridge_diag.md
```
Apply the backoff drop-in from BUG_FIXES.md (Bug 4, step 2) immediately, then fix the ranked root cause.
Verify: `systemctl --user show abl-market-bridge -p NRestarts` stays constant for 10 minutes.

**6e. OllamaEmbedder warm-up refused**
```bash
grep -rn embedding_base_url ~/abl/config/ ~/abl/odysseus/config/ 2>/dev/null
# set it to "" (local ollama) as shown in BUG_FIXES B5, then:
ollama list | grep qwen3-embedding || ollama pull qwen3-embedding:0.6b
```
Verify: after the next backend restart, `journalctl --user -u abl-backend -n 200 | grep -ci "warm.*refused"` = 0.

---

## Step 7: Voice: desktop ears → abl1 (tailnet first, SSH-tunnel fallback)
Full detail is in `patches/EARS_PATCH.md`. In order:
```bash
# abl1: an owner API token for the speaker (_require_owner already accepts owner bearer tokens)
#   create via the existing /tokens route (owner session) and copy the value
# DESKTOP:
mkdir -p ~/.config/abl && chmod 700 ~/.config/abl
printf '%s' '<token>' > ~/.config/abl/token && chmod 600 ~/.config/abl/token
scp rescue@192.168.1.208:~/abl-cloud-session/src/voice_bridge.py ~/ops/speaker/
scp -r rescue@192.168.1.208:~/abl-cloud-session/patches /tmp/abl-patches
python3 /tmp/abl-patches/patch_ears.py ~/ops/speaker/ears.py            # dry run: read the diff + MANUAL lines
python3 /tmp/abl-patches/patch_ears.py ~/ops/speaker/ears.py --apply
# fallback tunnel (keep it running with autossh / a user unit)
ssh -fN -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
    -L 127.0.0.1:7800:127.0.0.1:7800 rescue@192.168.1.208
```
**Verify:**
```bash
python3 ~/ops/speaker/voice_bridge.py --health     # tailnet UP, tunnel UP, active = tailnet, capture true
# restart ears.py, say "what time is it"; on abl1:
journalctl --user -u abl-backend -n 50 --no-pager | grep -i 'voice'
# failover test: block the tailnet target for 30 s and speak again → it still answers via the tunnel
```
**Rollback:** `cp ~/ops/speaker/ears.py.bak-cloudcc-20261103 ~/ops/speaker/ears.py`.
If the desktop Odysseus could ever come back, use the dedicated tunnel port (17800 + `ABL_VOICE_TARGETS`) instead (see EARS_PATCH.md).

---

## Step 8: Morning briefing + spending summary timers (abl1)
```bash
mkdir -p ~/.config/abl ~/.config/systemd/user
cp ~/abl-cloud-session/deploy/systemd/abl1.env.example ~/.config/abl/abl1.env && chmod 600 ~/.config/abl/abl1.env
${EDITOR:-nano} ~/.config/abl/abl1.env          # mail source, home lat/lon, optional calendar/bills/contacts
cp ~/abl-cloud-session/deploy/systemd/abl-{morning-briefing,finance-voice,research-bridge}.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
# dry runs first (no timer yet):
cd ~/abl/odysseus && set -a && . ~/.config/abl/abl1.env && set +a
venv/bin/python -m src.morning_briefing_abl1 --speech
venv/bin/python -m src.finance_voice_abl1 --speech
venv/bin/python -m src.research_bridge_abl1 --dry-run        # what it WOULD speak; changes nothing
systemctl --user enable --now abl-morning-briefing.timer abl-finance-voice.timer abl-research-bridge.timer
```
**Verify:**
```bash
systemctl --user list-timers | grep -E 'abl-(morning|finance|research)'
systemctl --user start abl-morning-briefing.service && journalctl --user -u abl-morning-briefing -n 20 --no-pager
jq '.status, .priority, .sections | map_values(.status)' ~/abl/odysseus/data/abl/briefing/latest.json
jq '.status, .totals' ~/abl/odysseus/data/abl/finance/latest.json
```
Every section should be `ok`, `empty`, or `unavailable` with a reason; `error` means read its message. The desktop speaker reads
`speech[]` from `latest.json`. Until a `/machine/briefing` route exists (spec P1), the desktop can pull it with
`ssh rescue@abl1 cat ~/abl/odysseus/data/abl/briefing/latest.json` from its own scheduler.
**Rollback:** `systemctl --user disable --now abl-morning-briefing.timer abl-finance-voice.timer abl-research-bridge.timer`.

---

## Step 9: Codex manifest audit
```bash
python3 ~/abl-cloud-session/tools/codex_manifest_audit.py --probe-ports
less ~/abl/reports/codex_todo_$(date +%Y%m%d).md
```
Paste the summary and the TODO table into `CODEX_TASK_AUDIT.md` §4. Anything `DONE_MISSING` goes to the top.

---

## Step 10: Close-out (from the session brief)
```bash
curl -s -o /dev/null -w 'backend %{http_code}\n' http://127.0.0.1:7800/api/health
systemctl --user status abl-dsh-web --no-pager | head -5
curl -s http://127.0.0.1:8085/health
nvidia-smi --query-gpu=memory.used,memory.total --format=csv; free -h
git -C ~/abl/odysseus status --short; git -C ~/abl status --short
cp ~/abl-cloud-session/RECEIPT.md ~/abl/CLOUD_SESSION_RECEIPT_20261103.md
```
Record what you applied (and anything you skipped) at the bottom of that receipt.

---

## Later: going live with the autoresponder (needs Asif)
1. Settings → Alerts: mode **draft**. Watch a day of `pending` holds and `audit.jsonl` in the inbox plugin or the API.
2. Wire a `sender_fn` for **one** channel (none ship yet).
3. Settings → mode **live**, with exactly that channel in *Channels allowed to auto-send* (the panel requires a confirmation).
4. One week later, review the audit log before adding a second channel.

## Rollback summary
| Change | Undo |
|---|---|
| Python modules | `<file>.bak-cloudcc-20261103` or delete new files |
| app.py routes | `cp app.py.bak-cloudcc-20261103 app.py` + restart |
| Settings plugin | `dsh plugin --profile web remove dsh-abl-settings-r1` + restart dsh-web |
| settings.json | `mv` it away → defaults; `settings.json.prev` = previous version |
| Fixes 6a/6b | per-file `.bak-cloudcc-20261103` |
| Fix 6c | `rm ~/.config/systemd/user/abl-dsh-web.service.d/30-one-project-context-ready.conf && systemctl --user daemon-reload` |
| Fix 6d | `rm ~/.config/systemd/user/abl-market-bridge.service.d/20-cloudcc-backoff.conf && systemctl --user daemon-reload` |
| ears.py | `cp ears.py.bak-cloudcc-20261103 ears.py` |
| Timers | `systemctl --user disable --now abl-morning-briefing.timer abl-finance-voice.timer` |
| ufw | `sudo ufw delete deny 7800/tcp` |
| pm2 deletes (desktop) | `pm2 resurrect` |
| Everything | restore the Step 0 snapshot `$SNAP` |
