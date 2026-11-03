#!/usr/bin/env python3
"""fix_one_project_context.py — stop one-project-context-v1 failing on dsh-web restart.

Bug: the plugin writes a "ready" marker file while it runs and never removes it. On the
next start it sees the stale marker and fails.

Fix (no dsh core edits, no plugin fork): a systemd drop-in on the dsh-web unit that
deletes the marker before every start and after every stop.

    ~/.config/systemd/user/<unit>.d/30-one-project-context-ready.conf
    [Service]
    ExecStartPre=-/bin/rm -f <marker paths>
    ExecStopPost=-/bin/rm -f <marker paths>

ExecStartPre covers the crash/SIGKILL case where ExecStopPost never ran on the old
process. The leading '-' means a missing file never blocks startup.

Marker discovery:
  1. files whose name contains "ready" inside the plugin dir;
  2. files whose name contains "ready" and whose path mentions one-project-context under
     ~/.dsh (never .credentials.yaml), ~/.local/share/dsh, ~/.cache/dsh, $XDG_RUNTIME_DIR, /tmp;
  3. string literals mentioning "ready" in the plugin's own source, so you can see where it
     writes the marker even when no stale file exists right now;
  4. anything passed with --path.
Only absolute paths whose basename contains "ready" are ever written into the drop-in.

Per the session boundaries this script does NOT restart dsh-web. It runs
`systemctl --user daemon-reload` (with --apply) and prints the test sequence.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import banner, is_forbidden, run  # noqa: E402

PLUGIN_GLOB = "one-project-context*"
DEFAULT_PLUGINS = "~/abl/tools/dsh/app/local-plugins"
DEFAULT_UNIT = "abl-dsh-web.service"
DROPIN_NAME = "30-one-project-context-ready.conf"
# (dir, max depth). /tmp is shallow on purpose: deep /tmp paths are other programs' scratch
# (pytest, build dirs), and hard-coding transient paths into a unit file is wrong.
STATE_DIRS: list[tuple[str, int]] = [("~/.dsh", 6), ("~/.local/share/dsh", 6), ("~/.cache/dsh", 6),
                                     (os.environ.get("XDG_RUNTIME_DIR", ""), 3), ("/tmp", 2)]
SKIP_STATE_DIRS = ("pytest-of-", "systemd-private-", "node-compile-cache")
SRC_EXT = {".js", ".mjs", ".cjs", ".ts", ".json"}
READY_LIT_RE = re.compile(r"""['"`]([^'"`\n]*ready[^'"`\n]*)['"`]""", re.I)


def find_plugin_dirs(plugins: Path) -> list[Path]:
    return sorted(p for p in plugins.glob(PLUGIN_GLOB) if p.is_dir()) if plugins.is_dir() else []


SRC_EXT_TUPLE = tuple(SRC_EXT) + (".md", ".map")


def _ready_name(p: Path) -> bool:
    n = p.name.lower()
    return "ready" in n and not n.endswith(SRC_EXT_TUPLE)


def find_markers(plugin_dirs: list[Path], state_dirs: list, max_depth: int = 6) -> list[Path]:
    found: set[Path] = set()
    for d in plugin_dirs:
        for p in d.rglob("*"):
            if p.is_file() and _ready_name(p) and "node_modules" not in p.parts:
                found.add(p.resolve())
    for entry in state_dirs:
        sd, depth = entry if isinstance(entry, tuple) else (entry, max_depth)
        if not sd:
            continue
        base = Path(sd).expanduser()
        if not base.is_dir():
            continue
        base_depth = len(base.parts)
        for dirpath, dirnames, filenames in os.walk(base):
            if len(Path(dirpath).parts) - base_depth >= depth:
                dirnames[:] = []
            dirnames[:] = [x for x in dirnames if x not in ("node_modules", ".git")
                           and not x.startswith(SKIP_STATE_DIRS)]
            for fn in filenames:
                p = Path(dirpath) / fn
                if _ready_name(p) and "one-project-context" in str(p).lower() and not is_forbidden(p):
                    found.add(p.resolve())
    return sorted(found)


def source_hints(plugin_dirs: list[Path]) -> list[tuple[Path, int, str]]:
    hints = []
    for d in plugin_dirs:
        for p in d.rglob("*"):
            if not p.is_file() or p.suffix not in SRC_EXT or "node_modules" in p.parts:
                continue
            try:
                for i, line in enumerate(p.read_text("utf-8", "replace").splitlines(), 1):
                    if READY_LIT_RE.search(line) and re.search(r"write|touch|open|unlink|exists|rm", line, re.I):
                        hints.append((p, i, line.strip()[:160]))
            except OSError:
                continue
    return hints


def render_dropin(paths: list[Path]) -> str:
    safe = []
    for p in paths:
        if not p.is_absolute() or "ready" not in p.name.lower() or is_forbidden(p):
            raise ValueError(f"unsafe marker path {p}")
        if any(c in str(p) for c in " \t\n'\"$`;&|<>*?"):
            raise ValueError(f"marker path needs quoting, refusing: {p}")
        safe.append(str(p))
    args = " ".join(safe)
    return ("# Added by fix_one_project_context.py (cloud session 2026-11-03).\n"
            "# one-project-context-v1 leaves a stale 'ready' marker that breaks the next start.\n"
            "# Remove this file + `systemctl --user daemon-reload` to roll back.\n"
            "[Service]\n"
            f"ExecStartPre=-/bin/rm -f {args}\n"
            f"ExecStopPost=-/bin/rm -f {args}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugins-dir", default=DEFAULT_PLUGINS)
    ap.add_argument("--unit", default=DEFAULT_UNIT)
    ap.add_argument("--path", action="append", default=[], help="extra absolute marker path(s)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    banner(a.apply, f"one-project-context stale ready marker → drop-in on {a.unit}")

    plugins = Path(a.plugins_dir).expanduser()
    pdirs = find_plugin_dirs(plugins)
    print(f"plugin dir(s): {[str(p) for p in pdirs] or 'NONE FOUND under ' + str(plugins)}")
    markers = find_markers(pdirs, STATE_DIRS) + [Path(p).expanduser() for p in a.path]
    markers = sorted(set(markers))
    print("marker file(s):", *(f"\n  {m}" for m in markers) or [" none currently on disk"])
    hints = source_hints(pdirs)
    if hints:
        print("where the plugin source handles 'ready':")
        for p, i, line in hints[:20]:
            print(f"  {p}:{i}: {line}")
    if not markers:
        print("\nNo marker found. If dsh-web is running, the marker may be created only during a failed\n"
              "start; reproduce once, re-run this script, or pass --path /abs/path/to/ready-file.")
        return 1

    unit = a.unit if a.unit.endswith(".service") else a.unit + ".service"
    dropin_dir = Path("~/.config/systemd/user").expanduser() / f"{unit}.d"
    text = render_dropin(markers)
    print(f"\n{dropin_dir / DROPIN_NAME}:\n" + "\n".join("  | " + ln for ln in text.splitlines()))
    if not a.apply:
        return 0
    dropin_dir.mkdir(parents=True, exist_ok=True)
    (dropin_dir / DROPIN_NAME).write_text(text, encoding="utf-8")
    r = run(["systemctl", "--user", "daemon-reload"])
    print(f"daemon-reload rc={r.returncode} {r.stderr.strip()}")
    v = run(["systemd-analyze", "--user", "verify", unit])
    print(f"systemd-analyze verify: rc={v.returncode} {(v.stderr or v.stdout).strip()[:400]}")
    print("\nNOT restarting dsh-web (session boundary). To test:\n"
          f"  systemctl --user restart {unit}\n"
          f"  journalctl --user -u {unit} -n 120 --no-pager | grep -iE 'one-project-context|plugin.*(fail|error)'\n"
          f"Rollback: rm {dropin_dir / DROPIN_NAME} && systemctl --user daemon-reload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
