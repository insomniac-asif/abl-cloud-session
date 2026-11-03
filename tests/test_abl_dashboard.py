"""Tests for src/abl_dashboard.py + routes/dashboard_routes.py."""
from __future__ import annotations

import json
import socket
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import abl_dashboard as d  # noqa: E402


def serve(routes: dict[str, tuple[int, object]]):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            code, body = routes.get(self.path, (404, {}))
            b = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, s.server_address[1]


def dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


SYSTEMCTL = """Id=abl-llama-27b.service
ActiveState=active
SubState=running
NRestarts=0
Result=success

Id=abl-market-bridge.service
ActiveState=activating
SubState=auto-restart
NRestarts=212
Result=exit-code

Id=abl-dsh-web.service
ActiveState=failed
SubState=failed
NRestarts=1
Result=exit-code
"""


def runner(cmd, timeout):
    if cmd[0] == "systemctl":
        return 0, SYSTEMCTL
    if cmd[0] == "nvidia-smi":
        return 0, "NVIDIA GeForce GTX 1070, 8000, 8192, 97, 71\n"
    return 127, ""


@pytest.fixture
def env(tmp_path, monkeypatch):
    llama, lp = serve({"/health": (200, {"status": "ok"}),
                       "/slots": (200, [{"id": 0, "is_processing": True}, {"id": 1, "is_processing": False}])})
    ollama, op = serve({"/api/version": (200, {"version": "0.5"}),
                        "/api/ps": (200, {"models": [{"name": "qwen2.5:3b", "size_vram": 2 * 2**30, "expires_at": "x"}]})})
    ody, odp = serve({"/api/health": (503, {})})
    monkeypatch.setenv("ABL_LLAMA_PORT", str(lp))
    monkeypatch.setenv("ABL_OLLAMA_PORT", str(op))
    now = datetime.now(timezone.utc)
    log = tmp_path / "router.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"ts": (now - timedelta(minutes=5)).isoformat(), "tier": "PRIMARY", "reason": "long", "prompt": "SECRET USER TEXT"},
        {"ts": (now - timedelta(minutes=4)).isoformat(), "tier": "LIGHTWEIGHT", "fallback_from": "cloud", "text": "more secret"},
        {"ts": (now - timedelta(hours=3)).isoformat(), "tier": "LIGHTWEIGHT", "fallback": True},
        "not json",
    ]) + "\n")
    monkeypatch.setenv("ABL_ROUTER_LOG", str(log))
    monkeypatch.setenv("ABL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ABL_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("ABL_AUTORESPONDER_DATA_DIR", str(tmp_path / "ar"))
    (tmp_path / "data" / "briefing").mkdir(parents=True)
    (tmp_path / "data" / "briefing" / "latest.json").write_text(json.dumps({"status": "ok"}))
    services = [{"name": "odysseus", "port": odp, "path": "/api/health", "critical": True},
                {"name": "llama-27b", "port": lp, "path": "/health", "critical": True},
                {"name": "ollama", "port": op, "path": "/api/version"},
                {"name": "hub", "port": dead_port()},
                {"name": "desk-futures", "port": dead_port(), "group": "desks"}]
    d._cache.update(at=0.0, snap=None)
    yield services
    for s in (llama, ollama, ody):
        s.shutdown()
        s.server_close()


def test_full_snapshot(env):
    snap = d.collect(fresh=True, services=env, run=runner)
    svc = {s["name"]: s for s in snap["services"]}
    assert svc["llama-27b"]["status"] == "up" and svc["llama-27b"]["latency_ms"] is not None
    assert svc["odysseus"]["status"] == "degraded" and "503" in svc["odysseus"]["detail"]
    assert svc["hub"]["status"] == "down" and svc["desk-futures"]["group"] == "desks"
    assert snap["models"]["llama"] == {"status": "ok", "slots": 2, "busy": 1}
    assert snap["models"]["ollama"]["loaded"][0] == {"name": "qwen2.5:3b", "vram_mb": 2048, "expires_at": "x"}
    u = {x["unit"]: x for x in snap["units"]}
    assert u["abl-market-bridge.service"]["restarts"] == 212 and u["abl-dsh-web.service"]["active"] == "failed"
    assert snap["host"]["gpu"]["name"] == "NVIDIA GeForce GTX 1070" and snap["host"]["gpu"]["util_pct"] == 97
    assert snap["abl"]["briefing"]["status"] == "ok" and snap["abl"]["finance"]["status"] == "never-run"
    assert snap["abl"]["autoresponder"]["mode"] == "shadow"


def test_router_allow_list_and_counts(env):
    snap = d.collect(fresh=True, services=env, run=runner)
    r = snap["router"]
    assert r["status"] == "ok" and r["total_in_tail"] == 3
    assert r["by_tier"] == {"PRIMARY": 1, "LIGHTWEIGHT": 2}
    assert r["last_hour"] == {"decisions": 2, "fallbacks": 1}
    blob = json.dumps(snap)
    assert "SECRET USER TEXT" not in blob and "more secret" not in blob      # prompts never leave


def test_status_and_alerts(env):
    snap = d.collect(fresh=True, services=env, run=runner)
    assert snap["status"] == "red"                       # critical odysseus degraded + failed unit
    a = " | ".join(snap["alerts"])
    assert "odysseus" in a and "hub" in a and "restarted 212" in a and "abl-dsh-web.service failed" in a
    assert "GPU memory 8000/8192" in a


def test_cache_and_fresh(env):
    a = d.collect(services=env, run=runner)
    b = d.collect(services=env, run=runner)
    assert a is b
    c = d.collect(fresh=True, services=env, run=runner)
    assert c is not a


def test_missing_tools_are_reported_not_raised(env):
    snap = d.collect(fresh=True, services=env, run=lambda c, t: (127, ""))
    assert snap["host"]["gpu"] == {"status": "unavailable"}
    assert all(u["active"] == "unknown" for u in snap["units"])


def test_no_router_log(env, monkeypatch):
    monkeypatch.delenv("ABL_ROUTER_LOG")
    monkeypatch.setattr(d, "ODYSSEUS", Path("/nonexistent"))
    assert d.router()["status"] == "no-log"


def test_route(env, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from routes.dashboard_routes import setup_dashboard_routes
    real = d.collect
    monkeypatch.setattr(d, "collect", lambda fresh=False: real(fresh=fresh, services=env, run=runner))
    app = fastapi.FastAPI()
    app.include_router(setup_dashboard_routes())
    r = TestClient(app).get("/api/abl/dashboard?fresh=1")
    assert r.status_code == 200 and r.json()["status"] == "red" and r.headers["cache-control"] == "no-store"
