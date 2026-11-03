#!/usr/bin/env python3
"""patch_ears.py — point desktop ears.py at abl1 (tailnet) with SSH-tunnel fallback.

Before: ears.py posts audio to http://localhost:7800/api/abl/voice/heard. That was the
        desktop Odysseus, which is now STOPPED, so every chunk dies.
After:  every HTTP call whose URL traces back to localhost:7800 / 127.0.0.1:7800 goes to
          1. http://abl1.tailda08f9.ts.net:7800   (tailnet, preferred)
          2. http://127.0.0.1:7800                 (SSH tunnel fallback)
        chosen PER CALL by voice_bridge's health checks, carrying abl1's owner bearer token.

How (no guessing at ears.py's structure):
  * AST-finds calls to requests/httpx .post/.get/.put/.request and
    urllib.request.Request/urlopen whose URL argument *statically traces* to a
    localhost:7800 literal: directly, via an f-string, via a constant
    (ABL_URL = "http://localhost:7800/..."), or via a derived name (URL = BASE + "/heard").
  * Wraps that URL argument in `_abl_vb.live(...)`, which re-resolves the base on every call.
    Rewriting the literal instead would freeze the choice at import time.
  * Adds the auth header ONLY to those calls: `headers=_abl_vb.auth_headers()`, or merges it
    into an existing headers= argument. Calls to any other host are left untouched, so the
    owner token can never leak to a third party.
  * Inserts a 3-line import block (marker `abl-voice-target-patch v1`) after the docstring and
    __future__ imports. Re-running is a no-op.
  * Reports what it could NOT do safely (calls with **kwargs, literals that reach no HTTP
    call directly) with line numbers, for a 30-second manual look.
  * Dry run by default. With --apply: backs up to ears.py.bak-cloudcc-20261103, writes,
    py_compiles, and restores the backup if compiling fails.

Usage (on the desktop):
  cp src/voice_bridge.py ~/ops/speaker/                       # next to ears.py
  python3 patches/patch_ears.py ~/ops/speaker/ears.py         # dry run: shows the diff
  python3 patches/patch_ears.py ~/ops/speaker/ears.py --apply
  python3 ~/ops/speaker/voice_bridge.py --health              # tailnet UP? tunnel UP?
"""
from __future__ import annotations

import argparse
import ast
import difflib
import py_compile
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "abl-voice-target-patch v1"
BACKUP_SUFFIX = ".bak-cloudcc-20261103"
LEGACY_RE = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1):7800\b")
HTTP_FUNCS = {"post", "get", "put", "patch", "delete", "request", "head"}
HTTP_MODULES = {"requests", "httpx", "session", "client", "_session", "_client", "sess", "s"}
URLLIB_FUNCS = {"Request", "urlopen"}

IMPORT_BLOCK = (
    f"import os as _abl_os, sys as _abl_sys  # {MARKER} (cloudcc 2026-11-03)\n"
    "_abl_sys.path.append(_abl_os.environ.get('ABL_VOICE_BRIDGE_DIR', "
    "_abl_os.path.dirname(_abl_os.path.abspath(__file__))))\n"
    "import voice_bridge as _abl_vb  # noqa: E402  tailnet first, SSH tunnel fallback, owner token\n"
)


@dataclass
class Report:
    wrapped: list[int] = field(default_factory=list)          # line numbers of calls rewired
    headers_added: list[int] = field(default_factory=list)
    headers_merged: list[int] = field(default_factory=list)
    manual: list[tuple[int, str]] = field(default_factory=list)
    already_patched: bool = False


# ── taint analysis: which expressions carry a localhost:7800 URL ─────────────

def _is_legacy_const(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(LEGACY_RE.search(node.value))


def _target_key(t: ast.AST):
    if isinstance(t, ast.Name):
        return ("name", t.id)
    if isinstance(t, ast.Attribute):
        return ("attr", t.attr)                  # self.url / cfg.url: tracked by attribute name
    return None


def _ref_key(n: ast.AST):
    if isinstance(n, ast.Name):
        return ("name", n.id)
    if isinstance(n, ast.Attribute):
        return ("attr", n.attr)
    return None


def _expr_tainted(expr: ast.AST, tainted: set) -> bool:
    for n in ast.walk(expr):
        if _is_legacy_const(n):
            return True
        k = _ref_key(n)
        if k is not None and k in tainted and isinstance(getattr(n, "ctx", None), ast.Load):
            return True
    return False


def tainted_names(tree: ast.AST) -> set:
    tainted: set = set()
    assigns = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            assigns += [(t, n.value) for t in n.targets]
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and n.value is not None:
            assigns.append((n.target, n.value))
    changed = True
    while changed:                               # fixpoint: BASE -> URL = BASE + "/heard" -> ...
        changed = False
        for t, v in assigns:
            k = _target_key(t)
            if k and k not in tainted and _expr_tainted(v, tainted):
                tainted.add(k)
                changed = True
    return tainted


def _is_http_call(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        if f.attr in URLLIB_FUNCS:
            return "urllib"
        if f.attr in HTTP_FUNCS:
            base = f.value
            name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
            if name.lower() in HTTP_MODULES or "session" in name.lower() or "client" in name.lower():
                return "http"
    if isinstance(f, ast.Name) and f.id in URLLIB_FUNCS:
        return "urllib"
    return ""


def _url_arg(call: ast.Call, kind: str):
    """(node, is_keyword). requests.request(method, url) has the URL second."""
    for kw in call.keywords:
        if kw.arg == "url":
            return kw.value, True
    f = call.func
    idx = 1 if isinstance(f, ast.Attribute) and f.attr == "request" else 0
    return (call.args[idx], False) if len(call.args) > idx else (None, False)


# ── source editing by byte offsets (ast offsets are UTF-8 byte columns) ──────

class _Src:
    def __init__(self, text: str):
        self.lines = text.splitlines(keepends=True)
        self.starts = [0]
        for ln in self.lines:
            self.starts.append(self.starts[-1] + len(ln.encode("utf-8")))
        self.data = text.encode("utf-8")

    def off(self, lineno: int, col: int) -> int:
        return self.starts[lineno - 1] + col

    def seg(self, node: ast.AST) -> str:
        return self.data[self.off(node.lineno, node.col_offset): self.off(node.end_lineno, node.end_col_offset)].decode("utf-8")


def _insert_import(src: str) -> str:
    tree = ast.parse(src)
    after, i, body = 0, 0, tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        after, i = body[0].end_lineno, 1
    while i < len(body) and isinstance(body[i], ast.ImportFrom) and body[i].module == "__future__":
        after, i = body[i].end_lineno, i + 1
    lines = src.splitlines(keepends=True)
    if lines and after and not lines[after - 1].endswith("\n"):
        lines[after - 1] += "\n"
    lines.insert(after, IMPORT_BLOCK)
    return "".join(lines)


def patch_source(src: str) -> tuple[str, Report]:
    rep = Report()
    if MARKER in src:
        rep.already_patched = True
        return src, rep
    tree = ast.parse(src)
    tainted = tainted_names(tree)
    s = _Src(src)
    edits: list[tuple[int, int, str]] = []          # (start_byte, end_byte, replacement)
    covered_lines: set[int] = set()

    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        kind = _is_http_call(call)
        if not kind:
            continue
        url, _ = _url_arg(call, kind)
        if url is None or not _expr_tainted(url, tainted):
            continue
        covered_lines.update(range(call.lineno, call.end_lineno + 1))
        edits.append((s.off(url.lineno, url.col_offset), s.off(url.end_lineno, url.end_col_offset),
                      f"_abl_vb.live({s.seg(url)})"))
        rep.wrapped.append(call.lineno)
        if kind == "urllib" and isinstance(call.func, (ast.Attribute, ast.Name)) and \
                (call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id) == "urlopen":
            continue                                   # urlopen(url-string): no header slot; Request handles headers
        hdr = next((kw for kw in call.keywords if kw.arg == "headers"), None)
        if hdr is not None:
            edits.append((s.off(hdr.value.lineno, hdr.value.col_offset),
                          s.off(hdr.value.end_lineno, hdr.value.end_col_offset),
                          f"{{**({s.seg(hdr.value)} or {{}}), **_abl_vb.auth_headers()}}"))
            rep.headers_merged.append(call.lineno)
        elif any(kw.arg is None for kw in call.keywords):
            rep.manual.append((call.lineno, "call uses **kwargs: add headers=_abl_vb.auth_headers() by hand if "
                                            "the kwargs carry no headers"))
        else:
            end = s.off(call.end_lineno, call.end_col_offset) - 1       # the closing ')'
            inner = s.data[s.off(call.lineno, call.col_offset):end].decode("utf-8").rstrip()
            sep = "" if inner.endswith(("(", ",")) else ", "
            edits.append((end, end, f"{sep}headers=_abl_vb.auth_headers()"))
            rep.headers_added.append(call.lineno)

    # Legacy literals that never reach a rewired call: tell a human where they are.
    # Backward reachability: names referenced by rewired URL args, then names those
    # names were built from, to a fixpoint. A literal is covered if it sits inside a
    # rewired call or inside the value of an assignment to a reached name.
    reached: set = set()
    for ln_call in (n for n in ast.walk(tree) if isinstance(n, ast.Call) and n.lineno in rep.wrapped):
        url, _ = _url_arg(ln_call, _is_http_call(ln_call))
        reached |= {k for k in (_ref_key(x) for x in ast.walk(url)) if k in tainted}
    assigns = [(t, n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets]
    grew = True
    while grew:
        grew = False
        for t, v in assigns:
            if _target_key(t) in reached:
                more = {k for k in (_ref_key(x) for x in ast.walk(v)) if k in tainted} - reached
                if more:
                    reached |= more
                    grew = True
    covered_ids = {id(x) for t, v in assigns if _target_key(t) in reached for x in ast.walk(v)}
    for n in ast.walk(tree):
        if _is_legacy_const(n) and n.lineno not in covered_lines and id(n) not in covered_ids:
            rep.manual.append((n.lineno, f"{n.value!r} is not passed to a recognised HTTP call; "
                                         "wrap its use in _abl_vb.live(...) by hand"))

    if not edits:
        return src, rep
    data = s.data
    for a, b, rep_text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        data = data[:a] + rep_text.encode("utf-8") + data[b:]
    out = data.decode("utf-8")
    ast.parse(out)                                       # never emit broken Python
    return _insert_import(out), rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Point ears.py at abl1 (tailnet) with SSH-tunnel fallback")
    ap.add_argument("ears", nargs="?", default=str(Path("~/ops/speaker/ears.py").expanduser()))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    path = Path(a.ears).expanduser()
    if not path.is_file():
        print(f"not found: {path}", file=sys.stderr)
        return 2
    src = path.read_text("utf-8")
    new, rep = patch_source(src)
    if rep.already_patched:
        print(f"{path}: already patched ({MARKER}); nothing to do")
        return 0
    print(f"== {path} — {'APPLY' if a.apply else 'DRY RUN (add --apply to write)'}")
    print(f"calls rewired: {rep.wrapped or 'none'} · headers added: {rep.headers_added or '-'} · "
          f"headers merged: {rep.headers_merged or '-'}")
    for ln, why in rep.manual:
        print(f"  MANUAL line {ln}: {why}")
    if new == src:
        print("no change: no HTTP call traces to localhost:7800. Check for a non-requests client and send me the file.")
        return 1
    sys.stdout.writelines(difflib.unified_diff(src.splitlines(keepends=True), new.splitlines(keepends=True),
                                               fromfile=str(path), tofile=f"{path} (patched)"))
    if not a.apply:
        return 0
    bak = path.with_name(path.name + BACKUP_SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(new, "utf-8")
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as e:
        shutil.copy2(bak, path)
        print(f"compile failed, restored backup: {e}", file=sys.stderr)
        return 3
    bridge = path.parent / "voice_bridge.py"
    print(f"\npatched (backup {bak.name}). voice_bridge.py next to ears.py: {'yes' if bridge.exists() else 'NO, copy it there'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
