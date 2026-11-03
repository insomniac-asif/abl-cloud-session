# ears.py → abl1 voice target patch

**Problem:** desktop `~/ops/speaker/ears.py` posts to `http://localhost:7800/api/abl/voice/heard`.
That was the desktop Odysseus, which is now **stopped**, so all voice input dies.
**Fix:** send to **`abl1.tailda08f9.ts.net:7800`** (tailnet), falling back to the
**SSH tunnel at `localhost:7800`**. The route is picked per call by health checks, and the owner bearer token is attached.

`ears.py` isn't in any repo, so this ships as a **patcher**, not a static diff. It rewrites
only the HTTP calls whose URL traces back to the `:7800` literal (constants, f-strings and
derived names included), and adds the token **only** to those calls. Tests: `tests/test_patch_ears.py` (13), including an
end-to-end run where the tailnet is down and the tunnel answers.

## Install (desktop, ~5 minutes)

**1. Token** (once; abl1 owner session, or ask the local CC session):
```bash
# on abl1, logged in as the owner: create an API token via the existing /tokens route
# (routes/api_token_routes.py); _require_owner on /api/abl/voice/* accepts owner tokens.
mkdir -p ~/.config/abl && chmod 700 ~/.config/abl
printf '%s' '<token>' > ~/.config/abl/token && chmod 600 ~/.config/abl/token     # on the DESKTOP
```

**2. Bridge + patch:**
```bash
cp src/voice_bridge.py ~/ops/speaker/
python3 patches/patch_ears.py ~/ops/speaker/ears.py            # dry run: read the diff + any MANUAL lines
python3 patches/patch_ears.py ~/ops/speaker/ears.py --apply    # backup: ears.py.bak-cloudcc-20261103
```

**3. Fallback tunnel** (keeps `localhost:7800` → abl1's `127.0.0.1:7800`):
```bash
ssh -N -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
    -L 127.0.0.1:7800:127.0.0.1:7800 rescue@192.168.1.208
```
Make it persistent with `autossh -M 0 …` (same flags) under a systemd user unit (WSL) or a Windows
scheduled task. `ExitOnForwardFailure=yes` matters: if something else holds 7800, the tunnel
exits loudly instead of silently "working".

**4. Verify:**
```bash
python3 ~/ops/speaker/voice_bridge.py --health    # expect: tailnet UP, tunnel UP, active = tailnet
# then restart ears.py and say something; abl1: journalctl --user -u abl-backend | grep voice
```

## What the patch does (real output on an ears.py-shaped file)
```diff
 """ears.py: Fifine mic -> faster-whisper VAD chunks -> ABL."""
+import os as _abl_os, sys as _abl_sys  # abl-voice-target-patch v1 (cloudcc 2026-11-03)
+_abl_sys.path.append(_abl_os.environ.get('ABL_VOICE_BRIDGE_DIR', _abl_os.path.dirname(_abl_os.path.abspath(__file__))))
+import voice_bridge as _abl_vb  # noqa: E402  tailnet first, SSH tunnel fallback, owner token
 import requests

 ABL_URL = "http://localhost:7800/api/abl/voice/heard"

 def send_chunk(wav_bytes):
-    r = requests.post(ABL_URL, files={"audio": ("chunk.wav", wav_bytes, "audio/wav")}, timeout=30)
+    r = requests.post(_abl_vb.live(ABL_URL), files={"audio": ("chunk.wav", wav_bytes, "audio/wav")}, timeout=30, headers=_abl_vb.auth_headers())
     return r.json()
```
`_abl_vb.live(url)` maps `localhost:7800` / `127.0.0.1:7800` (or any configured target) onto
whichever endpoint is healthy **at that moment**. Other URLs pass through untouched. The literal stays,
so if the patch is ever reverted, ears.py behaves exactly as before.

## Behaviour

| Situation | Result |
|---|---|
| Tailnet up | `abl1.tailda08f9.ts.net:7800` (WireGuard-encrypted) |
| Tailnet down, tunnel up | `127.0.0.1:7800` → SSH → abl1 |
| Tailnet recovers | moves back within one health interval (`ABL_VOICE_HEALTH_INTERVAL`, default 10 s) |
| Both down | the call goes to the primary and fails; ears.py's own error handling applies, and the health loop keeps probing |
| No token | abl1 answers 403; the bridge logs one warning at start-up |

`ears.py`'s existing timeout and retry code is unchanged. For buffering, failover-safe retries, and
the deafen gate, switch the send to `_abl_vb.get_bridge().send_audio(wav_bytes)`, which returns
`SendResult(action, reply, transcript, status)`.

## ⚠ Fallback port hazard
The requested fallback, `localhost:7800`, is the port the **stopped desktop Odysseus** used.
If it's ever started again, it takes 7800 first. Then the tunnel can't bind, and the "fallback"
quietly talks to the **desktop** Odysseus, not abl1. Two mitigations:
- `ExitOnForwardFailure=yes` (above) makes the conflict visible.
- **Recommended:** a dedicated tunnel port, with no code change:
  ```bash
  ssh -N … -L 127.0.0.1:17800:127.0.0.1:7800 rescue@192.168.1.208
  export ABL_VOICE_TARGETS=http://abl1.tailda08f9.ts.net:7800,http://127.0.0.1:17800
  ```
  `live()` still remaps the legacy `localhost:7800` literal onto whichever target is healthy.

## Rollback
```bash
cp ~/ops/speaker/ears.py.bak-cloudcc-20261103 ~/ops/speaker/ears.py
```
