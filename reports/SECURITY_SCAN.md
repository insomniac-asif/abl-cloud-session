# Security scan: abl, abl-fused, abl-cloud-session

**Date:** 2026-11-03 · **Scope:** git-tracked files at HEAD of `insomniac-asif/abl` (`48b6289`), `abl-fused` (`5dcc8be`),
and this repo. These are code snapshots from 2026-08-05; abl1 runs newer code, and runtime state (open ports, file modes) can only be seen on the
box. Run `tools/security_audit_abl1.sh` on abl1 **and** on the desktop WSL to cover that.
**Not covered:** git history (shallow clones), the helper `/home/sun-night/lib/abl_control_auth.py` (outside the repos), dsh plugins, desktop `~/ops/speaker`.

## Summary

| # | Severity | Finding | Where | Status |
|---|---|---|---|---|
| H1 | **HIGH** | Unauthenticated ABL core dashboard on `0.0.0.0:8081`: anyone on the LAN can list **and approve** agent proposals ("HUMAN OVERRIDE") and inject chat as the operator. `DashboardAuth` exists but is never wired in | `src/abl/web/app.py`, `src/abl/orchestrator.py:814` | fix below |
| H2 | **HIGH** | `gpuctl` on `0.0.0.0:7900` embeds the owner control token in its **unauthenticated** `GET /` page, so any LAN host can read it and POST `/api/free` (stops abl-backend + crons) | `odysseus/gpuctl.py:92,121` | fix below |
| H3 | **HIGH** (runtime, desktop) | Windows 35B llama-server on `0.0.0.0:8095`, an unauthenticated LLM endpoint on the LAN. Likely produced by the cookbook defaults (M2) | desktop launcher | DEPLOYMENT_GUIDE Step 1b |
| M1 | MEDIUM | Mesh peer server binds `0.0.0.0` and **falls back to plaintext** when certs are missing ("running without TLS"); its handlers include memory store | `src/abl/peer/__main__.py:160-175` | fix below |
| M2 | MEDIUM | Cookbook generates `vllm` / `sglang` / `llama-server` / `llama_cpp.server` launch commands with `--host 0.0.0.0` by default (no auth on those servers) | `odysseus/static/js/cookbook.js:403,435,530,534`, `cookbook-hwfit.js:1180,1190` | fix below |
| M3 | MEDIUM | Odysseus backend on `0.0.0.0:7800` (app auth exists, but it's exposed to the whole LAN) | `odysseus/run_abl.sh:5` | ufw now (Guide Step 2), loopback in spec P1 |
| L1 | LOW | Mesh client TLS sets `check_hostname = False`. Acceptable: TLS 1.3 mutual TLS against a **private CA** with `CERT_REQUIRED` | `src/abl/peer/tls.py:61` | accept; document |
| L2 | LOW | `shell=True` in `builtin_actions.py` (admin-only actions, gated in `task_routes.py:442`) and `borea_toolbelt.py` (behind ToolConsensusGate 3/3) | as listed | intentional; keep gates covered by tests |
| L3 | LOW | Template unit `odysseus-ui.service` uses `--host 0.0.0.0` | `odysseus/odysseus-ui.service:12` | change the template default |
| ✅ | — | **No real secrets in tracked files.** About 30 hits, all test fixtures (`sk-abc…`, fake `ghp_…`) used to test the redaction code, plus the AWS documentation example key `AKIAIOSFODNN7EXAMPLE` in a doc. No tracked `.env` / `.token` / `.key` / `.pem` files | — | — |
| ✅ | — | No `yaml.load` without a Loader, no `pickle`, no wildcard CORS, no `debug=True`, no `verify=False` in non-test code. The `exec` uses run constant local probe code | — | — |

---

## H1: unauthenticated dashboard can approve agent proposals

**Evidence:** `orchestrator._run_web_server` serves `create_app(...)` with `host="0.0.0.0"` (port `web_port = 8081`).
In `src/abl/web/app.py`:
- `GET /proposals` lists the proposals, including their IDs;
- `POST /proposals/{id}/approve` sets `status="approved"` ("HUMAN OVERRIDE: proposal … approved via dashboard");
- `POST /chat/send` broadcasts a `UserInputPayload` as `AgentID.HUMAN`, HMAC-signed with the server's secret, with `user_role` taken from the request;
- `WS /chat/stream` streams the bus.

None of these routes check anything. `src/abl/web/auth.py::DashboardAuth` (token, 24 h sessions,
`~/.abl/dashboard_token`) is referenced **nowhere** outside its own file.

**Impact:** any device on the LAN (or a malicious page in a LAN browser, via DNS rebinding or a simple form POST)
can approve a pending Borea tool execution and bypass the 3/3 consensus the whole architecture rests on.

**Fix (exact):**
```python
# src/abl/web/app.py, inside create_app(...), before the routes
from fastapi import Depends, Request, HTTPException
from abl.web.auth import DashboardAuth
_auth = DashboardAuth(_token_path=Path("~/.abl/dashboard_token").expanduser())

def require_owner(request: Request) -> None:
    # DashboardAuth API (src/abl/web/auth.py): validate_token(token) -> session_id | None,
    # validate_session(session_id) -> bool. Bearer token for scripts, session cookie for the browser.
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if bearer and _auth.validate_token(bearer):
        return
    sid = request.cookies.get("abl_dash_session", "")
    if sid and _auth.validate_session(sid):
        return
    raise HTTPException(401, "dashboard token required")

# login: POST /auth/login {token} -> sid = _auth.validate_token(token); set cookie abl_dash_session=sid
#        (HttpOnly, SameSite=Strict); this one route stays unauthenticated.

# then add `dependencies=[Depends(require_owner)]` to EVERY route except a bare /system/health,
# and for the websocket check the cookie/token before websocket.accept()
# also: pin user_role="operator" server-side in /chat/send and never read it from the body
```
```diff
# src/abl/orchestrator.py: bind loopback unless explicitly configured
-            host="0.0.0.0",
+            host=os.environ.get("ABL_WEB_HOST", "127.0.0.1"),
```
(and the BL-120 pre-flight probe at line ~797 uses the same host). Per the codebase's own rules, add an AST
invariant test that every `@app.post`/`@app.websocket` in `web/app.py` carries the auth dependency, the same pattern as BL-84 / PATTERNS #68.
**Verify:** `curl -s -o /dev/null -w '%{http_code}' -X POST http://<lan-ip>:8081/proposals/00000000-0000-0000-0000-000000000000/approve` → connection refused (loopback bind) or `401`.

## H2: gpuctl hands its control token to anyone who asks

**Evidence:** `do_GET("/")` returns `PAGE.replace("__CTRL_TOKEN__", auth.token_for_page())` with no check on the caller.
`token_for_page()` takes no request context, so it can't discriminate. `do_POST` correctly requires the token, which
any LAN client can just read from the page first. `/api/free` stops `abl-backend`, `abl-watch` and every heavy cron.
**Fix (exact):**
```diff
-PORT = int(os.environ.get("ABL_GPUCTL_PORT", "7900"))
+PORT = int(os.environ.get("ABL_GPUCTL_PORT", "7900"))
+HOST = os.environ.get("ABL_GPUCTL_HOST", "127.0.0.1")     # tailnet IP if the phone needs it, never 0.0.0.0
 ...
     def do_GET(self):
         p = self.path.split("?", 1)[0]
         if p in ("/", "/index.html"):
-            token = auth.token_for_page() if auth else ""
+            # Only a local browser gets the token pre-filled; remote clients must type it once (kept in localStorage).
+            local = self.client_address[0] in ("127.0.0.1", "::1")
+            token = auth.token_for_page() if (auth and local) else ""
             return self._send(200, PAGE.replace("__CTRL_TOKEN__", token), "text/html; charset=utf-8")
 ...
-    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
+    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
```
The page JS should read the token from `localStorage` when the placeholder is empty, and prompt for it once.
Check `price_verify` and `gallery` too: the comment says they share `abl_control_auth`, so the same leak pattern is likely there.
**Verify:** from another LAN host, `curl -s http://<desktop>:7900/ | grep -c <first 6 chars of token>` → `0` (or connection refused).

## M1: mesh peer server falls back to plaintext on all interfaces
```diff
# src/abl/peer/__main__.py
-            if errors:
-                logger.warning("TLS cert issues: %s — running without TLS", errors)
+            if errors:
+                if os.environ.get("ABL_PEER_ALLOW_PLAINTEXT") != "1":
+                    raise SystemExit(f"peer: TLS cert issues {errors}; refusing to serve plaintext "
+                                     "(set ABL_PEER_ALLOW_PLAINTEXT=1 for a lab-only override)")
+                logger.warning("TLS cert issues: %s — running WITHOUT TLS (explicit override)", errors)
 ...
-    config = PeerServerConfig(host="0.0.0.0", port=port)
+    config = PeerServerConfig(host=os.environ.get("ABL_PEER_HOST", "127.0.0.1"), port=port)  # tailnet IP for the mesh
```
Do the same for the "No TLS certs configured" branch. `[nodes.oracle] enabled = false` in `config/mesh.toml` reduces exposure today, but the default is still fail-open.

## M2: cookbook launches model servers on all interfaces
In `cookbook.js` / `cookbook-hwfit.js`, default the generated `--host` to `127.0.0.1`, with a visible checkbox
"Expose on network (no auth!)" that switches it to the **tailnet IP**, never `0.0.0.0`. That's the same choice
`cookbook_routes.py:900` already makes for Ollama (`"0.0.0.0" if remote else "127.0.0.1"`). Extend it to every backend,
and require `remote` to be an explicit opt-in.

## M3: Odysseus on 0.0.0.0:7800
Covered by DEPLOYMENT_GUIDE Step 2 (ufw allows only `tailscale0` and `lo`), and by spec P1 (loopback + `tailscale serve`).

---

## Run on the boxes
```bash
bash ~/abl-cloud-session/tools/security_audit_abl1.sh       # abl1
bash tools/security_audit_abl1.sh                           # desktop WSL (gpuctl, :8081, the 35B port)
```
It's read-only. It lists every non-loopback listener with its process, ufw state, whether H1/H2 are live, secret-file
permissions (`dev.token`, `.credentials.yaml`, `~/.config/abl/token`, IMAP password, `.env`), tracked secret-like files, tailscale serve/funnel,
and any plaintext peer starts in the logs.

## Fix order
1. **H1** (approve-bypass), **H2** (token leak), **H3** (35B bind): today.
2. Firewall (Guide Step 2), **M1**, **M2**.
3. L-items when the code is next touched.
