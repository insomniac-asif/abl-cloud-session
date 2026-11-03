# CLAUDE.md: ABL on abl1 (read this first)

> **Goal: any Claude Code session productive in under 2 minutes.** Run §1, skim §2–§4, and respect §5.
> Last rewritten 2026-11-03 (cloud session). The previous CLAUDE.md (the v0.1–v0.16 sprint log, written for the
> old WSL desktop) lives at `docs/CLAUDE_HISTORY_v0.16.md`; its design rules are carried over in §6.
> Living state goes in `PROJECT_CONTEXT.md`, not here. Update this file only when structure or rules change.

---

## 1. Orient (60 seconds, run it, don't skip it)

```bash
cd ~/abl && git status --short | head; git log --oneline -3
curl -s -o /dev/null -w 'odysseus %{http_code}\n' http://127.0.0.1:7800/api/health
curl -s -H "Authorization: Bearer $(cat ~/abl/odysseus/data/abl/dev.token)" \
     http://127.0.0.1:7800/api/abl/dashboard | jq '{status, alerts, collect_ms}'      # one call = whole box
systemctl --user list-units --type=service --state=running | grep -E 'abl|dsh|llama|ollama'
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader; free -h | head -2
tail -40 ~/abl/PROJECT_CONTEXT.md                                                   # what happened last
```
If `/api/abl/dashboard` 404s, the route isn't wired yet (see `~/abl-cloud-session/DEPLOYMENT_GUIDE.md` Step 4b). Fall back
to the individual curls in §3. **Tell the user what you saw in ≤5 lines before changing anything.**

---

## 2. What ABL is

ABL ("Aurora, Borea, Lis") is Asif's personal AI system: **three peer agents** (Aurora reasons and plans, Borea executes
tools, Lis owns memory and storage). They talk over a peer bus and need **3/3 consensus** to act; Asif is the only tiebreaker. Around that core:

```
phone / laptop ──tailnet──► dsh web (:3081, "ABL" UI, DeepSeek Harness, version-pinned, never forked)
                               └─ local-plugins/dsh-abl-* ──► hub API (:8902: catalog, write-locks)
                                                                └─► Odysseus backend (:7800, FastAPI, ~9k routes/modules)
                                                                      ├─ model_router ─► 27B llama-server :8085 (PRIMARY, 2 slots)
                                                                      │                 ├► Ollama 3B :11434 (LIGHTWEIGHT)
                                                                      │                 └► Jev/DeepSeek (hosted, egress-gated)
                                                                      ├─ memory (Lis): Chroma + SQLite
                                                                      ├─ autoresponder, alert_rules, briefing, finance, research bridge
                                                                      └─ voice (/api/abl/voice/*) ◄── desktop ears via voice_bridge
market desks :8090–8098 · fleet registry :8900 · hub inbox :8899
```
**Direction of travel** (`ABL_UNIFICATION_SPEC.md`): one HTTPS origin (`tailscale serve`) → dsh; everything else on
127.0.0.1; ABL features arrive as thin dsh plugins over the hub. `:7800` is **not** the permanent UI.

---

## 3. Machines, services, ports

| Machine | Address | Role |
|---|---|---|
| **abl1** (this box) | 192.168.1.208 · tailnet `abl1.tailda08f9.ts.net` · user `rescue` | headless Ubuntu, GTX 1070 8 GB, 16 GB RAM: runs everything below |
| desktop | 192.168.1.204 · tailnet 100.117.30.114 | Windows + WSL: speaker (`~/ops/speaker`: speaker.py :7961, ears.py :7962), 35B llama-server :8095 (must be 127.0.0.1), gamemode |
| Mac M1 | 192.168.1.160 | optional Ollama satellite (embeddings were pointed here; see BUG_FIXES B5) |
| Oracle ARM | — | Lis failover node; **disabled** (`config/mesh.toml [nodes.oracle] enabled=false`) |

| Port | Service | Unit / supervisor | Health |
|---|---|---|---|
| 7800 | Odysseus backend | pm2 `abl-backend` (`odysseus/run_abl.sh`) or systemd | `curl :7800/api/health` |
| 8085 | 27B IQ3_XXS llama-server (`-ngl 35 -np 2`) | `abl-llama-27b.service` | `curl :8085/health` · `/slots` |
| 11434 | Ollama (3B, embeddings) | system `ollama` (drop-in `OLLAMA_LOAD_TIMEOUT=60m`) | `curl :11434/api/ps` |
| 3081 | dsh web (ABL UI) | `abl-dsh-web.service` | `systemctl --user status abl-dsh-web` |
| 8902 | hub API (mirrors Odysseus via `/abl/catalog`, `/abl/call`) | — | TCP |
| 8899 / 8900 | hub inbox / fleet registry | — | TCP |
| 8090–8098 | market desks (futures, options*, polymarket, shorts, crypto, conviction, voledge, memescreen) | — | TCP (*options engine stopped: 6.8 GB RAM) |
| 8081 | ABL core web dashboard (`src/abl/web`) | `abl voice` / orchestrator | **unauthenticated until `patches/abl_h1_dashboard_auth.patch` is applied** |

Timers (after deploy): `abl-morning-briefing.timer` 07:25 · `abl-research-bridge.timer` every 2 h at :17 · `abl-finance-voice.timer` 20:40.

---

## 4. Where things are

| Path | What |
|---|---|
| `~/abl/` | this repo: ABL core (`src/abl/`: agents, bus, consensus, memory, voice…), `config/*.toml`, `planning/`, `docs/` |
| `~/abl/odysseus/` | the **Odysseus backend** (FastAPI): `app.py`, `routes/*_routes.py`, `src/` (llm_core, model_router, autoresponder…), `core/` (egress_gateway, auth), `tests/`, `data/abl/` (runtime state, **not in git**) |
| `~/abl/odysseus/src/model_router.py` | PRIMARY / LIGHTWEIGHT / NONE router (Jev-compatible; no TYPESAFE key yet) |
| `~/abl/odysseus/core/egress_gateway.py` | **every hosted-LLM call goes through this** (`egress_ok`, `hosted_reply`) |
| `~/abl/odysseus/data/abl/` | `settings.json` (Settings panel), `autoresponder/`, `briefing/`, `finance/` (0600), `research_bridge/`, `morning_queue.jsonl`, `research_finds.json`, `dev.token` |
| `~/abl/tools/dsh/app/` | dsh profile; `local-plugins/dsh-abl-*` (console r1/r2, settings r1, hub); `cordis.patch.yml` |
| `~/abl-fused/` | older fused tree (snapshot of odysseus + docs); prefer `~/abl/odysseus` |
| `~/abl-cloud-session/` | cloud-session deliverables: spec, guide, fixes, patches, plugins. **Deploy from here; don't edit in place** |
| `~/abl/backups/` | dated backups (`dsh-plugins-20261102/`, `pre-cloudcc-*`) |
| `~/abl/reports/` | audits, diagnoses, codex manifest audit |
| `~/.config/systemd/user/` | user units, drop-ins (`*.d/*.conf`), timers |
| `~/.config/abl/` | `abl1.env` (briefing/finance config), tokens; **0600** |

Key docs: `PROJECT_CONTEXT.md` (living log) · `ABL_REPO_ROADMAP.md` · `DECISIONS.md` (AD-NNN) · `PATTERNS.md` ·
`~/abl-cloud-session/{ABL_UNIFICATION_SPEC,DEPLOYMENT_GUIDE,BUG_FIXES}.md` · `reports/SECURITY_SCAN.md`.

---

## 5. Do NOT touch (hard rules)

1. **`abl-llama-27b.service`**: stable; don't edit or restart it unless explicitly asked.
2. **`node_modules/@deepseek-ai/*`**: never patch dsh core. Extend via plugins only. Pin versions; no auto-update.
3. **`~/.dsh/.credentials.yaml`** and any `*.token` / `.env`: never read into output, commit, or print.
4. **Odysseus DB schema**: no migrations without Asif.
5. **dsh-web restarts**: test the change offline first (`node --test` for the plugin, `dsh --profile web --dump-config`), then restart and check the journal.
6. **Hosted APIs**: nothing leaves the box except through `core/egress_gateway`. Personal and financial data is local-only.
7. **Before modifying any file:** `cp file file.bak-cloudcc-$(date +%Y%m%d)` (or the session's own suffix). Never overwrite an existing backup.
8. **Don't stop running services** without a stated reason. Crash-looping units: diagnose first (`fixes/diagnose_unit.py`).
9. **Autoresponder** stays in `shadow` until Asif moves it to `draft` or `live` in Settings. No `sender_fn` is wired yet.
10. **No new `0.0.0.0` binds.** Use loopback, or the tailnet IP plus ufw. See SECURITY_SCAN H1–H3 and M1–M3.
11. **dsh owns conversations** (session controller). ABL plugins are adapters and projections only.

---

## 6. Design rules (carried over from the original CLAUDE.md; still binding for `src/abl/`)

- **Peers, not hierarchy:** propose → challenge → execute over the bus; **3/3 consensus**; Asif breaks deadlocks; no degraded mode.
- **Lis owns all storage.** Aurora and Borea never write to disk.
- Frozen Pydantic configs (`ConfigDict(frozen=True)`); **append-only JSONL logs**; fail-fast config (`ConfigError`); enums not strings.
- `logger.py`, not `logging.py`; async-first (never block the event loop; use `run_in_threadpool` for blocking I/O in routes).
- Protected paths (no self-modification): `src/abl/safety/`, `src/abl/consensus/`, `src/abl/bus/`.
- Constructor injection for testability (`_clock_fn`, `_token_override`, …). Tests are fully mocked: no Ollama needed.
- **Source-level invariants use AST, not substrings** (PATTERNS #68), e.g. "create_app always gets auth=".
- The recurring bug class here is **"capability built, wiring missing"** (11+ instances). When you build a thing, add a test that it is *called*.

---

## 7. Testing

```bash
cd ~/abl && source venv/bin/activate && pytest -q                      # core (~6k tests, mocked)
cd ~/abl/odysseus && venv/bin/python -m pytest -q tests/               # backend (500+ files)
venv/bin/python -m pytest -q tests/test_autoresponder.py tests/test_alert_rules.py tests/test_abl_dashboard.py   # cloud-session modules
venv/bin/python -m src.autoresponder --self-test                       # triage battery, no network
venv/bin/python -m src.model_router --self-test                        # router
node --test ~/abl/tools/dsh/app/local-plugins/dsh-abl-settings-r1/tests/*.test.mjs   # a dsh plugin
bash ~/abl-cloud-session/tools/security_audit_abl1.sh                  # read-only exposure audit
```
**After any Odysseus change:** `curl :7800/api/health` → 200. **After any plugin change:** restart dsh-web, then
`journalctl --user -u abl-dsh-web -n 120 | grep -iE 'plugin.*(fail|error)'`. **After model changes:** `nvidia-smi; free -h`
(16 GB is tight: no third resident model, and the options engine stays off).

---

## 8. Common recipes

| Task | How |
|---|---|
| See everything at once | `GET /api/abl/dashboard` (or the Console r2 panel) |
| Change a behaviour setting | the Settings panel (dsh sidebar gear). Writes `data/abl/settings.json`; Python reads it via `src/abl_settings.py` within 5 s |
| Add an autoresponder/email rule | `odysseus/src/alert_rules.py`: add a `Rule(...)` to `RULES`, then a test in `tests/test_alert_rules.py` |
| Restart the backend | `pm2 restart abl-backend` (or the systemd unit), then `curl :7800/api/health` |
| Wire a new route | `app.include_router(...)` next to the others in `odysseus/app.py`; back up app.py first |
| Diagnose a crash-looping unit | `python3 ~/abl-cloud-session/fixes/diagnose_unit.py --unit <name>` |
| Deploy cloud-session work | `bash ~/abl-cloud-session/deploy/deploy_abl1.sh` (dry run), then `--apply` |
| Read a unit's logs | `journalctl --user -u <unit> -n 200 --no-pager` |

---

## 9. Session end (every session)

1. Append a dated entry to `PROJECT_CONTEXT.md` §Session log: what changed (files), what was tested, what's pending.
2. Run `git status` in `~/abl` and `~/abl/odysseus`. Commit code and docs separately, and don't commit `data/` or tokens.
3. Health: `curl :7800/api/health` · `systemctl --user is-active abl-dsh-web` · `curl :8085/health`.
4. If you made an architectural decision, append it to `DECISIONS.md` as AD-NNN and mark it PROPOSED until Asif approves.
