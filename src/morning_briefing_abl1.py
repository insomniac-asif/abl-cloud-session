"""src/morning_briefing_abl1.py — the morning briefing, running on abl1.

A port of the desktop ``~/ops/speaker/morning_briefing.py`` (one of the 8 screen-off systems)
to the box that now hosts ABL. It assembles five independent sections. Each is fail-soft and
reports its own ``status`` (ok | empty | unavailable | disabled | error), so a dead source never
blocks the briefing:

  weather    Open-Meteo (no API key). The home location is rounded to 0.1° (~11 km) and the
             request passes the egress gateway's public-data preflight when the gateway is present.
  email      overnight mail from a Maildir / .eml directory (ABL_MAIL_DIR) or read-only IMAP
             (ABL_IMAP_HOST/USER/PASSWORD_FILE), classified by src/alert_rules.py
  autopilot  overnight autoresponder activity (audit.jsonl), YELLOW drafts held for quiet
             hours (the "morning batch" from pending.json), and an optional autopilot event log
  bills      due dates extracted from the last 30 days of bill mail, plus an optional ledger
             (ABL_BILLS_FILE), for anything due within 7 days
  calendar   today's events from an ICS file or a local ICS URL (ABL_CALENDAR_ICS), if configured

Output (stdout and data/abl/briefing/{latest,YYYY-MM-DD}.json): structured JSON whose
``speech`` list is short, TTS-ready lines in delivery order, for the speaker bridge. RED items
come first. One-time codes never appear (masked by alert_rules). ``deliver_after`` is set when
the briefing is generated inside quiet hours.

Settings panel: autopilot.systems.morning_briefing=false → status "disabled", nothing fetched.

Usage:
  python -m src.morning_briefing_abl1                 # print JSON + write files
  python -m src.morning_briefing_abl1 --speech        # just the lines
  python -m src.morning_briefing_abl1 --since-hours 14
"""
from __future__ import annotations

import argparse
import imaplib
import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import alert_rules  # noqa: E402

log = logging.getLogger("abl.briefing")

ODYSSEUS = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("ABL_DATA_DIR") or ODYSSEUS / "data" / "abl")

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
       80: "showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "snow showers",
       95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _settings():
    try:
        from src import abl_settings
        return abl_settings
    except ImportError:
        return None


def _tz():
    st = _settings()
    name = (st.get("quiet.tz") if st else None) or os.environ.get("ABL_TZ", "America/New_York")
    try:
        return ZoneInfo(name) if ZoneInfo else timezone.utc
    except Exception:
        return timezone.utc


@dataclass
class Section:
    status: str = "ok"
    data: dict[str, Any] = field(default_factory=dict)
    speech: list[str] = field(default_factory=list)
    priority: int = 50                      # lower = spoken earlier
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {"status": self.status, "data": self.data}
        if self.error:
            d["error"] = self.error
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Weather
# ─────────────────────────────────────────────────────────────────────────────

def _http_json(url: str, timeout: float = 8.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "abl-briefing/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def weather_section(fetch: Callable[[str], Any] = _http_json) -> Section:
    lat, lon = os.environ.get("ABL_HOME_LAT"), os.environ.get("ABL_HOME_LON")
    if not lat or not lon:
        return Section("unavailable", error="set ABL_HOME_LAT / ABL_HOME_LON", priority=20)
    try:
        lat_r, lon_r = round(float(lat), 1), round(float(lon), 1)       # ~11 km: city-level, not a street
    except ValueError:
        return Section("error", error="ABL_HOME_LAT/LON not numbers", priority=20)
    units = os.environ.get("ABL_UNITS", "fahrenheit")
    q = urllib.parse.urlencode({
        "latitude": lat_r, "longitude": lon_r, "timezone": "auto", "forecast_days": 1,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "temperature_unit": units, "wind_speed_unit": "mph",
    })
    url = f"https://api.open-meteo.com/v1/forecast?{q}"
    try:
        from core import egress_gateway as gw  # type: ignore
        if not gw.egress_ok_public(url, provider="open-meteo", source="morning_briefing"):
            return Section("disabled", error="egress gateway denied the weather request", priority=20)
    except ImportError:
        pass
    try:
        d = fetch(url)
        cur, day = d.get("current", {}), d.get("daily", {})
        hi, lo = day.get("temperature_2m_max", [None])[0], day.get("temperature_2m_min", [None])[0]
        pop = day.get("precipitation_probability_max", [None])[0]
        cond = WMO.get(day.get("weather_code", [cur.get("weather_code")])[0], "")
        unit = "°F" if units == "fahrenheit" else "°C"
        data = {"now": cur.get("temperature_2m"), "high": hi, "low": lo, "precip_chance": pop, "conditions": cond, "unit": unit}
        line = f"Weather: {cond or 'mixed'}, high of {round(hi)} and low of {round(lo)}." if hi is not None and lo is not None else ""
        if pop is not None and pop >= 40:
            line += f" {int(pop)} percent chance of rain; take an umbrella."
        return Section("ok", data, [line] if line else [], priority=20)
    except Exception as e:
        return Section("unavailable", error=f"{type(e).__name__}: {e}", priority=20)


# ─────────────────────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────────────────────

def _iter_mail_dir(root: Path):
    for sub in ("new", "cur", ""):
        d = root / sub if sub else root
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and (sub or p.suffix.lower() == ".eml"):
                yield p.read_bytes()


def _iter_imap(since: datetime):
    host, user = os.environ.get("ABL_IMAP_HOST"), os.environ.get("ABL_IMAP_USER")
    pw_file = os.environ.get("ABL_IMAP_PASSWORD_FILE")
    if not (host and user and pw_file):
        return
    pw = Path(pw_file).expanduser().read_text("utf-8").strip()
    with imaplib.IMAP4_SSL(host) as M:
        M.login(user, pw)
        M.select(os.environ.get("ABL_IMAP_FOLDER", "INBOX"), readonly=True)       # never marks anything read
        typ, ids = M.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        for num in (ids[0].split() if typ == "OK" and ids and ids[0] else [])[-300:]:
            typ, parts = M.fetch(num, "(BODY.PEEK[])")
            if typ == "OK" and parts and isinstance(parts[0], tuple):
                yield parts[0][1]


def collect_mail(since: datetime, *, days_back_for_bills: int = 30) -> tuple[list[tuple[alert_rules.Email, alert_rules.Classification]], str]:
    """Return (classified messages since `since - days_back_for_bills`, source label)."""
    floor = since - timedelta(days=days_back_for_bills)
    out, source = [], ""
    mdir = os.environ.get("ABL_MAIL_DIR")
    raw_iter = None
    if mdir and Path(mdir).expanduser().is_dir():
        raw_iter, source = _iter_mail_dir(Path(mdir).expanduser()), f"maildir:{mdir}"
    elif os.environ.get("ABL_IMAP_HOST"):
        raw_iter, source = _iter_imap(floor), f"imap:{os.environ.get('ABL_IMAP_HOST')}"
    if raw_iter is None:
        return [], ""
    for raw in raw_iter:
        try:
            e = alert_rules.parse_email(raw)
        except Exception:
            continue
        when = e.date
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when is not None and when < floor:
            continue
        out.append((e, alert_rules.classify_email(e, ref_date=since.date())))
    return out, source


def _is_overnight(e: alert_rules.Email, since: datetime) -> bool:
    if e.date is None:
        return True
    d = e.date if e.date.tzinfo else e.date.replace(tzinfo=timezone.utc)
    return d >= since


def _n(count: int, one: str, many: str) -> str:
    """'1 alert' / '3 alerts' — TTS reads grammar mistakes out loud."""
    return f"{count} {one if count == 1 else many}"


def _sentence(s: str) -> str:
    s = s.strip()
    return s if s.endswith((".", "!", "?")) else s + "."


def _short(s: str, n: int = 70) -> str:
    s = alert_rules.mask_codes(re.sub(r"\s+", " ", s or "").strip())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def email_section(mail, source: str, since: datetime) -> Section:
    if not source:
        return Section("unavailable", error="no mail source (set ABL_MAIL_DIR or ABL_IMAP_*)", priority=10)
    overnight = [(e, c) for e, c in mail if _is_overnight(e, since)]
    if not overnight:
        return Section("empty", {"source": source, "total": 0}, ["No new email overnight."], priority=10)
    by_tier: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    items = []
    for e, c in overnight:
        by_tier[c.tier or "none"] = by_tier.get(c.tier or "none", 0) + 1
        for cat in c.categories:
            by_cat[cat] = by_cat.get(cat, 0) + 1
        if c.tier in ("RED", "YELLOW"):
            items.append({"tier": c.tier, "category": c.category, "urgent": c.urgent,
                          "from": e.from_name or e.from_domain, "subject": _short(e.subject),
                          "facts": {k: v for k, v in c.facts.items() if k in ("max_amount", "due_date", "tracking")}})
    items.sort(key=lambda i: (0 if i["urgent"] else 1, 0 if i["tier"] == "RED" else 1))
    data = {"source": source, "total": len(overnight), "by_tier": by_tier, "by_category": by_cat,
            "bulk": sum(1 for _, c in overnight if c.bulk), "items": items[:12]}
    speech = []
    urgent = [i for i in items if i["urgent"]]
    for i in urgent[:3]:
        what = "possible scam" if i["category"] == "fraud" else "security alert" if i["category"] == "security" else "large transaction"
        speech.append(_sentence(f"Heads up: {what} from {i['from']}: {i['subject']}"))
    n_red = by_tier.get("RED", 0) - len(urgent)
    parts = [_n(len(overnight), "new email", "new emails") + " overnight"]
    if n_red > 0:
        parts.append(_n(n_red, "money or account notice", "money or account notices"))
    if by_tier.get("YELLOW"):
        parts.append(f"{by_tier['YELLOW']} that may need a reply")
    if by_cat.get("parcels"):
        parts.append(_n(by_cat["parcels"], "delivery update", "delivery updates"))
    speech.append(_sentence(", ".join(parts)))
    people = [i for i in items if i["category"] == "people"][:3]
    if people:
        speech.append(_sentence("From people: " + "; ".join(f"{i['from']} about {i['subject'].rstrip('.?!')}" for i in people)))
    return Section("ok", data, speech, priority=5 if urgent else 10)


# ─────────────────────────────────────────────────────────────────────────────
# Autopilot recap
# ─────────────────────────────────────────────────────────────────────────────

def _jsonl_since(path: Path, since_ts: float) -> list[dict]:
    out = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                ts = r.get("ts")
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        ts = None
                if isinstance(ts, (int, float)) and ts >= since_ts:
                    out.append(r)
    except OSError:
        pass
    return out


def autopilot_section(since: datetime) -> Section:
    ar_dir = Path(os.environ.get("ABL_AUTORESPONDER_DATA_DIR") or DATA / "autoresponder")
    audit = _jsonl_since(ar_dir / "audit.jsonl", since.timestamp())
    effects: dict[str, int] = {}
    for r in audit:
        eff = str(r.get("effect", "")).split(":")[0]
        effects[eff] = effects.get(eff, 0) + 1
    held = []
    try:
        pending = json.loads((ar_dir / "pending.json").read_text("utf-8"))
        held = [p for p in pending.values() if p.get("status") == "pending"]
    except (OSError, ValueError):
        pass
    quiet_held = [p for p in held if p.get("held_for_quiet_hours")]
    events_path = os.environ.get("ABL_AUTOPILOT_LOG")
    events = _jsonl_since(Path(events_path), since.timestamp()) if events_path else []
    if not audit and not held and not events:
        return Section("empty", {"autoresponder": {}, "held": 0}, [], priority=30)
    data = {"autoresponder": effects, "messages_triaged": len(audit), "held": len(held),
            "held_for_quiet_hours": len(quiet_held), "events": len(events),
            "event_kinds": sorted({str(e.get("kind") or e.get("type") or "event") for e in events})}
    speech = []
    if quiet_held:
        one = len(quiet_held) == 1
        speech.append(f"{_n(len(quiet_held), 'draft reply is', 'draft replies are')} waiting from overnight; "
                      f"{'it sends' if one else 'they send'} in a minute unless you cancel.")
    if audit:
        alerted = effects.get("alerted", 0)
        speech.append(f"Overnight I triaged {_n(len(audit), 'message', 'messages')}"
                      + (f" and raised {_n(alerted, 'alert', 'alerts')}." if alerted else "."))
    return Section("ok", data, speech, priority=15 if quiet_held else 30)


# ─────────────────────────────────────────────────────────────────────────────
# Bills
# ─────────────────────────────────────────────────────────────────────────────

def bills_section(mail, today: date, horizon_days: int = 7) -> Section:
    found: dict[tuple[str, str], dict] = {}
    for e, c in mail:
        if "bills" not in c.categories or "due_date" not in c.facts:
            continue
        due = date.fromisoformat(c.facts["due_date"])
        if not (today - timedelta(days=1) <= due <= today + timedelta(days=horizon_days)):
            continue
        payee = (c.facts.get("merchant") or e.from_domain or "a bill").strip()
        key = (payee.lower(), due.isoformat())
        found[key] = {"payee": payee, "due": due.isoformat(), "amount": c.facts.get("max_amount"),
                      "autopay": bool(re.search(r"(?i)autopay (is )?(on|enabled|scheduled)|will be (automatically )?(paid|debited)", e.text)),
                      "source": "email"}
    ledger = os.environ.get("ABL_BILLS_FILE")
    if ledger:
        try:
            for b in json.loads(Path(ledger).expanduser().read_text("utf-8")):
                due = date.fromisoformat(b["due"])
                if today - timedelta(days=1) <= due <= today + timedelta(days=horizon_days):
                    found[(b["payee"].lower(), b["due"])] = {**b, "source": "ledger"}
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("bills ledger unreadable: %r", e)
    bills = sorted(found.values(), key=lambda b: b["due"])
    if not bills:
        return Section("empty", {"due_soon": []}, [], priority=25)
    speech = []
    for b in bills[:4]:
        d = date.fromisoformat(b["due"])
        when = "today" if d == today else "tomorrow" if d == today + timedelta(days=1) else \
            "overdue" if d < today else d.strftime("%A")
        amt = f" for ${b['amount']:,.2f}" if b.get("amount") else ""
        auto = ", on autopay" if b.get("autopay") else ""
        speech.append(f"{b['payee']}{amt} is due {when}{auto}.")
    return Section("ok", {"due_soon": bills}, speech, priority=12 if any(b["due"] <= today.isoformat() for b in bills) else 25)


# ─────────────────────────────────────────────────────────────────────────────
# Calendar (minimal ICS reader: DTSTART/SUMMARY/LOCATION, all-day + timed, UTC or local)
# ─────────────────────────────────────────────────────────────────────────────

def _ics_text() -> Optional[str]:
    src = os.environ.get("ABL_CALENDAR_ICS")
    if not src:
        return None
    if src.startswith(("http://", "https://")):
        host = urllib.parse.urlparse(src).hostname or ""
        if host not in ("127.0.0.1", "localhost") and not host.endswith(".ts.net"):
            raise ValueError("calendar URL must be local or tailnet (personal data)")
        with urllib.request.urlopen(src, timeout=8) as r:  # noqa: S310
            return r.read().decode("utf-8", "replace")
    return Path(src).expanduser().read_text("utf-8", "replace")


def parse_ics_today(text: str, today: date, tz) -> list[dict]:
    text = re.sub(r"\r?\n[ \t]", "", text)                      # unfold lines
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        props = {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            props.setdefault(k.split(";")[0].upper(), (k, v.strip()))
        if "DTSTART" not in props:
            continue
        key, val = props["DTSTART"]
        try:
            if "VALUE=DATE" in key.upper() or re.fullmatch(r"\d{8}", val):
                d, start = datetime.strptime(val[:8], "%Y%m%d").date(), None
            else:
                dt = datetime.strptime(val.rstrip("Z"), "%Y%m%dT%H%M%S")
                if val.endswith("Z"):
                    dt = dt.replace(tzinfo=timezone.utc).astimezone(tz)
                elif "TZID=" in key.upper() and ZoneInfo:
                    try:
                        dt = dt.replace(tzinfo=ZoneInfo(key.split("TZID=")[1].split(";")[0])).astimezone(tz)
                    except Exception:
                        dt = dt.replace(tzinfo=tz)
                else:
                    dt = dt.replace(tzinfo=tz)
                d, start = dt.date(), dt
        except ValueError:
            continue
        if d != today:
            continue
        events.append({"summary": props.get("SUMMARY", ("", ""))[1].replace("\\,", ",") or "(no title)",
                       "start": start.strftime("%H:%M") if start else None, "all_day": start is None,
                       "location": props.get("LOCATION", ("", ""))[1].replace("\\,", ",") or None})
    return sorted(events, key=lambda e: (e["start"] is not None, e["start"] or ""))


def calendar_section(today: date, tz) -> Section:
    try:
        text = _ics_text()
    except Exception as e:
        return Section("error", error=f"{type(e).__name__}: {e}", priority=18)
    if text is None:
        return Section("unavailable", error="set ABL_CALENDAR_ICS (file or local URL)", priority=18)
    events = parse_ics_today(text, today, tz)
    if not events:
        return Section("empty", {"events": []}, ["Nothing on the calendar today."], priority=18)

    def spoken(t: Optional[str]) -> str:
        if not t:
            return "all day"
        h, m = map(int, t.split(":"))
        return f"{(h % 12) or 12}{'' if m == 0 else ':%02d' % m} {'a.m.' if h < 12 else 'p.m.'}"
    lines = [f"{len(events)} event{'s' if len(events) != 1 else ''} today."]
    lines += [f"{spoken(e['start'])}: {e['summary']}." for e in events[:5]]
    return Section("ok", {"events": events}, lines, priority=18)


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────

def build_briefing(now: Optional[datetime] = None, since_hours: Optional[float] = None,
                   weather_fetch: Callable[[str], Any] = _http_json) -> dict[str, Any]:
    tz = _tz()
    now = (now or datetime.now(tz)).astimezone(tz)
    st = _settings()
    if st is not None and not (st.get("autopilot.enabled") and st.get("autopilot.systems.morning_briefing")):
        return {"status": "disabled", "generated_at": now.isoformat(), "speech": [],
                "reason": "autopilot.systems.morning_briefing is off in Settings"}
    if since_hours is None:
        # From 18:00 yesterday (local) to now: "overnight".
        since = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    else:
        since = now - timedelta(hours=since_hours)
    mail, source = collect_mail(since.astimezone(timezone.utc))
    sections = {
        "weather": weather_section(weather_fetch),
        "email": email_section(mail, source, since.astimezone(timezone.utc)),
        "autopilot": autopilot_section(since),
        "bills": bills_section(mail, now.date()),
        "calendar": calendar_section(now.date(), tz),
    }
    hour = now.hour
    greeting = "Good morning." if hour < 12 else "Good afternoon." if hour < 17 else "Good evening."
    ordered = sorted(sections.items(), key=lambda kv: kv[1].priority)
    speech = [greeting] + [line for _, s in ordered for line in s.speech if line]
    max_chars = int(os.environ.get("ABL_BRIEFING_MAX_CHARS", "1200"))
    trimmed, total = [], 0
    for line in speech:
        if total + len(line) > max_chars:
            trimmed.append("More in the dashboard.")
            break
        trimmed.append(line)
        total += len(line)
    urgent = any(i.get("urgent") for i in sections["email"].data.get("items", []))
    deliver_after = None
    if st is not None and st.quiet_now(now):
        end = st.quiet_end_after(now)
        deliver_after = end.isoformat() if end else None
    return {
        "status": "ok", "version": 1, "generated_at": now.isoformat(), "date": now.date().isoformat(),
        "window": {"since": since.isoformat(), "until": now.isoformat()},
        "priority": "urgent" if urgent else "normal", "deliver_after": deliver_after,
        "sections": {k: v.to_dict() for k, v in sections.items()},
        "speech": trimmed,
    }


def write_outputs(b: dict[str, Any]) -> Path:
    out = DATA / "briefing"
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(b, indent=2, default=str)
    for name in ("latest.json", f"{b.get('date') or date.today().isoformat()}.json"):
        tmp = out / f".{name}.tmp"
        tmp.write_text(body, "utf-8")
        os.replace(tmp, out / name)
    return out / "latest.json"


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ABL morning briefing (abl1)")
    ap.add_argument("--since-hours", type=float)
    ap.add_argument("--speech", action="store_true", help="print only the spoken lines")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    b = build_briefing(since_hours=a.since_hours)
    if not a.no_write:
        write_outputs(b)
    print("\n".join(b["speech"]) if a.speech else json.dumps(b, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
