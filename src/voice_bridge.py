"""voice_bridge.py — desktop ears → abl1 Odysseus voice bridge (runs on the DESKTOP).

Routes each captured audio chunk from ``~/ops/speaker/ears.py`` to abl1's
``POST /api/abl/voice/heard`` over the tailnet, with an SSH-tunnel fallback, health
checks, reconnect/backoff, and a short offline buffer.

Contract (verified against routes/abl_voice_routes.py in the abl snapshot):
  POST {base}/api/abl/voice/heard   multipart field ``audio`` (≤ 25 MB)
        → {"transcript", "action": "speak"|"silent", "reply"?, "reason", "lane"?, "mode"?}
  GET  {base}/api/abl/voice/mode    → {"mode": "active"|"muted"|"deafened", ...}
  GET  {base}/api/health            → 200 when the backend is up
  Auth: ``_require_owner`` accepts the owner's session OR an **Asif-owned API bearer
  token**. The bridge sends ``Authorization: Bearer <token>``.

Endpoints, in priority order (override with ABL_VOICE_TARGETS, comma-separated):
  1. http://abl1.tailda08f9.ts.net:7800   tailnet (WireGuard-encrypted)
  2. http://127.0.0.1:7800                 SSH tunnel:  ssh -N -L 7800:127.0.0.1:7800 rescue@abl1
After spec P1 binds :7800 to loopback, set
ABL_VOICE_TARGETS=https://abl.tailda08f9.ts.net/machine,http://127.0.0.1:7800. That's a config change; no code changes.

RELIABILITY RULES
* **Health-driven routing.** A background checker probes every endpoint. Sends go to the
  highest-priority healthy endpoint, and traffic moves back to the tailnet as soon as it recovers.
* **Backoff per endpoint.** Exponential (1 s → 60 s, ±20 % jitter) after each failure.
  A down endpoint is skipped until its retry time, so a dead tailnet costs nothing per chunk.
* **Failover only when it's safe.** Both endpoints are the SAME backend. If the request
  never reached it (DNS, connect refused, connect timeout), the next endpoint is tried. If
  it was sent and the response timed out, abl1 may already have heard it and replied.
  Re-sending would double-reply and double-write memory, so the chunk is reported
  ``ambiguous`` and NOT retried.
* **4xx never retries.** 401/403 (token) or 413 (too big) won't get better on another route. They are
  surfaced loudly once.
* **Offline buffer is short.** Up to ``buffer_max`` chunks, each dropped after
  ``buffer_max_age_s`` (default 30 s). A late reply to old speech is worse than none.
  Buffered chunks flush oldest-first on recovery.
* **Deafened means nothing leaves.** ``should_capture()`` reads the backend mode (cached 5 s).
  ears.py should skip recording while it returns False. The backend also drops audio when deafened.
* **Tailnet/loopback only.** The audio is Asif's voice. Targets must be loopback, RFC1918,
  Tailscale CGNAT (100.64/10) or ``*.ts.net``. Anything else is refused at construction.
* **Token hygiene.** Read from ABL_TOKEN or ``~/.config/abl/token``. It is never logged or put in errors.

Stdlib only (the desktop speaker venv may be minimal). Usage:
  python voice_bridge.py --health                 # probe all endpoints, print JSON
  python voice_bridge.py --send clip.wav          # one-shot send, print the result
  python voice_bridge.py --watch                  # live health table every 5 s
In ears.py:  patches/patch_ears.py rewires the existing calls through live() + auth_headers();
for buffering/failover-safe sends use get_bridge().send_audio(wav_bytes). See patches/EARS_PATCH.md.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("abl.voice_bridge")

DEFAULT_TARGETS = ("http://abl1.tailda08f9.ts.net:7800", "http://127.0.0.1:7800")
HEARD_PATH = "/api/abl/voice/heard"
MODE_PATH = "/api/abl/voice/mode"
HEALTH_PATH = "/api/health"
MAX_AUDIO_BYTES = 25 * 1024 * 1024          # mirrors VOICE_MAX_AUDIO_BYTES on the backend
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


# ─────────────────────────────────────────────────────────────────────────────
# Target validation
# ─────────────────────────────────────────────────────────────────────────────

def is_allowed_target(url: str) -> bool:
    """Loopback / RFC1918 / Tailscale CGNAT IP literal, localhost, or *.ts.net only."""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.lower()
    if host == "localhost" or host.endswith(".ts.net"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False                         # arbitrary hostnames: refused (no DNS-based trust)
    return ip.is_loopback or ip.is_private or (ip.version == 4 and ip in _CGNAT)


def load_token(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    tok = (env.get("ABL_TOKEN") or "").strip()
    if tok:
        return tok
    path = Path(env.get("ABL_TOKEN_FILE") or "~/.config/abl/token").expanduser()
    try:
        return path.read_text("utf-8").strip()
    except OSError:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EndpointState:
    base: str
    priority: int
    healthy: bool = False
    checked: bool = False
    consecutive_failures: int = 0
    next_retry_at: float = 0.0
    last_ok_at: Optional[float] = None
    last_error: str = ""
    last_latency_ms: Optional[float] = None

    def public(self) -> dict:
        return asdict(self)


@dataclass
class SendResult:
    ok: bool
    action: str = "silent"                 # speak | silent
    reply: str = ""
    transcript: str = ""
    endpoint: str = ""
    latency_ms: float = 0.0
    status: str = "ok"                     # ok | buffered | ambiguous | rejected | offline | dropped
    error: str = ""
    raw: dict = field(default_factory=dict)


class _ConnectPhaseError(Exception):
    """The request provably never reached the server: safe to try the next endpoint."""


class _AmbiguousError(Exception):
    """The request may have been processed: do NOT re-send."""


class _RejectedError(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(f"HTTP {code}: {detail}")
        self.code = code


# ─────────────────────────────────────────────────────────────────────────────
# Bridge
# ─────────────────────────────────────────────────────────────────────────────

class VoiceBridge:
    def __init__(self, targets: Optional[list[str]] = None, *, token: Optional[str] = None,
                 connect_timeout: float = 3.0, read_timeout: float = 45.0, health_timeout: float = 2.0,
                 backoff_base: float = 1.0, backoff_max: float = 60.0,
                 buffer_max: int = 10, buffer_max_age_s: float = 30.0,
                 on_state_change: Optional[Callable[[str, str], None]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 opener: Optional[urllib.request.OpenerDirector] = None):
        raw = targets or [t.strip() for t in (os.environ.get("ABL_VOICE_TARGETS") or "").split(",") if t.strip()] \
            or list(DEFAULT_TARGETS)
        bad = [t for t in raw if not is_allowed_target(t)]
        if bad:
            raise ValueError(f"refusing non-tailnet/non-local voice target(s): {bad}")
        self.endpoints = [EndpointState(base=t.rstrip("/"), priority=i) for i, t in enumerate(raw)]
        self._token = token if token is not None else load_token()
        self.connect_timeout, self.read_timeout, self.health_timeout = connect_timeout, read_timeout, health_timeout
        self.backoff_base, self.backoff_max = backoff_base, backoff_max
        self.buffer_max, self.buffer_max_age_s = buffer_max, buffer_max_age_s
        self._buffer: deque[tuple[float, bytes, str]] = deque()
        self._on_state_change = on_state_change
        self._clock = clock
        self._lock = threading.RLock()
        self._online: Optional[bool] = None
        self._mode_cache: tuple[float, str] = (-1e9, "active")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
        self._rejected_logged: set[int] = set()
        self._flushing = False
        self.flushed_results: list[SendResult] = []
        if not self._token:
            log.warning("voice bridge: no token (ABL_TOKEN or ~/.config/abl/token); abl1 will answer 403")

    # -- HTTP primitives -------------------------------------------------------
    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {"User-Agent": "abl-voice-bridge/1"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if extra:
            h.update(extra)
        return h

    def _probe_connect(self, base: str) -> None:
        """Bounded TCP connect so a dead route fails in connect_timeout, not read_timeout."""
        p = urllib.parse.urlparse(base)
        port = p.port or (443 if p.scheme == "https" else 80)
        try:
            with socket.create_connection((p.hostname, port), timeout=self.connect_timeout):
                pass
        except OSError as e:   # DNS failure, refused, unreachable, connect timeout
            raise _ConnectPhaseError(f"{type(e).__name__}: {e}") from e

    def _request(self, method: str, url: str, *, body: Optional[bytes] = None,
                 headers: Optional[dict] = None, timeout: float) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, method=method, headers=self._headers(headers))
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace") if e.fp else ""
            return e.code, detail.encode("utf-8")
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
                raise _ConnectPhaseError(repr(reason)) from e
            raise _AmbiguousError(repr(reason)) from e
        except (TimeoutError, socket.timeout, ConnectionResetError) as e:
            raise _AmbiguousError(f"{type(e).__name__}") from e

    # -- health ----------------------------------------------------------------
    def check(self, ep: EndpointState) -> bool:
        t0 = self._clock()
        try:
            self._probe_connect(ep.base)
            code, _ = self._request("GET", ep.base + HEALTH_PATH, timeout=self.health_timeout)
            ok = code == 200
            err = "" if ok else f"health HTTP {code}"
        except (_ConnectPhaseError, _AmbiguousError) as e:
            ok, err = False, str(e)
        self._record(ep, ok, err, (self._clock() - t0) * 1000)
        return ok

    def check_all(self) -> list[dict]:
        for ep in self.endpoints:
            self.check(ep)
        self._update_online()
        return self.health()

    def health(self) -> list[dict]:
        with self._lock:
            return [ep.public() for ep in self.endpoints]

    def _record(self, ep: EndpointState, ok: bool, err: str, latency_ms: Optional[float] = None) -> None:
        with self._lock:
            ep.checked = True
            ep.last_latency_ms = round(latency_ms, 1) if latency_ms is not None else ep.last_latency_ms
            if ok:
                ep.healthy, ep.consecutive_failures, ep.next_retry_at = True, 0, 0.0
                ep.last_ok_at, ep.last_error = self._clock(), ""
            else:
                ep.healthy = False
                ep.consecutive_failures += 1
                ep.last_error = err[:200]
                delay = min(self.backoff_max, self.backoff_base * (2 ** (ep.consecutive_failures - 1)))
                ep.next_retry_at = self._clock() + delay * random.uniform(0.8, 1.2)

    def _candidates(self) -> list[EndpointState]:
        """Healthy endpoints first (by priority), then unchecked/retry-due ones. Down-and-
        waiting endpoints are skipped entirely."""
        now = self._clock()
        with self._lock:
            healthy = [e for e in self.endpoints if e.healthy]
            due = [e for e in self.endpoints if not e.healthy and now >= e.next_retry_at]
        return sorted(healthy, key=lambda e: e.priority) + sorted(due, key=lambda e: e.priority)

    def _update_online(self) -> None:
        with self._lock:
            online = any(e.healthy for e in self.endpoints)
            changed, self._online = (self._online != online), online
            active = next((e.base for e in sorted(self.endpoints, key=lambda e: e.priority) if e.healthy), "")
        if changed and self._on_state_change is not None:
            try:
                self._on_state_change("online" if online else "offline", active)
            except Exception as e:      # a chime failure must never break the bridge
                log.warning("on_state_change callback failed: %r", e)
        if changed:
            log.warning("voice bridge %s%s", "ONLINE via " if online else "OFFLINE", active)
        if online:
            self._flush_buffer()

    def active_endpoint(self) -> str:
        c = [e for e in self._candidates() if e.healthy]
        return c[0].base if c else ""

    # -- mode gate ----------------------------------------------------------------
    def should_capture(self, ttl_s: float = 5.0) -> bool:
        """False while the backend is 'deafened'. Unknown/unreachable → True (the backend
        still enforces deafen, and the offline buffer is bounded)."""
        now = self._clock()
        ts, mode = self._mode_cache
        if now - ts < ttl_s:
            return mode != "deafened"
        for ep in self._candidates():
            try:
                code, body = self._request("GET", ep.base + MODE_PATH, timeout=self.health_timeout)
                if code == 200:
                    mode = (json.loads(body or b"{}").get("mode") or "active").lower()
                    self._mode_cache = (now, mode)
                    return mode != "deafened"
            except (_ConnectPhaseError, _AmbiguousError, ValueError):
                continue
        return True

    # -- send ----------------------------------------------------------------------
    @staticmethod
    def _multipart(audio: bytes, filename: str) -> tuple[bytes, str]:
        boundary = "ablbridge" + uuid.uuid4().hex
        ctype = "audio/wav" if filename.lower().endswith(".wav") else "application/octet-stream"
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode("utf-8")
        return head + audio + f"\r\n--{boundary}--\r\n".encode("utf-8"), f"multipart/form-data; boundary={boundary}"

    def send_audio(self, audio: bytes, filename: str = "chunk.wav", *, _from_buffer: bool = False) -> SendResult:
        if not audio:
            return SendResult(ok=False, status="dropped", error="empty audio")
        if len(audio) > MAX_AUDIO_BYTES:
            return SendResult(ok=False, status="rejected", error=f"audio {len(audio)} B exceeds {MAX_AUDIO_BYTES} B")
        body, ctype = self._multipart(audio, filename)
        tried = []
        for ep in self._candidates():
            tried.append(ep.base)
            t0 = self._clock()
            try:
                self._probe_connect(ep.base)
                code, raw = self._request("POST", ep.base + HEARD_PATH, body=body,
                                          headers={"Content-Type": ctype}, timeout=self.read_timeout)
            except _ConnectPhaseError as e:
                self._record(ep, False, str(e))
                continue                                   # never reached abl1: safe to fail over
            except _AmbiguousError as e:
                self._record(ep, False, str(e))
                self._update_online()
                return SendResult(ok=False, status="ambiguous", endpoint=ep.base,
                                  error=f"sent but no response ({e}); not re-sent to avoid a double reply")
            latency = (self._clock() - t0) * 1000
            if 400 <= code < 500:
                self._record(ep, True, "", latency)        # server is up; the request is the problem
                if code not in self._rejected_logged:
                    self._rejected_logged.add(code)
                    log.error("voice bridge: abl1 rejected the chunk with HTTP %s (%s)", code,
                              "check the owner token" if code in (401, 403) else raw[:120].decode("utf-8", "replace"))
                return SendResult(ok=False, status="rejected", endpoint=ep.base, latency_ms=latency,
                                  error=f"HTTP {code}")
            if code >= 500:
                self._record(ep, False, f"HTTP {code}", latency)
                self._update_online()
                return SendResult(ok=False, status="ambiguous", endpoint=ep.base, latency_ms=latency,
                                  error=f"HTTP {code} after send; not re-sent")
            self._record(ep, True, "", latency)
            self._update_online()
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                data = {}
            return SendResult(ok=True, action=str(data.get("action") or "silent"), reply=str(data.get("reply") or ""),
                              transcript=str(data.get("transcript") or ""), endpoint=ep.base,
                              latency_ms=round(latency, 1), raw=data)
        # Nothing reachable: buffer (unless this IS a buffer flush).
        self._update_online()
        if _from_buffer:
            return SendResult(ok=False, status="offline", error=f"no endpoint reachable (tried {tried})")
        with self._lock:
            self._buffer.append((self._clock(), audio, filename))
            while len(self._buffer) > self.buffer_max:
                self._buffer.popleft()
        return SendResult(ok=False, status="buffered", error=f"no endpoint reachable (tried {tried or 'none due'})")

    def _flush_buffer(self) -> list[SendResult]:
        with self._lock:
            if self._flushing or not self._buffer:      # re-entrancy guard: send_audio -> _update_online
                return []
            self._flushing = True
        out: list[SendResult] = []
        try:
            self._drain_into(out)
        finally:
            with self._lock:
                self._flushing = False
        self.flushed_results = out
        return out

    def _drain_into(self, out: list[SendResult]) -> None:
        while True:
            with self._lock:
                if not self._buffer:
                    break
                ts, audio, name = self._buffer.popleft()
            if self._clock() - ts > self.buffer_max_age_s:
                out.append(SendResult(ok=False, status="dropped", error="stale buffered speech"))
                continue
            res = self.send_audio(audio, name, _from_buffer=True)
            out.append(res)
            if res.status == "offline":
                with self._lock:
                    self._buffer.appendleft((ts, audio, name))
                break

    def buffered(self) -> int:
        with self._lock:
            return len(self._buffer)

    # -- background health loop ----------------------------------------------------
    def start(self, interval_s: float = 10.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while True:
                try:
                    for ep in self.endpoints:
                        if ep.healthy or self._clock() >= ep.next_retry_at:
                            self.check(ep)
                    self._update_online()
                except Exception as e:
                    log.error("voice bridge health loop: %r", e)
                if self._stop.wait(interval_s):
                    return

        self._thread = threading.Thread(target=loop, name="abl-voice-bridge-health", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# ─────────────────────────────────────────────────────────────────────────────
# Drop-in helpers for legacy callers (used by the ears.py patch)
# ─────────────────────────────────────────────────────────────────────────────

_SINGLETON: Optional[VoiceBridge] = None
_SINGLETON_LOCK = threading.Lock()


def get_bridge() -> VoiceBridge:
    """Process-wide bridge with its background health loop running (lazy)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = VoiceBridge()
            _SINGLETON.check_all()                 # one synchronous probe so the first call routes well
            _SINGLETON.start(interval_s=float(os.environ.get("ABL_VOICE_HEALTH_INTERVAL", "10")))
        return _SINGLETON


def resolve_base() -> str:
    """Base URL to use RIGHT NOW: the highest-priority healthy target (tailnet, then
    tunnel). If nothing is healthy yet, the primary, so the caller's own error handling
    applies and the health loop keeps probing for recovery."""
    b = get_bridge()
    return b.active_endpoint() or b.endpoints[0].base


LEGACY_BASES = ("localhost:7800", "127.0.0.1:7800")


def live(url: str) -> str:
    """Map a legacy ``http://localhost:7800/...`` (or any configured target) URL onto the
    endpoint that is healthy right now; other URLs pass through untouched."""
    try:
        p = urllib.parse.urlparse(str(url))
    except ValueError:
        return url
    netloc = (p.netloc or "").lower()
    bases = {urllib.parse.urlparse(e.base).netloc.lower() for e in get_bridge().endpoints} | set(LEGACY_BASES)
    if netloc not in bases:
        return url
    rest = urllib.parse.urlunparse(("", "", p.path, p.params, p.query, p.fragment))
    return resolve_base().rstrip("/") + rest


def auth_headers() -> dict:
    """{'Authorization': 'Bearer …'} for abl1, or {} if no token is configured."""
    tok = get_bridge()._token
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _reset_singleton_for_test() -> None:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is not None:
            _SINGLETON.stop()
        _SINGLETON = None


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ABL desktop → abl1 voice bridge")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--send", metavar="WAV")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--targets", help="comma-separated base URLs (default: tailnet, then SSH tunnel)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    b = VoiceBridge([t for t in a.targets.split(",")] if a.targets else None)
    if a.health:
        print(json.dumps({"endpoints": b.check_all(), "active": b.active_endpoint(),
                          "capture": b.should_capture()}, indent=2))
        return 0 if b.active_endpoint() else 1
    if a.send:
        b.check_all()
        print(json.dumps(asdict(b.send_audio(Path(a.send).read_bytes(), Path(a.send).name)), indent=2))
        return 0
    if a.watch:
        try:
            while True:
                rows = b.check_all()
                print(time.strftime("%H:%M:%S"), " | ".join(
                    f"{r['base']} {'UP' if r['healthy'] else 'down'} {r['last_latency_ms'] or '-'}ms" for r in rows))
                time.sleep(5)
        except KeyboardInterrupt:
            return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_main())
