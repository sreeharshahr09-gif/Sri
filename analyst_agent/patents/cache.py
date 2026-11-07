"""What the patent pipeline keeps on disk between runs, how much space it takes, and clearing it.

Only the app's own parts of the cache folder are touched: its sub-folders for saved answers, patent readings and
patent PDFs, and the patent map's vector files. Other files in the folder are left alone, whatever it is."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import semantic


@dataclass(frozen=True)
class Part:
    key: str
    label: str
    about: str
    files: int = 0
    size: int = 0  # bytes


PARTS = (
    ("replies", "Saved answers", "every reply of the model, so a repeated run with the same settings asks nothing again"),
    ("extract", "Patent readings", "what the model read from each patent, kept per study set-up"),
    ("maps", "Patent map vectors", "the semantic map's numbers for each set of patents (quick to compute again)"),
    ("pdf", "Patent PDFs", "downloaded patent documents for the figures, and any PDF you saved there yourself"),
)
LABELS = {key: label for key, label, _ in PARTS}


def location() -> Path:
    """The cache folder: PATENT_CACHE_DIR, or .research_agent/patent_cache in the user's home folder."""
    return semantic.CACHE_DIR


def _files(key: str, folder: Path) -> list[Path]:
    if key == "maps":
        return [p for p in folder.glob("emb_*.npy") if p.is_file() and not p.is_symlink()]
    root = folder / key
    if not root.is_dir() or root.is_symlink():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):  # does not follow links into other folders
        out += [Path(dirpath) / name for name in filenames]
    return out


def summary(folder: Path | None = None) -> list[Part]:
    """Each part of the cache with its number of files and size."""
    folder = Path(folder or location())
    parts = []
    for key, label, about in PARTS:
        files = _files(key, folder)
        size = 0
        for path in files:
            try:
                size += path.lstat().st_size
            except OSError:
                pass
        parts.append(Part(key, label, about, len(files), size))
    return parts


def clear(keys, folder: Path | None = None) -> tuple[int, int, list[str]]:
    """Delete the chosen parts. Returns (files deleted, bytes freed, problems): a file another program holds
    open is left and named in the problems; the rest is deleted."""
    folder = Path(folder or location())
    deleted, freed, problems = 0, 0, []
    for key in keys:
        if key not in LABELS:
            continue
        for path in _files(key, folder):
            try:
                size = path.lstat().st_size
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                problems.append(f"{path.name}: {exc.strerror or type(exc).__name__}")
                continue
            deleted, freed = deleted + 1, freed + size
        root = folder / key
        if key != "maps" and root.is_dir() and not root.is_symlink():
            for dirpath, _, _ in sorted(os.walk(root), key=lambda w: len(w[0]), reverse=True):
                try:
                    Path(dirpath).rmdir()  # only empty folders go
                except OSError:
                    pass
    return deleted, freed, problems


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def open_folder(folder: Path | None = None) -> bool:
    """Show the cache folder in the file manager of the PC the app runs on (Windows only). False if it cannot."""
    folder = Path(folder or location())
    if sys.platform != "win32":
        return False
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # type: ignore[attr-defined]  # Windows only
        return True
    except OSError:
        return False
