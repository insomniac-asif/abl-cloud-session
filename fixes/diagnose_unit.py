#!/usr/bin/env python3
"""diagnose_unit.py — explain a systemd --user crash loop (built for abl-market-bridge).

Read-only by default. Collects the unit's state (`systemctl show`), its unit file and
drop-ins, the last N journal lines, checks that ExecStart's interpreter/script and
WorkingDirectory exist, lists listeners on the ports the logs mention, then matches the
journal against known failure signatures and prints a ranked diagnosis with the exact
next command for each finding.

  python3 diagnose_unit.py                                   # abl-market-bridge.service
  python3 diagnose_unit.py --unit abl-market-bridge --lines 500 --report ~/abl/reports/mb.md
  python3 diagnose_unit.py --quarantine    # stop + disable a looping unit (explicit opt-in)

--quarantine exists because a unit that restarts every few seconds burns CPU and floods
the journal. It is never run implicitly, and it prints the re-enable command.
"""
from __future__ import annotations

import argparse
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import run  # noqa: E402


@dataclass
class Finding:
    key: str
    severity: int          # 3 = root cause candidate, 2 = likely contributor, 1 = context
    title: str
    evidence: str
    fix: str


SIGNATURES: list[tuple[str, int, re.Pattern[str], str, str]] = [
    ("module_missing", 3, re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'"),
     "Python dependency missing: {0}",
     "Install it into the interpreter ExecStart uses: <venv>/bin/pip install {0}  "
     "(or point ExecStart at the venv that already has it)"),
    ("import_name", 3, re.compile(r"ImportError: cannot import name '([\w.]+)' from '([\w.]+)'"),
     "API mismatch: {1} has no {0}",
     "A sibling module changed. grep -rn 'def {0}\\|{0} =' in {1}; pin or update the caller"),
    ("addr_in_use", 3, re.compile(r"(?:Address already in use|EADDRINUSE|Errno 98)(?:[^\n]*?:(\d{2,5})\b)?"),
     "Port already bound{0_sfx}",
     "Find the owner: ss -ltnp | grep ':{0}' ; stop the duplicate or move one of them"),
    ("wsl_path", 3, re.compile(r"(?:No such file or directory|FileNotFoundError|ENOENT)[^\n]*(/mnt/c/[^'\"\s]+)"),
     "WSL-only path on abl1: {0}",
     "Override via env in a drop-in (systemctl --user edit <unit>) or create the dir on abl1; "
     "see fix_errands_path.py for the pattern"),
    ("file_missing", 2, re.compile(r"(?:No such file or directory|FileNotFoundError)[^\n]*?'([^']+)'"),
     "File missing: {0}", "ls -la {0} ; restore it or fix the path in the unit/config"),
    ("conn_refused", 2, re.compile(r"(?:Connection refused|ECONNREFUSED|ConnectionRefusedError)(?:[^\n]*?:(\d{2,5})\b)?"),
     "Dependency not listening{0_sfx}",
     "Is the upstream up? ss -ltn | grep ':{0}' ; add After=/Wants= on its unit, or retry with backoff instead of exiting"),
    ("exec_203", 3, re.compile(r"status=203/EXEC"),
     "ExecStart binary not executable / not found", "systemctl --user cat <unit> ; check the interpreter path and +x"),
    ("chdir_200", 3, re.compile(r"status=200/CHDIR"),
     "WorkingDirectory does not exist", "systemctl --user cat <unit> ; mkdir it or fix WorkingDirectory="),
    ("perm", 2, re.compile(r"(?:PermissionError|EACCES|Permission denied)(?:[^\n]*?'?([/~][^'\s]*))?"),
     "Permission denied{0_sfx}", "ls -la the path; user units run as rescue, so check ownership"),
    ("syntax", 3, re.compile(r'File "([^"]+)", line (\d+)[^\n]*\n(?:[^\n]*\n){0,3}?\s*(?:SyntaxError|IndentationError)'),
     "Syntax error in {0}:{1}", "python3 -m py_compile {0} ; restore from the latest .bak if it was a bad edit"),
    ("env_missing", 2, re.compile(r"KeyError: '([A-Z][A-Z0-9_]{2,})'"),
     "Environment variable {0} not set",
     "Add Environment={0}=… (or EnvironmentFile=) in a drop-in: systemctl --user edit <unit>"),
    ("json_decode", 2, re.compile(r"json\.decoder\.JSONDecodeError|JSONDecodeError"),
     "Corrupt JSON config/state", "Find the file in the traceback above; restore from backup or delete if it is a cache"),
    ("db_locked", 2, re.compile(r"database is locked"),
     "SQLite lock contention", "Another process holds the DB; check `fuser <db>`; add a busy timeout"),
    ("oom", 3, re.compile(r"(?:oom-kill|Out of memory|Killed process|status=9/KILL)"),
     "Killed by the OOM killer", "free -h ; the 27B + desks are tight on 16 GB. Cap the bridge (MemoryMax=) or stop a desk"),
    ("start_limit", 1, re.compile(r"Start request repeated too quickly|start-limit-hit"),
     "systemd start limit hit", "After fixing: systemctl --user reset-failed <unit> && systemctl --user start <unit>"),
    ("clean_exit_loop", 2, re.compile(r"(?:Main process exited, code=exited, status=0/SUCCESS|Deactivated successfully)"),
     "Process exits cleanly but Restart= restarts it",
     "If it is a one-shot job, use Type=oneshot + a .timer instead of Restart=always"),
    ("traceback", 1, re.compile(r"Traceback \(most recent call last\):"),
     "Python traceback present", "Read the last traceback in the journal excerpt below"),
]


def classify_log(text: str) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str] = set()
    for key, sev, rx, title, fix in SIGNATURES:
        matches = list(rx.finditer(text))
        if not matches:
            continue
        m = matches[-1]                                   # latest occurrence is most relevant
        groups = [g or "" for g in m.groups()]
        sfx = f" on :{groups[0]}" if groups and groups[0] and groups[0].isdigit() else (
            f": {groups[0]}" if groups and groups[0] else "")
        t = title.replace("{0_sfx}", sfx)
        try:
            t = t.format(*groups) if groups else t
            f = fix.format(*groups) if groups else fix
        except (IndexError, KeyError):
            f = fix
        f = f.replace("grep ':' ", "grep LISTEN ")              # no port captured
        if key == "file_missing" and "wsl_path" in seen and "/mnt/c/" in (groups[0] if groups else ""):
            continue
        seen.add(key)
        line = text[: m.end()].count("\n") + 1
        out.append(Finding(key, sev, t, f"journal line ~{line}: {m.group(0).strip()[:200]}", f))
    # A traceback-only finding is context unless nothing better was found.
    if len(out) > 1:
        out = [x for x in out if x.key != "traceback"] or out
    return sorted(out, key=lambda x: -x.severity)


def unit_props(unit: str) -> dict[str, str]:
    props = "ActiveState,SubState,Result,NRestarts,ExecMainStatus,ExecStart,WorkingDirectory,FragmentPath," \
            "DropInPaths,Restart,RestartUSec,EnvironmentFiles,Environment,MemoryCurrent,After,Requires,Wants"
    r = run(["systemctl", "--user", "show", unit, "-p", props])
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def exec_checks(props: dict[str, str]) -> list[Finding]:
    out = []
    es = props.get("ExecStart", "")
    m = re.search(r"argv\[\]=([^;]+)", es) or re.search(r"path=([^ ;]+)", es)
    argv = shlex.split(m.group(1)) if m else []
    for tok in argv[:3]:
        if tok.startswith(("/", "~")):
            p = Path(tok).expanduser()
            if not p.exists():
                out.append(Finding("exec_path", 3, f"ExecStart path missing: {p}", es[:200],
                                   "Fix the path in the unit (systemctl --user edit --full <unit>)"))
    wd = props.get("WorkingDirectory", "")
    if wd and wd not in ("!/", "~") and not Path(wd.lstrip("!-")).expanduser().exists():
        out.append(Finding("wd_missing", 3, f"WorkingDirectory missing: {wd}", "", f"mkdir -p {wd} or fix the unit"))
    for ef in (props.get("EnvironmentFiles") or "").split():
        p = ef.split("(")[0].lstrip("-")
        if p.startswith("/") and not Path(p).exists() and not ef.startswith("-"):
            out.append(Finding("envfile_missing", 3, f"EnvironmentFile missing: {p}", "", "create it or prefix with '-'"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", default="abl-market-bridge.service")
    ap.add_argument("--lines", type=int, default=300)
    ap.add_argument("--report", help="also write a markdown report here")
    ap.add_argument("--quarantine", action="store_true", help="stop + disable the unit (explicit opt-in)")
    a = ap.parse_args(argv)
    unit = a.unit if "." in a.unit else a.unit + ".service"

    props = unit_props(unit)
    cat = run(["systemctl", "--user", "cat", unit]).stdout
    journal = run(["journalctl", "--user", "-u", unit, "-n", str(a.lines), "--no-pager", "-o", "short-iso"]).stdout
    findings = exec_checks(props) + classify_log(journal)
    ports = sorted({p for p in re.findall(r":(\d{4,5})\b", journal + cat) if 1024 <= int(p) <= 65535})
    listeners = run(["ss", "-ltnp"]).stdout if ports else ""

    rep = [f"# Diagnosis: {unit}", "",
           f"state: {props.get('ActiveState')}/{props.get('SubState')}  result={props.get('Result')}  "
           f"restarts={props.get('NRestarts')}  last-exit={props.get('ExecMainStatus')}  Restart={props.get('Restart')}",
           f"unit file: {props.get('FragmentPath')}  drop-ins: {props.get('DropInPaths') or '-'}", ""]
    if not props:
        rep.append("!! systemctl returned nothing; is the unit name right? `systemctl --user list-units | grep -i bridge`")
    rep.append("## Findings (most likely root cause first)")
    if findings:
        for i, f in enumerate(findings, 1):
            rep += [f"{i}. [{'ROOT' if f.severity == 3 else 'LIKELY' if f.severity == 2 else 'CONTEXT'}] {f.title}",
                    f"   evidence: {f.evidence}" if f.evidence else "", f"   next: {f.fix}"]
    else:
        rep.append("No known signature matched. Read the journal tail below; run the ExecStart command by hand "
                   "in the WorkingDirectory to see the error unbuffered (PYTHONUNBUFFERED=1).")
    if ports:
        rep += ["", f"## Listeners on ports mentioned ({', '.join(ports)})"]
        rep += [ln for ln in listeners.splitlines() if any(f":{p} " in ln or ln.rstrip().endswith(f":{p}") for p in ports)] or ["  (none of those ports is listening)"]
    rep += ["", "## Unit", "```", cat.strip()[:3000], "```", "", f"## Journal (last {min(a.lines, 60)} of {a.lines} lines)",
            "```", "\n".join(journal.splitlines()[-60:]), "```"]
    text = "\n".join(x for x in rep if x is not None)
    print(text)
    if a.report:
        p = Path(a.report).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"\nreport written to {p}")
    if a.quarantine:
        s = run(["systemctl", "--user", "stop", unit])
        d = run(["systemctl", "--user", "disable", unit])
        print(f"\nQUARANTINED {unit}: stop rc={s.returncode} disable rc={d.returncode}\n"
              f"Undo: systemctl --user enable --now {unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
