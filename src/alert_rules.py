"""src/alert_rules.py — email classification engine (fraud/money/bills/security/food/parcels/people).

Every rule is a small, named, testable unit that returns a ``RuleMatch``:
``(rule_id, category, tier, evidence)``. ``classify_email`` runs them all and returns a
``Classification`` with the final tier (max over matches, safety floors applied), the
primary category, every match, and **extracted facts** (amounts, due dates, tracking
numbers, merchant) that the morning briefing and the finance summary reuse.

Email-specific signals that plain-text triage can't see:
  * display-name brand spoofing: From "PayPal Security" <alerts@paypa1-support.co>
  * lookalike / punycode domains (paypa1, amaz0n, xn--)
  * Reply-To on a different registrable domain than From, combined with urgency or a money ask
  * SPF / DKIM / DMARC failure in Authentication-Results
  * a link whose visible text names one domain while the href goes to another
  * risky attachments (.exe .scr .js .iso .img .html .htm .zip in "invoice" mail)
  * bulk mail (List-Unsubscribe) never counts as "people"

Tiers per category come from the Settings panel (src/abl_settings.py) when available, so
rules that carry ``tier=None`` follow Asif's overrides. Rules with an explicit tier
(fraud, security) are fixed. Floors (fraud/security RED, money >= YELLOW) always apply.

Privacy: evidence never contains a one-time code. Digits next to "code"/"OTP" are
masked, and bodies are never logged.

Adapters:
  classify(text, sender=None, channel=None) -> {"categories": [...], "tier": "RED", ...}
      the shape src/autoresponder.py's alert_rules adapter auto-discovers
  classify_email(msg) where msg is an Email, a dict, raw RFC 822 bytes/str, or email.message.Message
  RULES: list of every Rule (introspection / docs / tests)

CLI:  python -m src.alert_rules path/to/message.eml [...]   → JSON per message
"""
from __future__ import annotations

import email
import email.policy
import html
import ipaddress
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from email.message import Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

TIERS = ("GREEN", "YELLOW", "RED")
CATEGORIES = ("fraud", "security", "money", "bills", "people", "food", "parcels")
DEFAULT_CATEGORY_TIERS = {"fraud": "RED", "security": "RED", "money": "RED", "bills": "YELLOW",
                          "people": "YELLOW", "food": "GREEN", "parcels": "GREEN"}
TIER_FLOORS = {"fraud": "RED", "security": "RED", "money": "YELLOW"}


def _rank(t: Optional[str]) -> int:
    return TIERS.index(t) if t in TIERS else -1


def max_tier(*ts: Optional[str]) -> Optional[str]:
    present = [t for t in ts if t in TIERS]
    return max(present, key=_rank) if present else None


def category_tiers() -> dict[str, str]:
    """Settings panel tiers if the store is importable, else defaults; floors always applied."""
    tiers = dict(DEFAULT_CATEGORY_TIERS)
    try:
        try:
            from src import abl_settings  # type: ignore
        except ImportError:
            import abl_settings  # type: ignore
        tiers.update(abl_settings.category_tiers())
    except Exception:
        pass
    for c, floor in TIER_FLOORS.items():
        tiers[c] = max_tier(tiers.get(c), floor) or floor
    return tiers


# ─────────────────────────────────────────────────────────────────────────────
# Email model + parsing
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Email:
    subject: str = ""
    body: str = ""                       # plain text (HTML is converted)
    html: str = ""                       # raw HTML part if any (for link checks)
    from_addr: str = ""
    from_name: str = ""
    reply_to: str = ""
    to: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)   # lower-cased names
    attachments: list[str] = field(default_factory=list)
    date: Optional[datetime] = None
    message_id: str = ""

    @property
    def from_domain(self) -> str:
        return _domain(self.from_addr)

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}"

    @property
    def is_bulk(self) -> bool:
        h = self.headers
        return bool(h.get("list-unsubscribe") or h.get("list-id")
                    or h.get("precedence", "").lower() in ("bulk", "list", "junk"))


def _domain(addr: str) -> str:
    addr = (addr or "").strip().lower().rstrip(">")
    return addr.rsplit("@", 1)[1] if "@" in addr else ""


_MULTI_TLD = {"co.uk", "com.au", "co.jp", "com.br", "co.in", "co.nz", "org.uk", "ac.uk", "gov.uk"}


def registrable(domain: str) -> str:
    """paypal.com from mail.service.paypal.com; amazon.co.uk from x.amazon.co.uk (no PSL dependency)."""
    parts = [p for p in (domain or "").lower().strip(".").split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_TLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", s))).strip()


def parse_email(msg: Union["Email", dict, bytes, str, Message]) -> Email:
    if isinstance(msg, Email):
        return msg
    if isinstance(msg, dict):
        e = Email(**{k: v for k, v in msg.items() if k in Email.__dataclass_fields__})
        if e.from_addr and not e.from_name and "<" in e.from_addr:
            e.from_name, e.from_addr = parseaddr(e.from_addr)
        e.headers = {k.lower(): str(v) for k, v in (e.headers or {}).items()}
        if e.html and not e.body:
            e.body = html_to_text(e.html)
        return e
    if isinstance(msg, (bytes, str)):
        msg = email.message_from_bytes(msg if isinstance(msg, bytes) else msg.encode("utf-8", "replace"),
                                       policy=email.policy.default)
    m: Message = msg
    name, addr = parseaddr(str(m.get("From", "")))
    plain, htm, atts = [], [], []
    for part in (m.walk() if m.is_multipart() else [m]):
        if part.is_multipart():
            continue
        fn = part.get_filename()
        disp = (part.get("Content-Disposition") or "").lower()
        if fn or "attachment" in disp:
            atts.append(fn or "unnamed")
            continue
        ctype = part.get_content_type()
        try:
            payload = part.get_content() if hasattr(part, "get_content") else part.get_payload(decode=True)
        except Exception:
            payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            payload = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/plain":
            plain.append(payload or "")
        elif ctype == "text/html":
            htm.append(payload or "")
    when = None
    try:
        when = parsedate_to_datetime(str(m.get("Date"))) if m.get("Date") else None
    except (TypeError, ValueError):
        pass
    html_raw = "\n".join(htm)
    return Email(
        subject=str(m.get("Subject", "")), body="\n".join(plain) or html_to_text(html_raw), html=html_raw,
        from_addr=addr, from_name=name, reply_to=parseaddr(str(m.get("Reply-To", "")))[1],
        to=[a for _, a in getaddresses([str(m.get("To", ""))]) if a],
        headers={k.lower(): str(v) for k, v in m.items()}, attachments=atts, date=when,
        message_id=str(m.get("Message-ID", "")),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Extraction helpers (shared with morning briefing / finance)
# ─────────────────────────────────────────────────────────────────────────────

AMOUNT_RE = re.compile(r"(?<![\w$])(?:USD\s?)?\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)(?!\d)")
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DUE_RE = re.compile(
    rf"(?i)\b(?:due|payable|payment\s+(?:is\s+)?due|due\s+date|by)\s*(?:on|by|:)?\s*"
    rf"((?:{_MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:,?\s+\d{{4}})?|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|\d{{4}}-\d{{2}}-\d{{2}})")
TRACKING_RES = {
    "ups": re.compile(r"\b1Z[0-9A-Z]{16}\b"),
    "usps": re.compile(r"\b(?:94|93|92|95)\d{20}\b"),
    "fedex": re.compile(r"(?i)fedex[^\n]{0,60}?\b(\d{12}|\d{15})\b"),
    "amazon": re.compile(r"\bTBA\d{12}\b"),
}
CODE_CONTEXT_RE = re.compile(r"(?i)(code|otp|passcode|pin|verification)[^\n]{0,30}?\b\d{4,8}\b|\b\d{4,8}\b(?=[^\n]{0,30}(?i:is your|code))")


def mask_codes(s: str) -> str:
    """Replace digits that look like a one-time code (near 'code'/'OTP'/'PIN') with •."""
    return CODE_CONTEXT_RE.sub(lambda m: re.sub(r"\d", "•", m.group(0)), s)


def parse_amounts(text: str) -> list[float]:
    out = []
    for m in AMOUNT_RE.finditer(text or ""):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def parse_due_date(text: str, ref: Optional[date] = None) -> Optional[date]:
    m = DUE_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).strip().rstrip(".").replace(",", "")
    ref = ref or date.today()
    raw = raw.replace("Sept", "Sep")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    for fmt in ("%m/%d", "%B %d", "%b %d"):            # year-less: parse WITH the ref year (Py3.15-safe, leap-day-safe)
        try:
            d = datetime.strptime(f"{raw} {ref.year}", f"{fmt} %Y").date()
        except ValueError:
            continue
        if (ref - d).days > 31:                         # "Jan 5" seen in December means next year
            try:
                d = d.replace(year=ref.year + 1)
            except ValueError:                          # Feb 29 → no leap next year
                d = d.replace(year=ref.year + 1, day=28)
        return d
    return None


def tracking_numbers(text: str) -> list[dict[str, str]]:
    found, seen = [], set()
    for carrier, rx in TRACKING_RES.items():
        for m in rx.finditer(text or ""):
            num = m.group(1) if m.groups() else m.group(0)
            num = num.strip()
            if num and num not in seen and re.fullmatch(r"[0-9A-Z]+", num):
                seen.add(num)
                found.append({"carrier": carrier, "number": num})
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Rules
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RuleMatch:
    rule_id: str
    category: str
    tier: str
    evidence: str


@dataclass
class Rule:
    id: str
    category: str
    description: str
    test: Callable[[Email], Optional[str]]          # returns evidence or None
    tier: Optional[str] = None                      # None → category tier from Settings
    weight: int = 1

    def run(self, e: Email, tiers: dict[str, str]) -> Optional[RuleMatch]:
        try:
            ev = self.test(e)
        except Exception as exc:                    # a broken rule must not break triage
            ev = None
            _rule_errors.append(f"{self.id}: {exc!r}")
        if not ev:
            return None
        return RuleMatch(self.id, self.category, self.tier or tiers.get(self.category, "YELLOW"), mask_codes(str(ev))[:160])


_rule_errors: list[str] = []


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I | re.S)


def _first(rx: re.Pattern[str], s: str) -> Optional[str]:
    m = rx.search(s or "")
    return m.group(0) if m else None


BRANDS: dict[str, tuple[str, ...]] = {
    "paypal": ("paypal.com",), "amazon": ("amazon.com", "amazon.co.uk", "amazonses.com"),
    "apple": ("apple.com", "icloud.com"), "chase": ("chase.com", "jpmorgan.com"),
    "bank of america": ("bankofamerica.com", "bofa.com"), "wells fargo": ("wellsfargo.com",),
    "netflix": ("netflix.com",), "microsoft": ("microsoft.com", "live.com", "outlook.com"),
    "google": ("google.com", "gmail.com"), "coinbase": ("coinbase.com",), "venmo": ("venmo.com",),
    "irs": ("irs.gov",), "usps": ("usps.com",), "ups": ("ups.com",), "fedex": ("fedex.com",),
    "dhl": ("dhl.com",), "capital one": ("capitalone.com",), "citi": ("citi.com", "citibank.com"),
    "discover": ("discover.com",), "american express": ("americanexpress.com", "aexp.com"),
}
_LOOKALIKE = {"0": "o", "1": "l", "3": "e", "5": "s", "rn": "m", "vv": "w"}


def _brand_in(s: str) -> Optional[str]:
    low = (s or "").lower()
    for b in BRANDS:
        if re.search(rf"\b{re.escape(b)}\b", low):
            return b
    return None


def _official(brand: str, dom: str) -> bool:
    reg = registrable(dom)
    return any(reg == registrable(d) for d in BRANDS[brand])


def r_display_name_spoof(e: Email) -> Optional[str]:
    b = _brand_in(e.from_name)
    if b and e.from_domain and not _official(b, e.from_domain):
        return f"From name says {b!r} but domain is {e.from_domain}"
    return None


def r_lookalike_domain(e: Email) -> Optional[str]:
    dom = e.from_domain
    if not dom:
        return None
    if "xn--" in dom:
        return f"punycode sender domain {dom}"
    label = registrable(dom).split(".")[0]
    norm = label
    for k, v in _LOOKALIKE.items():
        norm = norm.replace(k, v)
    for b, doms in BRANDS.items():
        for d in doms:
            official = registrable(d).split(".")[0]
            if norm != label and norm == official:
                return f"lookalike of {d}: {dom}"
            # brand glued to a hyphen/digit (paypal-secure, secure-paypal, amazon1), not amazonaws
            if (len(official) >= 4 and not _official(b, dom)
                    and re.search(rf"(^|[-0-9]){re.escape(official)}([-0-9]|$)", label) and label != official):
                return f"brand inside foreign domain: {dom}"
    return None


_URGENT = _rx(r"\b(urgent|immediately|within 24 ?h(ours)?|final notice|act now|suspend(ed)?|locked|unusual activity|verify (your|now))\b")
_MONEY_ASK = _rx(r"\b(wire|gift ?cards?|bitcoin|crypto|zelle|payment|invoice|bank details|routing number)\b")


def r_reply_to_mismatch(e: Email) -> Optional[str]:
    if e.reply_to and e.from_addr and registrable(_domain(e.reply_to)) != registrable(e.from_domain):
        if _URGENT.search(e.text) or _MONEY_ASK.search(e.text):
            return f"Reply-To {_domain(e.reply_to)} differs from From {e.from_domain} + urgency/money ask"
    return None


def r_auth_fail(e: Email) -> Optional[str]:
    ar = e.headers.get("authentication-results", "").lower()
    fails = [k for k in ("spf", "dkim", "dmarc") if re.search(rf"\b{k}=(fail|softfail|permerror)", ar)]
    if fails and (_brand_in(e.from_name) or _brand_in(e.subject) or _URGENT.search(e.text)):
        return f"authentication failed ({', '.join(fails)}) on a brand/urgent message"
    return None


_HREF_RE = re.compile(r"""<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)


def r_link_text_mismatch(e: Email) -> Optional[str]:
    for href, label in _HREF_RE.findall(e.html or ""):
        txt = html_to_text(label)
        m = re.search(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b", txt.lower())
        hd = re.match(r"(?i)https?://([^/:?#]+)", href.strip())
        if m and hd:
            shown, real = registrable(m.group(1)), registrable(hd.group(1))
            if shown != real and "." in shown:
                return f"link text shows {shown} but goes to {real}"
        if hd:
            try:
                ipaddress.ip_address(hd.group(1))
                return f"link to a raw IP address {hd.group(1)}"
            except ValueError:
                pass
    return None


_RISKY_EXT = (".exe", ".scr", ".js", ".jse", ".vbs", ".bat", ".cmd", ".iso", ".img", ".lnk", ".hta", ".msi")


def r_risky_attachment(e: Email) -> Optional[str]:
    for a in e.attachments:
        low = a.lower()
        if low.endswith(_RISKY_EXT) or re.search(r"\.(pdf|docx?|xlsx?)\.(exe|js|scr|html?)$", low):
            return f"risky attachment {a}"
        if low.endswith((".html", ".htm", ".zip")) and re.search(r"(?i)invoice|payment|remittance|statement|voicemail|receipt|document",
                                                                  e.text + " " + low):
            return f"{a} attached to a payment/voicemail mail"
    return None


_SCAM_PHRASES = _rx(r"\b(gift ?cards?|seed phrase|recovery phrase|you('ve| have) won|lottery|sweepstakes|claim your (prize|refund|reward)|"
                    r"wire (the )?(money|funds)|western union|moneygram|send (bitcoin|btc|crypto|usdt)|irs (refund|penalty|warrant)|"
                    r"redelivery fee|account (has been |is |was )?(suspended|locked|compromised)|verify your (account|identity))\b")

_SECURITY = _rx(r"\b((verification|security|login|confirmation|one[- ]time|authentication) (code|pin|passcode)|"
                r"\b(otp|2fa|mfa)\b|two[- ]factor|password (reset|change[d]?)|new (sign[- ]?in|login|device)|"
                r"security alert|(sign[- ]?in|login) attempt|recovery (code|email|phone) (changed|added)|"
                r"your code is|do not share (this|the) code)")

_MONEY = _rx(r"\b(transaction|purchase|deposit(ed)?|withdrawal|transfer(red)?|zelle|venmo|cash ?app|paypal|"
             r"payment (received|sent)|you (sent|received|paid)|direct deposit|refund (issued|processed)|"
             r"debit card|credit card (was )?(used|charged)|charge of|large purchase|low balance|overdraft|balance (alert|is))\b")

_BILLS = _rx(r"\b((bill|invoice|statement)( is)? (ready|due|available)|payment( is)? (due|overdue|failed|declined)|due date|past due|"
             r"autopay|minimum payment|amount due|(rent|utility|electric|gas|water|internet|phone|insurance) (bill|payment)|"
             r"subscription (renew|renewal|expir)|your (bill|statement))")

_FOOD_SENDERS = ("doordash.com", "ubereats.com", "uber.com", "grubhub.com", "postmates.com", "instacart.com", "seamless.com", "caviar.com")
_FOOD = _rx(r"\b(your (food|order|meal) (is|has) (arrived|here|on (its|the) way|ready|been picked up)|dasher|"
            r"(driver|courier) is (here|outside|arriving|nearby)|order (is )?ready for pickup|out for delivery.{0,20}(order|food))\b")

_PARCEL_SENDERS = ("ups.com", "usps.com", "fedex.com", "dhl.com", "amazon.com", "shipment-tracking.amazon.com", "ontrac.com", "lasership.com")
_PARCELS = _rx(r"\b(out for delivery|(has been |was )?delivered|shipped|arriving (today|tomorrow)|tracking (number|#|id)|"
               r"delivery (attempt|exception|update)|your (package|parcel|shipment))\b")

_PERSONAL_DOMAINS = {"gmail.com", "googlemail.com", "icloud.com", "me.com", "outlook.com", "hotmail.com", "live.com",
                     "yahoo.com", "proton.me", "protonmail.com", "aol.com"}


def _contacts() -> set[str]:
    raw = os.environ.get("ABL_AUTORESPONDER_CONTACTS", "")
    out = {c.strip().lower() for c in raw.split(",") if c.strip()}
    f = os.environ.get("ABL_CONTACTS_FILE")
    if f:
        try:
            out |= {ln.strip().lower() for ln in Path(f).expanduser().read_text("utf-8").splitlines() if ln.strip()}
        except OSError:
            pass
    return out


def r_known_contact(e: Email) -> Optional[str]:
    if e.from_addr.lower() in _contacts():
        return "sender is a known contact"
    return None


def r_personal_sender(e: Email) -> Optional[str]:
    if e.is_bulk or not e.from_name or e.from_domain not in _PERSONAL_DOMAINS:
        return None
    if re.search(r"(?i)no-?reply|newsletter|notifications?|support|team", e.from_addr + e.from_name):
        return None
    return f"personal mailbox ({e.from_domain}), not bulk"


def r_large_amount(e: Email) -> Optional[str]:
    thr = float(os.environ.get("ABL_LARGE_AMOUNT", "500"))
    big = [a for a in parse_amounts(e.text) if a >= thr]
    if big and _MONEY.search(e.text):
        return f"amount ${max(big):,.2f} ≥ ${thr:,.0f}"
    return None


def _sender_in(domains: Iterable[str]) -> Callable[[Email], Optional[str]]:
    regs = {registrable(d) for d in domains}
    return lambda e: f"sender {e.from_domain}" if registrable(e.from_domain) in regs else None


def _text(rx: re.Pattern[str]) -> Callable[[Email], Optional[str]]:
    return lambda e: _first(rx, e.text)


def _not_bulk(fn: Callable[[Email], Optional[str]]) -> Callable[[Email], Optional[str]]:
    """Marketing mail (List-Unsubscribe) mentioning 'balance'/'PayPal' is not a transaction."""
    return lambda e: None if e.is_bulk else fn(e)


def _and(*fns: Callable[[Email], Optional[str]]) -> Callable[[Email], Optional[str]]:
    def run(e: Email) -> Optional[str]:
        parts = []
        for f in fns:
            ev = f(e)
            if not ev:
                return None
            parts.append(ev)
        return " + ".join(parts)
    return run


RULES: list[Rule] = [
    # fraud: fixed RED, the strongest signals carry weight 3
    Rule("fraud.display_name_spoof", "fraud", "brand in From name, sender domain not the brand's", r_display_name_spoof, "RED", 3),
    Rule("fraud.lookalike_domain", "fraud", "punycode or lookalike brand domain", r_lookalike_domain, "RED", 3),
    Rule("fraud.reply_to_mismatch", "fraud", "Reply-To domain differs + urgency/money ask", r_reply_to_mismatch, "RED", 2),
    Rule("fraud.auth_fail", "fraud", "SPF/DKIM/DMARC fail on brand/urgent mail", r_auth_fail, "RED", 2),
    Rule("fraud.link_mismatch", "fraud", "link text domain ≠ link target / raw IP link", r_link_text_mismatch, "RED", 2),
    Rule("fraud.risky_attachment", "fraud", "executable or disguised attachment", r_risky_attachment, "RED", 3),
    Rule("fraud.scam_phrase", "fraud", "classic scam phrasing (gift cards, seed phrase, prize, wire)", _text(_SCAM_PHRASES), "RED", 2),
    # security: fixed RED (codes, sign-ins, resets)
    Rule("security.account_event", "security", "codes, sign-ins, password/2FA changes", _text(_SECURITY), "RED", 2),
    # money / bills / food / parcels: tier from Settings (None)
    Rule("money.large_amount", "money", "money movement above ABL_LARGE_AMOUNT (not bulk)", _not_bulk(r_large_amount), None, 2),
    Rule("money.transaction", "money", "bank/card/P2P transaction notice (not bulk)", _not_bulk(_text(_MONEY)), None, 1),
    Rule("bills.due", "bills", "statement/bill/payment due", _text(_BILLS), None, 1),
    Rule("food.sender", "food", "food delivery service sender", _sender_in(_FOOD_SENDERS), None, 1),
    Rule("food.status", "food", "order arriving / ready", _text(_FOOD), None, 1),
    Rule("parcels.carrier", "parcels", "carrier/marketplace shipping sender + shipping language",
         _and(_sender_in(_PARCEL_SENDERS), _text(_PARCELS)), None, 1),
    Rule("parcels.tracking", "parcels", "tracking number present", lambda e: (lambda t: f"{t[0]['carrier']} {t[0]['number']}" if t else None)(tracking_numbers(e.text)), None, 1),
    Rule("people.contact", "people", "sender is a known contact", r_known_contact, None, 2),
    Rule("people.personal", "people", "personal mailbox, not bulk", r_personal_sender, None, 1),
]


@dataclass
class Classification:
    tier: Optional[str]                   # None = no rule matched
    category: Optional[str]               # primary category (highest tier, then weight)
    categories: list[str]
    matches: list[RuleMatch]
    facts: dict[str, Any]
    bulk: bool
    urgent: bool                          # RED + fraud/security, or a large amount

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["matches"] = [asdict(m) for m in self.matches]
        return d


def classify_email(msg: Union[Email, dict, bytes, str, Message], *, rules: Optional[list[Rule]] = None,
                   ref_date: Optional[date] = None) -> Classification:
    e = parse_email(msg)
    tiers = category_tiers()
    matches = [m for m in (r.run(e, tiers) for r in (rules or RULES)) if m]
    # A carrier/brand that is genuinely official cannot also be "people".
    if any(m.category == "fraud" for m in matches):
        matches = [m for m in matches if m.category not in ("people", "food", "parcels")]
    weights = {r.id: r.weight for r in (rules or RULES)}
    tier = None
    for m in matches:
        floor = TIER_FLOORS.get(m.category)
        m.tier = max_tier(m.tier, floor) or m.tier
        tier = max_tier(tier, m.tier)
    cats = sorted({m.category for m in matches}, key=lambda c: CATEGORIES.index(c))
    primary = None
    if matches:
        best = max(matches, key=lambda m: (_rank(m.tier), weights.get(m.rule_id, 1), -CATEGORIES.index(m.category)))
        primary = best.category
    text = e.text
    facts: dict[str, Any] = {}
    amounts = parse_amounts(text)
    if amounts:
        facts["amounts"] = amounts
        facts["max_amount"] = max(amounts)
    due = parse_due_date(text, ref_date)
    if due:
        facts["due_date"] = due.isoformat()
    tr = tracking_numbers(text)
    if tr:
        facts["tracking"] = tr
    facts["sender"] = e.from_domain
    facts["merchant"] = (e.from_name or registrable(e.from_domain).split(".")[0]).strip() or None
    urgent = tier == "RED" and (bool({"fraud", "security"} & set(cats)) or any(m.rule_id == "money.large_amount" for m in matches))
    return Classification(tier=tier, category=primary, categories=cats, matches=matches, facts=facts,
                          bulk=e.is_bulk, urgent=urgent)


def classify(text: str, sender: Any = None, channel: Optional[str] = None) -> dict[str, Any]:
    """Autoresponder adapter: plain text (+ sender) → {"categories", "tier", "category"}."""
    from_addr = sender if isinstance(sender, str) and "@" in sender else ""
    c = classify_email(Email(subject="", body=text or "", from_addr=from_addr))
    return {"categories": c.categories, "tier": c.tier, "category": c.category}


def rule_errors() -> list[str]:
    return list(_rule_errors)


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.split("\n\n")[0])
        print("\nusage: python -m src.alert_rules message.eml [...]")
        return 2
    for p in argv:
        c = classify_email(Path(p).read_bytes())
        print(json.dumps({"file": p, **c.to_dict()}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
