# Codex Task Audit

**Date:** 2026-11-03 · **Manifest:** `~/abl-fused/odysseus/data/abl/codex_work_manifest.json` (127 tasks, per PROMPT.md)

## Read this first: what this document is and isn't

The cloud session **could not read the manifest**. abl1 is not reachable from the cloud, and
`data/` is gitignored in both `abl` and `abl-fused`, so neither snapshot contains it. This
document therefore has two parts:

1. **An estimate** (§2–§3): the work Codex was carrying, reconstructed from PROMPT.md, the
   Codex handoff decisions, and the code snapshots. Every row says what the evidence is.
   Treat the statuses as hypotheses.
2. **The real audit, one command away** (§1): `tools/codex_manifest_audit.py` reads all
   127 tasks, checks every file/port a task names against the disk, and writes the verified,
   prioritised TODO. The local session should run it and append its output to §4.

---

## 1. Run the real audit (local session, on abl1)

```bash
cd ~/abl-cloud-session
python3 -m pytest tools/tests -q                       # 33 passed
python3 tools/codex_manifest_audit.py --probe-ports    # reads the manifest, checks ~/abl ~/abl-fused + plugins
# → ~/abl/reports/codex_todo_20261103.md   (human)
# → ~/abl/reports/codex_audit_20261103.json (machine)
# optional: push to a LOCAL coordination endpoint (non-local URLs are refused)
python3 tools/codex_manifest_audit.py --push http://127.0.0.1:7800/<coordination-endpoint>
```
> `:7800/build` is an HTML **page** in the Aug snapshot ✅ (`routes/abl_routes.py:641`), not a POST API. The closest existing write surface is `POST /api/abl/builder-ask` ✅. Confirm which endpoint the coordination API actually is before using `--push`. The tool reports the HTTP status either way.

**How it judges each task**

| Verdict | Rule | Action |
|---|---|---|
| `VERIFIED_DONE` | claimed done, every named file exists | none |
| `DONE_MISSING` | claimed done, named files **missing** | **reopen**: top of the TODO |
| `DONE_UNVERIFIABLE` | claimed done, names nothing checkable | spot-check |
| `MAYBE_DONE` | claimed open, but its files exist | verify, then close |
| `PARTIAL` | claimed open, some files exist | finish |
| `PENDING` | open, nothing exists | schedule |
| `BLOCKED` / `DROPPED` | as claimed | decide / archive |

Scoring for the TODO: explicit priority (P0 = +50 … P4 = 0), then the strongest keyword
bucket (security/egress/auth/exposed +40; crash/broken/data-loss +30; voice/router/dsh/console
+20; bug/fix/wire +10), DONE_MISSING +30, PARTIAL +10, a named port that is down +10.
Write/schema/unlock work gets −10, because it needs an approval gate, not a quick build.
Tasks stale for more than 45 days get −5. The loader accepts a list, `{tasks|items|work|entries: [...]}`, or an id→task map; unknown fields pass through.

---

## 2. Estimated workstreams and status (from context, not the manifest)

| # | Workstream (likely manifest category) | Est. status | Evidence |
|---|---|---|---|
| 1 | **Model router** (PRIMARY/LIGHTWEIGHT/NONE, Jev interface) | Done | 📄 557 lines on abl1, wired into llm_core egress fallback |
| 2 | **Jev activation** | Blocked on `TYPESAFE_API_KEY` | 📄 "no API key set yet". Autoresponder falls back to DeepSeek/local meanwhile |
| 3 | **27B serving** on :8085 | Done, stable | 📄 `abl-llama-27b.service`, `-ngl 35 -np 2` |
| 4 | **Silent 3B fallback** every ~5.5 min | Open (symptom) | 📄 A cloud lane is failing quietly. Needs router JSONL + alerting (spec §6) |
| 5 | **Console plugin** `dsh-abl-console-r1` | Done; needs polish | 📄 5 tabs, `/api/abl-console/read` |
| 6 | **dsh branding / phone layout** | Done | 📄 palette applied, phone layout plugins, 365-day cookie |
| 7 | **Hub API** mirror (`/abl/catalog`, `/abl/call`) | Done (read); writes locked | 📄 9,219 lines; "write routes locked pending approval" |
| 8 | **Hub write unlocks** | Blocked (approval) | 📄 This is spec Phase 2. Each one needs a rollback demo |
| 9 | **Plugin cleanup** | Done | 📄 10 dead folders backed up 2026-11-02 |
| 10 | **one-project-context-v1** stale marker | Open → fix ready | `fixes/fix_one_project_context.py` |
| 11 | **Fleet registry** desktop IP | Open → fix ready | `fixes/fix_fleet_ip.py` |
| 12 | **Errands scanner** `/mnt/c` path | Open → fix ready | `fixes/fix_errands_path.py` |
| 13 | **abl-market-bridge** crash loop | Open → diagnoser ready | `fixes/diagnose_unit.py`; hypothesis: the options desk it needs was stopped |
| 14 | **OllamaEmbedder** warm-up refused | Open → likely cause found | ✅ `embedding_base_url` points at the Mac satellite (BUG_FIXES B5) |
| 15 | **Options engine** | Stopped deliberately (RAM) | 📄 6.8 GB. Its dependents must tolerate it being gone |
| 16 | **Desktop 35B :8095 on 0.0.0.0** | Open, **security** | 📄 BUG_FIXES B6 |
| 17 | **ears.py → dead :7800** | Open, voice broken | 📄 BUG_FIXES B7 / spec §7 |
| 18 | **Duplicate desktop Odysseus** | Done (stopped) | 📄 freed 750 MB |
| 19 | **Autoresponder** (AUTORESPONDER_SPEC.md) | **Built this session** | `src/autoresponder.py`, 53 tests; ships in shadow mode |
| 20 | **Unification spec** | **Written this session** | `ABL_UNIFICATION_SPEC.md` |
| 21 | **Screen-off systems** (autopilot, briefing, research bridge, finance voice, chimes …) | Built on desktop; **broken by #17** | 📄 8 systems. They depend on the ears/speaker → ABL path |
| 22 | **Egress gateway** audit → enforce | Built ✅; mode on abl1 unknown | `core/egress_gateway.py`. Mode policy "block_personal" chosen 2026-07-18 ✅ |
| 23 | **Ollama load timeout** | Done | 📄 `OLLAMA_LOAD_TIMEOUT=60m` drop-in |

**Rough split (estimate):** about 40% of the named workstreams are done, about 35% are open with a fix
or diagnoser now ready in this repo, and about 25% are blocked on a decision or key. The 127-task
manifest is finer-grained than this table. Expect the tool to find several `MAYBE_DONE`
items (Codex often finishes work without flipping status) and a handful of `DONE_MISSING` ones.

---

## 3. Prioritised TODO (estimate; re-rank with the tool's output)

| Rank | Item | Why it's here | Effort | Ready? |
|---:|---|---|---|---|
| 1 | Bind desktop 35B :8095 to 127.0.0.1 (B6) | Security: LLM exposed on every interface | 5 min | ✅ exact change |
| 2 | Reroute ears.py off dead :7800 (B7) | Voice and all 8 screen-off systems are dark | 30 min | ✅ snippet; needs the token decision |
| 3 | market-bridge crash loop (Bug 4) | Burning CPU and journal; desks data stale | 15 min diagnose + fix | ✅ diagnoser + backoff drop-in |
| 4 | Deploy autoresponder in **shadow** | Zero-risk; starts collecting the audit log that tunes the rules | 10 min | ✅ |
| 5 | Fleet IP (Bug 1) | Fleet tab and any desktop-directed call are wrong | 5 min | ✅ |
| 6 | Embedder warm-up (B5) | Noise on every start; possible slow recall | 5 min | ✅ config diff |
| 7 | one-project-context (Bug 3) | Restart reliability of the whole UI | 10 min + restart test | ✅ |
| 8 | Errands path (Bug 2) | A scanner doing nothing on abl1 | 5 min | ✅ |
| 9 | Find the failing cloud lane behind the 3B fallback | Hidden degradation | 1 h | spec §6 |
| 10 | Spec P1: loopback binds + `tailscale serve` + Wave-1 plugins | One origin; closes the LAN exposure | 1–2 sessions | spec §9 |
| 11 | `TYPESAFE_API_KEY` → Jev live | Better triage, and lets the autoresponder drop DeepSeek | key + 15 min | blocked on key |
| 12 | Hub write unlocks (P2), one at a time | Each with a rollback demo | per route | blocked on approval |

---

## 4. Tool output (local session: paste here)

```
<paste the summary line and the Prioritised TODO table from ~/abl/reports/codex_todo_<date>.md>
```
Then reconcile: anything the tool marks `DONE_MISSING` outranks rows 5–12 above.
