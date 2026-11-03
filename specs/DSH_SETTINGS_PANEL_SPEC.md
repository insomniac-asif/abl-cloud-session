# dsh Plugin Spec: ABL Settings / Admin panel (`dsh-abl-settings-r1`)

**Status:** PROPOSED · **Date:** 2026-11-03 · **Parent spec:** `ABL_UNIFICATION_SPEC.md` (§5 Wave 2, §9 P2 bounded writes)
**Evidence key:** ✅ verified in the `abl` code snapshot · 📄 from PROMPT.md · 💡 proposal

## 1. Purpose

One panel where Asif changes how ABL behaves, from the phone or the desktop, without SSH and without
editing env files. It covers five controls: **model selection, autopilot, quiet hours, alert tier
overrides, speaker volume**. It also surfaces two existing controls that belong next to them: voice mode
(active/muted/deafened ✅) and the autoresponder mode.

**Non-goals:** editing secrets or API keys, editing systemd units (the 27B unit is off-limits 📄),
anything touching `~/.dsh/.credentials.yaml`, and per-friend permissions.

## 2. Why a settings store first (the actual problem)

Today the same setting lives in several places, and some are hard-coded:

| Setting | Where it lives now |
|---|---|
| Quiet hours | ✅ hard-coded `_QUIET_START_H = 23`, `_QUIET_END_H = 9` in `companion_checkin.py`, "copied from abl-discord/bot.py"; ✅ separate policy in `services/bandwidth.py::_in_quiet_hours` |
| Voice mode | ✅ `data/abl/voice_mode.json` (`active` / `muted` / `deafened`) |
| Autoresponder tiers/mode | env vars (`ABL_AUTORESPONDER_TIERS`, `_MODE`, `_AUTOSEND_CHANNELS`) |
| Models | ✅ model endpoints (`/api/model-endpoints`, `/api/default-chat`) plus 📄 model_router's role mapping |
| Speaker volume, autopilot | 📄 desktop-only (`~/ops/speaker/`), invisible to abl1 |

A panel that writes to five different places would make five different consistency bugs. So:

💡 **One store, one writer, many readers.** `odysseus/data/abl/settings.json`, written only by a new
route `PATCH /api/abl/settings`. Every consumer reads it through a 5 s-cached helper
(`abl_settings.get(key)`). Precedence: **settings.json > env > code default**, so nothing changes until a
value is actually set, and deleting the file is a full rollback.

## 3. Placement and layout

- Sidebar entry: gear icon, label **Settings**, `order: 990` (last). `ctx.slots.register({name:'sidebar.panellist', id:'abl-settings', order:990, label:'Settings'}, GearIcon)`, plus `{name:'main', key:'abl-settings'}`.
- Phone-first: a single column of collapsible sections, each with a status chip ("default", "changed", "locked").
- Desktop: the same sections in a two-column grid. No modals except confirmations.
- Palette (CSS variables, spec §11): navy background `#161b30`, lavender `#cbbbff` for focus and active state, amber `#f7ad31` for "unsaved / needs confirm", slate `#a7b3c1` for secondary text and disabled controls.

```
┌ Settings ─────────────────────────────── v42 · saved 2m ago by asif ┐
│ ● Voice       [ Active | Muted | Deafened ]                          │
│ ▸ Models                                   primary: 27B · light: 3B  │
│ ▸ Autopilot                                ON · 6/8 systems          │
│ ▸ Quiet hours                              23:00–09:00 ET · active ◐ │
│ ▸ Alerts & autoresponder                   shadow · 1 override       │
│ ▸ Speaker                                  volume 70 · chimes 40     │
│ ▸ About / audit                            last 20 changes           │
└──────────────────────────────────────────────────────────────────────┘
```

## 4. Controls

Every row gives: key in `settings.json`, type and validation, default (= current behaviour),
who consumes it and how it applies, and the guardrails.

### 4.1 Voice mode (existing control, surfaced at the top)
- Reuses ✅ `GET/POST /api/abl/voice/mode` and `data/abl/voice_mode.json`. **Not** moved into
  settings.json: it's already a single-writer store with its own gates (deafened drops audio before
  Whisper ✅). The panel is just another client.

### 4.2 Models
| Key | Type / validation | Default | Consumer |
|---|---|---|---|
| `models.primary` | endpoint id from ✅ `GET /api/model-endpoints` | the 27B llama-server endpoint | model_router PRIMARY role |
| `models.light` | endpoint id + model name from ✅ `GET /api/model-endpoints/{id}/models` | ollama `qwen2.5:3b` | model_router LIGHTWEIGHT role |
| `models.triage_chain` | ordered subset of `jev, deepseek, local, light` | `jev,deepseek,local,light` | `autoresponder.build_chain` |
| `models.review_enabled` | bool | `false` | review role (spec §6 [DECIDE]) |
| `models.reserve_interactive_slot` | bool | `true` | router slot budget (27B has 2 slots 📄) |

- The UI shows each endpoint's live probe result (✅ `GET /api/model-endpoints/{id}/probe`) and the last
  hour's fallback count from the router JSONL (spec §6). A model whose probe fails can't be selected (it's greyed out, with the reason).
- **Selecting never reloads a model server.** It changes which already-running endpoint a role
  points at. Loading a different GGUF into llama-server is out of scope (the unit is off-limits 📄).
- Confirm dialog for `primary`: "Chat will use X. Current: Y. RAM headroom: Z GB" (from `free`).
- Hosted endpoints in `triage_chain` show a lock-badge reminder that the egress gateway applies.

### 4.3 Autopilot (desktop screen-off systems 📄)
| Key | Type | Default | Consumer |
|---|---|---|---|
| `autopilot.enabled` | bool, master switch | `true` | desktop autopilot loop |
| `autopilot.systems.<name>` | bool per system: `autopilot`, `morning_briefing`, `research_bridge`, `finance_voice`, `chimes`, + the other three 📄 (names confirmed on the desktop) | `true` | each system checks its flag before acting |
| `autopilot.follow_quiet_hours` | bool | `true` | all systems |

- **Desktop pulls, abl1 never pushes** (spec §7). The desktop polls
  `GET /machine/settings/desktop` (hub → Odysseus `GET /api/abl/settings/desktop`, owner token,
  the same token as the voice bridge) every 30 s, and applies changes on the next tick.
- The panel shows "desktop last pulled 12 s ago". If that's older than 3 minutes: an amber chip, "desktop offline; change queued".

### 4.4 Quiet hours
| Key | Type / validation | Default |
|---|---|---|
| `quiet.enabled` | bool | `true` |
| `quiet.start` / `quiet.end` | `HH:MM`, 24 h; may wrap midnight | `23:00` / `09:00` (✅ current hard-coded values) |
| `quiet.tz` | IANA zone | `America/New_York` (✅ "ET" in current code) |
| `quiet.days` | subset of Mon–Sun | all |
| `quiet.breakthrough` | `urgent_red` \| `all_red` \| `none` | `urgent_red` (fraud/security only) |
| `quiet.speaker` | `silent` \| `chimes_only` \| `volume:N` | `silent` |
| `quiet.yellow_policy` | `hold` \| `send` | **`hold`** |

- **`quiet.yellow_policy = hold` is the important one.** A YELLOW draft auto-releases after 60 s. At
  3 am nobody is reviewing, so "60 s review window" means "auto-send unreviewed". With `hold`,
  YELLOW items created during quiet hours wait in the pending queue until `quiet.end`, plus the normal
  60 s timer. The inbox shows them as a morning batch. (The `PendingQueue` change is small: `due_at = max(now + hold, quiet_end + hold)`.)
- Consumers to migrate to `abl_settings.quiet_now()`: ✅ `companion_checkin._in_quiet_hours`,
  abl-discord `bot.py` (same constants), ✅ `services/bandwidth._in_quiet_hours`, the autoresponder,
  and the desktop speaker/autopilot (via the desktop pull).
- The UI shows a 24 h ring with the quiet arc, a "quiet now" indicator, and the next transition time.

### 4.5 Alerts & autoresponder (tier overrides)
| Key | Type / validation | Default |
|---|---|---|
| `alerts.tiers.<category>` | `GREEN`/`YELLOW`/`RED` for `fraud, security, money, bills, people, food, parcels` | `fraud/security/money = RED`, `bills/people = YELLOW`, `food/parcels = GREEN` (current code) |
| `autoresponder.mode` | `shadow` \| `draft` \| `live` | `shadow` |
| `autoresponder.autosend_channels` | subset of known channels | `[]` |
| `autoresponder.hold_seconds` | int 15–600 | `60` |

**Guardrails (enforced server-side, not just in the UI):**
- `fraud` and `security` are **locked at RED**. The control is disabled, with the tooltip "safety floor". PATCH rejects any lower value with 422.
- `money` may go no lower than `YELLOW`.
- Switching to `live` needs a typed confirmation, plus at least one channel in `autosend_channels`.
  Adding a channel to `autosend_channels` needs a confirmation that names the channel.
- **Live preview:** a text box, "Try a message", calls ✅ `POST /api/abl/autoresponder/classify`
  (pure, no side effects) with the *unsaved* tier overrides passed as `{"tiers_override": {...}}`
  (💡 small extension to the route). The verdict shows inline: tier chip, categories, reasons. So Asif
  sees what a change does before saving it.
- Reads `GET /api/abl/autoresponder/status` for the current chain, the egress gateway presence and the pending count.

`autoresponder.category_tiers()` precedence becomes: settings.json > `ABL_AUTORESPONDER_TIERS` env > defaults (exact diff in §8).

### 4.6 Speaker (desktop)
| Key | Type | Default | Applied by |
|---|---|---|---|
| `speaker.volume` | int 0–100 | `70` | desktop speaker.py: software gain on the WAV before playback (portable, no OS mixer API) |
| `speaker.chime_volume` | int 0–100 | `40` | chimes |
| `speaker.rate` | float 0.7–1.3 | `1.0` | piper `length_scale = 1/rate` |
| `speaker.voice` | piper voice id from the desktop's installed list | current | speaker.py |
| `speaker.max_utterance_s` | int 5–60 | `20` | speaker.py truncates long replies |

- **"Test speaker" button:** queues the line "Speaker test, volume 70" for the desktop. ⚠ The current
  ✅ `GET /api/abl/voice/pending` queue is the **phone's**, and it calls `voice_dispatch.mark_phone_seen()`. A
  desktop poller would corrupt the phone-presence heuristic. 💡 Needs a per-device outbox
  (`/api/abl/voice/pending?device=desktop`, which doesn't mark the phone seen) before the test button ships.
- Volume changes reach the desktop on its next pull (≤ 30 s). The panel says so.

### 4.7 About / audit
- Last 20 changes from `data/abl/settings_audit.jsonl`: time, user, key, old → new. Settings are not
  personal data, so values are logged. Secrets are never stored here by schema.
- "Reset section to defaults" per section, which removes those keys so env/default apply again.
- Store version and file path, plus a "Download settings.json" button for backups.

## 5. API contract (Odysseus; reached through the hub catalog)

```http
GET  /api/abl/settings
→ 200 {"version": 42, "updated_at": "...", "updated_by": "asif",
       "values": {...only keys that are set...},
       "effective": {...every key with its resolved value...},
       "source": {"quiet.start": "settings", "alerts.tiers.bills": "default", ...},
       "schema": {...JSON-schema-ish: type, enum, min, max, locked, confirm...}}

PATCH /api/abl/settings            (owner only; hub catalog entry LOCKED until P2 unlock)
{"version": 42, "set": {"quiet.end": "08:30"}, "unset": ["speaker.rate"]}
→ 200 {...new document, version 43...}
→ 409 {"error": "version_conflict", "current": 43}      // another tab saved first
→ 422 {"error": "invalid", "fields": {"alerts.tiers.fraud": "locked at RED"}}

GET  /api/abl/settings/desktop     (owner token; subset: autopilot.*, quiet.*, speaker.*)
→ 200 {"version": 43, "values": {...}, "quiet_now": false}
```

- Writes are atomic (tmp, fsync, rename; same pattern as ✅ `prefs_routes._save`). Before each write,
  the previous file goes to `settings.json.prev` (single-step undo).
- Validation lives in one schema module that both the route and `abl_settings.get` use. The client renders from `schema`, so a new key needs no client release.
- Every PATCH appends one audit line, and broadcasts `settings.changed {version, keys}` on the existing event bus, so open panels refresh.
- **Why not reuse `/api/prefs`?** ✅ prefs is a per-user, untyped key/value store, with no versioning, validation or audit. ABL settings are system-wide and safety-relevant.

## 6. Plugin structure

```
local-plugins/dsh-abl-settings-r1/
  package.json          dsh.bundle.patch + dsh.client
  server/index.js       {name, inject:['connection'], apply(ctx)}
                        GET  /api/abl-settings/read    → hub /abl/call settings.get (+ voice mode, autoresponder status, model endpoints)
                        POST /api/abl-settings/write   → hub /abl/call settings.patch   (409/422 passed through)
                        POST /api/abl-settings/preview → hub /abl/call autoresponder.classify
                        all with a 5 s timeout, typed {ok:false, error, retry_after?} envelope
  client/index.js       window.__ModuleLoader__.load({id:'abl-settings', factory}) · React.createElement
  client/sections/*.js  Voice, Models, Autopilot, Quiet, Alerts, Speaker, Audit
```

- **Edit model:** per-section draft state, so an amber "unsaved" bar appears with Save/Discard. Save sends one PATCH with the
  version it loaded. A 409 shows "changed elsewhere": reload and re-apply the draft on top, with conflicts highlighted.
- **Degraded states:** hub down → the whole panel goes read-only, showing the last cached values and a slate banner. Write route locked at the
  hub → sections show a "locked (P2 approval pending)" chip, and preview still works (it's read-only).
- **Undo toast** for 10 s after a save: PATCHes the previous values back (a new version, also audited).

## 7. Build order (each step separately shippable and reversible)

| Step | Ships | Risk | Rollback |
|---|---|---|---|
| S1 | `abl_settings` module + schema + `GET /api/abl/settings` + read-only panel (all sections show effective values and their source) | none | remove the plugin folder |
| S2 | PATCH + audit + **quiet hours + speaker** (low-risk keys); migrate `companion_checkin` + bot.py to `quiet_now()` | low | `rm data/abl/settings.json` → hard-coded defaults again |
| S3 | Alert tier overrides + preview + autoresponder mode/channels (guardrails enforced server-side) | medium | unset `alerts.*` / `autoresponder.*` |
| S4 | Desktop pull endpoint + speaker.py/autopilot readers + per-device outbox + test button | medium | the desktop ignores settings when the pull fails (keeps local defaults) |
| S5 | Model role selection (probe-gated, confirm dialog) | highest | unset `models.*` → router defaults |

## 8. Exact code change for S3 (autoresponder reads the store)

```python
# src/autoresponder.py — category_tiers(): settings.json > env > defaults
def category_tiers() -> dict[str, Tier]:
    tiers = dict(DEFAULT_CATEGORY_TIERS)
    raw = _env("ABL_AUTORESPONDER_TIERS")
    ...                                                # existing env merge, unchanged
    try:
        from src import abl_settings                   # S1 module; absent → env/defaults only
        for cat in list(tiers):
            v = Tier.parse(abl_settings.get(f"alerts.tiers.{cat}"))
            if v is not None:
                tiers[cat] = v
    except ImportError:
        pass
    for cat in ("fraud", "security"):                  # safety floor, even if the file is hand-edited
        tiers[cat] = Tier.RED
    tiers["money"] = max_tier(tiers["money"], Tier.YELLOW)
    return tiers
```
The floor is applied at the **consumer** as well as in PATCH validation, so a hand-edited
`settings.json` can't weaken it either.

## 9. Acceptance tests

1. A fresh install with no `settings.json` behaves byte-for-byte as today; the panel shows every value as "default".
2. Set quiet hours to 22:00–07:00 → `companion_checkin` and the autoresponder both honour it within 5 s, with no restart.
3. PATCH `alerts.tiers.fraud = GREEN` → 422. Hand-edit the file to GREEN → `category_tiers()["fraud"]` is still RED.
4. Two tabs: save in A, then save in B → B gets 409 and the merge UI.
5. A YELLOW message at 02:00 with `quiet.yellow_policy=hold` → `due_at` is 09:01, and the inbox shows it in the morning batch.
6. Desktop offline for 10 minutes → the panel shows the amber chip. The desktop comes back → it applies the latest version once and logs it.
7. Hub stopped → the panel is read-only with the cached values, nothing is blank, and it recovers without a reload.
8. Delete `settings.json` → everything reverts to defaults; the audit log keeps its history.

## 10. Decisions for Asif
1. `quiet.yellow_policy` default: `hold` (recommended) or `send`?
2. Quiet-hours breakthrough: only urgent RED (fraud/security), or all RED including money?
3. Should `money` be lowerable to YELLOW from the panel at all, or locked at RED like fraud/security?
4. The names of the 8 desktop screen-off systems, for `autopilot.systems.*`.
