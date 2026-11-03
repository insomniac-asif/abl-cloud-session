# dsh-abl-console-r2: ABL Console, revision 2

A polished replacement for `dsh-abl-console-r1`. Its `client.js` exists only on abl1, so it couldn't be edited from
the cloud session; this is a new revision per the plugin-revision convention. **Install it beside r1, verify, then remove r1.**

| r1 | r2 |
|---|---|
| probes ~7 services from the plugin | one call: `/api/abl-console/dashboard` → Odysseus `GET /api/abl/dashboard` (`src/abl_dashboard.py`) |
| blank cards when a service is down | every card has a **spinner** while loading, and **"Service unreachable"** with the reason and a Retry button on failure |
| manual reload | **auto-refresh every 30 s** (paused while the tab is hidden, refreshes on return), plus a ↻ button |
| no freshness info | **"updated Xs ago" on every card**, ticking each second; amber after 90 s |
| instant tab swap | **fade/slide tab transition** (180 ms; disabled under `prefers-reduced-motion`) |
| — | last good data is kept on a failed refresh, with a banner saying how old it is |

The same five tabs: Home, Models & Router, Voice & Autopilot, Fleet, Desks. The ABL palette is navy, lavender, amber and slate.
The Odysseus token is read **server-side** from `tokenFile` and is never sent to the browser.

## Install (abl1)
Prerequisite: the dashboard route is wired in Odysseus (DEPLOYMENT_GUIDE Step 4b) and
`curl -s -H "Authorization: Bearer $(cat ~/abl/odysseus/data/abl/dev.token)" http://127.0.0.1:7800/api/abl/dashboard | jq .status` works.
```bash
cp -r ~/abl-cloud-session/plugins/dsh-abl-console-r2 ~/abl/tools/dsh/app/local-plugins/
cd ~/abl/tools/dsh/app && dsh plugin --profile web add ./local-plugins/dsh-abl-console-r2
node --test local-plugins/dsh-abl-console-r2/tests/server.test.mjs          # before restarting dsh-web
systemctl --user restart abl-dsh-web && journalctl --user -u abl-dsh-web -n 80 --no-pager | grep -iE 'console|plugin.*(fail|error)'
```
Open **Console** in the sidebar and check every tab. Then remove r1 with `dsh plugin --profile web remove dsh-abl-console-r1` (or move its folder to backups).
**Rollback:** `dsh plugin --profile web remove dsh-abl-console-r2`; r1 is untouched until you remove it.

## Tests
`ABL_TEST_NODE_MODULES=/path/with/react18/node_modules node --test tests/*.test.mjs`: 10 tests. The server tests cover the token
forwarded and not leaked, `fresh` passed through, and readable 401/500/unreachable/timeout errors. The client tests cover the factory-only
contract, every tab rendering a realistic snapshot, spinners, unreachable cards, stale stamps, and slot registration.
