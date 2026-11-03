# abl-cloud-session

ABL cloud Claude Code session: unification sprint + autoresponder build (2026-11-03).
Everything here targets the Odysseus codebase on **abl1**. A local session deploys it.

| Start here | |
|---|---|
| [RECEIPT.md](RECEIPT.md) | What was built and tested, the deploy steps, open questions for Asif |
| [ABL_UNIFICATION_SPEC.md](ABL_UNIFICATION_SPEC.md) | Master blueprint: dsh becomes ABL |
| [BUG_FIXES.md](BUG_FIXES.md) | Exact fixes for the four known abl1 bugs, plus three bonus fixes |
| [CODEX_TASK_AUDIT.md](CODEX_TASK_AUDIT.md) | Codex manifest audit: estimate now, verified run on abl1 |
| [specs/DSH_SETTINGS_PANEL_SPEC.md](specs/DSH_SETTINGS_PANEL_SPEC.md) | Settings/Admin dsh plugin spec |
| [patches/EARS_PATCH.md](patches/EARS_PATCH.md) | Desktop ears.py → abl1 (voice bridge + patcher) |
| [reports/LOCALHOST_7800_SCAN.md](reports/LOCALHOST_7800_SCAN.md) | Hard-coded `:7800` audit + desktop gamemode resurrection risk |
| [PROMPT.md](PROMPT.md) | The original session brief |

```bash
python3 -m pytest -q                  # 163 tests (needs pytest; fastapi + httpx for the route tests)
bash deploy/deploy_abl1.sh            # on abl1: dry run
bash deploy/deploy_abl1.sh --apply    # on abl1: install + in-tree tests
```

Layout mirrors Odysseus: `src/`, `routes/`, `tests/` drop into `~/abl/odysseus/`;
`tools/` and `fixes/` run from this checkout.
