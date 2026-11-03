# ABL Unification Spec

**Status:** PROPOSED. Every decision below marked **[DECIDE]** needs Asif's sign-off before build.
**Date:** 2026-11-03 · **Author:** cloud session (Claude Code) · **Deploys to:** abl1 (192.168.1.208)
**Supersedes:** nothing. This is the first master spec for "dsh becomes ABL".

> **Evidence key.** ✅ verified in code (`insomniac-asif/abl` and `abl-fused` snapshots, Aug 2026).
> 📄 taken from PROMPT.md (abl1 state as of 2026-11-02; not verifiable from the cloud session).
> 💡 proposal.

---

## 0. One-paragraph summary

ABL becomes **one HTTPS origin on the tailnet**: `https://abl.<tailnet>.ts.net`, served by
`tailscale serve`. Behind it, **dsh (:3081)** is the only human-facing app and owns
conversations. ABL features arrive as **thin dsh plugins** that call one policy gateway,
the **hub (:8902)**. The hub fronts Odysseus (:7800), the desks, the fleet registry and the
inbox. Every other port binds to **127.0.0.1**. Humans are authenticated by **Tailscale
identity**: the owner gets no login wall. Machines (desktop speaker/ears, Codex, cron) use
**scoped bearer tokens** on a separate `/machine` path of the same origin. Models are
addressed by **logical role** (primary / lightweight / review / triage) through the model
router, and every hosted call passes the **egress gateway**. The build is three phases:
**read paths → bounded writes → retire old endpoints**. Each phase has an exit test and a
demonstrated rollback.

---

## 1. Goals and non-goals

**Goals**
1. One app, one URL, one look (navy `#161b30`, lavender `#cbbbff`, amber `#f7ad31`, slate `#a7b3c1`), on desktop and phone.
2. One source of truth per entity: conversations, memory, tasks, alerts.
3. No new public exposure. Nothing listens on a LAN/tailnet interface except what is in §3.
4. Every write path is single-writer, audited, and has a kill switch.
5. Upgrading dsh stays a version bump plus an adapter test. We never fork it.

**Non-goals (this spec)**
- Rewriting Odysseus internals, or changing its DB schema (📄 boundary).
- Replacing the 3/3 consensus protocol. Bounded writes that represent agent *actions* still
  go through propose → challenge → execute. The UI only surfaces the proposals.
- Public internet access (no Tailscale Funnel, no Cloudflare tunnel for the unified app).

---

## 2. Target topology

```
 phone / laptop (tailnet)                          desktop 192.168.1.204 / 100.117.30.114
   │ https://abl.<tailnet>.ts.net                    ears.py :7962 ─┐  speaker.py :7961 ─┐
   ▼                                                 (initiates)    │  (long-polls)      │
┌──────────────────────── abl1 (100.x / 192.168.1.208) ─────────────┼────────────────────┼──┐
│ tailscale serve :443                                             │ bearer token       │  │
│   /          → 127.0.0.1:3081  dsh  (humans; Tailscale identity) │                    │  │
│   /machine/  → 127.0.0.1:8902  hub  (machines; scoped tokens) ◄──┴────────────────────┘  │
│                                                                                          │
│ dsh :3081 ── session controller (owns conversations)                                     │
│   └─ local-plugins/dsh-abl-*  ──server routes──►  hub :8902 (catalog · write-locks · audit)
│                                                     ├─► Odysseus :7800 (/api/abl/*, memory, todos,
│                                                     │                    voice, autoresponder)
│                                                     ├─► desks :8090-8098
│                                                     ├─► fleet registry :8900
│                                                     └─► hub inbox :8899
│ model plane (loopback only):                                                             │
│   model_router ─► primary  : llama-server 27B :8085 (2 slots)                            │
│                ─► light    : ollama 3B :11434                                            │
│                ─► triage   : Jev (TYPESAFE) → DeepSeek fallback (egress-gated)           │
│                ─► review   : 35B on desktop :8095 via tailnet (opt-in, see §6)           │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Why the hub and not direct plugin → Odysseus calls:** the hub already holds the
`/abl/catalog` allowlist and the write locks (📄). One policy point means a plugin can never
reach a write route the hub has not unlocked. Plugins stay dumb projections, which is
what the Codex handoff decision requires.

---

## 3. Service routing map

| Port | Service | Bind today (📄) | Bind target 💡 | Reached by | Phase |
|---|---|---|---|---|---|
| 443 | `tailscale serve` | — | tailnet only | browsers, phone, desktop | P1 |
| 3081 | dsh web (`abl-dsh-web.service`) | LAN? | **127.0.0.1** | tailscale serve `/` | P1 |
| 8902 | hub API | ? | **127.0.0.1** | dsh plugins; tailscale serve `/machine/` | P1 |
| 7800 | Odysseus backend | ? | **127.0.0.1** | hub only | P1 (bind), P3 (UI retired) |
| 8085 | llama-server 27B | 127.0.0.1? | 127.0.0.1 (**unit untouched**) | model_router | — |
| 11434 | Ollama 3B | ? | 127.0.0.1 | model_router | P1 |
| 8090–8098 | market desks | ? | 127.0.0.1 | hub | P1 |
| 8900 | fleet registry | ? | 127.0.0.1 | hub | P1 |
| 8899 | hub inbox | ? | 127.0.0.1 | hub, autoresponder alerts | P1 |
| desktop 8095 | 35B llama-server (Windows) | **0.0.0.0** ⚠ | **127.0.0.1**, or the tailnet IP only if §6 review is approved | — | **P0** |
| desktop 7961/7962 | speaker / ears | local | local (outbound only) | — | P0 |

**Bind audit (run on abl1 at P1 start, and after every change):**
```bash
ss -ltnp | awk 'NR>1 && $4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print}'   # must print only tailscaled / sshd
```

**Routing rules 💡**
- `/api/abl-<area>/*`: dsh plugin server routes (dsh sign-in applies automatically). Each plugin route calls `http://127.0.0.1:8902/abl/call` with the catalog key. It never calls a port directly.
- `/machine/*`: hub, bearer token required, no cookie accepted. Separate path so the cookie and token auth domains can never be confused.
- Odysseus `/api/abl/*` stays the implementation surface. Its HTML pages are retired in P3 (§9).

---

## 4. Auth model

### 4.1 Humans: Tailscale identity, no login wall for the owner
- `tailscale serve` proxies to `127.0.0.1:3081` and adds `Tailscale-User-Login` /
  `Tailscale-User-Name` headers for tailnet users.
- 💡 A small dsh auth adapter plugin (`dsh-abl-tsauth-r1`) runs *before* the dsh sign-in
  gate. If the request arrives from `127.0.0.1` (the only way in once dsh is
  loopback-bound) **and** `Tailscale-User-Login ∈ ABL_OWNER_LOGINS`, it mints the normal
  dsh session cookie (365-day lifetime, already configured 📄) and redirects. Anyone else
  sees the ordinary dsh sign-in.
- **Security precondition (hard):** the identity header is trustworthy **only** when dsh is
  unreachable except through `tailscale serve`. Ship §3's 127.0.0.1 binding for :3081
  *before* enabling the adapter. The adapter must also refuse the header if
  `X-Forwarded-For` is absent or the peer is not loopback.
- [DECIDE] If the dsh sign-in hook can't be extended without touching
  `node_modules/@deepseek-ai/` (forbidden), fallback: keep dsh sign-in, plus a
  one-time-per-device 365-day cookie. That is what exists today.

### 4.2 Machines: scoped bearer tokens (existing Odysseus mechanism ✅)
Odysseus already has scoped API tokens (`routes/api_token_routes.py`: `ALLOWED_SCOPES`,
`TOKEN_PROFILES`; Codex routes enforce them via `request.state.api_token_scopes`). Extend it
rather than invent a new scheme:

| Profile 💡 | Scopes | Holder |
|---|---|---|
| `speaker` | `voice:ingest`, `voice:outbox`, `autoresponder:ingest` | desktop ears.py + speaker.py |
| `codex_todos` ✅ | `todos:read`, `todos:write` | Codex |
| `fleet_agent` | `fleet:heartbeat` | node heartbeats |

- Tokens live in `~/.config/abl/token` (mode 0600) on the client. They're sent as
  `Authorization: Bearer …` to `https://abl.<tailnet>/machine/…`.
- The hub validates the token against Odysseus (`/tokens`). `_require_owner` on
  `/api/abl/voice/*` ✅ **already accepts an Asif-owned bearer token** (corrected 2026-11-03).
  So the speaker works today with an owner token. 💡 Narrowing it to `voice:*` scopes (like
  `codex_routes._scope_owner`) is a hardening step, not a prerequisite.
- Rotation: 90 days, plus revoke on device loss (`DELETE /tokens/{id}` ✅).

---

## 5. Plugin migration plan

**Plugin contract (every `dsh-abl-*` plugin):**
- `package.json` declares `dsh.bundle.patch` + `dsh.client`. Server
  `{name, inject:['connection'], apply(ctx)}` registers `ctx.connection.fetch.register({path:'/api/abl-<area>/…'})`.
  Client `window.__ModuleLoader__.load({id, factory})` with React.createElement. Sidebar via
  `ctx.slots.register({name:'sidebar.panellist', …})`, main via `{name:'main', key}`. (📄)
- Server routes call **only** the hub, with a 5 s timeout and a typed error envelope
  `{ok:false, error, retry_after?}`. The client always renders a degraded state; it never shows a blank panel.
- Palette via CSS custom properties (§11). Phone layout first, because phone access is the main reason for the 365-day cookie.
- Naming `dsh-abl-<area>-r<rev>`. A new revision is a new folder. The old one stays until
  the new one passes its smoke test, and is then moved to `~/abl/backups/dsh-plugins-<date>/`.
  That's the same pattern as the 2026-11-02 cleanup 📄.

**Odysseus has 49 HTML pages ✅.** Grouped into plugins and ordered by (value ÷ risk):

| Wave | Plugin | Replaces Odysseus pages | Hub surface | Writes? |
|---|---|---|---|---|
| 0 (exists 📄) | `dsh-abl-console-r1` | Home, Models & Router, Voice & Autopilot, Fleet, Desks tabs | `/api/abl-console/read` | no |
| 1 | `dsh-abl-today-r1` | `abl_today`, `abl_home`, `abl_activity` | brain/agent_activity, home | no |
| 1 | `dsh-abl-memory-r1` | `abl_memory`, `abl_mind`, `abl_data` | memory routes (13 ✅), data/browse | no |
| 1 | `dsh-abl-fleet-r1` (fold into console) | `abl_fleet`, `abl_free_gpu`, mobile nodes | `/api/abl/m/nodes` ✅, :8900 | no |
| 1 | `dsh-abl-build-r1` | `abl_build`, `abl_agents`, `abl_roadmap`, `abl_proposals` | brain routes (116 ✅) | no |
| 1 | `dsh-abl-desks-r1` (fold into console) | `abl_crypto`, `abl_portfolio` | desks :8090-98 | no |
| 2 | `dsh-abl-inbox-r1` **(new)** | none (new capability) | `/api/abl/autoresponder/*` | approve / cancel held drafts |
| 2 | voice mode toggle (in console) | `abl_control` | `/api/abl/voice/mode` ✅ | mute / deafen |
| 2 | `dsh-abl-tasks-r1` | `abl_quest` + hub task tab | todos (task routes 22 ✅) | create / toggle todo |
| 3 | `dsh-abl-finance-r1` | `abl_finance` (35 routes ✅) | finance | read first, writes later |
| 3 | `dsh-abl-people-r1` | `abl_people`, `friend` | people mgmt | later |
| 3 | `dsh-abl-life-r1` | `abl_music`, `abl_photos`, `abl_fileorg`, `abl_wellbeing`, `abl_learning`, `abl_news` | various | later |
| 3 | `dsh-abl-work-r1` | `abl_career`, `abl_career_agent`, `abl_businesses`, `abl_creator`, `abl_inventory` | various | later |
| — | **not migrated** | `index.html` / Odysseus chat, `login.html` | dsh owns chat + sign-in | — |
| — | [DECIDE] keep or drop | `abl_sisters`, `abl_constitution`, `abl_sovereignty`, `abl_calibrate`, `abl_mirofish`, `abl_eros`, `abl_companion`, `abl_brain_ideation` | — | — |

Ideation (`abl_brain_ideation`) likely folds into the existing dsh Hub **ideation tab** 📄
rather than becoming its own plugin.

---

## 6. Model routing architecture

```
request ─► model_router.classify ─┬─ NONE        → no LLM (rules / cached / template)
 (✅ 557 lines on abl1 📄)         ├─ LIGHTWEIGHT → role:light   → ollama 3B :11434
                                   ├─ PRIMARY     → role:primary → llama 27B :8085 (2 slots)
                                   └─ (triage)    → role:triage  → Jev ─► DeepSeek ─► primary ─► light
                                                     every hosted hop: core/egress_gateway ✅
```

**Logical roles, one registry 💡** `config/model_roles.json`, read by model_router and the
console Models tab:
```json
{
  "primary":     {"url": "http://127.0.0.1:8085/v1/chat/completions", "slots": 2, "reserve_interactive": 1},
  "light":       {"url": "http://127.0.0.1:11434/v1/chat/completions", "model": "qwen2.5:3b"},
  "triage":      {"chain": ["jev", "deepseek", "primary", "light"], "hosted_policy": "strict"},
  "review":      {"url": null, "enabled": false, "note": "desktop 35B over tailnet; see DECIDE below"}
}
```
- **Slot budget.** The 27B has 2 parallel slots. 💡 Reserve one for interactive chat. Background
  work (autoresponder, dream jobs, research) may only take the second, and falls to `light` when it's busy.
- **Jev.** Exposed by model_router once `TYPESAFE_API_KEY` is set. `src/autoresponder.py`
  calls it through the router's Jev hook, and falls back to DeepSeek without the key (spec Goal 2).
- **Egress.** All hosted calls pass `egress_gateway.egress_ok()` ✅. Triage adds the **strict**
  policy: content classified personal / financial / transcript never leaves, even in audit mode.
- **The silent 3B fallback every ~5.5 min 📄** is a symptom: a cloud lane is failing quietly
  and llm_core's egress fallback is absorbing it. 💡 model_router writes one JSONL line per
  decision (`role`, `reason`, `fallback_from`). The console Models tab shows fallbacks per hour,
  and fires an amber alert when they exceed 6/h. Find the failing lane; don't just absorb it.
- **Memory budget.** 16 GB RAM with the 27B partly offloaded (`-ngl 35`) leaves no room for a
  third resident model on abl1. The options engine (6.8 GB) stays stopped 📄. New services
  must declare `MemoryMax=`.
- [DECIDE] **Review role → desktop 35B.** It requires the 35B to listen on the tailnet IP
  (not 0.0.0.0) and be called with a token. Alternatively, leave `review` disabled and bind the 35B to 127.0.0.1 (P0 default).

---

## 7. Voice integration (desktop speaker/ears ↔ abl1)

Principle (📄 decision): **the desktop initiates; abl1 never connects into the desktop.**

| Flow | Today | Target 💡 |
|---|---|---|
| Ears → ABL | ears.py posts to `localhost:7800`, which is **dead** since the duplicate Odysseus was stopped 📄 | ears.py → `POST https://abl.<tailnet>/machine/voice/heard` (bearer `speaker` token) → hub → Odysseus `/api/abl/voice/heard` ✅ |
| ABL → speaker | ? | speaker.py long-polls `GET /machine/voice/outbox?wait=25` → hub → Odysseus `/api/abl/voice/pending` ✅. It plays with local piper. ABL never dials :7961. |
| Mute / deafen | Odysseus voice mode ✅ (`deafened` drops audio before Whisper) | Same switch in the console; ears.py also checks mode before recording |
| RED autoresponder alerts | — | Autoresponder → outbox item `{kind:"chime", priority}` → speaker plays the chime. Alert payloads carry hashes, never message text. |

**Where STT runs [DECIDE]:** abl1's GTX 1070 is saturated by the 27B, so Whisper there competes
with chat. 💡 Recommended: transcribe on the desktop (faster-whisper is already there 📄) and
send **text** to a new `POST /api/abl/voice/heard_text`. It reuses `_ambient_decision`, plus the
same mode gates and owner check. Audio never leaves the desktop, which is also the better privacy posture.

**Failure behaviour (implemented in `src/voice_bridge.py`):** endpoints are tried in priority
order (tailnet → SSH tunnel), with per-endpoint exponential backoff. If none is reachable, the
bridge buffers at most 10 chunks and **drops anything older than 30 s**, because a late reply to old
speech is worse than none. It fires one `offline`/`online` callback per transition (ears.py
plays the chime). A response timeout after the audio was sent is never re-sent, to avoid
double replies. It never falls back to a hosted STT/LLM.

---

## 8. Data unification

**Rule: one writer per entity.** Everything else is a projection.

| Entity | Single source of truth | Writers | Projections (read-only) |
|---|---|---|---|
| Conversations | **dsh session controller** (`@deepseek-ai/dsh-api-session-controller`) 📄 | dsh only | Lis memory (via ingest adapter), console |
| Long-term memory | **Lis / Odysseus memory** (Chroma + SQLite) ✅ | Lis ingest only | dsh plugins, console |
| Tasks / todos | **Odysseus todos** (`task_routes`, `/codex/todos`) ✅ | Odysseus API (hub-gated) | dsh Hub task/project tabs, Codex |
| Ideation / projects | [DECIDE] dsh Hub tabs, *or* Odysseus brain | one of the two, never both | the other |
| Alerts / inbox | **hub inbox :8899** | autoresponder, desks, fleet | console, speaker |
| Autoresponder queue | `data/abl/autoresponder/pending.json` | autoresponder only | inbox plugin |
| Fleet nodes | **fleet registry :8900** | node heartbeats | console Fleet tab |

**Conversations → memory.** 💡 A one-way, append-only adapter: on session close (or hourly)
it reads dsh sessions through the session controller's public API, and posts turns to Lis
with `source:"dsh"` and the dsh session id as an idempotency key. Lis never writes back into
dsh. Odysseus chat history stays in Odysseus. It's read-only and searchable through memory, and **not** migrated into dsh sessions.

**Migration order (from the Codex handoff 📄):**
1. **Pin writes.** Declare the single writer per entity (table above). Lock every other write path at the hub.
2. **Additive imports.** One-way copies keyed by source id, re-runnable, never deleting.
3. **Sync.** Only where two systems must both write, which this spec avoids on purpose.

Each step needs a **demonstrated rollback** before the next one starts. The rollback is
proven by doing it once, on the box, and recording it in DECISIONS.md.

---

## 9. Build order

### P0: Hygiene (this sprint; scripts in `fixes/`, see BUG_FIXES.md)
- Desktop 35B :8095 bound to 127.0.0.1 (security).
- ears.py target moved off the dead `localhost:7800` (interim: abl1 tailnet IP plus a `speaker` token, until P1 provides `/machine`).
- Fleet desktop IP fixed. Errands path portable. one-project-context drop-in. market-bridge diagnosed.
- Autoresponder deployed in **shadow** mode (classify plus hash-only log, zero side effects).
- **Exit:** `curl :7800/api/health` → 200; `systemctl --user is-active abl-dsh-web` → active; `curl :8085/health` → ok; `journalctl -u abl-dsh-web` has no plugin failures; bind audit shows no 0.0.0.0 on the desktop.

### P1: Read paths (one origin)
1. Bind :3081, :8902, :7800, :11434, the desks, :8900 and :8899 to 127.0.0.1 (one service at a time; health check after each).
2. `tailscale serve` with `/` → :3081 and `/machine/` → :8902. Verify from the phone.
3. `dsh-abl-tsauth-r1` (owner auto-sign-in), **after** step 1 passes the bind audit.
4. Wave-1 plugins (today, memory, build; fleet and desks folded into the console).
5. model_router JSONL plus fallback-rate panel.
- **Exit:** every Wave-0/1 panel renders from the phone over the tailnet. `ss` shows only tailscaled/sshd off-loopback. Kill test: stop the hub, and every panel shows its degraded state (none go blank). Restart the hub, and they recover without a reload.
- **Rollback:** `tailscale serve reset`; restore the previous bind flags from the unit `.bak`; move the plugin folders to backups.

### P2: Bounded writes
Each write path ships with: hub catalog entry (locked by default), an audit line, a kill
switch (env or flag file), a UI confirm, and a rollback rehearsal.
1. Autoresponder **draft** mode, then `dsh-abl-inbox-r1` approve/cancel.
2. Voice mode toggle.
3. Todos create/toggle (single writer: Odysseus).
4. Autoresponder **live** on exactly one channel (`ABL_AUTORESPONDER_AUTOSEND_CHANNELS=<one>`) for a week, then expand.
- **Exit:** a week of audit logs with zero writes outside the catalog, and one rehearsed rollback per write path.

### P3: Retire old endpoints
1. Odysseus HTML pages return 302 → the matching dsh panel (keep for 30 days, then 410).
2. `:7800` is no longer reachable from anything but the hub (already true after P1; now enforced by test).
3. Remove the legacy Cloudflare tunnel/basic-auth for the Odysseus dashboard if it's still configured ✅ (v0.13 S3 shipped one).
4. Archive retired plugin revisions.
- **Exit:** 30 days with no 302 hits in the access log, then delete the redirects.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Tailscale identity header spoofed by a LAN client | dsh bound to 127.0.0.1 *before* the adapter ships; adapter checks the loopback peer |
| dsh upgrade breaks plugins | Pinned version; `npm ci` from the lockfile; per-plugin smoke test before bumping; never patch `node_modules/@deepseek-ai/` |
| Hub becomes a bottleneck / single point of failure | It's loopback HTTP and stateless except for locks; systemd `Restart=on-failure`; plugins degrade, they don't hang |
| 16 GB RAM pressure | `MemoryMax=` on new units; options engine stays off; light role preferred for background work |
| Autoresponder sends something wrong | Shadow → draft → live per channel; rule floor; draft re-check; RED is terminal; one-line kill: `ABL_AUTORESPONDER_MODE=shadow` |
| Personal data to hosted models | egress_gateway on every hosted hop, plus the triage strict policy |

---

## 11. Appendix: design tokens

```css
:root {
  --abl-navy:     #161b30;  /* app background */
  --abl-lavender: #cbbbff;  /* primary accent, focus rings, active tab */
  --abl-amber:    #f7ad31;  /* attention: YELLOW holds, warnings, fallback alerts */
  --abl-slate:    #a7b3c1;  /* secondary text, borders, disabled */
  --abl-red:      #ff6b7a;  /* RED alerts only (proposed; not in the palette yet) [DECIDE] */
  --abl-green:    #7ee0b0;  /* GREEN / healthy (proposed) [DECIDE] */
}
```
Tier colours in the inbox plugin: GREEN → `--abl-green`, YELLOW → `--abl-amber`, RED → `--abl-red`. Text on navy uses `--abl-slate` for secondary text and `#ffffff` at 90% for primary.

---

## 12. Decisions needed from Asif

1. §4.1 Owner auto-sign-in via Tailscale identity, or keep dsh sign-in plus the 365-day cookie?
2. §6 Desktop 35B: enable the `review` role over the tailnet, or bind to 127.0.0.1 and leave review off?
3. §7 STT on the desktop (send text) vs. on abl1 (send audio). Recommendation: desktop.
4. §8 Ideation/projects: does the dsh Hub tab or Odysseus brain own them?
5. §5 Keep or drop the eight unlisted pages.
6. §11 Add red/green to the palette for tier colours.
