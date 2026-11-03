# ABL — Cloud Session: Unification Sprint + Autoresponder Build
## Model: Opus | Effort: max

## ORIENT FIRST (mandatory)
You are working on ABL, a triplet AI agent system (Aurora/Borea/Lis) that is being unified into a single professional platform. You have SSH access to `abl1` at 192.168.1.208 (user: rescue) — this is a headless Ubuntu box with a GTX 1070, 16GB RAM, running all ABL services.

Connect and orient:
```bash
ssh rescue@192.168.1.208
cat ~/abl/ABL_REPO_ROADMAP.md
cat ~/abl/DECISIONS.md | head -100
cat ~/abl/AI_HANDOFF_2026-10-08-console-and-pm2.md
cat ~/abl/odysseus/src/model_router.py | head -80
ls ~/abl/tools/dsh/app/local-plugins/
systemctl --user list-units --type=service | grep active
```
Brief me on what you see in 5 lines before starting work.

## CONTEXT
### What exists NOW on abl1 (as of Nov 2, 2026):
- **ABL backend (Odysseus)** on :7800 — FastAPI, v0.9.1, healthy, with model router wired into llm_core.py
- **27B IQ3_XXS model** serving on :8085 via llama-server (systemd unit `abl-llama-27b.service`, -ngl 35, -np 2, 2 parallel slots)
- **3B model** via Ollama on :11434 — being called every ~5.5min by Odysseus fallback (silent cloud failure route)
- **Model router** at `odysseus/src/model_router.py` (557 lines) — classifies requests into PRIMARY (27B), LIGHTWEIGHT (3B), or NONE. Jev-compatible interface ready, no API key set yet. Wired into llm_core.py's egress fallback.
- **dsh (DeepSeek Harness)** on :3081 — the web UI. Has ABL branding (navy #161b30, lavender #cbbbff, amber #f7ad31). Phone layout plugins. Hub panel with task/project/ideation tabs.
- **Console plugin** just installed at `local-plugins/dsh-abl-console-r1/` — 5 tabs (Home, Models & Router, Voice & Autopilot, Fleet, Desks). Server routes at `/api/abl-console/read`. Working but needs polish.
- **Hub API** on :8902 — 9,219 lines, mirrors Odysseus routes via /abl/catalog + /abl/call. Write routes locked pending approval.
- **Market desks** on :8090-8098 (futures, options, polymarket, shorts, crypto, conviction, voledge, memescreen)
- **Fleet registry** on :8900, hub inbox on :8899
- **OLLAMA_LOAD_TIMEOUT=60m** applied as systemd drop-in
- **Options engine stopped** (was using 6.8GB RAM)
- **10 dead plugin folders cleaned** (backed up at ~/abl/backups/dsh-plugins-20261102/)
- **Cookie extended to 365 days** for phone access

### What exists on the desktop PC (192.168.1.204):
- Speaker system at ~/ops/speaker/ (speaker.py :7961, ears.py :7962)
- Voice pipeline: piper TTS + faster-whisper STT + Fifine SC3 mic
- 8 screen-off systems built (autopilot, morning briefing, research bridge, finance voice, chimes, etc.)
- Duplicate Odysseus STOPPED (was wasting 750MB)
- 35B llama-server on Windows at 0.0.0.0:8095 — SECURITY ISSUE, needs binding to 127.0.0.1
- ears.py currently sends voice to localhost:7800 which is now dead — needs rerouting to abl1

### Architecture decisions (from Codex handoff):
- dsh's native session controller (`@deepseek-ai/dsh-api-session-controller`) owns conversations — DO NOT compete with it
- ABL hub/console bundles are ADAPTERS and PROJECTIONS only
- Keep ABL as a version-pinned downstream of dsh with thin adapters — don't fork, don't auto-update
- Use logical model roles (primary/lightweight/review) via shared registry, not blanket model switching
- Pin writes first (safest), then additive imports, then sync — each needs demonstrated rollback
- One HTTPS origin called ABL. :7800 is NOT the permanent interface — everything lives in the dsh-based product
- Desktop speakers initiate authenticated connections TO ABL, not the other way around

### Plugin architecture (how to build dsh plugins):
- package.json declares `dsh.bundle.patch` and `dsh.client`
- Server: exports `{name, inject:['connection',...], apply(ctx)}`, routes via `ctx.connection.fetch.register({path, methods, fetch})`
- Client: `window.__ModuleLoader__.load({id, factory})` with React.createElement UI
- Sidebar panel: `ctx.slots.register({name:'sidebar.panellist', id, order, label}, Icon)` and `ctx.slots.register({name:'main', key:id}, Panel)`
- Routes go through dsh sign-in automatically

### Known bugs on abl1:
- `abl-market-bridge.service` in crash loop (activating/auto-restart)
- `one-project-context-v1` plugin fails on restart (stale "ready" file, never cleaned up)
- OllamaEmbedder warm-up fails with "Connection refused" on every backend start
- Errands scanner looks for `/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands` which doesn't exist on abl1
- Fleet registry has desktop at old tailnet IP (100.127.203.4, should be 100.117.30.114)

## GOALS (in priority order)

### Goal 1: ABL Unification Spec (write the blueprint)
Write `~/abl/ABL_UNIFICATION_SPEC.md` — the master spec for turning dsh into ABL. Cover:
- Single endpoint architecture (which port, what's behind it)
- Service routing map (what proxies where)
- Auth model (tailnet-native, cookie-based, no login wall for owner)
- Plugin migration plan (which Odysseus pages become plugins, in what order)
- Model routing architecture (router → Jev → model tiers)
- Voice integration (how desktop speaker/ears talks to abl1 ABL)
- Data unification (one session store, one memory, one task system)
- Build order (phase 1: read paths, phase 2: bounded writes, phase 3: retire old endpoints)

### Goal 2: Autoresponder with Jev Routing
Build the autoresponder system described in `~/ops/speaker/AUTORESPONDER_SPEC.md` (read it on the desktop at 192.168.1.204 via the SSH tunnel, or find it in the Codex work manifest).
- `jev_classify(message, sender, channel)` function
- 3-tier routing: GREEN (auto-handle), YELLOW (draft + 60s timer), RED (alert only)
- DeepSeek fallback when no TYPESAFE_API_KEY set
- Wire into the existing model router's classification interface
- Alert rules integration (fraud/money/bills/security/food/parcels/people from alert_rules.py)
- Build on abl1 at `~/abl/odysseus/src/autoresponder.py`

### Goal 3: Scan Codex Sessions for Unfinished Work
Read the work manifest at `~/abl-fused/odysseus/data/abl/codex_work_manifest.json` (127 tasks).
- Identify what's done vs. still pending
- Cross-reference against what was actually built (check if files/features exist)
- Produce a prioritized TODO list of what's still worth doing
- Push findings to the coordination API at :7800/build if accessible

### Goal 4: Fix Known Bugs
- Fix the fleet registry desktop IP (100.127.203.4 → 100.117.30.114)
- Fix the errands scanner path for abl1 (remove or redirect /mnt/c/ reference)
- Fix the one-project-context-v1 stale file issue (clean up on stop)
- Investigate and fix abl-market-bridge crash loop

## BOUNDARIES
- Don't modify the dsh core code (anything under node_modules/@deepseek-ai/)
- Don't restart dsh-web without testing the change offline first
- Don't touch ~/.dsh/.credentials.yaml
- Don't send anything to external APIs without checking the egress gateway
- Don't modify the 27B llama-server service — it's stable
- Back up any file before modifying it: `cp file file.bak-cloudcc-20261103`
- Don't change the Odysseus database schema
- Don't stop running services without explicit reason

## TESTING
- After any Odysseus change: verify /api/health returns 200
- After any plugin change: restart dsh-web and check journal for plugin failures
- After router changes: run the model_router.py self-test
- Check GPU/RAM/swap after any model-related change: `nvidia-smi; free -h`

## CLOSE-OUT (before ending session)
- Write `~/abl/CLOUD_SESSION_RECEIPT_20261103.md` with:
  - What was built/changed (with file paths)
  - What was tested and results
  - What's still pending
  - Any bugs found
  - Recommended next steps
- `git status` in ~/abl/odysseus and ~/abl showing all changes
- Verify abl-backend is healthy: curl http://127.0.0.1:7800/api/health
- Verify dsh-web is running: systemctl --user status abl-dsh-web
- Verify 27B is serving: curl http://127.0.0.1:8085/health
- Final structural consistency review of all files edited more than twice
