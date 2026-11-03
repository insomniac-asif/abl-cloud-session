"""Tests for src/voice_bridge.py against real local HTTP servers emulating Odysseus.

Each fake server implements GET /api/health, GET /api/abl/voice/mode and
POST /api/abl/voice/heard (multipart 'audio', owner bearer token), and counts hits,
so double-sends are directly observable.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import voice_bridge as vb  # noqa: E402

TOKEN = "owner-token-123"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64


class FakeOdysseus:
    def __init__(self, *, mode="active", heard_delay=0.0, heard_status=200, health_status=200, reply="Sure."):
        self.mode, self.heard_delay, self.heard_status, self.health_status = mode, heard_delay, heard_status, health_status
        self.reply = reply
        self.heard_hits, self.health_hits, self.auth_seen, self.bodies = 0, 0, [], []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path == "/api/health":
                    outer.health_hits += 1
                    return self._json(outer.health_status, {"status": "ok"})
                if self.path == "/api/abl/voice/mode":
                    return self._json(200, {"mode": outer.mode})
                self._json(404, {})

            def do_POST(self):
                if self.path != "/api/abl/voice/heard":
                    return self._json(404, {})
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                outer.heard_hits += 1
                outer.auth_seen.append(self.headers.get("Authorization"))
                outer.bodies.append((self.headers.get("Content-Type"), body))
                if outer.heard_delay:
                    time.sleep(outer.heard_delay)
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._json(403, {"detail": {"message": "owner only"}})
                if outer.heard_status != 200:
                    return self._json(outer.heard_status, {"detail": "boom"})
                ok = b'name="audio"' in body and WAV in body
                self._json(200, {"transcript": "what time is it", "action": "speak" if ok else "silent",
                                 "reply": outer.reply, "reason": "addressed", "lane": "fast", "mode": outer.mode})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def dead_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                     # nothing listens here now → connect refused
    return f"http://127.0.0.1:{port}"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def servers():
    made = []

    def make(**kw):
        s = FakeOdysseus(**kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def bridge(targets, **kw):
    kw.setdefault("token", TOKEN)
    kw.setdefault("connect_timeout", 0.5)
    kw.setdefault("read_timeout", 2.0)
    kw.setdefault("health_timeout", 0.5)
    return vb.VoiceBridge(targets, **kw)


# ── target validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,ok", [
    ("http://abl1.tailda08f9.ts.net:7800", True), ("http://127.0.0.1:7800", True), ("http://localhost:7800", True),
    ("http://100.117.30.114:7800", True), ("http://192.168.1.208:7800", True), ("https://abl.tailda08f9.ts.net/machine", True),
    ("http://8.8.8.8:7800", False), ("https://evil.example.com", False), ("http://192.168.1.208.evil.com", False),
    ("ftp://127.0.0.1", False), ("not a url", False),
])
def test_target_allowlist(url, ok):
    assert vb.is_allowed_target(url) is ok


def test_constructor_refuses_remote_target():
    with pytest.raises(ValueError, match="refusing"):
        vb.VoiceBridge(["http://127.0.0.1:7800", "https://api.example.com"], token=TOKEN)


def test_default_targets_are_tailnet_then_tunnel(monkeypatch):
    monkeypatch.delenv("ABL_VOICE_TARGETS", raising=False)
    b = vb.VoiceBridge(token=TOKEN)
    assert [e.base for e in b.endpoints] == ["http://abl1.tailda08f9.ts.net:7800", "http://127.0.0.1:7800"]


def test_env_targets(monkeypatch):
    monkeypatch.setenv("ABL_VOICE_TARGETS", "http://127.0.0.1:1, http://127.0.0.1:2")
    assert [e.base for e in vb.VoiceBridge(token=TOKEN).endpoints] == ["http://127.0.0.1:1", "http://127.0.0.1:2"]


def test_token_loading(tmp_path):
    f = tmp_path / "token"
    f.write_text("  file-token \n")
    assert vb.load_token({"ABL_TOKEN_FILE": str(f)}) == "file-token"
    assert vb.load_token({"ABL_TOKEN": "env-token", "ABL_TOKEN_FILE": str(f)}) == "env-token"
    assert vb.load_token({"ABL_TOKEN_FILE": str(tmp_path / "missing")}) == ""


# ── health + routing ─────────────────────────────────────────────────────────

def test_health_and_send_via_primary(servers):
    p, f = servers(), servers()
    b = bridge([p.url, f.url])
    rows = b.check_all()
    assert all(r["healthy"] for r in rows) and b.active_endpoint() == p.url
    r = b.send_audio(WAV)
    assert r.ok and r.action == "speak" and r.reply == "Sure." and r.transcript == "what time is it"
    assert r.endpoint == p.url and p.heard_hits == 1 and f.heard_hits == 0
    assert p.auth_seen == [f"Bearer {TOKEN}"]
    ctype, body = p.bodies[0]
    assert ctype.startswith("multipart/form-data; boundary=") and b'filename="chunk.wav"' in body


def test_fails_over_when_primary_down(servers):
    f = servers()
    b = bridge([dead_url(), f.url])
    r = b.send_audio(WAV)                       # unchecked primary: connect refused → fallback
    assert r.ok and r.endpoint == f.url and f.heard_hits == 1
    assert b.endpoints[0].healthy is False and b.endpoints[0].consecutive_failures == 1


def test_down_endpoint_is_skipped_until_backoff_expires(servers):
    f = servers()
    clk = Clock()
    b = bridge([dead_url(), f.url], clock=clk, backoff_base=10)
    b.send_audio(WAV)
    t_retry = b.endpoints[0].next_retry_at
    assert 8 <= t_retry - clk.t <= 12             # 10 s ±20 %
    assert [e.base for e in b._candidates()] == [f.url]        # primary skipped while waiting
    clk.t = t_retry + 0.01
    assert b._candidates()[-1].base == b.endpoints[0].base       # due again → retried (after healthy ones)


def test_backoff_grows_and_caps():
    clk = Clock()
    b = bridge([dead_url()], clock=clk, backoff_base=1, backoff_max=8)
    delays = []
    for _ in range(6):
        b.check(b.endpoints[0])
        delays.append(b.endpoints[0].next_retry_at - clk.t)
    assert delays[0] < delays[2] < delays[3] and max(delays) <= 8 * 1.2


def test_fails_back_to_primary_when_it_recovers(servers):
    f = servers()
    prim_dead = dead_url()
    b = bridge([prim_dead, f.url])
    b.check_all()
    assert b.active_endpoint() == f.url
    port = int(prim_dead.rsplit(":", 1)[1])
    p = FakeOdysseus()                            # bring a server up on the primary's port
    p.httpd.server_close()
    from http.server import ThreadingHTTPServer as T
    p.httpd = T(("127.0.0.1", port), p.httpd.RequestHandlerClass)
    threading.Thread(target=p.httpd.serve_forever, daemon=True).start()
    try:
        b.endpoints[0].next_retry_at = 0
        b.check_all()
        assert b.active_endpoint() == prim_dead
        assert b.send_audio(WAV).endpoint == prim_dead
    finally:
        p.httpd.shutdown()
        p.httpd.server_close()


def test_unhealthy_health_status(servers):
    p = servers(health_status=503)
    b = bridge([p.url])
    b.check_all()
    assert b.endpoints[0].healthy is False and "503" in b.endpoints[0].last_error


# ── no double sends ──────────────────────────────────────────────────────────

def test_read_timeout_is_ambiguous_and_not_resent(servers):
    slow, f = servers(heard_delay=1.5), servers()
    b = bridge([slow.url, f.url], read_timeout=0.5)
    b.check_all()
    r = b.send_audio(WAV)
    assert r.status == "ambiguous" and not r.ok
    assert slow.heard_hits == 1 and f.heard_hits == 0          # the fallback never saw it


def test_5xx_after_send_is_not_resent(servers):
    p, f = servers(heard_status=500), servers()
    b = bridge([p.url, f.url])
    b.check_all()
    r = b.send_audio(WAV)
    assert r.status == "ambiguous" and p.heard_hits == 1 and f.heard_hits == 0


def test_403_is_rejected_not_retried_and_logged_once(servers, caplog):
    p, f = servers(), servers()
    b = bridge([p.url, f.url], token="wrong")
    b.check_all()
    r1, r2 = b.send_audio(WAV), b.send_audio(WAV)
    assert r1.status == r2.status == "rejected" and "403" in r1.error
    assert f.heard_hits == 0
    assert sum("rejected the chunk" in m for m in caplog.messages) == 1
    assert "wrong" not in " ".join(caplog.messages)             # token never logged


def test_oversize_and_empty_rejected_locally(servers):
    p = servers()
    b = bridge([p.url])
    assert b.send_audio(b"").status == "dropped"
    assert b.send_audio(b"x" * (vb.MAX_AUDIO_BYTES + 1)).status == "rejected"
    assert p.heard_hits == 0


# ── offline buffer ───────────────────────────────────────────────────────────

def test_buffer_when_offline_then_flush_on_recovery(servers):
    clk = Clock()
    events = []
    b = bridge([dead_url()], clock=clk, on_state_change=lambda s, ep: events.append(s))
    r = b.send_audio(WAV, "a.wav")
    assert r.status == "buffered" and b.buffered() == 1
    assert events == ["offline"]
    p = servers()
    b.endpoints = [vb.EndpointState(base=p.url, priority=0)]
    clk.t += 5
    b.check_all()
    assert events == ["offline", "online"] and b.buffered() == 0 and p.heard_hits == 1
    assert b.flushed_results[0].ok


def test_stale_buffer_is_dropped_not_answered_late(servers):
    clk = Clock()
    b = bridge([dead_url()], clock=clk, buffer_max_age_s=30)
    b.send_audio(WAV)
    p = servers()
    b.endpoints = [vb.EndpointState(base=p.url, priority=0)]
    clk.t += 31
    b.check_all()
    assert p.heard_hits == 0 and b.flushed_results[0].status == "dropped"


def test_buffer_is_bounded(servers):
    b = bridge([dead_url()], buffer_max=3)
    for _ in range(5):
        b.send_audio(WAV)
    assert b.buffered() == 3


def test_state_change_callback_fires_once_per_transition_and_survives_errors(servers):
    calls = []

    def cb(state, ep):
        calls.append(state)
        raise RuntimeError("chime device busy")
    b = bridge([dead_url()], on_state_change=cb)
    b.check_all()
    b.check_all()
    assert calls == ["offline"]


# ── mode gate ────────────────────────────────────────────────────────────────

def test_should_capture_respects_deafened_and_caches(servers):
    p = servers(mode="deafened")
    clk = Clock()
    b = bridge([p.url], clock=clk)
    b.check_all()
    assert b.should_capture() is False
    p.mode = "active"
    assert b.should_capture() is False            # cached for 5 s
    clk.t += 6
    assert b.should_capture() is True


def test_should_capture_true_when_unreachable():
    assert bridge([dead_url()]).should_capture() is True


# ── background loop ──────────────────────────────────────────────────────────

def test_background_health_loop(servers):
    p = servers()
    b = bridge([p.url])
    b.start(interval_s=0.05)
    try:
        deadline = time.time() + 2
        while time.time() < deadline and not b.active_endpoint():
            time.sleep(0.02)
        assert b.active_endpoint() == p.url and p.health_hits >= 1
    finally:
        b.stop()


def test_cli_health(servers, capsys):
    p = servers()
    rc = vb._main(["--health", "--targets", p.url])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["active"] == p.url and out["capture"] is True
