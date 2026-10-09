"""Read-only access to a local folder (the "workspace") for the workspace agent.

Every path is resolved inside the workspace root (symlinks included) before use, secret-looking
files are never read, and there are deliberately no methods that write, move or delete.
Rich formats (Word, PowerPoint, PDF, notebooks, spreadsheets) are converted to plain text so
the model can read and cite them by line number like source code.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

IGNORED_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", "env", ".env",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode", ".ipynb_checkpoints",
        "site-packages", "dist", "build", ".cache", ".DS_Store",
    }
)
# Never read: credentials and private keys. Content would be sent to the model.
SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "*.keystore",
    "credentials*", "*secret*", ".netrc", ".pgpass", ".npmrc", ".pypirc",
)
TEXT_EXTENSIONS = frozenset(
    {
        ".py", ".m", ".r", ".jl", ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".js", ".ts", ".tsx",
        ".jsx", ".go", ".rs", ".f", ".f90", ".for", ".sh", ".bat", ".ps1", ".sql", ".tex", ".bib",
        ".md", ".rst", ".txt", ".log", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml", ".xml",
        ".html", ".css", ".csv", ".tsv", ".dat", ".json", ".jsonl", ".tir", ".mdl", ".slx.txt",
    }
)
DATA_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".parquet"})
MAX_FILE_BYTES = 50 * 1024 * 1024  # plain text and anything not listed below
# Word/PowerPoint files are mostly images: their limit applies to the text XML inside instead.
SIZE_LIMITS = {".pdf": 500 * 1024 * 1024, ".docx": None, ".pptx": None,
               **{ext: 150 * 1024 * 1024 for ext in (".xlsx", ".xlsm", ".xls", ".parquet", ".csv", ".tsv")}}
MAX_XML_BYTES = 200 * 1024 * 1024
MAX_PDF_PAGES = 3000
MAX_ROW_LINES = 200_000
MAX_LINE_CHARS = 1500
READ_MAX_LINES = 250
READ_MAX_CHARS = 14_000


class WorkspaceError(ValueError):
    """A request the workspace refuses (outside the root, secret file, missing, ...)."""


@dataclass
class Document:
    """Plain-text view of a file."""

    path: str  # relative POSIX path
    kind: str  # "text", "word", "powerpoint", "pdf", "notebook", "data", "binary"
    lines: list[str]
    note: str = ""


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Workspace:
    def __init__(self, root: str | os.PathLike):
        path = Path(root).expanduser()
        if not path.exists():
            raise WorkspaceError(f"Folder not found: {path}")
        if not path.is_dir():
            raise WorkspaceError(f"Not a folder: {path}")
        self.root = path.resolve()
        self._cache: dict[str, tuple[tuple[int, int], Document]] = {}
        # Proposed (not yet applied) file contents, keyed by relative path. Set by the agent
        # during an edit run so its own reads see its proposals; empty otherwise.
        self.overlay: dict[str, str] = {}

    # ------------------------------------------------------------------ paths

    def resolve(self, rel: str | os.PathLike = ".") -> Path:
        """Absolute path for `rel`, guaranteed to be inside the workspace."""
        text = str(rel).strip().strip("`'\"") or "."
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceError(f"'{rel}' is outside the workspace folder.")
        return resolved

    def relative(self, path: Path) -> str:
        rel = path.relative_to(self.root).as_posix()
        return rel or "."

    def normalize(self, rel: str) -> str:
        """Canonical relative form of a user/model supplied path."""
        return self.relative(self.resolve(rel))

    @staticmethod
    def is_secret(name: str) -> bool:
        lowered = name.lower()
        return any(fnmatch.fnmatch(lowered, pat) for pat in SECRET_PATTERNS)

    def _check_readable(self, path: Path) -> None:
        if self.is_secret(path.name):
            raise WorkspaceError(f"'{self.relative(path)}' looks like a credentials file and is never read.")
        if any(part in IGNORED_DIRS for part in path.relative_to(self.root).parts[:-1]):
            raise WorkspaceError(f"'{self.relative(path)}' is inside an ignored folder.")

    def iter_files(self, start: str = ".", glob: str | None = None):
        """Yield readable files (as absolute paths) under `start`, skipping ignored folders."""
        base = self.resolve(start)
        if base.is_file():
            yield base
            return
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for name in sorted(filenames):
                if self.is_secret(name):
                    continue
                full = Path(dirpath) / name
                rel = self.relative(full)
                if glob and not (fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(rel, glob)):
                    continue
                if full.is_symlink():
                    try:
                        self.resolve(full)
                    except WorkspaceError:
                        continue
                yield full

    # ------------------------------------------------------------------ overview / listing

    def overview(self, max_entries: int = 120) -> str:
        counts: Counter[str] = Counter()
        total = 0
        size = 0
        for f in self.iter_files():
            total += 1
            counts[f.suffix.lower() or "(no extension)"] += 1
            try:
                size += f.stat().st_size
            except OSError:
                pass
        kinds = ", ".join(f"{ext} ×{n}" for ext, n in counts.most_common(12))
        header = f"{total:,} files, {_fmt_size(size)} total. By type: {kinds or 'none'}."
        return header + "\n\n" + self.list_files(".", depth=2, max_entries=max_entries)

    def list_files(self, path: str = ".", depth: int = 2, max_entries: int = 300) -> str:
        base = self.resolve(path)
        if base.is_file():
            return f"{self.relative(base)} ({_fmt_size(base.stat().st_size)})"
        depth = max(1, min(int(depth), 6))
        lines: list[str] = [f"{self.relative(base)}/"]
        shown = 0
        truncated = False

        def walk(folder: Path, level: int) -> None:
            nonlocal shown, truncated
            try:
                entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return
            for entry in entries:
                if entry.name in IGNORED_DIRS or self.is_secret(entry.name):
                    continue
                if shown >= max_entries:
                    truncated = True
                    return
                indent = "  " * level
                if entry.is_dir():
                    try:
                        n = sum(1 for _ in entry.iterdir())
                    except OSError:
                        n = 0
                    lines.append(f"{indent}{entry.name}/ ({n} items)")
                    shown += 1
                    if level < depth:
                        walk(entry, level + 1)
                else:
                    try:
                        size = _fmt_size(entry.stat().st_size)
                    except OSError:
                        size = "?"
                    lines.append(f"{indent}{entry.name} ({size})")
                    shown += 1

        walk(base, 1)
        if truncated:
            lines.append(f"... listing truncated at {max_entries} entries; list a subfolder for more.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ reading

    def document(self, rel: str) -> Document:
        path = self.resolve(rel)
        key = self.relative(path)
        if key in self.overlay:
            note = "Shows your proposed changes (not yet applied to the file on disk)."
            return Document(key, "text", self.overlay[key].splitlines(), note=note)
        if not path.exists():
            raise WorkspaceError(f"No such file: '{rel}'. Use list_files or search to find the right path.")
        if path.is_dir():
            raise WorkspaceError(f"'{rel}' is a folder; use list_files to see its contents.")
        self._check_readable(path)
        stat = path.stat()
        limit = SIZE_LIMITS.get(path.suffix.lower(), MAX_FILE_BYTES)
        if limit is not None and stat.st_size > limit:
            raise WorkspaceError(f"'{rel}' is {_fmt_size(stat.st_size)}, above the {_fmt_size(limit)} limit for this file type.")
        version = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(key)
        if cached and cached[0] == version:
            return cached[1]
        doc = extract_document(path, key)
        self._cache[key] = (version, doc)
        return doc

    def read(self, rel: str, start: int = 1, end: int | None = None) -> tuple[str, Document, int, int]:
        """Numbered lines `start..end` of a file, capped in size. Returns (text, doc, start, end)."""
        doc = self.document(rel)
        n = len(doc.lines)
        if n == 0:
            return f"{doc.path} ({doc.kind}) is empty.{(' ' + doc.note) if doc.note else ''}", doc, 0, 0
        start = max(1, int(start or 1))
        if start > n:
            raise WorkspaceError(f"'{doc.path}' has only {n} lines; start={start} is past the end.")
        end = n if end is None else max(start, min(int(end), n))
        end = min(end, start + READ_MAX_LINES - 1)
        out: list[str] = []
        chars = 0
        width = len(str(n))
        last = start - 1
        for i in range(start, end + 1):
            line = doc.lines[i - 1]
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + " …[line truncated]"
            entry = f"{i:>{width}}| {line}"
            if chars + len(entry) > READ_MAX_CHARS and out:
                break
            out.append(entry)
            chars += len(entry) + 1
            last = i
        header = f"{doc.path} ({doc.kind}, {n} lines) — lines {start}-{last}"
        if doc.note:
            header += f"\nNote: {doc.note}"
        footer = ""
        if last < n:
            footer = f"\n[{n - last} more lines; read_file with start={last + 1} to continue]"
        return header + "\n" + "\n".join(out) + footer, doc, start, last

    # ------------------------------------------------------------------ search

    def search(
        self,
        query: str,
        path: str = ".",
        glob: str | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 40,
        time_budget_s: float = 15.0,
    ) -> tuple[str, list[tuple[str, int]]]:
        """Search file contents. Returns (report text, [(path, line), ...])."""
        if not query:
            raise WorkspaceError("search needs a non-empty query.")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query if regex else re.escape(query), flags)
        except re.error as exc:
            raise WorkspaceError(f"Invalid regular expression: {exc}") from exc

        hits: list[tuple[str, int]] = []
        lines_out: list[str] = []
        name_hits: list[str] = []
        files_scanned = 0
        stopped = ""
        deadline = time.monotonic() + time_budget_s
        for full in self.iter_files(path, glob):
            if time.monotonic() > deadline:
                stopped = f" Search stopped after {time_budget_s:.0f}s; narrow it with path= or glob=."
                break
            rel = self.relative(full)
            if pattern.search(full.name):
                name_hits.append(rel)
            if not _searchable(full):
                continue
            try:
                doc = self.document(rel)
            except WorkspaceError:
                continue
            files_scanned += 1
            for i, line in enumerate(doc.lines, 1):
                if pattern.search(line):
                    hits.append((rel, i))
                    snippet = _snippet(line, pattern)
                    lines_out.append(f"{rel}:{i}: {snippet}")
                    if len(hits) >= max_results:
                        stopped = f" Showing the first {max_results} matches; refine the query for more."
                        break
            if len(hits) >= max_results:
                break

        parts = [f"Searched {files_scanned} files for {query!r}: {len(hits)} matching lines.{stopped}"]
        if name_hits:
            parts.append("File names matching: " + ", ".join(name_hits[:20]))
        if lines_out:
            parts.append("\n".join(lines_out))
        return "\n".join(parts), hits


def _snippet(line: str, pattern: re.Pattern) -> str:
    """A short view of a matching line, centred on the match (keeps a spreadsheet [sheet rN] label)."""
    line = line.strip()
    if len(line) <= 220:
        return line
    label = line[: line.index("]") + 1] + " " if line.startswith("[") and "]" in line[:60] else ""
    m = pattern.search(line)
    start = max(len(label), m.start() - 90) if m else len(label)
    window = line[start : start + 200]
    return f"{label}{'…' if start > len(label) else ''}{window}{'…' if start + 200 < len(line) else ''}"


def _searchable(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext in (".docx", ".pptx", ".ipynb", ".pdf") or ext in TEXT_EXTENSIONS or ext in DATA_EXTENSIONS:
        return True
    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            return False
        with path.open("rb") as fh:
            return b"\x00" not in fh.read(4096)
    except OSError:
        return False


# --------------------------------------------------------------------------- extraction


def extract_document(path: Path, rel: str) -> Document:
    ext = path.suffix.lower()
    try:
        if ext == ".docx":
            return Document(rel, "word", _docx_lines(path))
        if ext == ".pptx":
            return Document(rel, "powerpoint", _pptx_lines(path))
        if ext == ".ipynb":
            return Document(rel, "notebook", _notebook_lines(path))
        if ext == ".pdf":
            return _pdf_document(path, rel)
        if ext in (".xlsx", ".xlsm", ".xls", ".parquet") or (
            ext in (".csv", ".tsv") and path.stat().st_size > 512 * 1024
        ):
            return _data_document(path, rel)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError) as exc:
        return Document(rel, "binary", [], note=f"Could not extract text: {exc}")

    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return Document(rel, "binary", [], note=f"Binary file ({_fmt_size(len(raw))}); its contents cannot be shown.")
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    return Document(rel, "text", text.splitlines())


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _docx_lines(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        if zf.getinfo("word/document.xml").file_size > MAX_XML_BYTES:
            raise ValueError("the document text is too large to extract")
        root = ElementTree.fromstring(zf.read("word/document.xml"))
    body = root.find(f"{_W}body")
    lines: list[str] = []
    for block in (body if body is not None else []):
        if block.tag == f"{_W}p":
            text = "".join(t.text or "" for t in block.iter(f"{_W}t"))
            style = block.find(f"{_W}pPr/{_W}pStyle")
            level = style.get(f"{_W}val", "") if style is not None else ""
            if text and level.lower().startswith("heading"):
                digits = "".join(ch for ch in level if ch.isdigit()) or "1"
                text = "#" * min(int(digits), 6) + " " + text
            lines.append(text)
        elif block.tag == f"{_W}tbl":
            for row in block.iter(f"{_W}tr"):
                cells = ["".join(t.text or "" for t in cell.iter(f"{_W}t")) for cell in row.iter(f"{_W}tc")]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    return lines


def _pptx_lines(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        slides = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)),
        )
        if sum(zf.getinfo(n).file_size for n in slides) > MAX_XML_BYTES:
            raise ValueError("the slide text is too large to extract")
        lines: list[str] = []
        for i, name in enumerate(slides, 1):
            root = ElementTree.fromstring(zf.read(name))
            lines.append(f"## Slide {i}")
            for para in root.iter(f"{_A}p"):
                text = "".join(t.text or "" for t in para.iter(f"{_A}t")).strip()
                if text:
                    lines.append(text)
            notes = f"ppt/notesSlides/notesSlide{name.rsplit('slide', 1)[1]}"
            if notes in zf.namelist():
                note_root = ElementTree.fromstring(zf.read(notes))
                texts = [t.text for t in note_root.iter(f"{_A}t") if t.text and t.text.strip()]
                if texts:
                    lines.append("Speaker notes: " + " ".join(texts))
            lines.append("")
    return lines


def _notebook_lines(path: Path) -> list[str]:
    nb = json.loads(path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for i, cell in enumerate(nb.get("cells", []), 1):
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        lines.append(f"# %% [cell {i}: {cell.get('cell_type', '?')}]")
        lines.extend(source.splitlines())
        for out in cell.get("outputs", [])[:3]:
            text = out.get("text") or out.get("data", {}).get("text/plain")
            if text:
                text = "".join(text) if isinstance(text, list) else str(text)
                out_lines = text.splitlines()
                lines.extend(f"# out: {ln}" for ln in out_lines[:15])
                if len(out_lines) > 15:
                    lines.append(f"# out: … ({len(out_lines) - 15} more lines)")
        lines.append("")
    return lines


def _pdf_document(path: Path, rel: str) -> Document:
    try:
        from pypdf import PdfReader
    except ImportError:
        return Document(rel, "pdf", [], note="PDF text extraction needs the 'pypdf' package (pip install pypdf).")
    reader = PdfReader(str(path))
    lines: list[str] = []
    n_pages = len(reader.pages)
    for i, page in enumerate(reader.pages, 1):
        if i > MAX_PDF_PAGES:
            lines.append(f"## [Stopped after {MAX_PDF_PAGES} of {n_pages} pages]")
            break
        lines.append(f"## Page {i}")
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # malformed pages should not hide the rest of the document
            text = f"[page could not be read: {exc}]"
        lines.extend(ln.rstrip() for ln in text.splitlines())
    note = ""
    if not any(ln.strip() for ln in lines if not ln.startswith("## Page")):
        note = "No text layer found (scanned PDF?); text cannot be extracted without OCR."
    return Document(rel, "pdf", lines, note=note)


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else f"{value:.10g}"
    if hasattr(value, "isoformat"):
        text = value.isoformat()
        return text[:-9] if text.endswith("T00:00:00") else text
    return " / ".join(part.strip() for part in str(value).strip().splitlines() if part.strip())


def _row_lines(grid, label: str, budget: int) -> tuple[list[str], bool]:
    """One line per non-empty row, `[label rN] Column: value | ...`; N is the spreadsheet row number."""
    values = grid.to_numpy(dtype=object)
    header_idx = next((i for i, row in enumerate(values) if any(_cell(v) for v in row)), None)
    if header_idx is None:
        return [], False
    header = [_cell(v) or _column_letter(j) for j, v in enumerate(values[header_idx])]
    lines = [f"[{label}r{header_idx + 1}] (header) " + " | ".join(header)]
    for i in range(header_idx + 1, len(values)):
        if len(lines) >= budget:
            return lines, True
        parts = [f"{header[j]}: {text}" for j, v in enumerate(values[i]) if (text := _cell(v))]
        if parts:
            lines.append(f"[{label}r{i + 1}] " + " | ".join(parts))
    return lines, False


def _data_document(path: Path, rel: str) -> Document:
    """Spreadsheet as text: a short data profile, then one line per row (searchable, citable)."""
    from .data import DataLoadError, describe_for_llm, list_sheets, load_dataset, read_raw_grid

    raw = path.read_bytes()
    try:
        sheets = list_sheets(raw, path.name)
    except DataLoadError:
        sheets = []
    targets = sheets or [None]

    lines: list[str] = ["## Data profile"]
    for sheet in targets[:5]:
        try:
            ds = load_dataset(raw, path.name, sheet=sheet)
        except DataLoadError as exc:
            lines.append(f"Could not load{f' sheet {sheet!r}' if sheet else ''}: {exc}")
            continue
        lines.extend(describe_for_llm(ds, sample_rows=3).splitlines())
        lines.append("")
    if len(targets) > 5:
        lines.append(f"... profiles of {len(targets) - 5} more sheets omitted: {', '.join(targets[5:])}")

    lines.append("## Rows (one line per spreadsheet row; rN is the row number as shown in Excel)")
    truncated_at = None
    for sheet in targets:
        budget = MAX_ROW_LINES - len(lines)
        if budget <= 0:
            truncated_at = sheet
            break
        try:
            grid = read_raw_grid(raw, path.name, sheet=sheet)
        except Exception as exc:  # one unreadable sheet should not hide the others
            lines.append(f"[{sheet}] could not be read: {exc}")
            continue
        label = f"{sheet} " if sheet is not None else ""
        rows, cut = _row_lines(grid, label, budget)
        lines.extend(rows)
        if cut:
            truncated_at = sheet or "file"
            break

    note = ("Spreadsheet: a data profile, then one line per row labelled [sheet rN]. search finds text in "
            "any cell. To count, filter or aggregate many rows, use Python with load_table(path, sheet=...).")
    if sheets:
        note += f" Sheets: {', '.join(sheets)}."
    if truncated_at:
        note += f" Row view stopped at {MAX_ROW_LINES:,} lines (in {truncated_at}); use Python for the rest."
    return Document(rel, "data", lines, note=note)


def is_local_url(url: str) -> bool:
    host = re.sub(r"^\w+://", "", url).split("/")[0].split(":")[0].lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]") or host.endswith(".local")


def posix(rel: str) -> str:
    return PurePosixPath(rel.replace("\\", "/")).as_posix()


__all__ = ["Document", "Workspace", "WorkspaceError", "extract_document", "is_local_url", "posix"]
