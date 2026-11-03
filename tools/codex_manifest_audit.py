#!/usr/bin/env python3
"""codex_manifest_audit.py — reconcile the Codex work manifest against what actually exists.

Reads ~/abl-fused/odysseus/data/abl/codex_work_manifest.json (127 tasks as of
2026-11-02), and for every task:

  1. normalises its claimed status (done / pending / blocked / dropped / unknown),
  2. extracts every artifact it names — explicit fields (files, paths, artifacts, outputs,
     deliverables, ...) plus path-shaped tokens and ``:port`` mentions in its free text,
  3. checks those artifacts on disk (several candidate roots) and, with --probe-ports,
     whether the mentioned localhost ports answer,
  4. assigns a VERDICT:

       VERIFIED_DONE       claimed done, every named artifact exists
       DONE_MISSING        claimed done, but artifacts are missing   ← top of the TODO list
       DONE_UNVERIFIABLE   claimed done, names no artifact to check
       MAYBE_DONE          claimed pending, but its artifacts already exist (close it?)
       PARTIAL             claimed pending, some artifacts exist
       PENDING             claimed pending, nothing exists yet
       BLOCKED / DROPPED   as claimed

  5. scores the still-open work (security/egress/auth/data-loss/crash keywords, explicit
     priority, DONE_MISSING bonus, staleness) and writes a ranked TODO.

Outputs (default dir ~/abl/reports/): codex_audit_<date>.json and codex_todo_<date>.md.
With --push URL, POSTs the JSON summary to a LOCAL coordination endpoint (refuses
non-local URLs; the manifest describes Asif's systems and never leaves the box).

The manifest schema was not visible from the cloud session, so the loader accepts a
top-level list, or a dict holding the list under tasks/items/work/entries/manifest, or a
dict of id -> task. Unknown fields are carried through untouched.

Stdlib only.  Usage:
  python3 codex_manifest_audit.py                         # defaults
  python3 codex_manifest_audit.py --manifest PATH --roots ~/abl ~/abl-fused --probe-ports
  python3 codex_manifest_audit.py --push http://127.0.0.1:7800/api/abl/build/findings
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_MANIFEST = "~/abl-fused/odysseus/data/abl/codex_work_manifest.json"
DEFAULT_ROOTS = ("~/abl", "~/abl/odysseus", "~/abl-fused", "~/abl-fused/odysseus",
                 "~/abl/tools/dsh/app/local-plugins", "~")

LIST_KEYS = ("tasks", "items", "work", "entries", "manifest", "todo", "records")
ID_KEYS = ("id", "task_id", "key", "slug", "uid")
TITLE_KEYS = ("title", "name", "task", "summary", "subject", "description", "goal")
STATUS_KEYS = ("status", "state", "phase", "progress", "result", "outcome")
ARTIFACT_KEYS = ("files", "paths", "artifacts", "outputs", "deliverables", "file", "path",
                 "artifact", "touched_files", "changed_files", "created_files", "targets", "evidence")
TEXT_KEYS = ("description", "notes", "details", "body", "plan", "acceptance", "summary", "next_steps",
             "prompt", "context", "title", "name", "task")
PRIORITY_KEYS = ("priority", "prio", "severity", "importance", "p")
DATE_KEYS = ("updated", "updated_at", "last_updated", "modified", "created", "created_at", "date", "ts")

STATUS_MAP = {
    "done": ("done", "complete", "completed", "finished", "shipped", "merged", "closed", "resolved",
             "verified", "landed", "deployed", "fixed", "sealed", "ok", "success", "succeeded"),
    "blocked": ("blocked", "waiting", "on hold", "on_hold", "needs approval", "needs_approval",
                "awaiting", "parked", "pending approval", "pending_approval", "locked"),
    "dropped": ("dropped", "cancelled", "canceled", "wontfix", "won't fix", "abandoned", "obsolete",
                "superseded", "skip", "skipped", "rejected"),
    "pending": ("pending", "todo", "to do", "open", "in progress", "in_progress", "wip", "started",
                "planned", "queued", "backlog", "draft", "partial", "partially", "partly", "unfinished",
                "incomplete", "new", "not done", "half done", "mostly done", "not started", "needs work"),
}

# Path-ish tokens in free text: things with a slash and a file extension, or ~/ paths.
PATH_RE = re.compile(r"(?:(?<=\s)|(?<=^)|(?<=[`'\"(]))((?:~|\.{0,2})?/?[\w.@+-]+(?:/[\w.@+-]+)+\.[A-Za-z0-9]{1,6}|~/[\w./@+-]+)", re.M)
BARE_FILE_RE = re.compile(r"(?<![\w/.])([\w-]+\.(?:py|js|mjs|cjs|ts|tsx|json|md|sh|service|toml|yaml|yml|html|css))\b")
PORT_RE = re.compile(r"(?:(?<=:)|(?<=port )|(?<=port: ))(\d{4,5})\b")

PRIORITY_WORDS = {
    40: ("security", "egress", "leak", "credential", "secret", "auth", "exposed", "0.0.0.0", "cve", "privacy"),
    30: ("data loss", "corrupt", "crash", "crash loop", "broken", "regression", "fails", "failing", "dead", "down"),
    20: ("voice", "speaker", "ears", "autoresponder", "router", "unification", "dsh", "console", "phone"),
    10: ("bug", "fix", "wire", "rollback", "backup", "test"),
}
WRITE_WORDS = ("write route", "write path", "migration", "schema", "delete", "unlock")


@dataclass
class TaskAudit:
    index: int
    id: str
    title: str
    claimed: str
    raw_status: str
    verdict: str = ""
    artifacts_found: list[str] = field(default_factory=list)
    artifacts_missing: list[str] = field(default_factory=list)
    ports_up: list[int] = field(default_factory=list)
    ports_down: list[int] = field(default_factory=list)
    priority_raw: str = ""
    score: int = 0
    score_why: list[str] = field(default_factory=list)
    updated: str = ""


# ── loading ─────────────────────────────────────────────────────────────────

def load_tasks(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text("utf-8"))
    if isinstance(data, list):
        return [t if isinstance(t, dict) else {"title": str(t)} for t in data]
    if isinstance(data, dict):
        for k in LIST_KEYS:
            v = data.get(k)
            if isinstance(v, list):
                return [t if isinstance(t, dict) else {"title": str(t)} for t in v]
            if isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()):
                return [{"id": kk, **vv} for kk, vv in v.items()]
        if data and all(isinstance(v, dict) for v in data.values()):
            return [{"id": k, **v} for k, v in data.items()]
    raise ValueError(f"unrecognised manifest shape in {path} (top-level {type(data).__name__})")


def _first(task: dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in task and task[k] not in (None, "", [], {}):
            return task[k]
    return None


def normalise_status(raw: Any, task: Optional[dict[str, Any]] = None) -> str:
    if isinstance(raw, bool):
        return "done" if raw else "pending"
    if isinstance(raw, (int, float)) and 0 <= raw <= 100:
        return "done" if raw >= 100 or raw == 1 else "pending"
    s = str(raw or "").strip().lower().replace("-", " ")
    if task is not None and not s:
        if task.get("done") is True or task.get("completed") is True:
            return "done"
        if task.get("done") is False or task.get("completed") is False:
            return "pending"
    if not s:
        return "unknown"
    for norm, words in (("dropped", STATUS_MAP["dropped"]), ("blocked", STATUS_MAP["blocked"]),
                        ("done", STATUS_MAP["done"]), ("pending", STATUS_MAP["pending"])):
        if s in words:
            return norm
    for norm, words in (("dropped", STATUS_MAP["dropped"]), ("blocked", STATUS_MAP["blocked"]),
                        ("pending", STATUS_MAP["pending"]), ("done", STATUS_MAP["done"])):
        if any(re.search(rf"\b{re.escape(w)}\b", s) for w in words):
            return norm
    return "unknown"


def _flatten_strings(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        out = []
        for k in ("path", "file", "name", "artifact"):
            if isinstance(v.get(k), str):
                out.append(v[k])
        return out
    if isinstance(v, (list, tuple, set)):
        out = []
        for x in v:
            out += _flatten_strings(x)
        return out
    return []


def extract_artifacts(task: dict[str, Any]) -> tuple[list[str], list[int]]:
    explicit: list[str] = []
    for k in ARTIFACT_KEYS:
        explicit += _flatten_strings(task.get(k))
    text = "\n".join(s for k in TEXT_KEYS for s in _flatten_strings(task.get(k)))
    found = list(explicit)
    for m in PATH_RE.finditer(text):
        found.append(m.group(1))
    for m in BARE_FILE_RE.finditer(text):
        found.append(m.group(1))
    cleaned, seen = [], set()
    for p in found:
        p = p.strip().strip("`'\"()[],;").rstrip(".:")
        if not p or p.startswith(("http:", "https:")) or "://" in p or len(p) > 240:
            continue
        if p not in seen:
            seen.add(p)
            cleaned.append(p)
    # Drop bare names already covered by a longer path ending in them.
    cleaned = [p for p in cleaned if "/" in p or not any(q != p and q.endswith("/" + p) for q in cleaned)]
    ports = sorted({int(m.group(1)) for m in PORT_RE.finditer(text) if 1024 <= int(m.group(1)) <= 65535})
    return cleaned, ports


# ── existence checks ────────────────────────────────────────────────────────

class Resolver:
    def __init__(self, roots: Iterable[str], max_index_files: int = 400_000):
        self.roots = [Path(os.path.expanduser(r)) for r in roots]
        self.roots = [r for r in self.roots if r.exists()]
        self._name_index: Optional[dict[str, list[str]]] = None
        self._max = max_index_files

    def _index(self) -> dict[str, list[str]]:
        if self._name_index is not None:
            return self._name_index
        idx: dict[str, list[str]] = {}
        n = 0
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".npm", ".local", "backups"}
        for root in self.roots:
            if root == Path.home():
                continue                     # never walk all of ~
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
                for fn in filenames:
                    idx.setdefault(fn, []).append(os.path.join(dirpath, fn))
                    n += 1
                if n > self._max:
                    break
        self._name_index = idx
        return idx

    def exists(self, p: str) -> Optional[str]:
        raw = os.path.expanduser(p)
        cand = Path(raw)
        if cand.is_absolute():
            return str(cand) if cand.exists() else self._by_tail(p)
        for root in self.roots:
            c = root / raw.lstrip("./")
            if c.exists():
                return str(c)
        return self._by_tail(p)

    def _by_tail(self, p: str) -> Optional[str]:
        name = os.path.basename(p.rstrip("/"))
        if not name or "." not in name:
            return None
        hits = self._index().get(name, [])
        tail = p.replace("~/", "").lstrip("./").lstrip("/")
        for h in hits:                       # prefer a hit whose path ends with the full tail
            if h.endswith(tail):
                return h
        if "/" not in p and len(hits) == 1:  # bare filename: accept a unique match
            return hits[0]
        return None


def port_up(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── scoring ─────────────────────────────────────────────────────────────────

def _priority_points(raw: Any) -> tuple[int, str]:
    if raw is None:
        return 0, ""
    s = str(raw).strip().lower()
    m = re.match(r"p?\s*([0-4])\b", s)
    if m:
        return {0: 50, 1: 35, 2: 20, 3: 8, 4: 0}[int(m.group(1))], f"priority {s}"
    for word, pts in (("critical", 50), ("urgent", 45), ("high", 35), ("medium", 20), ("med", 20), ("low", 5)):
        if word in s:
            return pts, f"priority {s}"
    return 0, ""


def score(a: TaskAudit, task: dict[str, Any], today: dt.date) -> None:
    text = json.dumps(task, default=str).lower()
    pts, why = 0, []
    p, pw = _priority_points(_first(task, PRIORITY_KEYS))
    if p:
        pts += p
        why.append(pw)
    for weight, words in sorted(PRIORITY_WORDS.items(), reverse=True):
        hit = next((w for w in words if w in text), None)
        if hit:
            pts += weight
            why.append(f"+{weight} '{hit}'")
            break                                  # strongest bucket only
    if a.verdict == "DONE_MISSING":
        pts += 30
        why.append("+30 claimed done but artifacts missing")
    if a.verdict == "PARTIAL":
        pts += 10
        why.append("+10 partially built")
    if a.ports_down:
        pts += 10
        why.append(f"+10 port(s) down {a.ports_down}")
    if any(w in text for w in WRITE_WORDS):
        pts -= 10
        why.append("-10 write/schema change (needs approval gate)")
    if a.updated:
        try:
            age = (today - dt.date.fromisoformat(a.updated[:10])).days
            if age > 45:
                pts -= 5
                why.append(f"-5 stale ({age}d)")
        except ValueError:
            pass
    a.score, a.score_why = pts, why


# ── audit ───────────────────────────────────────────────────────────────────

def audit(tasks: list[dict[str, Any]], resolver: Resolver, probe_ports: bool = False,
          today: Optional[dt.date] = None) -> list[TaskAudit]:
    today = today or dt.date.today()
    out = []
    for i, t in enumerate(tasks):
        raw_status = _first(t, STATUS_KEYS)
        a = TaskAudit(index=i, id=str(_first(t, ID_KEYS) or f"#{i + 1}"),
                      title=str(_first(t, TITLE_KEYS) or "(untitled)")[:160].replace("\n", " "),
                      claimed=normalise_status(raw_status, t), raw_status=str(raw_status or ""),
                      priority_raw=str(_first(t, PRIORITY_KEYS) or ""),
                      updated=str(_first(t, DATE_KEYS) or ""))
        arts, ports = extract_artifacts(t)
        for p in arts:
            hit = resolver.exists(p)
            (a.artifacts_found if hit else a.artifacts_missing).append(hit if hit else p)
        if probe_ports:
            for port in ports:
                (a.ports_up if port_up(port) else a.ports_down).append(port)
        n_found, n_missing = len(a.artifacts_found), len(a.artifacts_missing)
        if a.claimed == "done":
            a.verdict = ("DONE_UNVERIFIABLE" if not (n_found or n_missing)
                         else "VERIFIED_DONE" if not n_missing else "DONE_MISSING")
        elif a.claimed == "blocked":
            a.verdict = "BLOCKED"
        elif a.claimed == "dropped":
            a.verdict = "DROPPED"
        else:
            a.verdict = ("MAYBE_DONE" if n_found and not n_missing
                         else "PARTIAL" if n_found else "PENDING")
        score(a, t, today)
        out.append(a)
    return out


OPEN_VERDICTS = ("DONE_MISSING", "PARTIAL", "PENDING", "BLOCKED", "MAYBE_DONE")


def render_markdown(audits: list[TaskAudit], manifest: Path, roots: list[Path], top: int = 40) -> str:
    c = Counter(a.verdict for a in audits)
    claimed = Counter(a.claimed for a in audits)
    lines = [
        f"# Codex Work Manifest Audit — {dt.date.today().isoformat()}",
        "",
        f"Manifest: `{manifest}` · {len(audits)} tasks · roots checked: "
        + ", ".join(f"`{r}`" for r in roots),
        "",
        "## Summary",
        "",
        "| Verdict | Count | Meaning |",
        "|---|---:|---|",
    ]
    meaning = {
        "VERIFIED_DONE": "claimed done, artifacts present",
        "DONE_MISSING": "claimed done, artifacts **missing**: reopen",
        "DONE_UNVERIFIABLE": "claimed done, nothing checkable",
        "MAYBE_DONE": "claimed open, artifacts exist: verify and close",
        "PARTIAL": "claimed open, some artifacts exist",
        "PENDING": "open, nothing built",
        "BLOCKED": "waiting on a decision/approval",
        "DROPPED": "abandoned/superseded",
    }
    for v in ("VERIFIED_DONE", "DONE_MISSING", "DONE_UNVERIFIABLE", "MAYBE_DONE", "PARTIAL", "PENDING",
              "BLOCKED", "DROPPED"):
        lines.append(f"| {v} | {c.get(v, 0)} | {meaning[v]} |")
    lines += ["", "Claimed status as written in the manifest: "
              + ", ".join(f"{k}={v}" for k, v in sorted(claimed.items())), ""]

    open_items = sorted((a for a in audits if a.verdict in OPEN_VERDICTS), key=lambda a: (-a.score, a.index))
    lines += [f"## Prioritised TODO (top {min(top, len(open_items))} of {len(open_items)} open)", "",
              "| # | Score | Verdict | Task | Why | Missing |", "|---:|---:|---|---|---|---|"]
    for n, a in enumerate(open_items[:top], 1):
        miss = ", ".join(f"`{m}`" for m in a.artifacts_missing[:3]) + (" …" if len(a.artifacts_missing) > 3 else "")
        lines.append(f"| {n} | {a.score} | {a.verdict} | **{a.id}** {a.title.replace('|', '/')} | "
                     f"{'; '.join(a.score_why) or '-'} | {miss or '-'} |")
    maybe = [a for a in audits if a.verdict == "MAYBE_DONE"]
    if maybe:
        lines += ["", "## Probably finished: verify, then mark done", ""]
        lines += [f"- **{a.id}** {a.title}: found {', '.join(f'`{x}`' for x in a.artifacts_found[:3])}"
                  for a in maybe]
    miss = [a for a in audits if a.verdict == "DONE_MISSING"]
    if miss:
        lines += ["", "## Claimed done but artifacts are missing", ""]
        lines += [f"- **{a.id}** {a.title}: missing {', '.join(f'`{x}`' for x in a.artifacts_missing[:5])}"
                  for a in miss]
    lines += ["", "_Artifact checks are heuristic: path tokens are extracted from free text, and a bare "
              "filename counts as found only when it is unique under the roots. Read the task before "
              "closing it._", ""]
    return "\n".join(lines)


def is_local_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")


def push(url: str, payload: dict[str, Any], timeout: float = 8.0) -> tuple[bool, str]:
    if not is_local_url(url):
        return False, f"refusing to push to non-local URL {url}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} (endpoint exists? /build is a page in the Aug snapshot)"
    except OSError as e:
        return False, f"unreachable: {e}"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--roots", nargs="*", default=list(DEFAULT_ROOTS))
    ap.add_argument("--out-dir", default="~/abl/reports")
    ap.add_argument("--probe-ports", action="store_true", help="TCP-probe localhost ports named in tasks")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--push", metavar="URL", help="POST summary JSON to a LOCAL coordination endpoint")
    a = ap.parse_args(argv)

    manifest = Path(os.path.expanduser(a.manifest))
    if not manifest.is_file():
        print(f"manifest not found: {manifest}", file=sys.stderr)
        return 2
    tasks = load_tasks(manifest)
    resolver = Resolver(a.roots)
    audits = audit(tasks, resolver, probe_ports=a.probe_ports)

    out_dir = Path(os.path.expanduser(a.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    counts = Counter(x.verdict for x in audits)
    summary = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "manifest": str(manifest), "tasks": len(audits), "verdicts": dict(counts),
        "todo": [asdict(x) for x in sorted((x for x in audits if x.verdict in OPEN_VERDICTS),
                                           key=lambda x: (-x.score, x.index))],
        "all": [asdict(x) for x in audits],
    }
    jpath, mpath = out_dir / f"codex_audit_{stamp}.json", out_dir / f"codex_todo_{stamp}.md"
    jpath.write_text(json.dumps(summary, indent=1), "utf-8")
    mpath.write_text(render_markdown(audits, manifest, resolver.roots, a.top), "utf-8")
    print(f"{len(audits)} tasks · " + " · ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"wrote {jpath}\nwrote {mpath}")
    if a.push:
        ok, msg = push(a.push, {k: summary[k] for k in ("generated", "manifest", "tasks", "verdicts", "todo")})
        print(f"push → {a.push}: {'OK' if ok else 'FAILED'} ({msg})")
        if not ok:
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
