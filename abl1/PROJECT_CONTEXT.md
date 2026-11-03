# PROJECT_CONTEXT.md: ABL on abl1 (living log)

> Append-only session log plus a current-state board. Newest session at the bottom.
> **Evidence key:** ✅ verified (on the box, or by tests against real code) · 📄 reported by Asif / earlier sessions, not re-verified · ⏳ pending.
> Last updated: 2026-11-03 by the cloud session (which could not reach abl1, so everything it built is ⏳ until deployed).

---

## Current state board

### Services on abl1 (as of 2026-11-02, 📄)
| Service | State | Notes |
|---|---|---|
| Odysseus backend :7800 | 📄 healthy, v0.9.1 | model router wired into `llm_core.py` egress fallback. Binds `0.0.0.0` → firewall it (Guide Step 2) |
| 27B IQ3_XXS llama-server :8085 | 📄 stable | `abl-llama-27b.service`, `-ngl 35 -np 2`. **Do not touch** |
| Ollama 3B :11434 | 📄 up | called every ~5.5 min by Odysseus fallback, which is a symptom of a cloud lane failing quietly (⏳ find it) |
| dsh web :3081 | 📄 up | ABL branding, phone layouts, Hub panel; cookie 365 d |
| Console plugin r1 | 📄 installed | 5 tabs, `/api/abl-console/read`; r2 ready to replace it (⏳) |
| Hub API :8902 | 📄 up | 9,219 lines; write routes locked pending approval |
| Market desks :8090–8098 | 📄 up | options engine **stopped** (6.8 GB RAM) |
| Fleet registry :8900 | 📄 up, **wrong desktop IP** | 100.127.203.4 → 100.117.30.114 (⏳ Bug 1; check the desktop's own `abl-fleet-registry` first) |
| Hub inbox :8899 | 📄 up | |
| abl-market-bridge | 📄 **crash loop** | ⏳ Bug 4: diagnoser + backoff drop-in ready; suspect the stopped options desk |
| one-project-context-v1 | 📄 fails on restart | ⏳ Bug 3: stale ready marker; drop-in ready |
| OllamaEmbedder warm-up | 📄 "Connection refused" each start | ⏳ B5: `embedding_base_url` points at the Mac satellite (✅ in code) |
| Errands scanner | 📄 looks in `/mnt/c/...` | ⏳ Bug 2: env-overridable path fix ready |

### Desktop (📄)
| Item | State |
|---|---|
| Duplicate Odysseus | stopped (750 MB freed). ⚠ desktop gamemode **restarts it within 10 s** unless it was `pm2 delete`d (✅ in code), so Guide Step 1a comes first |
| 35B llama-server :8095 | **on 0.0.0.0**: security (⏳ Guide Step 1b) |
| ears.py | posts to the dead `localhost:7800` (⏳ voice bridge + `patch_ears.py`) |
| 8 screen-off systems | built. Briefing, research bridge and finance are now ported to abl1 (⏳ timers) |

### Deployed vs pending (cloud-session work, all in `~/abl-cloud-session`, PRs #1–#3)
| Deliverable | Where it goes | State |
|---|---|---|
| Autoresponder (`src/autoresponder.py` + routes) | abl1 Odysseus | ⏳ install + wire (Guide Steps 3–4); ships in **shadow** |
| Settings store + plugin (`abl_settings.py`, `dsh-abl-settings-r1`) | abl1 Odysseus + dsh | ⏳ Step 5 |
| `alert_rules.py` email classifier | abl1 Odysseus | ⏳ Step 3 |
| Morning briefing / finance / research bridge (+ timers) | abl1 | ⏳ Step 8 |
| Dashboard endpoint `/api/abl/dashboard` + Console r2 | abl1 Odysseus + dsh | ⏳ Steps 4b / 5b |
| Voice bridge + ears patch + tunnel | desktop | ⏳ Step 7 |
| Bug fixes 1–4, B5–B7 | abl1 / desktop | ⏳ Step 6 |
| Security patches H1 (dashboard auth), H2 (gpuctl token) | `~/abl` repo | ⏳ **apply first** (`git am`) |
| Codex manifest audit | report | ⏳ Step 9 (tool ready; 127 tasks unread from the cloud) |

### Open decisions for Asif
1. Autoresponder go-live: which channel goes first, the sender adapter, the signature.
2. Quiet hours: YELLOW **hold** (default) vs send; breakthrough scope; whether money may be lowered to YELLOW.
3. Spec §12: Tailscale-identity sign-in; the 35B review role; STT on the desktop vs abl1; who owns ideation; the 8 unlisted pages; red/green palette tokens.
4. Ears tunnel port: 7800 (as asked) or 17800 (recommended).
5. The other three autopilot system names (5 known: autopilot, morning_briefing, research_bridge, finance_voice, chimes).
6. `TYPESAFE_API_KEY` for Jev, and which router function exposes it.

---

## Session log

### 2026-11-02: local session (📄 from the brief)
- Applied `OLLAMA_LOAD_TIMEOUT=60m` as a systemd drop-in. Stopped the options engine (6.8 GB). Cleaned 10 dead dsh plugin folders
  (backup `~/abl/backups/dsh-plugins-20261102/`). Extended the dsh cookie to 365 days for the phone.
- Installed console plugin `dsh-abl-console-r1` (5 tabs). Stopped the duplicate desktop Odysseus.
- Model router (557 lines) wired into `llm_core.py`'s egress fallback; Jev interface ready, no key.

### 2026-11-03: cloud session (Claude Code, no access to abl1 or the desktop)
**Access:** the cloud container had no route to the LAN or tailnet. Everything was built against the `insomniac-asif/abl` /
`abl-fused` snapshots (2026-08-05) plus the published dsh sources, then verified with tests, including in-tree runs against a copy
of the real Odysseus tree.

**PR #1 (merged):** `ABL_UNIFICATION_SPEC.md`. Autoresponder (`jev_classify`, GREEN/YELLOW/RED; deterministic rule floor;
RED never reaches a model; DeepSeek only through the egress gateway, with a strict personal-data policy; shadow/draft/live modes;
hash-only audit). Codex manifest auditor. Bug-fix scripts + `BUG_FIXES.md`. Deploy script. Bills tier → **YELLOW** (Asif).
**PR #2 (merged):** desktop `voice_bridge.py` (tailnet → tunnel failover, no double replies), `patch_ears.py`, the Settings panel
spec, the `:7800` scan. **Found:** desktop gamemode re-starts pm2-stopped backends every 10 s; `_require_owner` already
accepts owner bearer tokens.
**PR #3 (open):** the `dsh-abl-settings-r1` plugin (built against the published dsh contracts), `abl_settings.py`, the autoresponder
reading settings (quiet-hours hold), `alert_rules.py` (17 email rules), `morning_briefing_abl1.py`, `finance_voice_abl1.py`,
`DEPLOYMENT_GUIDE.md`, systemd timers, the security scan + audit script, and tested patches **H1** (dashboard auth) and **H2** (gpuctl token).
**Later in PR #3:** `research_bridge_abl1.py` (2-hourly; via voice_dispatch; `VoiceBridge.pull_lines()`), `/api/abl/dashboard`
(`abl_dashboard.py`), `dsh-abl-console-r2` (spinners, unreachable states, 30 s refresh, per-card timestamps), and this file + the new CLAUDE.md.

**Bugs found and fixed by tests along the way:**
- The autoresponder couldn't load an `alert_rules.py` that uses `@dataclass`, and an explicit rules path lost to an already-imported module.
- Phishing bait carried only in an attachment's filename went undetected.
- Year-less due dates used a parsing form that Python 3.15 deprecates.
- The finance summary counted fees as purchases.
- Timer tests depended on the wall clock.
- The settings parity test assumed the repo layout.
- The H1 patch's login route returned 422 under `from __future__ import annotations` (caught before shipping).
- The research bridge ignored an explicit `ABL_DATA_DIR` whenever the real harvester was importable, and in a full-suite run one test reached the *real* `voice_dispatch` (writing a real outbox). Both fixed; the delivery path now has a test seam.

**Security (`reports/SECURITY_SCAN.md`):**
- **H1:** the core dashboard `:8081` is unauthenticated. Anyone on the LAN can read agent chat and inject operator messages, and the approve-override is latent.
- **H2:** gpuctl leaks its control token.
- **H3:** the 35B is on 0.0.0.0.
- **M1:** the mesh peer server falls back to plaintext.
- **M2:** cookbook-generated launch commands bind 0.0.0.0.
- **M3:** `:7800` binds 0.0.0.0.
- No real secrets in tracked files.

**Tests at close:** 257 Python + 29 node tests passing in the repo (full suite, real egress gateway included); pyflakes clean.
**Next session should:** follow `~/abl-cloud-session/DEPLOYMENT_GUIDE.md` in order (snapshot → desktop hygiene → firewall →
H1/H2 patches → modules → routes → plugins → fixes → voice → timers), ticking the board above from ⏳ to ✅ as each step's Verify passes.

<!-- Append new sessions below this line: ### YYYY-MM-DD: <who> -->
