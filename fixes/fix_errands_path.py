#!/usr/bin/env python3
"""fix_errands_path.py — make the errands scanner portable off WSL.

Bug: on abl1 the errands scanner looks for /mnt/c/Users/Asif/Desktop/ABL_INBOX/errands,
a WSL-only path. Every scan logs a miss.

Fix: every plain string literal that starts with /mnt/c/Users/Asif/Desktop/ABL_INBOX
is rewritten to an env-overridable expression:

    "/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands"
  → os.path.join(os.environ.get("ABL_INBOX_DIR", os.path.expanduser("~/abl/inbox")), "errands")

So on abl1 the scanner reads ~/abl/inbox/errands (created by --apply), and on the WSL
desktop, ABL_INBOX_DIR=/mnt/c/Users/Asif/Desktop/ABL_INBOX restores the old behaviour
exactly. Only STRING tokens are touched (via tokenize), so comments and docstrings that
mention the path are left alone. f-strings and bytes are reported, never rewritten.
``import os`` is added if missing. Each edited file must compile (py_compile) or the
backup is restored.

Usage:
  python3 fix_errands_path.py                 # dry run: list hits with line numbers
  python3 fix_errands_path.py --apply
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import py_compile
import re
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import atomic_write, banner, walk  # noqa: E402

PREFIX = "/mnt/c/Users/Asif/Desktop/ABL_INBOX"
ENV = "ABL_INBOX_DIR"
DEFAULT_DIR = "~/abl/inbox"
DEFAULT_ROOTS = ["~/abl/odysseus", "~/abl-fused/odysseus", "~/abl/tools", "~/ops"]
_STR_PREFIX_RE = re.compile(r"^([rRuU]?)(['\"]{1,3})")


def replacement(value: str) -> str:
    rest = value[len(PREFIX):].strip("/")
    base = f'os.environ.get("{ENV}", os.path.expanduser("{DEFAULT_DIR}"))'
    if not rest:
        return base
    parts = ", ".join(repr(x) for x in rest.split("/") if x)
    return f"os.path.join({base}, {parts})"


def rewrite_source(src: str) -> tuple[str, list[tuple[int, str]], list[tuple[int, str]]]:
    """Return (new_source, rewritten[(line, literal)], skipped[(line, why)])."""
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    lines = src.splitlines(keepends=True)
    edits, done, skipped = [], [], []
    for t in toks:
        if t.type != tokenize.STRING or PREFIX not in t.string:
            continue
        m = _STR_PREFIX_RE.match(t.string)
        if not m or m.group(2) in ('"""', "'''"):
            skipped.append((t.start[0], "f-string/bytes/triple-quoted literal — edit by hand"))
            continue
        try:
            value = ast.literal_eval(t.string)
        except (ValueError, SyntaxError):
            skipped.append((t.start[0], "unparseable literal"))
            continue
        if not isinstance(value, str) or not value.startswith(PREFIX):
            skipped.append((t.start[0], "path is not at the start of the literal"))
            continue
        edits.append((t.start, t.end, replacement(value)))
        done.append((t.start[0], t.string))
    if not edits:
        return src, done, skipped
    # apply from the end so earlier offsets stay valid
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln))
    out = src
    for (sl, sc), (el, ec), rep in sorted(edits, reverse=True):
        a, b = offs[sl - 1] + sc, offs[el - 1] + ec
        out = out[:a] + rep + out[b:]
    if not re.search(r"^\s*import os\b|^\s*import [\w, ]*\bos\b", out, re.M):
        out = _insert_import_os(out)
    return out, done, skipped


def _insert_import_os(src: str) -> str:
    """Insert `import os` after the module docstring and any __future__ imports."""
    tree = ast.parse(src)
    insert_after = 0
    body = tree.body
    i = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        insert_after = body[0].end_lineno
        i = 1
    while i < len(body) and isinstance(body[i], ast.ImportFrom) and body[i].module == "__future__":
        insert_after = body[i].end_lineno
        i += 1
    lines = src.splitlines(keepends=True)
    lines.insert(insert_after, "import os  # added by fix_errands_path (cloudcc 2026-11-03)\n")
    return "".join(lines)


def find_targets(roots):
    for p in walk([Path(r) for r in roots]):
        if p.suffix != ".py" or "tests" in p.parts:
            continue
        try:
            text = p.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if PREFIX in text:
            yield p, text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="*", default=DEFAULT_ROOTS)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    banner(a.apply, f"errands path {PREFIX} → ${ENV} (default {DEFAULT_DIR})")
    changed = 0
    for p, text in find_targets(a.roots):
        new, done, skipped = rewrite_source(text)
        for ln, lit in done:
            print(f"  {p}:{ln}  {lit}  →  {replacement(ast.literal_eval(lit))}")
        for ln, why in skipped:
            print(f"  {p}:{ln}  SKIPPED: {why}")
        if not done:
            if not skipped:
                print(f"  {p}: mentions the path only in comments/docstrings; nothing to change")
            continue
        changed += 1
        if a.apply:
            b = atomic_write(p, new, validate=lambda f: py_compile.compile(str(f), doraise=True))
            print(f"    ✓ rewritten, compiles (backup {b.name})")
    if changed == 0:
        print("no string literals to rewrite (the scanner may live outside the roots; pass --roots)")
    if a.apply:
        d = Path(os.path.expanduser(DEFAULT_DIR)) / "errands"
        d.mkdir(parents=True, exist_ok=True)
        print(f"ensured {d} exists. On the WSL desktop, set {ENV}={PREFIX} to keep the old location.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
