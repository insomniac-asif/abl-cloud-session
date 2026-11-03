"""Tests for src/finance_voice_abl1.py with realistic bank/P2P alert emails."""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import finance_voice_abl1 as fv  # noqa: E402
from src import morning_briefing_abl1 as mb  # noqa: E402

NY = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 3)


def eml(frm, subject, body, when, extra=""):
    return (f"From: {frm}\nTo: asif@example.com\nSubject: {subject}\nMessage-ID: <{abs(hash((frm, subject, body)))}@x>\n"
            f"Date: {when.strftime('%a, %d %b %Y %H:%M:%S %z')}\n{extra}MIME-Version: 1.0\n"
            f"Content-Type: text/plain; charset=utf-8\n\n{body}\n").encode()


def at(h, m=0, d=TODAY):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NY)


MAIL = {
    "chase1": ("Chase <no.reply.alerts@chase.com>", "Your credit card transaction",
               "A charge of $42.10 at STARBUCKS was made on your credit card ending in 1234.", at(8, 5)),
    "chase1dup": ("Chase <no.reply.alerts@chase.com>", "Large purchase alert",
                  "A charge of $42.10 at STARBUCKS was made on your credit card ending in 1234.", at(8, 6)),
    "bofa": ("Bank of America <onlinebanking@ealerts.bankofamerica.com>", "Debit card transaction",
             "Amount: $63.20\nWhere: WHOLE FOODS MARKET\nCard ending in 9876", at(12, 30)),
    "capone": ("Capital One <capitalone@notification.capitalone.com>", "A new transaction was charged",
               "A charge or hold for $1,250.00 at DELTA AIR LINES was made on your Venture card ending in 5555.", at(14)),
    "venmo_out": ("Venmo <venmo@venmo.com>", "You paid Alex", "You paid Alex Kim $20.00 for pizza.", at(19)),
    "zelle_in": ("Chase <no.reply.alerts@chase.com>", "You received money with Zelle", "Sam Lee sent you $45.00 with Zelle.", at(10)),
    "deposit": ("Chase <no.reply.alerts@chase.com>", "Direct deposit posted", "A direct deposit of $1,800.00 posted to account ending in 1234.", at(6)),
    "fee": ("Chase <no.reply.alerts@chase.com>", "Overdraft notice", "An overdraft fee of $35.00 was charged to account ending in 1234.", at(7)),
    "refund": ("Chase <no.reply.alerts@chase.com>", "Refund issued", "A refund of $12.99 from NETFLIX was issued to your card ending in 1234.", at(9)),
    "phish": ('"Chase Alerts" <alerts@chase-secure-verify.co>', "You sent $500.00",
              "You sent $500.00 to John. If this wasn't you verify your account immediately.", at(3)),
    "promo": ("Chase <offers@chase.com>", "Earn more on purchases", "A charge of $0.00 at nothing. Transfer your balance!",
              at(11), "List-Unsubscribe: <mailto:u@chase.com>\n"),
    "unknown_sender": ("Some Shop <orders@randomshop.io>", "Payment received", "Payment received: A charge of $99.00 at RANDOMSHOP.", at(13)),
    "yesterday": ("Chase <no.reply.alerts@chase.com>", "Transaction", "A charge of $15.00 at CHIPOTLE was made on card ending in 1234.",
                  at(20, d=TODAY - timedelta(days=1))),
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    md = tmp_path / "Maildir" / "new"
    md.mkdir(parents=True)
    for k, (frm, sub, body, when, *extra) in MAIL.items():
        (md / k).write_bytes(eml(frm, sub, body, when, *(extra or [""])))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"version": 1, "values": {"quiet.enabled": False}}))
    monkeypatch.setenv("ABL_MAIL_DIR", str(tmp_path / "Maildir"))
    monkeypatch.setenv("ABL_SETTINGS_PATH", str(settings))
    monkeypatch.delenv("ABL_IMAP_HOST", raising=False)
    monkeypatch.delenv("ABL_LARGE_AMOUNT", raising=False)
    monkeypatch.setattr(mb, "DATA", tmp_path / "data")
    from src import abl_settings
    abl_settings.reset_cache()
    return tmp_path


def by_merchant(s):
    return {(t["merchant"], t["kind"], t["direction"]): t for t in s["transactions"]}


def test_parses_every_bank_format_and_excludes_untrusted(env):
    s = fv.build_summary(TODAY)
    assert s["status"] == "ok"
    tx = by_merchant(s)
    assert tx[("Starbucks", "purchase", "debit")]["amount"] == 42.10
    assert tx[("Starbucks", "purchase", "debit")]["account_last4"] == "1234"
    assert tx[("Starbucks", "purchase", "debit")]["category"] == "dining"
    assert tx[("Whole Foods Market", "purchase", "debit")]["category"] == "groceries"
    assert tx[("Delta Air Lines", "purchase", "debit")]["amount"] == 1250.0
    assert tx[("Alex Kim", "transfer", "debit")]["amount"] == 20.0
    assert tx[("Sam Lee", "transfer", "credit")]["amount"] == 45.0
    assert any(k[1] == "deposit" and t["amount"] == 1800.0 for k, t in tx.items())
    assert any(k[1] == "fee" and t["amount"] == 35.0 for k, t in tx.items())
    assert tx[("Netflix", "refund", "credit")]["amount"] == 12.99
    amounts = [t["amount"] for t in s["transactions"]]
    assert 500.0 not in amounts                       # phishing "You sent $500" never counted
    assert 99.0 not in amounts                        # unknown sender domain not trusted
    assert 0.0 not in amounts and 15.0 not in amounts   # marketing + yesterday excluded
    assert amounts.count(42.10) == 1                  # duplicate alert deduped
    assert s["skipped_money_mail"] >= 1


def test_totals_and_flags(env):
    s = fv.build_summary(TODAY)
    t = s["totals"]
    assert t["purchases"] == 3                         # the overdraft fee is NOT a purchase
    assert t["spent"] == round(42.10 + 63.20 + 1250.00, 2) and t["fees"] == 35.0
    assert t["net"] == round(45.00 + 1800.00 + 12.99 - t["spent"] - 35.00 - 20.00, 2)
    assert t["received"] == round(45.00 + 1800.00 + 12.99, 2)
    assert t["transfers_out"] == 20.0
    kinds = {f["type"] for f in s["flags"]}
    assert {"fee", "large"} <= kinds and "new_merchant" not in kinds        # no history yet → no "first time" claims
    assert s["largest"]["merchant"] == "Delta Air Lines"


def test_speech(env):
    sp = fv.build_summary(TODAY)["speech"]
    j = " ".join(sp)
    assert sp[0].startswith("Today you spent $1,355.30 across 3 purchases")
    assert sp[1] == "Heads up: Chase charged a $35 overdraft fee."           # actionable line right after the headline
    assert "mostly travel" in j
    assert "Largest was $1,250 at Delta Air Lines." in j
    assert "You received $1,857.99." in j and "You sent $20 in transfers." in j
    assert "Large purchase: $1,250 at Delta Air Lines." in j


def test_baseline_and_new_merchants_from_history(env):
    hp = mb.DATA / "finance"
    hp.mkdir(parents=True)
    rows = [{"date": (TODAY - timedelta(days=i)).isoformat(), "spent": 100.0, "merchants": ["Starbucks", "Whole Foods Market"]}
            for i in (1, 2, 3, 4)]
    (hp / "history.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    s = fv.build_summary(TODAY)
    assert s["baseline_daily_spend"] == 100.0 and s["vs_baseline_pct"] > 1000
    new = [f["text"] for f in s["flags"] if f["type"] == "new_merchant"]
    assert "Delta Air Lines" in new and "Starbucks" not in new
    assert "percent above your usual day" in " ".join(s["speech"])


def test_outputs_are_private_and_history_appends(env):
    s = fv.build_summary(TODAY)
    p = fv.write_outputs(s)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(p.parent).st_mode) == 0o700
    fv.write_outputs(s)
    lines = (p.parent / "history.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["spent"] == s["totals"]["spent"]
    assert len(fv.load_history(TODAY + timedelta(days=1))) == 1      # re-runs collapse to one row per day


def test_no_network_calls(env, monkeypatch):
    import socket

    def deny(*a, **k):
        raise AssertionError("finance summary must not touch the network")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    assert fv.build_summary(TODAY)["status"] == "ok"


def test_disabled_and_unavailable(env, monkeypatch):
    (env / "settings.json").write_text(json.dumps({"version": 2, "values": {"autopilot.systems.finance_voice": False}}))
    from src import abl_settings
    abl_settings.reset_cache()
    assert fv.build_summary(TODAY)["status"] == "disabled"
    (env / "settings.json").write_text(json.dumps({"version": 3, "values": {}}))
    abl_settings.reset_cache()
    monkeypatch.delenv("ABL_MAIL_DIR")
    assert fv.build_summary(TODAY)["status"] == "unavailable"


def test_quiet_day_has_simple_speech(env, monkeypatch):
    for f in (env / "Maildir" / "new").iterdir():
        f.unlink()
    s = fv.build_summary(TODAY)
    assert s["speech"] == ["No card purchases today."]


@pytest.mark.parametrize("merchant,kind,cat", [
    ("UBER *TRIP", "purchase", "transport"), ("UBER EATS", "purchase", "dining"), ("Netflix.com", "purchase", "subscriptions"),
    ("Mystery Co", "purchase", "other"), ("Sam", "transfer", "transfers"),
])
def test_categorize(merchant, kind, cat):
    assert fv.categorize(merchant, kind) == cat
