"""Tests for src/morning_briefing_abl1.py with a realistic overnight fixture."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import morning_briefing_abl1 as mb  # noqa: E402

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 9, 30, tzinfo=NY)          # Wednesday 09:30


def eml(frm, subject, body, when, extra=""):
    return (f"From: {frm}\nTo: asif@example.com\nSubject: {subject}\n"
            f"Date: {when.strftime('%a, %d %b %Y %H:%M:%S %z')}\n{extra}MIME-Version: 1.0\n"
            f"Content-Type: text/plain; charset=utf-8\n\n{body}\n").encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    md = tmp_path / "Maildir" / "new"
    md.mkdir(parents=True)
    night = NOW - timedelta(hours=8)
    msgs = {
        "1": eml('"PayPal Security" <alert@secure-paypa1.co>', "Account limited", "Verify your account immediately.", night),
        "2": eml("Google <no-reply@accounts.google.com>", "Your verification code", "Your verification code is 482913.", night),
        "3": eml("ConEd <billing@coned.com>", "Your bill is ready", "Amount due: $134.22. Payment due Nov 7, 2026.", NOW - timedelta(days=10)),
        "4": eml("Chase <no.reply.alerts@chase.com>", "Transaction alert", "A charge of $42.10 at STARBUCKS.", night),
        "5": eml("Sam Lee <sam.lee@gmail.com>", "dinner saturday?", "are you free?", night),
        "6": eml("UPS <mcinfo@ups.com>", "Your package has shipped", "Tracking number 1Z999AA10123456784", night),
        "7": eml("Old <a@b.com>", "ancient", "nothing", NOW - timedelta(days=60)),
    }
    for k, v in msgs.items():
        (md / k).write_bytes(v)
    ar_dir = tmp_path / "ar"
    ar_dir.mkdir()
    ts = (NOW - timedelta(hours=6)).timestamp()
    (ar_dir / "audit.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"ts": ts, "effect": "alerted"}, {"ts": ts, "effect": "held"}, {"ts": ts, "effect": "shadow:log"},
        {"ts": ts - 86400 * 3, "effect": "alerted"}]) + "\n")
    (ar_dir / "pending.json").write_text(json.dumps({
        "a": {"status": "pending", "held_for_quiet_hours": True}, "b": {"status": "sent"}}))
    ics = tmp_path / "cal.ics"
    ics.write_text("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20261104T150000Z\r\nSUMMARY:Dentist\\, Dr. Ray\r\n"
                   "LOCATION:Main St\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261104\r\nSUMMARY:Mom's birthday\r\n"
                   "END:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART:20261105T150000Z\r\nSUMMARY:Tomorrow thing\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"version": 1, "values": {}}))
    for k, v in {"ABL_MAIL_DIR": str(tmp_path / "Maildir"), "ABL_AUTORESPONDER_DATA_DIR": str(ar_dir),
                 "ABL_CALENDAR_ICS": str(ics), "ABL_HOME_LAT": "40.7589", "ABL_HOME_LON": "-73.9851",
                 "ABL_SETTINGS_PATH": str(settings), "ABL_DATA_DIR": str(tmp_path / "data")}.items():
        monkeypatch.setenv(k, v)
    for k in ("ABL_IMAP_HOST", "ABL_BILLS_FILE", "ABL_AUTOPILOT_LOG", "ABL_AUTORESPONDER_CONTACTS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mb, "DATA", tmp_path / "data")
    from src import abl_settings
    abl_settings.reset_cache()
    return tmp_path


def fake_weather(seen):
    def f(url):
        seen.append(url)
        return {"current": {"temperature_2m": 51.2, "weather_code": 3},
                "daily": {"temperature_2m_max": [58.4], "temperature_2m_min": [44.1],
                          "precipitation_probability_max": [70], "weather_code": [61]}}
    return f


def test_full_briefing(env):
    urls = []
    b = mb.build_briefing(now=NOW, weather_fetch=fake_weather(urls))
    s = b["sections"]
    assert b["status"] == "ok" and b["priority"] == "urgent" and b["deliver_after"] is None
    # weather: rounded coordinates only
    assert "latitude=40.8" in urls[0] and "longitude=-74.0" in urls[0] and "40.7589" not in urls[0]
    assert s["weather"]["data"]["conditions"] == "light rain"
    # email: overnight only (5 messages), old bill excluded from the overnight count
    e = s["email"]["data"]
    assert e["total"] == 5
    assert e["by_category"]["fraud"] == 1 and e["by_category"]["security"] == 1
    assert e["items"][0]["urgent"] is True
    # bills: found from the 10-day-old statement, due Saturday
    bills = s["bills"]["data"]["due_soon"]
    assert bills == [{"payee": "ConEd", "due": "2026-11-07", "amount": 134.22, "autopay": False, "source": "email"}]
    # autopilot: only overnight audit lines; the quiet-hours batch is announced
    ap = s["autopilot"]["data"]
    assert ap["messages_triaged"] == 3 and ap["held_for_quiet_hours"] == 1
    # calendar: UTC 15:00 → 10:00 ET; all-day first; tomorrow excluded
    ev = s["calendar"]["data"]["events"]
    assert [x["summary"] for x in ev] == ["Mom's birthday", "Dentist, Dr. Ray"] and ev[1]["start"] == "10:00"
    sp = b["speech"]
    assert sp[0] == "Good morning."
    assert sp[1].startswith("Heads up: possible scam")          # urgent first
    joined = " ".join(sp)
    assert "482913" not in json.dumps(b)                         # codes never leave alert_rules
    assert "ConEd for $134.22 is due Saturday." in joined
    assert "10 a.m.: Dentist, Dr. Ray." in joined
    assert "chance of rain" in joined
    assert "1 draft reply is waiting from overnight; it sends in a minute" in joined
    assert "From people: Sam Lee about dinner saturday." in joined
    assert "raised 1 alert." in joined and "1 money or account notice," in joined and "1 delivery update." in joined
    assert not any(x.endswith(("?.", "..", "!.")) for x in sp)


def test_sources_missing_are_reported_not_fatal(env, monkeypatch):
    for k in ("ABL_MAIL_DIR", "ABL_CALENDAR_ICS", "ABL_HOME_LAT"):
        monkeypatch.delenv(k)

    def boom(url):
        raise AssertionError("weather must not be fetched without a location")
    b = mb.build_briefing(now=NOW, weather_fetch=boom)
    st = {k: v["status"] for k, v in b["sections"].items()}
    assert st["email"] == "unavailable" and st["calendar"] == "unavailable" and st["weather"] == "unavailable"
    assert b["speech"][0] == "Good morning."


def test_weather_failure_is_isolated(env):
    def down(url):
        raise TimeoutError("open-meteo slow")
    b = mb.build_briefing(now=NOW, weather_fetch=down)
    assert b["sections"]["weather"]["status"] == "unavailable"
    assert b["sections"]["email"]["status"] == "ok"


def test_disabled_in_settings(env):
    (env / "settings.json").write_text(json.dumps({"version": 2, "values": {"autopilot.systems.morning_briefing": False}}))
    from src import abl_settings
    abl_settings.reset_cache()
    b = mb.build_briefing(now=NOW, weather_fetch=fake_weather([]))
    assert b["status"] == "disabled" and b["speech"] == []


def test_generated_in_quiet_hours_sets_deliver_after(env):
    b = mb.build_briefing(now=NOW.replace(hour=6, minute=0), weather_fetch=fake_weather([]))
    assert b["deliver_after"].startswith("2026-11-04T09:00")


def test_bills_ledger_and_autopay(env, monkeypatch):
    led = env / "bills.json"
    led.write_text(json.dumps([{"payee": "Rent", "due": "2026-11-04", "amount": 2100.0, "autopay": True},
                               {"payee": "Far future", "due": "2026-12-30", "amount": 1.0}]))
    monkeypatch.setenv("ABL_BILLS_FILE", str(led))
    b = mb.build_briefing(now=NOW, weather_fetch=fake_weather([]))
    payees = [x["payee"] for x in b["sections"]["bills"]["data"]["due_soon"]]
    assert payees == ["Rent", "ConEd"]
    assert "Rent for $2,100.00 is due today, on autopay." in b["speech"]


def test_speech_is_capped(env, monkeypatch):
    monkeypatch.setenv("ABL_BRIEFING_MAX_CHARS", "120")
    b = mb.build_briefing(now=NOW, weather_fetch=fake_weather([]))
    assert b["speech"][-1] == "More in the dashboard." and sum(len(x) for x in b["speech"][:-1]) <= 120


def test_calendar_url_must_be_local(env, monkeypatch):
    monkeypatch.setenv("ABL_CALENDAR_ICS", "https://calendar.example.com/me.ics")
    b = mb.build_briefing(now=NOW, weather_fetch=fake_weather([]))
    assert b["sections"]["calendar"]["status"] == "error" and "local" in b["sections"]["calendar"]["error"]


def test_outputs_written_atomically(env):
    b = mb.build_briefing(now=NOW, weather_fetch=fake_weather([]))
    p = mb.write_outputs(b)
    assert json.loads(p.read_text())["date"] == "2026-11-04"
    assert (p.parent / "2026-11-04.json").exists()
