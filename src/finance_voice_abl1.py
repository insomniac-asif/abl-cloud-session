"""src/finance_voice_abl1.py — daily spending summary from bank email alerts (abl1).

A port of the desktop ``~/ops/speaker/finance_voice.py`` (one of the 8 screen-off systems).
It reads the same mail source as the morning briefing (``ABL_MAIL_DIR`` or read-only IMAP),
parses bank / card / P2P alert emails into transactions, and writes a structured daily
summary plus TTS-ready lines.

SAFETY / PRIVACY (non-negotiable):
  * Only mail that src/alert_rules.py classifies as **money and NOT fraud**, from a
    **known bank/payments sender domain**, is parsed. A phishing "You sent $500" never
    becomes spending, and neither does bank marketing (bulk mail is excluded by alert_rules).
  * Financial data never leaves the box: no network calls at all; files are written 0600.
  * Account numbers are reduced to their last 4 digits; nothing else from the account is kept.

Output: data/abl/finance/{latest,YYYY-MM-DD}.json, and one line per day appended to
data/abl/finance/history.jsonl (trailing-average baseline + first-time-merchant detection).

Usage:
  python -m src.finance_voice_abl1                 # today (local tz), JSON
  python -m src.finance_voice_abl1 --date 2026-11-03 --speech
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import alert_rules  # noqa: E402
from src import morning_briefing_abl1 as mb  # noqa: E402  (shared mail collection + tz)

log = logging.getLogger("abl.finance")

BANK_DOMAINS = {
    "chase.com": "Chase", "jpmorgan.com": "Chase", "bankofamerica.com": "Bank of America", "bofa.com": "Bank of America",
    "wellsfargo.com": "Wells Fargo", "capitalone.com": "Capital One", "citi.com": "Citi", "citibank.com": "Citi",
    "americanexpress.com": "Amex", "aexp.com": "Amex", "discover.com": "Discover", "usbank.com": "U.S. Bank",
    "pnc.com": "PNC", "td.com": "TD", "tdbank.com": "TD", "ally.com": "Ally", "schwab.com": "Schwab",
    "sofi.com": "SoFi", "chime.com": "Chime", "venmo.com": "Venmo", "paypal.com": "PayPal", "cash.app": "Cash App",
    "square.com": "Cash App", "squareup.com": "Cash App", "zellepay.com": "Zelle", "apple.com": "Apple Card",
    "navyfederal.org": "Navy Federal", "usaa.com": "USAA", "marcus.com": "Marcus",
}

AMT = r"\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)"
MERCH = r"([A-Z0-9][\w&'*.#/ -]{1,40}?)"
END = r"(?=\s+(?:on|was|has|for|using|with your|ending|at \d|\.|,)|[.,\n]|$)"

# (kind, direction, regex). Order matters: specific before generic. group 'amt' and optional 'who'.
PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("fee", "debit", re.compile(rf"(?i)\b(overdraft|late|returned item|monthly service|foreign transaction|atm)\s+fee\b[^$\n]{{0,40}}?{AMT}")),
    ("fee", "debit", re.compile(rf"(?i){AMT}[^\n]{{0,25}}?\b(overdraft|late|service|foreign transaction|atm) fee\b")),
    ("deposit", "credit", re.compile(rf"(?i)\bdirect deposit\b[^$\n]{{0,40}}?{AMT}")),
    ("deposit", "credit", re.compile(rf"(?i){AMT}[^\n]{{0,30}}?\b(direct )?deposit(ed)?\b")),
    ("refund", "credit", re.compile(rf"(?i)\brefund(?: of)?\s+{AMT}(?:\s+(?:from|by)\s+{MERCH}{END})?")),
    ("transfer", "credit", re.compile(rf"(?i)\b{MERCH}\s+sent you\s+{AMT}")),
    ("transfer", "credit", re.compile(rf"(?i)\byou (?:received|got)\s+{AMT}\s+from\s+{MERCH}{END}")),
    ("transfer", "debit", re.compile(rf"(?i)\byou (?:sent|paid)\s+{AMT}\s+to\s+{MERCH}{END}")),
    ("transfer", "debit", re.compile(rf"(?i)\byou (?:sent|paid)\s+{MERCH}\s+{AMT}")),
    ("purchase", "debit", re.compile(rf"(?i)\b(?:a )?(?:charge|purchase|transaction|payment)(?: or hold)?(?: of| for)?\s+{AMT}\s+(?:at|with|to)\s+{MERCH}{END}")),
    ("purchase", "debit", re.compile(rf"(?i)\byou made an? {AMT}\s+(?:transaction|purchase|payment)\s+(?:at|with|to)\s+{MERCH}{END}")),
    ("purchase", "debit", re.compile(rf"(?i)\byour {AMT}\s+(?:transaction|purchase)\s+(?:at|with)\s+{MERCH}{END}")),
    ("purchase", "debit", re.compile(rf"(?i)\bamount:?\s*{AMT}[\s\S]{{0,80}}?\b(?:where|merchant|at):?\s*{MERCH}{END}")),
    ("purchase", "debit", re.compile(rf"(?i)\b(?:merchant|where):?\s*{MERCH}\s*[\s\S]{{0,60}}?\bamount:?\s*{AMT}")),
]

LAST4_RE = re.compile(r"(?i)(?:ending|ends) in\s*(?:\*+|x+)?(\d{4})|\b(?:\*{2,}|x{2,})(\d{4})\b|account\s*(?:#|no\.?|number)?\s*[x*]+(\d{4})")

CATEGORIES: dict[str, tuple[str, ...]] = {
    "dining": ("starbucks", "coffee", "cafe", "restaurant", "pizza", "burger", "chipotle", "mcdonald", "doordash", "uber eats",
               "ubereats", "grubhub", "sweetgreen", "taco", "sushi", "bar ", "grill", "kitchen", "dunkin", "seamless"),
    "groceries": ("whole foods", "trader joe", "safeway", "kroger", "wegmans", "costco", "aldi", "instacart", "grocery", "market", "publix", "h mart"),
    "transport": ("uber", "lyft", "mta", "metro", "transit", "parking", "toll", "amtrak", "citi bike"),
    "gas": ("shell", "exxon", "chevron", "bp ", "mobil", "sunoco", "gas"),
    "shopping": ("amazon", "amzn", "target", "walmart", "best buy", "apple.com/bill", "etsy", "ebay", "ikea", "home depot"),
    "subscriptions": ("netflix", "spotify", "hulu", "disney", "youtube", "icloud", "apple.com", "openai", "anthropic", "github", "patreon", "adobe"),
    "utilities": ("con ed", "coned", "verizon", "t-mobile", "at&t", "comcast", "xfinity", "spectrum", "pg&e", "electric", "water"),
    "health": ("cvs", "walgreens", "pharmacy", "dental", "clinic", "medical", "hospital"),
    "travel": ("airbnb", "hotel", "delta", "united", "jetblue", "american airlines", "marriott", "expedia"),
    "entertainment": ("steam", "playstation", "xbox", "nintendo", "cinema", "amc", "ticketmaster", "fandango"),
}


@dataclass
class Txn:
    date: str
    amount: float
    direction: str                 # debit | credit
    kind: str                      # purchase | transfer | deposit | refund | fee
    merchant: str
    category: str
    bank: str
    account_last4: Optional[str]
    message_id: str
    detail: Optional[str] = None   # e.g. "overdraft" for a fee

    @property
    def key(self) -> tuple:
        return (self.date, round(self.amount, 2), self.direction, self.merchant.lower(), self.account_last4)


def categorize(merchant: str, kind: str) -> str:
    if kind in ("transfer", "deposit", "refund", "fee"):
        return {"transfer": "transfers", "deposit": "income", "refund": "refunds", "fee": "fees"}[kind]
    m = f" {merchant.lower()} "
    for cat, words in CATEGORIES.items():
        if any(w in m for w in words):
            return cat
    return "other"


def _clean_merchant(s: Optional[str]) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip(" .,:;-*#"))
    s = re.sub(r"(?i)\s+(on|was|for|using)$", "", s)
    if not s:
        return "unknown"
    return s.title() if s.isupper() and len(s) > 3 else s


def bank_for(domain: str) -> Optional[str]:
    reg = alert_rules.registrable(domain)
    return BANK_DOMAINS.get(reg)


def parse_transactions(e: alert_rules.Email, c: alert_rules.Classification, tz) -> list[Txn]:
    """Transactions in one alert email. Empty unless the mail is trusted money mail."""
    if "money" not in c.categories or "fraud" in c.categories or c.bulk:
        return []
    bank = bank_for(e.from_domain)
    if not bank:
        return []
    text = alert_rules.mask_codes(f"{e.subject}\n{e.body}")
    when = e.date or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    day = when.astimezone(tz).date().isoformat()
    m4 = LAST4_RE.search(text)
    last4 = next((g for g in m4.groups() if g), None) if m4 else None
    out, spans = [], []
    for kind, direction, rx in PATTERNS:
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in spans):           # already consumed by a more specific pattern
                continue
            gd = [g for g in m.groups() if g]
            amt_s = next((g for g in gd if re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?", g)), None)
            if not amt_s:
                continue
            fee_words = r"(?i)overdraft|late|returned item|monthly service|foreign transaction|atm|service"
            detail = next((g.lower() for g in gd if re.fullmatch(fee_words, g)), None) if kind == "fee" else None
            others = [g for g in gd if g != amt_s and not re.fullmatch(fee_words + r"|direct |direct|ed", g)]
            merchant = _clean_merchant(others[0] if others else (bank if kind in ("fee", "deposit") else ""))
            amount = float(amt_s.replace(",", ""))
            if amount <= 0:
                continue
            spans.append((m.start(), m.end()))
            out.append(Txn(day, amount, direction, kind, merchant, categorize(merchant, kind), bank, last4, e.message_id, detail))
    return out


def dedupe(txns: list[Txn]) -> list[Txn]:
    seen, out = set(), []
    for t in txns:
        if t.key in seen:                     # banks send "transaction" + "large purchase" for the same charge
            continue
        seen.add(t.key)
        out.append(t)
    return out


def _history_path() -> Path:
    return mb.DATA / "finance" / "history.jsonl"


def load_history(before: date, days: int = 30) -> list[dict]:
    rows = []
    try:
        for line in _history_path().read_text("utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            d = date.fromisoformat(r.get("date", "1970-01-01"))
            if before - timedelta(days=days) <= d < before:
                rows.append(r)
    except OSError:
        pass
    latest: dict[str, dict] = {}
    for r in rows:                             # re-runs append again: keep the last row per day
        latest[r["date"]] = r
    return [latest[k] for k in sorted(latest)]


def _money(x: float) -> str:
    return f"${x:,.2f}" if x % 1 else f"${x:,.0f}"


def summarize(txns: list[Txn], day: date, history: list[dict]) -> dict[str, Any]:
    todays = [t for t in txns if t.date == day.isoformat()]
    debits = [t for t in todays if t.direction == "debit" and t.kind == "purchase"]
    fees = [t for t in todays if t.kind == "fee"]
    transfers_out = [t for t in todays if t.direction == "debit" and t.kind == "transfer"]
    credits = [t for t in todays if t.direction == "credit"]
    spent = round(sum(t.amount for t in debits), 2)
    fees_total = round(sum(t.amount for t in fees), 2)
    by_cat: dict[str, float] = defaultdict(float)
    by_merchant: dict[str, float] = defaultdict(float)
    for t in debits:
        by_cat[t.category] += t.amount
        by_merchant[t.merchant] += t.amount
    baseline_days = [h["spent"] for h in history if isinstance(h.get("spent"), (int, float))]
    baseline = round(sum(baseline_days) / len(baseline_days), 2) if len(baseline_days) >= 3 else None
    known = {m.lower() for h in history for m in h.get("merchants", [])}
    large_thr = float(os.environ.get("ABL_LARGE_AMOUNT", "500"))
    flags = []
    for t in fees:                               # avoidable charges first
        flags.append({"type": "fee", "text": f"{t.merchant} charged a {_money(t.amount)} {(t.detail + ' ') if t.detail else ''}fee"})
    for t in todays:
        if t.kind == "purchase" and t.amount >= large_thr:
            flags.append({"type": "large", "text": f"{_money(t.amount)} at {t.merchant}"})
    if history:
        for m in sorted({t.merchant for t in debits if t.merchant.lower() not in known and t.merchant != "unknown"}):
            flags.append({"type": "new_merchant", "text": m})
    largest = max(debits, key=lambda t: t.amount, default=None)
    return {
        "date": day.isoformat(),
        "totals": {"spent": spent, "purchases": len(debits), "received": round(sum(t.amount for t in credits), 2),
                   "fees": fees_total, "transfers_out": round(sum(t.amount for t in transfers_out), 2),
                   "net": round(sum(t.amount for t in credits) - spent - fees_total - sum(t.amount for t in transfers_out), 2)},
        "by_category": dict(sorted(((k, round(v, 2)) for k, v in by_cat.items()), key=lambda kv: -kv[1])),
        "top_merchants": [{"merchant": k, "amount": round(v, 2)} for k, v in sorted(by_merchant.items(), key=lambda kv: -kv[1])[:5]],
        "largest": asdict(largest) if largest else None,
        "baseline_daily_spend": baseline,
        "vs_baseline_pct": round((spent - baseline) / baseline * 100) if baseline else None,
        "flags": flags,
        "transactions": [asdict(t) for t in todays],
    }


def speech_for(s: dict[str, Any]) -> list[str]:
    t = s["totals"]
    lines = []
    if t["purchases"] == 0:
        lines.append("No card purchases today.")
    else:
        top = next(iter(s["by_category"]), None)
        mostly = f", mostly {top}" if top and top != "other" and s["by_category"][top] >= 0.4 * t["spent"] else ""
        lines.append(f"Today you spent {_money(t['spent'])} across {t['purchases']} purchase{'s' if t['purchases'] != 1 else ''}{mostly}.")
        pct = s["vs_baseline_pct"]
        if pct is not None and abs(pct) >= 15:
            lines.append(f"That's {abs(pct)} percent {'above' if pct > 0 else 'below'} your usual day.")
        if s["largest"] and t["purchases"] > 1:
            lines.append(f"Largest was {_money(s['largest']['amount'])} at {s['largest']['merchant']}.")
    if t["received"]:
        lines.append(f"You received {_money(t['received'])}.")
    if t["transfers_out"]:
        lines.append(f"You sent {_money(t['transfers_out'])} in transfers.")
    fee_lines = [f"Heads up: {f['text']}." for f in s["flags"] if f["type"] == "fee"]
    lines[1:1] = fee_lines                       # right after the headline: it's the actionable part
    for f in s["flags"]:
        if f["type"] == "large":
            lines.append(f"Large purchase: {f['text']}.")
    new = [f["text"] for f in s["flags"] if f["type"] == "new_merchant"]
    if new:
        lines.append(f"First time at {', '.join(new[:3])}.")
    return lines


def build_summary(day: Optional[date] = None) -> dict[str, Any]:
    tz = mb._tz()
    st = mb._settings()
    now = datetime.now(tz)
    day = day or now.date()
    if st is not None and not (st.get("autopilot.enabled") and st.get("autopilot.systems.finance_voice")):
        return {"status": "disabled", "date": day.isoformat(), "speech": [], "reason": "autopilot.systems.finance_voice is off in Settings"}
    since = datetime.combine(day - timedelta(days=1), datetime.min.time(), tz).astimezone(timezone.utc)
    mail, source = mb.collect_mail(since, days_back_for_bills=0)
    if not source:
        return {"status": "unavailable", "date": day.isoformat(), "speech": [], "error": "no mail source (ABL_MAIL_DIR or ABL_IMAP_*)"}
    txns, skipped_untrusted = [], 0
    for e, c in mail:
        parsed = parse_transactions(e, c, tz)
        if not parsed and "money" in c.categories:
            skipped_untrusted += 1
        txns += parsed
    s = summarize(dedupe(txns), day, load_history(day))
    deliver_after = None
    if st is not None and day == now.date() and st.quiet_now(now):
        end = st.quiet_end_after(now)
        deliver_after = end.isoformat() if end else None
    return {"status": "ok", "version": 1, "generated_at": now.isoformat(), "source": source,
            "skipped_money_mail": skipped_untrusted, "deliver_after": deliver_after, **s, "speech": speech_for(s)}


def write_outputs(summary: dict[str, Any]) -> Path:
    out = mb.DATA / "finance"
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    body = json.dumps(summary, indent=2, default=str)
    for name in ("latest.json", f"{summary['date']}.json"):
        tmp = out / f".{name}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, out / name)
    if summary.get("status") == "ok":
        row = {"date": summary["date"], "spent": summary["totals"]["spent"],
               "merchants": sorted({t["merchant"] for t in summary["transactions"] if t["direction"] == "debit"})}
        hp = _history_path()
        fd = os.open(hp, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    return out / "latest.json"


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ABL daily spending summary (abl1)")
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--speech", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = build_summary(a.date)
    if not a.no_write:
        write_outputs(s)
    print("\n".join(s.get("speech", [])) if a.speech else json.dumps(s, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
