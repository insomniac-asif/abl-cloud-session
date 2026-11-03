# dsh-abl-settings-r1: ABL Settings panel

A dsh plugin bundle that adds a **Settings** entry to the sidebar (gear icon, last in the list), covering models, autopilot,
quiet hours, alert tiers and the autoresponder, and the speaker. It is the **single writer** of
`~/abl/odysseus/data/abl/settings.json`. Python consumers read it through `src/abl_settings.py`.

Built against the dsh contracts as published in `deepseek-ai/deepseek-harness`:
| Piece | Contract |
|---|---|
| `package.json` | `dsh.bundle.patch` → `cordis.patch.yml`; `dsh.client {platform: web}`; `exports["./client"]` → `client.js` |
| `cordis.patch.yml` | `- insert: [{id: abl-settings, name: dsh-abl-settings-r1, config: {...}}]` |
| `index.mjs` | Cordis object plugin, `inject: ['connection']`; `ctx.connection.fetch.register({path:'/api/abl-settings/*', methods, requestBody:'buffered', fetch})`. The routes ride the shared authenticated `/api` handler (dsh sign-in) |
| `client.js` | `window.__ModuleLoader__.load({id:'dsh-abl-settings-r1', factory})`; `require('react')` from the seeded module table; `ctx.slots.inject('sidebar.panellist' / 'main', () => ctx.slots.register(...))` |

## Routes
| Method | Path | Notes |
|---|---|---|
| GET | `/api/abl-settings/read` | values, effective values, source per key, schema, sections |
| POST | `/api/abl-settings/write` | `{version, set, unset}`; returns 200, 409 (`version_conflict`) or 422 (`invalid` + per-field errors). A corrupt file gives 503: writes are refused rather than overwrite it |
| POST | `/api/abl-settings/undo` | `{version}`: restores `settings.json.prev` as a new, audited version |
| GET | `/api/abl-settings/audit?limit=N` | the newest records from `settings_audit.jsonl` |

**Guardrails, enforced on the server:** `alerts.tiers.fraud` and `alerts.tiers.security` are locked at RED, and
`alerts.tiers.money` can't go below YELLOW. They are re-applied on every read, both here and in `abl_settings.py` /
`autoresponder.category_tiers()`, so a hand-edited file can't weaken them. Live mode needs at least one channel.

## Install on abl1
```bash
cp -r plugins/dsh-abl-settings-r1 ~/abl/tools/dsh/app/local-plugins/
cd ~/abl/tools/dsh/app && dsh plugin --profile web add ./local-plugins/dsh-abl-settings-r1
dsh --profile web --dump-config | grep -A8 'dsh-abl-settings-r1'   # the layer shows up; no boot needed
# Optional override of settingsPath / autopilotSystems: restate the whole row in the profile's cordis.patch.yml
systemctl --user restart abl-dsh-web && journalctl --user -u abl-dsh-web -n 80 --no-pager | grep -iE 'abl-settings|plugin.*(fail|error)'
```
Check `ls ~/abl/tools/dsh/app/local-plugins/dsh-abl-console-r1` first. If the console is installed by some
other mechanism (path-based `insert` in the profile patch rather than `dsh plugin add`), install this one the same way.

## Verify
```bash
curl -s -b ~/.dsh-cookie http://127.0.0.1:3081/api/abl-settings/read | jq '.version, .effective["quiet.start"]'
python3 -c "import sys; sys.path.insert(0,'$HOME/abl/odysseus'); from src import abl_settings as s; print(s.quiet_now(), s.category_tiers())"
```
In the UI: Settings → Quiet hours → change *End* → Save → the toast offers Undo → **Recent changes** shows the edit.

## Tests
```bash
ABL_TEST_NODE_MODULES=/path/with/react18/node_modules node --test plugins/dsh-abl-settings-r1/tests/*.test.mjs   # 19
python3 -m pytest tests/test_abl_settings.py    # 14, including JS↔Python default parity
```

## Rollback
`dsh plugin --profile web remove dsh-abl-settings-r1`, then restart dsh-web. To reset values, delete `settings.json`
(every consumer falls back to the defaults, which equal the pre-panel behaviour); `settings_audit.jsonl` keeps the history.
