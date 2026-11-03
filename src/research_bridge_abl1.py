"""src/research_bridge_abl1.py — speak new research discoveries (abl1, every 2 hours).

A port of the desktop ``~/ops/speaker/research_bridge.py`` (one of the 8 screen-off
systems). Every run it reads two sources ABL already maintains on abl1:

  snake    abl_research_harvest.summary(): the "snake" research harvester's finds.
           Only *actionable* finds are spoken: helps=true, verdict adopt|test, safe,
           status pending. Read in-process; if that import fails, it falls back to
           data/abl/research_finds.json with the same filter.
  morning  data/abl/morning_queue.jsonl: the same rule insight_surfacer.gather() uses
           (finance alerts and anything severity=high; raw 'discovery' dumps are noise).

…keeps only what it hasn't spoken before (persistent seen-set), caps each run at
``ABL_RESEARCH_MAX_LINES`` (default 3) and hands each line to the voice path.

VOICE PATH (adapted from the desktop's direct speaker calls). abl1 never dials the
desktop (spec §7), so lines go out through Odysseus's own channel picker:
  ``voice_dispatch.dispatch(text)``: queues on the say-queue that the speaker pulls via
  ``GET /api/abl/voice/pending`` (the desktop pulls with ``voice_bridge.pull_lines()``), and
  falls back to the Discord outbox, which brings its own quiet-hours and hourly cap.
If voice_dispatch can't be imported (e.g. a dry environment), lines are written to
data/abl/research_bridge/latest.json for a pull consumer and marked ``delivered: false``.

Settings panel: autopilot.systems.research_bridge=false → nothing is spoken. During quiet
hours, high-severity finance alerts still go out (policy quiet.breakthrough != none), and
everything else waits for the next run after quiet hours. Unspoken items are NOT marked
seen, so they're spoken next time.

Usage:
  python -m src.research_bridge_abl1            # run once (the timer calls this every 2h)
  python -m src.research_bridge_abl1 --dry-run  # show what would be spoken, change nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

log = logging.getLogger("abl.research_bridge")

ODYSSEUS = Path(__file__).resolve().parents[1]


def _data() -> Path:
    return Path(os.environ.get("ABL_DATA_DIR") or ODYSSEUS / "data" / "abl")


def _settings():
    try:
        from src import abl_settings
        return abl_settings
    except ImportError:
        return None


# ── sources ──────────────────────────────────────────────────────────────────

def _actionable(items: list[dict]) -> list[dict]:
    return [x for x in items if x.get("helps") and x.get("verdict") in ("adopt", "test")
            and x.get("safe", True) and x.get("status") == "pending"]


def snake_finds() -> tuple[list[dict], str]:
    """(actionable finds, source label). Prefers the live summary(); falls back to the file.
    An explicit ABL_DATA_DIR always wins: summary() reads the harvester's own data dir,
    which would silently ignore the override."""
    if os.environ.get("ABL_DATA_DIR"):
        return _finds_from_file()
    try:
        sys.path.insert(0, str(ODYSSEUS))
        import abl_research_harvest as harvest  # type: ignore
        return list(harvest.summary().get("actionable", [])), "summary()"
    except Exception as e:
        log.debug("abl_research_harvest import failed (%r); reading research_finds.json", e)
    return _finds_from_file()


def _finds_from_file() -> tuple[list[dict], str]:
    try:
        doc = json.loads((_data() / "research_finds.json").read_text("utf-8"))
        return _actionable(doc.get("items", []))[:40], "research_finds.json"
    except (OSError, ValueError):
        return [], "unavailable"


def morning_items(now: datetime, window_hours: float = 26.0) -> list[dict]:
    path = _data() / "morning_queue.jsonl"
    out = []
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError:
        return []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        typ, sev, text = r.get("type", ""), (r.get("severity") or "").lower(), (r.get("text") or "").strip()
        if not text or (typ != "finance_alert" and sev != "high"):
            continue                                   # same rule as insight_surfacer.gather()
        ts = r.get("ts")
        if ts:
            try:
                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if now - t > timedelta(hours=window_hours):
                    continue
            except ValueError:
                pass
        out.append({"type": typ, "severity": sev, "text": text})
    return out


# ── lines ────────────────────────────────────────────────────────────────────

def _key(prefix: str, s: str) -> str:
    return prefix + hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def find_line(f: dict) -> str:
    kind = {"github": "a GitHub project", "arxiv": "a paper"}.get(f.get("kind", ""), "a find")
    verdict = "worth adopting" if f.get("verdict") == "adopt" else "worth testing"
    how = _clip(f.get("how") or f.get("desc") or "", 120)
    return f"Research: {kind}, {_clip(f.get('name') or 'unnamed', 70)}, looks {verdict}" + (f": {how}" if how else "") + "."


def build_candidates(now: datetime) -> tuple[list[dict], dict[str, Any]]:
    finds, src = snake_finds()
    cands = []
    for it in morning_items(now):
        urgent = it["type"] == "finance_alert" or it["severity"] == "high"
        cands.append({"key": _key("mq:", it["type"] + it["text"]), "text": _clip(it["text"], 220),
                      "priority": 0 if it["severity"] == "high" else 1, "urgent": urgent, "source": "morning_queue"})
    for f in finds:
        cands.append({"key": "snake:" + str(f.get("key") or _key("", f.get("name", ""))), "text": find_line(f),
                      "priority": 2 if f.get("verdict") == "adopt" else 3, "urgent": False, "source": "snake"})
    cands.sort(key=lambda c: c["priority"])
    return cands, {"snake_source": src, "snake_actionable": len(finds)}


# ── state + delivery ─────────────────────────────────────────────────────────

def _state_path() -> Path:
    return _data() / "research_bridge" / "state.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {"seen": [], "last_run": None}


def save_state(st: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    st["seen"] = st["seen"][-2000:]                       # bounded memory of what was spoken
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), "utf-8")
    os.replace(tmp, p)


def voice_dispatch_available() -> bool:
    """Seam for tests: is Odysseus's voice_dispatch importable from this tree?"""
    import importlib.util
    sys.path.insert(0, str(ODYSSEUS))
    try:
        return importlib.util.find_spec("voice_dispatch") is not None
    except (ImportError, ValueError):
        return False


def default_deliver(text: str) -> str:
    """Odysseus channel picker (phone/desktop say-queue → Discord outbox). Returns the channel."""
    sys.path.insert(0, str(ODYSSEUS))
    import voice_dispatch  # type: ignore
    return voice_dispatch.dispatch(text)


def run(now: Optional[datetime] = None, *, dry_run: bool = False,
        deliver: Optional[Callable[[str], str]] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    st = _settings()
    if st is not None and not (st.get("autopilot.enabled") and st.get("autopilot.systems.research_bridge")):
        return {"status": "disabled", "spoken": [], "reason": "autopilot.systems.research_bridge is off in Settings"}
    quiet = bool(st and st.quiet_now(now))
    breakthrough = (st.get("quiet.breakthrough") if st else "urgent_red") != "none"
    state = load_state()
    seen = set(state.get("seen", []))
    cands, meta = build_candidates(now)
    fresh = [c for c in cands if c["key"] not in seen]
    eligible = [c for c in fresh if not quiet or (breakthrough and c["urgent"])]
    cap = int(os.environ.get("ABL_RESEARCH_MAX_LINES", "3"))
    chosen = eligible[:cap]
    result: dict[str, Any] = {"status": "ok", "generated_at": now.isoformat(), "quiet_hours": quiet,
                              "candidates": len(cands), "fresh": len(fresh), "held_for_later": len(fresh) - len(chosen),
                              **meta, "spoken": []}
    if dry_run:
        result["would_speak"] = [c["text"] for c in chosen]
        return result
    if deliver is None:
        if voice_dispatch_available():
            deliver = default_deliver
        else:
            log.warning("voice_dispatch unavailable; writing lines to research_bridge/latest.json for pull only")
    for c in chosen:
        channel = "file"
        if deliver is not None:
            try:
                channel = deliver(c["text"])
            except Exception as e:
                log.error("delivery failed for %s: %r", c["key"], e)
                channel = "error"
        delivered = channel not in ("dropped", "error", "file")
        result["spoken"].append({"text": c["text"], "source": c["source"], "channel": channel, "delivered": delivered})
        if delivered:
            seen.add(c["key"])                         # undelivered items are retried next run
    out = _data() / "research_bridge"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(result, indent=1), "utf-8")
    state.update(seen=sorted(seen), last_run=now.isoformat())
    save_state(state)
    return result


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ABL research bridge (abl1)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(json.dumps(run(dry_run=a.dry_run), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
