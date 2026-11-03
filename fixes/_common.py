"""Shared helpers for the abl1 fix scripts: backups, dry-run output, safe walking.

Every fix script follows the same contract:
  * DRY RUN by default; nothing is written without --apply.
  * Before any file is modified it is copied to <file>.bak-cloudcc-20261103 (an existing
    backup is never overwritten, so re-running cannot destroy the original).
  * Writes are atomic (tmp + rename) and validated after writing; failed validation
    restores the backup.
  * Never touches ~/.dsh/.credentials.yaml or anything under node_modules/@deepseek-ai/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

BACKUP_SUFFIX = ".bak-cloudcc-20261103"

SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
                       ".pytest_cache", ".cache", ".npm", "backups", "site-packages", "dist", "build"})

FORBIDDEN = (
    Path("~/.dsh/.credentials.yaml").expanduser(),
)


def is_forbidden(p: Path) -> bool:
    rp = p.expanduser().resolve()
    if any(rp == f.resolve() for f in FORBIDDEN):
        return True
    parts = rp.parts
    return "node_modules" in parts and "@deepseek-ai" in parts


def backup(p: Path) -> Path:
    if is_forbidden(p):
        raise PermissionError(f"refusing to touch protected path {p}")
    b = p.with_name(p.name + BACKUP_SUFFIX)
    if not b.exists():
        shutil.copy2(p, b)
    return b


def atomic_write(p: Path, text: str, validate: Optional[Callable[[Path], None]] = None) -> Path:
    """Backup, write atomically, validate; restore the backup if validation raises."""
    b = backup(p)
    tmp = p.with_name(p.name + ".tmp-cloudcc")
    tmp.write_text(text, encoding="utf-8")
    shutil.copymode(p, tmp)
    os.replace(tmp, p)
    if validate is not None:
        try:
            validate(p)
        except Exception:
            shutil.copy2(b, p)
            raise
    return b


def walk(roots: Iterable[Path], *, max_bytes: int = 5_000_000,
         skip_dirs: frozenset[str] = SKIP_DIRS) -> Iterator[Path]:
    seen: set[Path] = set()
    for root in roots:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fn in filenames:
                p = Path(dirpath) / fn
                if BACKUP_SUFFIX in fn or ".bak" in fn or fn.endswith((".tmp-cloudcc", ".pyc")):
                    continue
                try:
                    rp = p.resolve()
                    if rp in seen or p.is_symlink() or p.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                seen.add(rp)
                if is_forbidden(p):
                    continue
                yield p


def run(cmd: list[str], check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
    """subprocess.run that never raises for a missing binary or a timeout (rc 127 / 124)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]}: timed out after {timeout}s")


def banner(apply: bool, what: str) -> None:
    mode = "APPLY" if apply else "DRY RUN (re-run with --apply to write)"
    print(f"== {what} — {mode}")
