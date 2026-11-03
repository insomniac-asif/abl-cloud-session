"""Tests for src/abl_settings.py, including parity with the plugin's schema.mjs."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import abl_settings as s  # noqa: E402

NY = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _file(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setenv("ABL_SETTINGS_PATH", str(p))
    s.reset_cache()
    yield p
    s.reset_cache()


def write(p, values, version=1):
    p.write_text(json.dumps({"version": version, "values": values}))
    s.reset_cache()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_defaults_match_plugin_schema():
    js = ("import { buildSchema } from './plugins/dsh-abl-settings-r1/schema.mjs';"
          "console.log(JSON.stringify(Object.fromEntries(buildSchema().map(f => [f.key, f.default]))))")
    out = subprocess.run(["node", "--input-type=module", "-e", js], cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == s.DEFAULTS


def test_missing_file_gives_defaults():
    assert s.get("quiet.start") == "23:00"
    assert s.category_tiers()["bills"] == "YELLOW"
    assert s.all_effective()["speaker.volume"] == 70


def test_values_override_and_invalid_values_fall_back(_file):
    write(_file, {"quiet.start": "22:15", "speaker.volume": "loud", "quiet.end": "9am"})
    assert s.get("quiet.start") == "22:15"
    assert s.get("speaker.volume") == 70
    assert s.get("quiet.end") == "09:00"


def test_floors_reapplied_against_hand_edits(_file):
    write(_file, {"alerts.tiers.fraud": "GREEN", "alerts.tiers.money": "GREEN", "alerts.tiers.bills": "RED"})
    t = s.category_tiers()
    assert t["fraud"] == "RED" and t["money"] == "YELLOW" and t["bills"] == "RED"


def test_corrupt_file_is_defaults_not_exception(_file, caplog):
    _file.write_text("{nope")
    s.reset_cache()
    assert s.get("quiet.start") == "23:00"
    assert any("unreadable" in m for m in caplog.messages)


def test_extra_autopilot_systems_are_surfaced(_file):
    write(_file, {"autopilot.systems.night_watch": False})
    assert s.all_effective()["autopilot.systems.night_watch"] is False


@pytest.mark.parametrize("hhmm,dow,expect", [
    ("23:30", 0, True), ("08:59", 1, True), ("09:00", 1, False), ("12:00", 2, False), ("22:59", 3, False),
])
def test_quiet_now_wraps_midnight(hhmm, dow, expect):
    h, m = map(int, hhmm.split(":"))
    base = datetime(2026, 11, 2, tzinfo=NY)       # Monday
    now = base.replace(day=2 + dow, hour=h, minute=m)
    assert s.quiet_now(now) is expect


def test_quiet_days_and_disable(_file):
    write(_file, {"quiet.days": ["sat", "sun"]})
    mon_night = datetime(2026, 11, 2, 23, 30, tzinfo=NY)
    sun_night = datetime(2026, 11, 8, 23, 30, tzinfo=NY)
    mon_early = datetime(2026, 11, 9, 2, 0, tzinfo=NY)          # belongs to Sunday's window
    assert not s.quiet_now(mon_night) and s.quiet_now(sun_night) and s.quiet_now(mon_early)
    write(_file, {"quiet.enabled": False})
    assert not s.quiet_now(sun_night)


def test_quiet_end_after():
    now = datetime(2026, 11, 3, 2, 0, tzinfo=NY)
    end = s.quiet_end_after(now)
    assert end.hour == 9 and end.day == 3
    assert s.quiet_end_after(datetime(2026, 11, 3, 12, 0, tzinfo=NY)) is None


def test_cache_picks_up_saves(_file, monkeypatch):
    write(_file, {"speaker.volume": 10})
    assert s.get("speaker.volume") == 10
    monkeypatch.setattr(s, "_CACHE_TTL", 0.0)
    _file.write_text(json.dumps({"version": 2, "values": {"speaker.volume": 33}}))
    import os
    st = _file.stat()
    os.utime(_file, (st.st_atime, st.st_mtime + 5))
    assert s.get("speaker.volume") == 33
