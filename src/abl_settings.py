"""src/abl_settings.py — read-only view of ABL settings for Python consumers.

The single WRITER is the dsh plugin ``dsh-abl-settings-r1`` (plugins/dsh-abl-settings-r1),
which stores flat dotted keys in ``data/abl/settings.json``:
    {"version": 7, "updated_at": "...", "values": {"quiet.start": "22:30", ...}}

Everything else (autoresponder, morning briefing, companion check-in, bot quiet hours,
the desktop pull) READS through this module:

    from src import abl_settings
    abl_settings.get("quiet.start")          # "23:00" unless set
    abl_settings.quiet_now()                 # True inside quiet hours (tz-aware, wraps midnight)
    abl_settings.category_tiers()            # floors re-applied (fraud/security RED, money >= YELLOW)

Precedence: settings.json value (if valid) > code default. A missing or corrupt file means
defaults, plus a one-time warning, never an exception. Reads are cached for 5 s, keyed on
the file's mtime, so a save in the panel reaches every consumer within 5 s without a restart.

DEFAULTS mirrors plugins/dsh-abl-settings-r1/schema.mjs; tests/test_abl_settings.py
runs the JS schema through node and fails on any drift.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

log = logging.getLogger("abl.settings")

TIERS = ("GREEN", "YELLOW", "RED")
TIER_FLOORS = {"fraud": "RED", "security": "RED", "money": "YELLOW"}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

DEFAULTS: dict[str, Any] = {
    "models.primary": "llama-27b@127.0.0.1:8085",
    "models.light": "qwen2.5:3b@127.0.0.1:11434",
    "models.triage_chain": ["jev", "deepseek", "local", "light"],
    "models.review_enabled": False,
    "models.reserve_interactive_slot": True,
    "autopilot.enabled": True,
    "autopilot.follow_quiet_hours": True,
    "autopilot.systems.autopilot": True,
    "autopilot.systems.morning_briefing": True,
    "autopilot.systems.research_bridge": True,
    "autopilot.systems.finance_voice": True,
    "autopilot.systems.chimes": True,
    "quiet.enabled": True,
    "quiet.start": "23:00",
    "quiet.end": "09:00",
    "quiet.tz": "America/New_York",
    "quiet.days": list(DAYS),
    "quiet.breakthrough": "urgent_red",
    "quiet.speaker": "silent",
    "quiet.speaker_volume": 20,
    "quiet.yellow_policy": "hold",
    "alerts.tiers.fraud": "RED",
    "alerts.tiers.security": "RED",
    "alerts.tiers.money": "RED",
    "alerts.tiers.bills": "YELLOW",
    "alerts.tiers.people": "YELLOW",
    "alerts.tiers.food": "GREEN",
    "alerts.tiers.parcels": "GREEN",
    "autoresponder.mode": "shadow",
    "autoresponder.autosend_channels": [],
    "autoresponder.hold_seconds": 60,
    "speaker.volume": 70,
    "speaker.chime_volume": 40,
    "speaker.rate": 1.0,
    "speaker.voice": "",
    "speaker.max_utterance_s": 20,
}

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_CACHE_TTL = 5.0
_lock = threading.Lock()
_cache: dict[str, Any] = {"path": None, "mtime": None, "read_at": 0.0, "values": {}}
_warned = False


def settings_path() -> Path:
    env = os.environ.get("ABL_SETTINGS_PATH")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[1] / "data" / "abl" / "settings.json"


def _valid(key: str, value: Any) -> bool:
    """Light type check against the default's shape (the plugin does full validation)."""
    d = DEFAULTS.get(key)
    if key.startswith("alerts.tiers."):
        return value in TIERS
    if key in ("quiet.start", "quiet.end"):
        return isinstance(value, str) and bool(_TIME_RE.match(value))
    if isinstance(d, bool):
        return isinstance(value, bool)
    if isinstance(d, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(d, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(d, list):
        return isinstance(value, list)
    if isinstance(d, str):
        return isinstance(value, str)
    return key.startswith("autopilot.systems.") and isinstance(value, bool)


def _load() -> dict[str, Any]:
    global _warned
    path = settings_path()
    now = time.monotonic()
    with _lock:
        if _cache["path"] == str(path) and now - _cache["read_at"] < _CACHE_TTL:
            return _cache["values"]
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if _cache["path"] == str(path) and mtime is not None and mtime == _cache["mtime"]:
            _cache["read_at"] = now
            return _cache["values"]
        values: dict[str, Any] = {}
        if mtime is not None:
            try:
                doc = json.loads(path.read_text("utf-8"))
                raw = doc.get("values") if isinstance(doc, dict) else None
                if isinstance(raw, dict):
                    values = raw
                _warned = False
            except (OSError, ValueError) as e:
                if not _warned:
                    log.warning("settings.json unreadable (%s); using defaults", e)
                    _warned = True
        _cache.update(path=str(path), mtime=mtime, read_at=now, values=values)
        return values


def reset_cache() -> None:
    with _lock:
        _cache.update(path=None, mtime=None, read_at=0.0, values={})


def get(key: str, default: Any = None) -> Any:
    values = _load()
    v = values.get(key)
    if v is not None and _valid(key, v):
        out = v
    else:
        out = DEFAULTS.get(key, default)
    if key.startswith("alerts.tiers."):
        cat = key.rsplit(".", 1)[1]
        floor = TIER_FLOORS.get(cat)
        if floor and TIERS.index(out) < TIERS.index(floor):
            out = floor
    return out


def is_set(key: str) -> bool:
    """True when settings.json holds a VALID value for key (used for settings > env precedence)."""
    v = _load().get(key)
    return v is not None and _valid(key, v)


def all_effective() -> dict[str, Any]:
    keys = set(DEFAULTS) | {k for k in _load() if k.startswith("autopilot.systems.")}
    return {k: get(k) for k in sorted(keys)}


def category_tiers() -> dict[str, str]:
    return {c: get(f"alerts.tiers.{c}") for c in ("fraud", "security", "money", "bills", "people", "food", "parcels")}


def _parse_hm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def quiet_now(now: Optional[datetime] = None) -> bool:
    """True inside the configured quiet window. Handles windows that wrap midnight and
    the day-of-week list (a window starting Sun 23:00 counts as Sunday's)."""
    if not get("quiet.enabled"):
        return False
    tzname = get("quiet.tz")
    tz = None
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = None
    now = now.astimezone(tz) if (now is not None and tz is not None and now.tzinfo) else (now or datetime.now(tz))
    start, end = _parse_hm(get("quiet.start")), _parse_hm(get("quiet.end"))
    t = now.time().replace(second=0, microsecond=0)
    days = set(get("quiet.days"))
    today = DAYS[now.weekday()]
    yesterday = DAYS[(now.weekday() - 1) % 7]
    if start == end:
        return False
    if start < end:                                   # same-day window, e.g. 13:00–15:00
        return today in days and start <= t < end
    if t >= start:                                    # wraps midnight: evening part belongs to today
        return today in days
    if t < end:                                       # early-morning part belongs to yesterday's window
        return yesterday in days
    return False


def quiet_end_after(now: Optional[datetime] = None) -> Optional[datetime]:
    """When the current quiet window ends (aware datetime), or None if not in quiet hours."""
    if not quiet_now(now):
        return None
    tz = ZoneInfo(get("quiet.tz")) if ZoneInfo is not None else None
    now = now.astimezone(tz) if (now is not None and tz is not None and now.tzinfo) else (now or datetime.now(tz))
    end = _parse_hm(get("quiet.end"))
    cand = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if cand <= now:
        from datetime import timedelta
        cand += timedelta(days=1)
    return cand


if __name__ == "__main__":  # python -m src.abl_settings
    print(json.dumps({"path": str(settings_path()), "quiet_now": quiet_now(), "effective": all_effective()}, indent=2, default=str))
