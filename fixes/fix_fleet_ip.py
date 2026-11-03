#!/usr/bin/env python3
"""fix_fleet_ip.py — move the desktop's fleet-registry entry to its current tailnet IP.

Bug: the fleet registry (:8900) still has the desktop at 100.127.203.4; the desktop is
now 100.117.30.114.

What it does:
  * scans config/data files (json/yaml/toml/env/ini/conf/service/txt) under the roots for
    the OLD IP as a whole token (100.127.203.40 is not a match);
  * reports .py/.md/test hits separately as INFORMATIONAL: the old IP appears on purpose in
    core/egress_gateway.py comments and in tests as a CGNAT example, so code is only
    rewritten with --include-code;
  * reports SQLite files containing the old IP, with a ready-made UPDATE to run by hand (no
    automatic DB edits; the schema is never touched);
  * with --apply: backs up each file, replaces, re-parses JSON (restores on failure).
  * with --restart-unit UNIT: stops the unit BEFORE editing and starts it after, so a
    registry that persists its in-memory state on shutdown cannot write the old IP back.
  * with --registry-url: after the fix, GETs the registry and confirms the new IP is listed.

Usage:
  python3 fix_fleet_ip.py                                  # dry run
  python3 fix_fleet_ip.py --apply --restart-unit abl-fleet-registry.service
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import atomic_write, banner, run, walk  # noqa: E402

OLD, NEW = "100.127.203.4", "100.117.30.114"
DEFAULT_ROOTS = ["~/abl", "~/abl-fused", "~/.config/abl", "~/.abl", "~/fleet", "~/abl/fleet",
                 "~/.config/systemd/user"]
DATA_EXT = {".json", ".yaml", ".yml", ".toml", ".env", ".ini", ".conf", ".cfg", ".service", ".txt", ".jsonl"}
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".md", ".html"}
DB_EXT = {".db", ".sqlite", ".sqlite3"}


def ip_re(ip: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\d.]){re.escape(ip)}(?![\d])")


def is_test_or_doc(p: Path) -> bool:
    parts = {x.lower() for x in p.parts}
    return bool(parts & {"tests", "test", "docs", "planning", "research"}) or p.suffix == ".md"


def scan(roots, old=OLD, include_code=False):
    rx = ip_re(old)
    data_hits, code_hits, db_hits = [], [], []
    for p in walk([Path(r) for r in roots]):
        suf = p.suffix.lower()
        if suf in DB_EXT:
            try:
                if old.encode() in p.read_bytes():
                    db_hits.append(p)
            except OSError:
                pass
            continue
        if suf not in DATA_EXT and suf not in CODE_EXT and p.name not in (".env",):
            continue
        try:
            text = p.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        n = len(rx.findall(text))
        if not n:
            continue
        if suf in DATA_EXT or p.name == ".env":
            (code_hits if is_test_or_doc(p) else data_hits).append((p, n))
        elif include_code and not is_test_or_doc(p):
            data_hits.append((p, n))
        else:
            code_hits.append((p, n))
    return data_hits, code_hits, db_hits


def sqlite_suggestions(db: Path, old: str, new: str) -> list[str]:
    out = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")') if (r[2] or "").upper() in ("", "TEXT", "VARCHAR", "JSON") or "CHAR" in (r[2] or "").upper()]
            for c in cols:
                n = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{c}" LIKE ?', (f"%{old}%",)).fetchone()[0]
                if n:
                    out.append(f"sqlite3 {db} \"UPDATE \\\"{table}\\\" SET \\\"{c}\\\" = REPLACE(\\\"{c}\\\", '{old}', '{new}') "
                               f"WHERE \\\"{c}\\\" LIKE '%{old}%';\"   # {n} row(s)")
        con.close()
    except sqlite3.Error as e:
        out.append(f"# could not inspect {db}: {e}")
    return out


def _validate(p: Path) -> None:
    if p.suffix == ".json":
        json.loads(p.read_text("utf-8"))
    elif p.suffix == ".jsonl":
        for line in p.read_text("utf-8").splitlines():
            if line.strip():
                json.loads(line)
    elif p.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            return
        yaml.safe_load(p.read_text("utf-8"))


def apply_fix(hits, old=OLD, new=NEW):
    rx = ip_re(old)
    done = []
    for p, _ in hits:
        text = p.read_text("utf-8")
        b = atomic_write(p, rx.sub(new, text), validate=_validate)
        done.append((p, b))
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default=OLD)
    ap.add_argument("--new", default=NEW)
    ap.add_argument("--roots", nargs="*", default=DEFAULT_ROOTS)
    ap.add_argument("--include-code", action="store_true", help="also rewrite non-test code files")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restart-unit", help="systemd --user unit to stop before / start after the edit")
    ap.add_argument("--registry-url", default="http://127.0.0.1:8900/", help="checked after --apply")
    a = ap.parse_args(argv)
    banner(a.apply, f"fleet desktop IP {a.old} → {a.new}")

    data_hits, code_hits, db_hits = scan(a.roots, a.old, a.include_code)
    print(f"\nwill rewrite ({len(data_hits)} file(s)):")
    for p, n in data_hits:
        print(f"  {p}  ({n}×)")
    if code_hits:
        print(f"\ninformational, NOT rewritten ({len(code_hits)}; code/tests/docs, use --include-code for code):")
        for p, n in code_hits[:30]:
            print(f"  {p}  ({n}×)")
    if db_hits:
        print("\nSQLite files containing the old IP (edit by hand, after stopping the registry):")
        for db in db_hits:
            for s in sqlite_suggestions(db, a.old, a.new):
                print("  " + s)
    if not data_hits:
        print("\nnothing to rewrite in config/data files." + (" See SQLite hints above." if db_hits else
              " If the registry keeps state in memory only, re-register the desktop via its API."))
        return 0
    if not a.apply:
        return 0

    if a.restart_unit:
        r = run(["systemctl", "--user", "stop", a.restart_unit])
        print(f"\nstopped {a.restart_unit}: rc={r.returncode} {r.stderr.strip()}")
    try:
        for p, b in apply_fix(data_hits, a.old, a.new):
            print(f"  fixed {p}  (backup {b.name})")
    finally:
        if a.restart_unit:
            r = run(["systemctl", "--user", "start", a.restart_unit])
            st = run(["systemctl", "--user", "is-active", a.restart_unit]).stdout.strip()
            print(f"started {a.restart_unit}: rc={r.returncode} state={st}")
    if a.registry_url:
        try:
            body = urllib.request.urlopen(a.registry_url, timeout=4).read().decode("utf-8", "replace")  # noqa: S310
            print(f"registry check {a.registry_url}: new IP {'PRESENT' if a.new in body else 'absent'}, "
                  f"old IP {'STILL PRESENT' if a.old in body else 'gone'}")
        except OSError as e:
            print(f"registry check skipped ({e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
