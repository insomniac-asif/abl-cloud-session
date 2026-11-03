"""Tests for src/research_bridge_abl1.py (and VoiceBridge.pull_lines, its desktop pull side)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import research_bridge_abl1 as rb  # noqa: E402

NY = ZoneInfo("America/New_York")
NOON = datetime(2026, 11, 3, 12, 0, tzinfo=NY)


def find(key, verdict="adopt", helps=True, safe=True, status="pending", **kw):
    return {"key": key, "kind": "github", "name": f"org/{key}", "verdict": verdict, "helps": helps,
            "safe": safe, "status": status, "how": f"use {key} for the router", **kw}


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "research_finds.json").write_text(json.dumps({"items": [
        find("fast-ggml", "adopt"), find("tiny-rag", "test"),
        find("unsafe-x", safe=False), find("meh", helps=False), find("done", status="accepted"), find("skipme", "skip"),
    ]}))
    now_utc = NOON.astimezone(timezone.utc)
    (data / "morning_queue.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"ts": (now_utc - timedelta(hours=1)).isoformat(), "type": "finance_alert", "severity": "high", "text": "Card declined at Costco"},
        {"ts": (now_utc - timedelta(hours=2)).isoformat(), "type": "discovery", "severity": "low", "text": "random web dump"},
        {"ts": (now_utc - timedelta(days=5)).isoformat(), "type": "finance_alert", "severity": "high", "text": "old alert"},
    ]) + "\n")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"version": 1, "values": {}}))
    monkeypatch.setenv("ABL_DATA_DIR", str(data))
    monkeypatch.setenv("ABL_SETTINGS_PATH", str(settings))
    monkeypatch.delenv("ABL_RESEARCH_MAX_LINES", raising=False)
    from src import abl_settings
    abl_settings.reset_cache()
    return tmp_path


class Spy:
    def __init__(self, channel="phone"):
        self.lines, self.channel = [], channel

    def __call__(self, text):
        self.lines.append(text)
        return self.channel


def test_filters_orders_and_caps(env):
    spy = Spy()
    r = rb.run(NOON, deliver=spy)
    assert r["status"] == "ok" and r["snake_source"] == "research_finds.json" and r["snake_actionable"] == 2
    assert spy.lines[0] == "Card declined at Costco"                         # high-severity queue item first
    assert spy.lines[1].startswith("Research: a GitHub project, org/fast-ggml, looks worth adopting")
    assert spy.lines[2].startswith("Research: a GitHub project, org/tiny-rag, looks worth testing")
    joined = " ".join(spy.lines)
    assert "unsafe-x" not in joined and "meh" not in joined and "random web dump" not in joined and "old alert" not in joined


def test_cap_holds_the_rest_for_next_run(env, monkeypatch):
    monkeypatch.setenv("ABL_RESEARCH_MAX_LINES", "1")
    a, b = Spy(), Spy()
    r1 = rb.run(NOON, deliver=a)
    r2 = rb.run(NOON + timedelta(hours=2), deliver=b)
    assert len(a.lines) == 1 and r1["held_for_later"] == 2
    assert len(b.lines) == 1 and b.lines[0] != a.lines[0]


def test_never_repeats(env):
    rb.run(NOON, deliver=Spy())
    again = Spy()
    r = rb.run(NOON + timedelta(hours=2), deliver=again)
    assert again.lines == [] and r["fresh"] == 0


def test_failed_delivery_is_retried(env):
    rb.run(NOON, deliver=Spy("dropped"))
    retry = Spy()
    rb.run(NOON + timedelta(hours=2), deliver=retry)
    assert len(retry.lines) == 3


def test_quiet_hours_only_urgent_breaks_through(env):
    spy = Spy()
    r = rb.run(datetime(2026, 11, 4, 1, 0, tzinfo=NY), deliver=spy)
    assert r["quiet_hours"] is True
    assert spy.lines == ["Card declined at Costco"] and r["held_for_later"] == 2


def test_quiet_breakthrough_none_holds_everything(env):
    (env / "settings.json").write_text(json.dumps({"version": 2, "values": {"quiet.breakthrough": "none"}}))
    from src import abl_settings
    abl_settings.reset_cache()
    spy = Spy()
    rb.run(datetime(2026, 11, 4, 1, 0, tzinfo=NY), deliver=spy)
    assert spy.lines == []


def test_dry_run_changes_nothing(env):
    r = rb.run(NOON, dry_run=True)
    assert len(r["would_speak"]) == 3
    assert not (env / "data" / "research_bridge" / "state.json").exists()


def test_disabled_in_settings(env):
    (env / "settings.json").write_text(json.dumps({"version": 2, "values": {"autopilot.systems.research_bridge": False}}))
    from src import abl_settings
    abl_settings.reset_cache()
    assert rb.run(NOON, deliver=Spy())["status"] == "disabled"


def test_without_voice_dispatch_writes_file_and_does_not_mark_seen(env):
    r = rb.run(NOON)                                     # no voice_dispatch importable in this repo
    assert all(s["channel"] == "file" and not s["delivered"] for s in r["spoken"])
    assert json.loads((env / "data" / "research_bridge" / "latest.json").read_text())["spoken"]
    assert rb.load_state()["seen"] == []


def test_seen_set_is_bounded(env):
    st = {"seen": [f"k{i}" for i in range(5000)], "last_run": None}
    rb.save_state(st)
    assert len(rb.load_state()["seen"]) == 2000


def test_voice_bridge_pull_lines(monkeypatch):
    from src import voice_bridge as vb
    b = vb.VoiceBridge(["http://127.0.0.1:9"], token="t")
    monkeypatch.setattr(b, "_request", lambda *a, **k: (200, json.dumps({"lines": ["hello", " ", "world"]}).encode()))
    assert b.pull_lines() == ["hello", "world"]
    monkeypatch.setattr(b, "_request", lambda *a, **k: (403, b"{}"))
    assert b.pull_lines() == []
