"""Tests for src/alert_rules.py with realistic emails (dicts and raw RFC 822)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import alert_rules as ar  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("ABL_SETTINGS_PATH", str(tmp_path / "settings.json"))   # absent → defaults
    for k in ("ABL_AUTORESPONDER_CONTACTS", "ABL_CONTACTS_FILE", "ABL_LARGE_AMOUNT"):
        monkeypatch.delenv(k, raising=False)
    from src import abl_settings
    abl_settings.reset_cache()


def C(**kw):
    return ar.classify_email(kw, ref_date=date(2026, 11, 3))


def ids(c):
    return {m.rule_id for m in c.matches}


# ── fraud ────────────────────────────────────────────────────────────────────

def test_display_name_spoof():
    c = C(from_addr="PayPal Security <alerts@secure-mail-center.co>", subject="Your account is limited",
          body="Unusual activity. Verify your account within 24 hours.")
    assert c.tier == "RED" and c.category == "fraud" and c.urgent
    assert "fraud.display_name_spoof" in ids(c)


def test_official_brand_sender_is_not_spoof():
    c = C(from_addr="PayPal <service@paypal.com>", subject="You sent a payment", body="You sent $25.00 to Sam.")
    assert "fraud.display_name_spoof" not in ids(c) and c.category == "money"


@pytest.mark.parametrize("addr", ["support@paypa1.com", "help@amaz0n.com", "a@xn--pypal-4ve.com", "billing@paypal-secure.net"])
def test_lookalike_domains(addr):
    assert "fraud.lookalike_domain" in ids(C(from_addr=addr, subject="hi", body="hello"))


def test_amazonaws_is_not_a_lookalike():
    assert "fraud.lookalike_domain" not in ids(C(from_addr="no-reply@sns.amazonaws.com", subject="AWS Notification", body="ok"))


def test_reply_to_mismatch_with_money_ask():
    c = C(from_addr="ceo@mycompany.com", reply_to="ceo.mycompany@gmail.com", subject="Quick favor",
          body="I need you to buy gift cards for a client, urgent.")
    assert {"fraud.reply_to_mismatch", "fraud.scam_phrase"} <= ids(c) and c.tier == "RED"


def test_auth_failure_on_brand_mail():
    c = C(from_addr="Chase <alerts@chase-notify.info>", subject="Chase: action required", body="Verify now",
          headers={"Authentication-Results": "mx.google.com; spf=fail smtp.mailfrom=chase-notify.info; dkim=none; dmarc=fail"})
    assert "fraud.auth_fail" in ids(c)


RAW = b"""From: "USPS" <track@usps-redelivery.top>
To: asif@example.com
Subject: Delivery attempt failed
Reply-To: help@another-domain.xyz
Authentication-Results: mx; spf=softfail; dkim=fail
Date: Mon, 02 Nov 2026 10:00:00 -0500
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="b1"

--b1
Content-Type: text/html; charset=utf-8

<p>We could not deliver your package. Pay the redelivery fee at
<a href="http://198.51.100.7/pay">www.usps.com/redeliver</a> immediately.</p>
--b1
Content-Type: text/html; name="invoice.html"
Content-Disposition: attachment; filename="invoice.html"

<html>x</html>
--b1--
"""


def test_raw_rfc822_phish_hits_many_signals_and_suppresses_parcels():
    c = ar.classify_email(RAW)
    got = ids(c)
    assert {"fraud.display_name_spoof", "fraud.link_mismatch", "fraud.risky_attachment",
            "fraud.scam_phrase", "fraud.auth_fail", "fraud.reply_to_mismatch"} <= got
    assert c.category == "fraud" and c.urgent
    assert "parcels" not in c.categories                  # fraud suppresses parcels/people/food


def test_risky_executable_attachment():
    c = C(from_addr="hr@company.com", subject="Payroll", body="see attached", attachments=["salary.pdf.exe"])
    assert "fraud.risky_attachment" in ids(c)


# ── security ─────────────────────────────────────────────────────────────────

def test_security_code_is_red_and_masked():
    c = C(from_addr="Google <no-reply@accounts.google.com>", subject="Your verification code",
          body="Your verification code is 482913. Do not share this code.")
    assert c.tier == "RED" and c.category == "security" and c.urgent
    blob = json.dumps(c.to_dict())
    assert "482913" not in blob


def test_new_signin_alert():
    c = C(from_addr="Microsoft account team <account-security-noreply@accountprotection.microsoft.com>",
          subject="New sign-in to your account", body="We noticed a new sign-in.")
    assert c.category == "security" and "fraud.display_name_spoof" not in ids(c)


# ── money / bills ────────────────────────────────────────────────────────────

def test_bank_transaction_alert_extracts_amount():
    c = C(from_addr="Chase <no.reply.alerts@chase.com>", subject="Your debit card transaction",
          body="A charge of $42.10 at STARBUCKS was made on your debit card.")
    assert c.category == "money" and c.tier == "RED" and not c.urgent
    assert c.facts["amounts"] == [42.10] and c.facts["merchant"] == "Chase"


def test_large_amount_is_urgent(monkeypatch):
    monkeypatch.setenv("ABL_LARGE_AMOUNT", "1000")
    c = C(from_addr="alerts@bankofamerica.com", subject="Transfer", body="You sent a transfer of $2,500.00 via Zelle.")
    assert "money.large_amount" in ids(c) and c.urgent and c.facts["max_amount"] == 2500.0


def test_bank_marketing_is_not_a_transaction():
    c = C(from_addr="offers@chase.com", subject="Earn 5% on your balance", body="Transfer your balance today!",
          headers={"List-Unsubscribe": "<mailto:u@chase.com>"})
    assert "money" not in c.categories and c.bulk


def test_bill_due_is_yellow_with_due_date_and_amount():
    c = C(from_addr="billing@coned.com", subject="Your bill is ready",
          body="Amount due: $134.22. Payment due Nov 15, 2026. Autopay is off.")
    assert c.tier == "YELLOW" and c.category == "bills"
    assert c.facts["due_date"] == "2026-11-15" and c.facts["amounts"] == [134.22]


@pytest.mark.parametrize("text,expect", [
    ("Payment due 12/01/2026", "2026-12-01"), ("due by 2026-11-20", "2026-11-20"),
    ("payment due Jan 5", "2027-01-05"), ("due on Nov 10", "2026-11-10"), ("no date", None),
])
def test_due_date_parsing(text, expect):
    d = ar.parse_due_date(text, ref=date(2026, 12, 20) if "Jan" in text else date(2026, 11, 3))
    assert (d.isoformat() if d else None) == expect


def test_settings_panel_tier_override(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"version": 1, "values": {"alerts.tiers.bills": "RED", "alerts.tiers.money": "GREEN"}}))
    from src import abl_settings
    abl_settings.reset_cache()
    assert C(from_addr="b@coned.com", subject="Your bill is ready", body="x").tier == "RED"
    # money floor holds even if settings say GREEN
    assert C(from_addr="a@chase.com", subject="Transaction", body="A charge of $5.00").tier == "YELLOW"


# ── food / parcels ───────────────────────────────────────────────────────────

def test_doordash_is_green_food():
    c = C(from_addr="DoorDash <no-reply@doordash.com>", subject="Your order is on its way", body="Your Dasher is nearby.")
    assert c.tier == "GREEN" and c.category == "food"


def test_ups_shipped_with_tracking():
    c = C(from_addr="UPS <mcinfo@ups.com>", subject="Your package has shipped", body="Tracking number 1Z999AA10123456784")
    assert c.tier == "GREEN" and c.category == "parcels"
    assert c.facts["tracking"] == [{"carrier": "ups", "number": "1Z999AA10123456784"}]


def test_usps_and_fedex_tracking_extraction():
    t = ar.tracking_numbers("USPS 9400111899223197428490 and FedEx tracking 123456789012")
    assert {"carrier": "usps", "number": "9400111899223197428490"} in t
    assert {"carrier": "fedex", "number": "123456789012"} in t


# ── people ───────────────────────────────────────────────────────────────────

def test_personal_mail_is_people_but_newsletter_is_not():
    friend = C(from_addr="Sam Lee <sam.lee@gmail.com>", subject="dinner?", body="free saturday?")
    assert friend.category == "people" and friend.tier == "YELLOW"
    news = C(from_addr="Sam's Newsletter <sam.lee@gmail.com>", subject="Issue 4", body="hi",
             headers={"List-Unsubscribe": "<x>"})
    assert "people" not in news.categories


def test_known_contact_from_env(monkeypatch):
    monkeypatch.setenv("ABL_AUTORESPONDER_CONTACTS", "mom@family.net")
    assert "people.contact" in ids(C(from_addr="mom@family.net", subject="call me", body=""))


def test_unmatched_mail_has_no_tier():
    c = C(from_addr="news@somesite.com", subject="Weekly digest", body="articles", headers={"List-Id": "x"})
    assert c.tier is None and c.category is None and c.bulk


# ── robustness + adapters ────────────────────────────────────────────────────

def test_broken_rule_does_not_break_classification():
    def boom(e):
        raise RuntimeError("bad rule")
    rules = ar.RULES + [ar.Rule("x.boom", "fraud", "broken", boom)]
    c = ar.classify_email({"from_addr": "a@doordash.com", "subject": "Your order is on its way"}, rules=rules)
    assert c.category == "food" and any("x.boom" in e for e in ar.rule_errors())


def test_classify_adapter_shape():
    out = ar.classify("Your verification code is 123456", sender="no-reply@accounts.google.com")
    assert out["tier"] == "RED" and "security" in out["categories"]


def test_autoresponder_discovers_this_module(monkeypatch):
    from src import autoresponder
    monkeypatch.delenv("ABL_ALERT_RULES_DISABLE", raising=False)
    monkeypatch.setenv("ABL_ALERT_RULES_PATH", str(ROOT / "src" / "alert_rules.py"))
    autoresponder.reset_alert_rules_cache()
    try:
        v = autoresponder.jev_classify("Your Chase statement is ready. Minimum payment due Friday.", "x", "email", chain=[])
        assert any(r.endswith(":alert_rules") for r in v.reasons)
        assert autoresponder.status()["alert_rules"]["has_fn"] is True
    finally:
        autoresponder.reset_alert_rules_cache()


def test_cli(tmp_path, capsys):
    p = tmp_path / "m.eml"
    p.write_bytes(RAW)
    assert ar._main([str(p)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["category"] == "fraud"
