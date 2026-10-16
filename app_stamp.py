"""Which version of the app's code a running app was started with.

The app writes a stamp of its code (and folder) and its process ID when it starts. The Windows
launcher compares it with the files on disk and with the process that answers on the app's port:
if either differs, the running app is older than this folder (for example after an update) or
comes from another copy, and the launcher restarts it instead of showing the old version.

    python app_stamp.py [PID]    exit code 0: process PID runs this folder's current code
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAMP_FILE = ROOT / ".app_stamp"
_SKIP = {"tests", ".git", "venv", ".venv", "__pycache__", "node_modules"}


def code_stamp(root: Path = ROOT) -> str:
    """Hash of the folder's path and the names, sizes and modification times of its Python files."""
    digest = hashlib.sha256(str(root).lower().encode("utf-8"))
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if _SKIP.intersection(rel.parts):
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        digest.update(f"{rel.as_posix()}:{info.st_size}:{int(info.st_mtime)}\n".encode())
    return digest.hexdigest()[:20]


def write_stamp(pid: int, root: Path = ROOT) -> str:
    stamp = code_stamp(root)
    try:
        (root / STAMP_FILE.name).write_text(f"{stamp} {pid}", encoding="utf-8")
    except OSError:
        pass  # a read-only folder: the launcher then restarts the app when asked to start it
    return stamp


def is_current(pid: str | int | None = None, root: Path = ROOT) -> bool:
    """True if the app that wrote the stamp runs the current code (and, given `pid`, is that process)."""
    try:
        parts = (root / STAMP_FILE.name).read_text(encoding="utf-8").split()
    except OSError:
        return False
    if not parts or parts[0] != code_stamp(root):
        return False
    return pid is None or (len(parts) > 1 and parts[1] == str(pid).strip())


if __name__ == "__main__":
    sys.exit(0 if is_current(sys.argv[1] if len(sys.argv) > 1 else None) else 1)
