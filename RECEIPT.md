# Cloud Session Receipt: 2026-11-03

**Session:** ABL Unification Sprint + Autoresponder Build (cloud, Claude Code)
**Branch:** `claude/affectionate-pascal-2r7tzq` · **Deploy target:** abl1 (`rescue@192.168.1.208`), via a local session
**Copied to abl1 by the deploy script as:** `~/abl/CLOUD_SESSION_RECEIPT_20261103.md`

## Access reality (read first)
The original brief assumed SSH to abl1. The cloud container has no route to `192.168.1.0/24`
or the tailnet, and no `ssh` binary. The desktop's Remote Control folders aren't offered to
cloud sessions. So **nothing on abl1 or the desktop was read or changed in this session.**
Everything was built against the newest code available: the private repos
`insomniac-asif/abl` and `insomniac-asif/abl-fused` (snapshots from 2026-08-05, about three months behind abl1).
Files newer than that (`model_router.py`, `alert_rules.py`, `AUTORESPONDER_SPEC.md`, the Codex
manifest, the dsh plugins, the errands scanner, the market bridge) were **not visible**. Code that couples
to them is written as tolerant adapters, with one place to adjust each.

## What was built

| Goal | Deliverable | Status |
|---|---|---|
| 1 | `ABL_UNIFICATION_SPEC.md`: one origin via `tailscale serve`, routing map, Tailscale-identity + scoped-token auth, 49-page plugin migration in waves, model roles, voice, single-writer data model, P0–P3 build order with exit tests and rollbacks | ✅ ready for review |
| 2 | `src/autoresponder.py` (`jev_classify`, GREEN/YELLOW/RED, 60 s hold queue, Jev → DeepSeek → 27B → 3B chain, alert_rules adapter, shadow/draft/live modes) + `routes/autoresponder_routes.py` | ✅ ready to deploy (shadow) |
| 3 | `tools/codex_manifest_audit.py` (verifies all 127 tasks against disk, ranks the TODO) + `CODEX_TASK_AUDIT.md` (context-based estimate plus run instructions) | ✅ tool ready; real numbers need one run on abl1 |
| 4 | `fixes/` (fleet IP, errands path, one-project-context drop-in, unit crash diagnoser) + `BUG_FIXES.md` with exact changes, verification and rollback | ✅ ready (dry-run first) |
| + | `BUG_FIXES.md` B5–B7: OllamaEmbedder root cause found (`embedding_base_url` → Mac satellite), 35B bind, ears.py reroute | ✅ |
| + | `deploy/deploy_abl1.sh`: dry run by default; backup, install, in-tree tests, self-test, fix dry runs, audit, health | ✅ |

### Autoresponder design guarantees (each pinned by tests)
1. **Deterministic floor.** The rules (built-ins plus `alert_rules.py`) set a minimum tier. Models can escalate it, never lower it, so prompt injection can't downgrade fraud.
2. **RED is terminal.** No model is called for RED; OTPs and scam links never leave the process.
3. **Egress-gated.** DeepSeek only goes through `core/egress_gateway`. It fails closed if the gateway is missing, and applies a strict policy (personal/financial content never goes hosted, even in audit mode). Hosted models never see the sender's identity.
4. **Writes pinned off.** Default mode is `shadow`. `live` sends only on allow-listed channels (the list is empty by default).
5. **Drafts re-checked.** A draft containing money, codes or links is dropped and the message is held.
6. **Hash-only JSONL audit** (same convention as the egress gateway). Drafts are scrubbed once resolved.
7. **Non-blocking.** HTTP routes run model calls in the threadpool, so a slow 27B can't stall the backend.

Category → tier defaults: fraud, security, money → **RED**; bills, people → **YELLOW** (bills moved from RED per Asif, 2026-11-03: 60 s review window); food, parcels → **GREEN**. A bill that also asks for money ("send $120 via Zelle") is still RED because the money rule wins. Override with `ABL_AUTORESPONDER_TIERS`.

## What was tested

| Suite | Result | Notes |
|---|---|---|
| `tests/test_autoresponder.py` | **54 passed** | includes the real `core/egress_gateway.py` from the `abl` snapshot |
| same suite inside a copy of the real Odysseus tree (its conftest) | **54 passed** | run by `deploy_abl1.sh --apply` end-to-end |
| `tools/tests` | **33 passed** | all manifest shapes; status normalisation; verdicts; scoring; local-only push |
| `fixes/tests` | **27 passed** | token-exact IP edits; tokenize-only rewrite; drop-in path safety; 17 crash signatures |
| **Total from repo root** | **114 passed** | `python3 -m pytest -q` |
| `python -m src.autoresponder --self-test` | 10/10 | rules only, no network |
| `deploy/deploy_abl1.sh` dry run + `--apply` against a fake HOME with a real Odysseus copy | passes | health checks WARN as expected (no services in the cloud) |
| pyflakes over all Python; `bash -n` on the deploy script | clean | |

Bugs caught by the tests during the build and fixed: "partially done" read as done; optional-port regexes never capturing the port; `/tmp` marker scan picking up unrelated deep paths for the drop-in; blocking model calls on the event loop.

## What the local session should do on abl1 (in order)

```bash
git clone -b claude/affectionate-pascal-2r7tzq https://github.com/insomniac-asif/abl-cloud-session ~/abl-cloud-session
cd ~/abl-cloud-session
bash deploy/deploy_abl1.sh            # dry run: read the output
bash deploy/deploy_abl1.sh --apply    # installs the autoresponder files + docs, runs in-tree tests
```
1. Wire the routes in `odysseus/app.py` (2 lines, printed at the end of the deploy), restart the backend, then `curl :7800/api/health` and `curl :7800/api/abl/autoresponder/status`.
2. Apply the fixes one by one per `BUG_FIXES.md`: B6 (35B bind) and Bug 4 (market bridge) first.
3. Run `tools/codex_manifest_audit.py --probe-ports` and paste the result into `CODEX_TASK_AUDIT.md` §4.
4. Close-out checks from the brief: `curl :7800/api/health`, `systemctl --user status abl-dsh-web`, `curl :8085/health`, `nvidia-smi; free -h`, `git status` in `~/abl/odysseus` and `~/abl`.

## Questions and decisions for Asif before deployment

1. **Autoresponder tiers.** ~~Bills~~ resolved: YELLOW. People YELLOW, food/parcels GREEN: confirm. Also compare against `AUTORESPONDER_SPEC.md`, which wasn't readable from the cloud.
2. **DeepSeek for personal messages.** The strict policy means most real messages from people will be triaged **locally** even when DeepSeek is configured. That is deliberate (personal content stays local). Do you want that, or to allow hosted triage in egress `audit` mode?
3. **Which channel goes live first**, and what's the auto-reply signature? (Default suffix: "— (auto-reply from Asif's assistant)".)
4. **Who sends?** The autoresponder needs a `sender_fn` per channel (SMS/Discord/email). None is wired yet, which is fine for shadow/draft mode.
5. **Spec §12 decisions:** Tailscale-identity auto sign-in; desktop 35B as a `review` role vs. 127.0.0.1-only; STT on the desktop vs. abl1; owner of ideation/projects; the eight unlisted pages; red/green palette additions.
6. **Coordination API.** `:7800/build` is a page in the Aug snapshot. Which endpoint should the manifest audit `--push` to?
7. **`TYPESAFE_API_KEY`.** When it arrives, does model_router expose Jev under one of `jev_call / call_jev / jev_complete / jev_chat / jev`? If not, set `ABL_JEV_CALLABLE=module:function`.

## Still pending / not done
- Nothing was deployed to or verified on abl1 or the desktop (no access).
- Routes are not yet wired into `app.py`; that's a deliberate manual step with a backend restart.
- Real Codex manifest numbers (needs one run on abl1).
- Market bridge root cause (diagnoser plus mitigation are ready; the cause needs its journal).
- Spec phases P1–P3; the `dsh-abl-inbox-r1` plugin (Wave 2) isn't started.

## Files
```
ABL_UNIFICATION_SPEC.md   BUG_FIXES.md   CODEX_TASK_AUDIT.md   RECEIPT.md   README.md
src/autoresponder.py      routes/autoresponder_routes.py      routes/__init__.py (local tests only; not deployed)
tests/test_autoresponder.py
tools/codex_manifest_audit.py      tools/tests/test_codex_manifest_audit.py
fixes/_common.py  fix_fleet_ip.py  fix_errands_path.py  fix_one_project_context.py  diagnose_unit.py  tests/test_fixes.py
deploy/deploy_abl1.sh
```

---

## Follow-up batch (after PR #1 merged, same session)

| Item | Deliverable | Tests |
|---|---|---|
| Bills tier | RED → **YELLOW** (60 s review window). A bill that asks for money is still RED via the money rule. Drafts mentioning payments are still dropped | +4 |
| Voice bridge | `src/voice_bridge.py`: desktop ears → abl1 `POST /api/abl/voice/heard`. Tailnet first, SSH tunnel fallback, health loop, per-endpoint backoff, failback to the primary, connect-phase-only failover (no double replies), 30 s offline buffer, deafen gate, tailnet/loopback-only targets | 33, against real local HTTP servers |
| Settings panel spec | `specs/DSH_SETTINGS_PANEL_SPEC.md`: models, autopilot, quiet hours, tier overrides, speaker volume, built on one versioned settings store | n/a (spec) |
| ears.py patch | `patches/patch_ears.py` + `patches/EARS_PATCH.md`: call-site rewiring through `voice_bridge.live()`, owner token only on ABL calls, idempotent, backup + compile guard | 13, incl. end-to-end tailnet-down → tunnel |
| `:7800` scan | `reports/LOCALHOST_7800_SCAN.md`: 243 hits → 6 live call sites → **2 to change**, 4 correct as-is | n/a |

**Corrections to earlier docs:** `/api/abl/voice/*` `_require_owner` **already accepts an
Asif-owned bearer token**. The earlier "token branch must land first" claim was wrong, and is fixed in the spec §4.2 and
BUG_FIXES B7. Spec §7's offline buffer is now 30 s, matching the implementation.

**New findings that need action:**
1. **Desktop gamemode can resurrect the stopped Odysseus.** `gamemode_watch._reconcile()` `pm2 start`s
   `abl-backend`, `abl-watch`, `abl-fleet-registry` and `abl-node-health` every 10 s unless they are
   `pm2 delete`d. Fix on the desktop: `pm2 delete abl-backend abl-watch abl-fleet-registry abl-node-health && pm2 save`.
2. **The ears fallback port 7800 collides** with that same desktop Odysseus if it ever comes back.
   A dedicated tunnel port (`17800` + `ABL_VOICE_TARGETS`) is documented as a no-code switch.
3. **abl1 :7800 listens on 0.0.0.0** (`run_abl.sh`), so it's reachable from the LAN as well as the tailnet. ufw rules are in the scan report.
4. Quiet hours are hard-coded in at least three places (the settings spec makes them one source of truth).
5. The desktop speaker and the phone share `/api/abl/voice/pending`, which marks the *phone* as seen. A per-device outbox is needed before the desktop polls it.

**Totals:** 163 tests passing from the repo root (`python3 -m pytest -q`); pyflakes clean.

**New decisions for Asif:** `quiet.yellow_policy` hold vs send; quiet-hours breakthrough scope;
whether `money` may be lowered to YELLOW; the 8 desktop autopilot system names; and the ears tunnel port (7800 as asked, or 17800 as recommended).

---

## Third batch (PR #3, same session)

| Item | Deliverable | Tests |
|---|---|---|
| Settings panel plugin | `plugins/dsh-abl-settings-r1`: `package.json`, `cordis.patch.yml`, server `index.mjs`, `client.js`, `schema.mjs`, built against the published deepseek-harness contracts. It's the single writer of `settings.json` (atomic, versioned 409, validated 422, undo, audit, corrupt-file refusal) | 19 node |
| Settings reader | `src/abl_settings.py`: cached reads, floors, `quiet_now()`; defaults pinned to `schema.mjs` | 14 |
| Autoresponder ← settings | mode/tiers/channels/hold come from settings.json > env > default, with floors always re-applied; **YELLOW drafts created in quiet hours wait until morning** | +6 |
| Email classifier | `src/alert_rules.py`: 17 rules, each returning (tier, category, evidence). Covers spoofing, lookalikes, auth failures, link mismatch, risky attachments; extracts amounts, due dates and tracking; codes masked | 29 |
| Morning briefing | `src/morning_briefing_abl1.py`: weather, overnight mail, autopilot recap, bills due, calendar, rendered as JSON plus TTS lines | 9 |
| Spending summary | `src/finance_voice_abl1.py`: bank/P2P alert parsing, daily summary, no egress, phishing excluded | 13 |
| Deployment | `DEPLOYMENT_GUIDE.md` (11 steps, verify + rollback each), `deploy/systemd/*` timers, `abl1.env.example`; the deploy script installs every abl1 module and runs its tests in-tree | e2e run |
| Security | `reports/SECURITY_SCAN.md` + `tools/security_audit_abl1.sh` (read-only, on-box) | n/a |

**Bugs found and fixed along the way:**
- The autoresponder's file loader failed on any `alert_rules.py` that uses `@dataclass`. It now registers the module before exec.
- An explicit `ABL_ALERT_RULES_PATH` was ignored once another `alert_rules` module was importable. The explicit path now wins.
- The phishing rule missed bait carried in an attachment's *filename*. It now checks the name too.
- Year-less due dates used a parsing form that Python 3.15 deprecates. They now parse with the reference year.
- Timer tests depended on the wall clock once quiet hours existed. They now use an isolated settings file.
- The settings parity test assumed the repo layout. It now locates `schema.mjs` (the in-tree deploy run caught this).

**Security, act today:**
- **H1:** the ABL core dashboard (`0.0.0.0:8081`) is unauthenticated. Anyone on the LAN can read all agent chat and inject operator messages, and the approve-override (a 3/3 bypass) is one wiring line from live. **Fixed by a tested patch:** `patches/abl_h1_dashboard_auth.patch` (15 new tests that all fail on the unpatched code).
- **H2:** `gpuctl` (`0.0.0.0:7900`) embeds its control token in an unauthenticated page. **Fixed by a tested patch:** `patches/abl_h2_gpuctl_token.patch` (10 tests that all fail on the unpatched file).
- **H3:** the Windows 35B is bound to `0.0.0.0`.

Exact fixes for all three are in `reports/SECURITY_SCAN.md`. No real secrets were found in tracked files.

**Totals:** 238 Python tests + 19 node tests passing; pyflakes clean.
