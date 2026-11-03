"""src/abl_dashboard.py — one JSON snapshot of everything on abl1 (for GET /api/abl/dashboard).

The console plugin used to probe ~7 services itself. This collects it all in one
place, concurrently, inside a ~2 s budget, cached for 5 s:

  services   TCP + optional HTTP health for Odysseus, the 27B llama-server, Ollama,
             dsh-web, hub, hub inbox, fleet registry and the market desks
  models     llama-server slots (busy/idle) and Ollama's loaded models (/api/ps)
  units      systemd --user state + NRestarts (crash loops show up as restarts)
  host       GPU (nvidia-smi), RAM / swap (/proc/meminfo), load, disk
  router     tail of the model_router decision log: counts by tier and fallbacks in the last hour,
             plus the last 10 decisions. ALLOW-LISTED fields only: prompts and texts never leave
  abl        autoresponder mode / pending count, settings version, briefing/finance/research freshness
  status     green | amber | red, plus human-readable alerts

Everything is fail-soft: a probe that errors reports itself, it never raises. Service list
and ports can be overridden with ABL_DASHBOARD_SERVICES (JSON list of {name, port, path?}).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ODYSSEUS = Path(__file__).resolve().parents[1]

DEFAULT_SERVICES: list[dict[str, Any]] = [
    {"name": "odysseus", "port": 7800, "path": "/api/health", "critical": True},
    {"name": "llama-27b", "port": 8085, "path": "/health", "critical": True},
    {"name": "ollama", "port": 11434, "path": "/api/version"},
    {"name": "dsh-web", "port": 3081, "critical": True},
    {"name": "hub", "port": 8902},
    {"name": "hub-inbox", "port": 8899},
    {"name": "fleet-registry", "port": 8900},
] + [{"name": f"desk-{n}", "port": p, "group": "desks"} for n, p in zip(
    ["futures", "options", "polymarket", "shorts", "crypto", "conviction", "voledge", "memescreen", "desk8098"],
    range(8090, 8099))]

DEFAULT_UNITS = ["abl-llama-27b.service", "abl-dsh-web.service", "abl-market-bridge.service",
                 "abl-morning-briefing.timer", "abl-finance-voice.timer", "abl-research-bridge.timer"]

ROUTER_KEYS = ("ts", "time", "tier", "role", "route", "decision", "reason", "model", "backend",
               "fallback", "fallback_from", "latency_ms", "ok", "error_kind")

Runner = Callable[[list[str], float], tuple[int, str]]


def _run(cmd: list[str], timeout: float = 2.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""


def _host() -> str:
    return os.environ.get("ABL_DASHBOARD_HOST", "127.0.0.1")


def _http_json(port: int, path: str, timeout: float) -> tuple[int, Any]:
    req = urllib.request.Request(f"http://{_host()}:{port}{path}", headers={"User-Agent": "abl-dashboard"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(65536)
            code = r.status
    except urllib.error.HTTPError as e:
        return e.code, None
    try:
        return code, json.loads(body or b"null")
    except ValueError:
        return code, None


def probe_service(svc: dict[str, Any], timeout: float = 1.5) -> dict[str, Any]:
    out = {"name": svc["name"], "port": svc["port"], "group": svc.get("group", "core"),
           "critical": bool(svc.get("critical")), "status": "down", "latency_ms": None, "detail": ""}
    t0 = time.monotonic()
    try:
        with socket.create_connection((_host(), svc["port"]), timeout=timeout):
            pass
    except OSError as e:
        out["detail"] = type(e).__name__
        return out
    out["status"] = "up"
    if svc.get("path"):
        try:
            code, _ = _http_json(svc["port"], svc["path"], timeout)
            if code != 200:
                out["status"], out["detail"] = "degraded", f"HTTP {code}"
        except (OSError, urllib.error.URLError) as e:
            out["status"], out["detail"] = "degraded", type(e).__name__
    out["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return out


def models(timeout: float = 1.5) -> dict[str, Any]:
    res: dict[str, Any] = {"llama": {"status": "unknown"}, "ollama": {"status": "unknown"}}
    try:
        code, slots = _http_json(8085 if not os.environ.get("ABL_LLAMA_PORT") else int(os.environ["ABL_LLAMA_PORT"]), "/slots", timeout)
        if code == 200 and isinstance(slots, list):
            busy = sum(1 for s in slots if s.get("is_processing") or s.get("state") == 1)
            res["llama"] = {"status": "ok", "slots": len(slots), "busy": busy}
        else:
            res["llama"] = {"status": "no-slots-endpoint", "http": code}
    except (OSError, urllib.error.URLError) as e:
        res["llama"] = {"status": "unreachable", "error": type(e).__name__}
    try:
        port = int(os.environ.get("ABL_OLLAMA_PORT", "11434"))
        code, ps = _http_json(port, "/api/ps", timeout)
        loaded = [{"name": m.get("name"), "vram_mb": round((m.get("size_vram") or 0) / 2**20),
                   "expires_at": m.get("expires_at")} for m in (ps or {}).get("models", [])] if code == 200 else []
        res["ollama"] = {"status": "ok" if code == 200 else f"http {code}", "loaded": loaded}
    except (OSError, urllib.error.URLError) as e:
        res["ollama"] = {"status": "unreachable", "error": type(e).__name__}
    return res


def units(names: list[str], run: Runner = _run) -> list[dict[str, Any]]:
    rc, out = run(["systemctl", "--user", "show", *names, "-p", "Id,ActiveState,SubState,NRestarts,Result"], 2.0)
    if rc == 127:
        return [{"unit": n, "active": "unknown", "detail": "systemctl not available"} for n in names]
    rows, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if cur:
                rows.append(cur)
                cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    res = []
    for r in rows:
        try:
            restarts = int(r.get("NRestarts") or 0)
        except ValueError:
            restarts = 0
        res.append({"unit": r.get("Id"), "active": r.get("ActiveState"), "sub": r.get("SubState"),
                    "restarts": restarts, "result": r.get("Result")})
    return res


def host(run: Runner = _run, meminfo: str = "/proc/meminfo") -> dict[str, Any]:
    out: dict[str, Any] = {}
    rc, gpu = run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                   "--format=csv,noheader,nounits"], 2.0)
    if rc == 0 and gpu.strip():
        parts = [p.strip() for p in gpu.strip().splitlines()[0].split(",")]
        try:
            out["gpu"] = {"name": parts[0], "mem_used_mb": int(parts[1]), "mem_total_mb": int(parts[2]),
                          "util_pct": int(parts[3]), "temp_c": int(parts[4])}
        except (IndexError, ValueError):
            out["gpu"] = {"status": "unparseable"}
    else:
        out["gpu"] = {"status": "unavailable"}
    try:
        mi = {}
        for line in Path(meminfo).read_text().splitlines():
            k, _, v = line.partition(":")
            mi[k] = int(v.split()[0])                   # kB
        out["ram"] = {"total_mb": mi["MemTotal"] // 1024, "available_mb": mi["MemAvailable"] // 1024,
                      "used_pct": round(100 * (1 - mi["MemAvailable"] / mi["MemTotal"]), 1)}
        st = mi.get("SwapTotal", 0)
        out["swap"] = {"total_mb": st // 1024, "used_mb": (st - mi.get("SwapFree", 0)) // 1024,
                       "used_pct": round(100 * (1 - mi.get("SwapFree", 0) / st), 1) if st else 0.0}
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        out["ram"] = {"status": "unavailable"}
    try:
        out["load"] = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        pass
    try:
        du = shutil.disk_usage(str(Path.home()))
        out["disk"] = {"free_gb": round(du.free / 2**30, 1), "used_pct": round(100 * du.used / du.total, 1)}
    except OSError:
        pass
    return out


def _router_log() -> Optional[Path]:
    env = os.environ.get("ABL_ROUTER_LOG")
    if env:
        return Path(env).expanduser()
    cands = []
    for pat in ("data/abl/*router*.jsonl", "data/*router*.jsonl", "logs/*router*.jsonl", "data/abl/**/*router*.jsonl"):
        cands += glob.glob(str(ODYSSEUS / pat), recursive=True)
    return Path(max(cands, key=os.path.getmtime)) if cands else None


def _tail(path: Path, max_bytes: int = 256_000) -> list[str]:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read().decode("utf-8", "replace")
    lines = data.splitlines()
    return lines[1:] if size > max_bytes else lines        # drop a possibly-cut first line


def router(now: Optional[datetime] = None, last: int = 10) -> dict[str, Any]:
    p = _router_log()
    if p is None or not p.exists():
        return {"status": "no-log", "hint": "set ABL_ROUTER_LOG to model_router's JSONL decision log"}
    now = now or datetime.now(timezone.utc)
    decisions, by_tier, fallbacks_1h, n_1h = [], {}, 0, 0
    for line in _tail(p):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        d = {k: r[k] for k in ROUTER_KEYS if k in r}           # allow-list: no prompts/texts
        decisions.append(d)
        tier = str(d.get("tier") or d.get("role") or d.get("decision") or "unknown")
        by_tier[tier] = by_tier.get(tier, 0) + 1
        ts = d.get("ts") or d.get("time")
        try:
            t = datetime.fromtimestamp(ts, timezone.utc) if isinstance(ts, (int, float)) else \
                datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if now - t <= timedelta(hours=1):
                n_1h += 1
                if d.get("fallback") or d.get("fallback_from"):
                    fallbacks_1h += 1
        except (TypeError, ValueError, OverflowError):
            pass
    return {"status": "ok", "log": str(p), "total_in_tail": len(decisions), "by_tier": by_tier,
            "last_hour": {"decisions": n_1h, "fallbacks": fallbacks_1h}, "recent": decisions[-last:][::-1]}


def abl_state() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from src import autoresponder
        q = autoresponder.PendingQueue()
        out["autoresponder"] = {"mode": autoresponder.get_mode(), "pending": len(q.list("pending"))}
    except Exception as e:
        out["autoresponder"] = {"status": "unavailable", "error": type(e).__name__}
    try:
        from src import abl_settings
        doc = json.loads(abl_settings.settings_path().read_text("utf-8"))
        out["settings"] = {"version": doc.get("version"), "updated_at": doc.get("updated_at")}
    except Exception:
        out["settings"] = {"version": 0}
    data = Path(os.environ.get("ABL_DATA_DIR") or ODYSSEUS / "data" / "abl")
    for name, rel in (("briefing", "briefing/latest.json"), ("finance", "finance/latest.json"),
                      ("research_bridge", "research_bridge/latest.json")):
        p = data / rel
        try:
            st = p.stat()
            doc = json.loads(p.read_text("utf-8"))
            out[name] = {"status": doc.get("status"), "updated": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()}
        except (OSError, ValueError):
            out[name] = {"status": "never-run"}
    return out


def _alerts(snap: dict[str, Any]) -> tuple[str, list[str]]:
    alerts, level = [], 0
    for s in snap["services"]:
        if s["status"] != "up":
            sev = 2 if s["critical"] else 1
            level = max(level, sev)
            alerts.append(f"{s['name']} :{s['port']} is {s['status']}" + (f" ({s['detail']})" if s["detail"] else ""))
    for u in snap["units"]:
        if u.get("restarts", 0) >= 5 and u.get("active") != "inactive":
            level = max(level, 1)
            alerts.append(f"{u['unit']} restarted {u['restarts']}×: crash loop?")
        if u.get("active") == "failed":
            level = max(level, 2)
            alerts.append(f"{u['unit']} failed")
    h = snap["host"]
    g = h.get("gpu", {})
    if g.get("mem_total_mb") and g["mem_used_mb"] / g["mem_total_mb"] > 0.97:
        level = max(level, 1)
        alerts.append(f"GPU memory {g['mem_used_mb']}/{g['mem_total_mb']} MB")
    if h.get("ram", {}).get("used_pct", 0) > 92:
        level = max(level, 1)
        alerts.append(f"RAM {h['ram']['used_pct']}% used")
    if h.get("swap", {}).get("used_pct", 0) > 50:
        level = max(level, 1)
        alerts.append(f"swap {h['swap']['used_pct']}% used")
    fb = snap["router"].get("last_hour", {}).get("fallbacks", 0)
    if fb > 6:
        level = max(level, 1)
        alerts.append(f"{fb} router fallbacks in the last hour: a lane is failing quietly")
    return ("green", "amber", "red")[level], alerts


_cache: dict[str, Any] = {"at": 0.0, "snap": None}
_lock = threading.Lock()


def collect(*, fresh: bool = False, services: Optional[list[dict[str, Any]]] = None,
            unit_names: Optional[list[str]] = None, run: Runner = _run, ttl: float = 5.0) -> dict[str, Any]:
    with _lock:
        if not fresh and _cache["snap"] is not None and time.monotonic() - _cache["at"] < ttl:
            return _cache["snap"]
    t0 = time.monotonic()
    svcs = services
    if svcs is None:
        raw = os.environ.get("ABL_DASHBOARD_SERVICES")
        try:
            svcs = json.loads(raw) if raw else DEFAULT_SERVICES
        except ValueError:
            svcs = DEFAULT_SERVICES
    with ThreadPoolExecutor(max_workers=min(24, len(svcs) + 4)) as ex:
        svc_futs = [ex.submit(probe_service, s) for s in svcs]
        f_models = ex.submit(models)
        f_units = ex.submit(units, unit_names or DEFAULT_UNITS, run)
        f_host = ex.submit(host, run)
        f_router = ex.submit(router)
        f_abl = ex.submit(abl_state)

        def get(f, default):
            try:
                return f.result(timeout=3.0)
            except Exception as e:
                return {**default, "error": type(e).__name__}
        snap = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "services": [get(f, {"name": s["name"], "port": s["port"], "group": s.get("group", "core"),
                                 "critical": bool(s.get("critical")), "status": "unknown", "latency_ms": None, "detail": "timeout"})
                         for f, s in zip(svc_futs, svcs)],
            "models": get(f_models, {}),
            "units": get(f_units, {}),
            "host": get(f_host, {}),
            "router": get(f_router, {"status": "error"}),
            "abl": get(f_abl, {}),
        }
    if isinstance(snap["units"], dict):
        snap["units"] = []
    snap["status"], snap["alerts"] = _alerts(snap)
    snap["collect_ms"] = round((time.monotonic() - t0) * 1000, 1)
    with _lock:
        _cache.update(at=time.monotonic(), snap=snap)
    return snap


if __name__ == "__main__":  # python -m src.abl_dashboard
    print(json.dumps(collect(fresh=True), indent=2, default=str))
