# `localhost:7800` scan: abl-fused

**Scanned:** `insomniac-asif/abl-fused` @ `5dcc8be` (2026-08-05 snapshot; abl1 is newer, so re-run the command below there).
**Hits:** 243 total. **217 are docs/specs/digests** (historical, no change). **26 are in code**, and only **6 of those are live call sites**.

```bash
# re-run on abl1 and on the desktop against the CURRENT trees:
grep -rInE --exclude-dir={.git,node_modules,vendor,venv,.venv} --exclude='*.min.js' \
  "(localhost|127\.0\.0\.1|0\.0\.0\.0)(:|\"?, ?)7800|port[ =:]+7800" ~/abl-fused ~/abl ~/ops 2>/dev/null \
  | grep -vE '/docs/|\.md:'
```

## The rule that decides each hit

`abl-fused/odysseus` **is** the backend, and it now runs **on abl1**. Code that runs on abl1 and
calls `127.0.0.1:7800` is talking to itself, which is **correct; leave it alone**. Pointing it at the
tailnet name would add a network hop and a DNS dependency for nothing. Only code that runs **on the
desktop** (or anywhere other than abl1) must target abl1.

## Live call sites

| # | File:line | What it is | Runs on | Verdict |
|---|---|---|---|---|
| 1 | `ask_abl.py:27` `HTTPConnection("127.0.0.1", 7800)` | builder CLI: Claude Code consults ABL | wherever CC runs: **desktop** in practice | **CHANGE**: env-configurable, desktop → abl1 |
| 2 | `odysseus/abl_self_train.py:36` `API = "http://127.0.0.1:7800"` | nightly pm2 cron `abl-self-train` | wherever its pm2 lives | **CHANGE IF on the desktop**: it's fail-quiet, so on the desktop it silently trains nothing every night. Better: run it only on abl1 |
| 3 | `odysseus/music_profile.py:32` `REDIRECT_URI = "http://127.0.0.1:7800/api/abl/spotify/callback"` | Spotify OAuth redirect (the browser follows it) | browser on the **desktop/phone** | **KEEP**, but complete auth with the SSH tunnel up (`localhost:7800 → abl1`). Spotify only allows plain-http redirects to loopback; re-registering a tailnet https URL is a P1 follow-up |
| 4 | `odysseus/lifeops_today.py:60` `ODYSSEUS_LOOPBACK` default | in-process self-call | abl1 | **KEEP** (already env-overridable) |
| 5 | `odysseus/needs_you.py:50` `ODYSSEUS_LOOPBACK` default | in-process self-call | abl1 | **KEEP** (already env-overridable) |
| 6 | `odysseus/run_abl.sh:5` `uvicorn … --host 0.0.0.0 --port 7800` | the backend's pm2 entry | abl1 | **KEEP for now, but firewall it** (see below). It's what makes `abl1.tailda08f9.ts.net:7800` reachable for the ears patch, and it *also* exposes the backend on the LAN |

**Comments and strings only (no change):** `gamemode.py:123/188/209`, `gamemode_watch.py:147/183`,
`tutor.py:146`, `selflens.py:7`, `self_history.py:262`, `routes/mobile_routes.py:351`,
`routes/abl_portfolio_routes.py:110`, `routes/selflens_routes.py:5`, `subagent_bridge.py:2`,
`substrate/governance/frontend_lint.py:22/80/256/402`, `core/loopback_host_guard.py:13/69/139`,
`notebooks/gen_colab_t4.py:124`, `static/build_activity.json:185/438`, `static/js/space_bg_3d.js:3`.
A few of these are user-visible text that says "the 3070 GPU" or "desktop" (e.g. `mobile_routes.py:351`).
They're cosmetic, so worth a sweep when the console copy is polished.

## ⚠ The finding that matters more than any URL: desktop gamemode can resurrect the stopped Odysseus

`odysseus/gamemode_watch.py::_reconcile()` (✅ line 137, every `POLL_SECS = 10`) enforces the invariant
**"gamemode off ⇒ every `BACKEND_PROCS` entry is online"** and runs `pm2 start` on any that pm2 reports
stopped. ✅ `gamemode.py:129`:

```python
BACKEND_PROCS = ["abl-watch", "abl-node-health", "abl-fleet-registry", "abl-backend"]
```

So, on the **desktop**: if the duplicate Odysseus was stopped with `pm2 stop abl-backend` (rather
than `pm2 delete`) and `gamemode-watch` is still running, it comes back **within ~10–60 s**, and
`abl-watch` would revive it too. The consequences:
1. The 750 MB saving is undone silently.
2. It grabs **`:7800` on the desktop**. The ears-patch fallback tunnel then can't bind
   (`ExitOnForwardFailure` makes that loud). Without that flag, "fallback" traffic would reach the
   **desktop** brain instead of abl1.
3. A desktop **`abl-fleet-registry`** is in the same list. A second, desktop-side fleet registry is
   a plausible source of the **stale `100.127.203.4` desktop entry** (BUG_FIXES Bug 1). Check
   this before editing files on abl1.

**Check (desktop, 10 s):**
```bash
pm2 ls | grep -E 'abl-backend|abl-watch|abl-fleet-registry|abl-node-health|gamemode'
ss -ltnp | grep ':7800'          # anything here on the DESKTOP is wrong
```
**Fix (desktop):** remove them from pm2 rather than stopping them, so the reconciler can't see them (✅ `_reconcile` only acts on
names pm2 knows: "an unregistered name can't be started"):
```bash
pm2 delete abl-backend abl-watch abl-fleet-registry abl-node-health
pm2 save
```
If the desktop still needs gamemode for GPU eviction (ollama/llama-server for games), it keeps
working: `evict_models()` and the heavy-cron stops don't depend on those four names.
**On abl1:** confirm gamemode isn't running there at all (headless box, no games):
`pm2 ls | grep -i gamemode` should be empty. Its `pkill -f "uvicorn app:app --host 0.0.0.0 --port 7800"`
would take down the real backend if a stray game-detection ever fired.

## Exact changes for the two CHANGE rows

**`ask_abl.py`** (desktop sets `ABL_BASE`; abl1 needs nothing):
```diff
+import os
 import http.client
 ...
-    c = http.client.HTTPConnection("127.0.0.1", 7800, timeout=180)
+    base = urllib.parse.urlparse(os.environ.get("ABL_BASE", "http://127.0.0.1:7800"))
+    conn_cls = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
+    c = conn_cls(base.hostname, base.port or (443 if base.scheme == "https" else 80), timeout=180)
```
```bash
# desktop ~/.bashrc
export ABL_BASE=http://abl1.tailda08f9.ts.net:7800
```
Note that `ask_abl.py` reads `odysseus/data/abl/dev.token`. The desktop copy of that file belongs to the
**stopped desktop** backend. Copy abl1's `dev.token` to the desktop, or abl1 will answer 401.

**`odysseus/abl_self_train.py`** (prefer moving the cron, then the env default as a guard):
```diff
-API = "http://127.0.0.1:7800"
+API = os.environ.get("ODYSSEUS_LOOPBACK", "http://127.0.0.1:7800")   # same knob as lifeops_today/needs_you
```
```bash
# desktop: stop training against a dead backend; abl1: make sure it runs there
pm2 delete abl-self-train && pm2 save                    # on the DESKTOP
pm2 ls | grep abl-self-train                             # on abl1: should be present
```

## `run_abl.sh` 0.0.0.0 → firewall now, rebind in P1

The ears patch's primary target (`abl1.tailda08f9.ts.net:7800`) needs :7800 listening on the
tailnet interface, so loopback-only binding (spec P1) has to wait for the `/machine` path. Until then,
keep `0.0.0.0` but **close the LAN side**:
```bash
sudo ufw allow in on tailscale0 to any port 7800 proto tcp
sudo ufw allow in on lo to any port 7800 proto tcp
sudo ufw deny 7800/tcp                     # LAN (192.168.1.0/24) can no longer reach the backend
sudo ufw status numbered
```
In spec P1: `--host 127.0.0.1`, `tailscale serve` `/machine/` → hub, and
`ABL_VOICE_TARGETS=https://abl.tailda08f9.ts.net/machine,http://127.0.0.1:7800` on the desktop.
That's a config change; no code changes.

## Summary
- **Change:** 2 call sites (`ask_abl.py`; `abl_self_train.py` if it runs on the desktop).
- **Keep:** 4 call sites (abl1 self-calls, the Spotify loopback redirect, the backend bind), plus about 20 comments.
- **Act on first:** the desktop `pm2 delete` of the four `BACKEND_PROCS`. Otherwise gamemode can resurrect the
  duplicate Odysseus, collide with the tunnel port, and possibly keep a stale desktop fleet registry alive.
