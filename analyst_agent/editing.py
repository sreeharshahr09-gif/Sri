"""Proposed file changes: stage in memory, review as diffs, apply with backups, undo.

Nothing here writes to the workspace except `apply_change` and `undo_change`, which the UI
calls only when the user clicks Apply / Undo. Safety properties:

- Edits are staged against an in-memory copy; the agent's later reads see the proposal.
- Apply refuses if the file changed on disk since the proposal (hash check), keeps a backup of
  the original outside the workspace, and writes atomically (temp file + rename), preserving
  encoding, BOM, line endings and permissions.
- Undo refuses if the file changed since it was applied.
- Every apply/undo is appended to a JSON-lines journal next to the backups.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .workspace import IGNORED_DIRS, Workspace, WorkspaceError

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

MAX_EDIT_BYTES = 2 * 1024 * 1024
NOT_EDITABLE = frozenset(
    {".docx", ".doc", ".pptx", ".ppt", ".pdf", ".xlsx", ".xlsm", ".xls", ".parquet", ".ipynb", ".zip",
     ".png", ".jpg", ".jpeg", ".gif", ".mat", ".mdf", ".mf4", ".h5", ".hdf5", ".pkl", ".exe", ".dll"}
)
_LINE_PREFIX = re.compile(r"^\s*\d+\| ?")


class EditError(ValueError):
    """A proposal or apply/undo request that cannot be carried out (explained for the user/model)."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TextFile:
    text: str  # with "\n" line endings
    encoding: str
    bom: bool
    newline: str
    sha256: str

    def encode(self, text: str) -> bytes:
        data = text.replace("\n", self.newline).encode(self.encoding)
        return (b"\xef\xbb\xbf" + data) if self.bom else data


def decode_text(raw: bytes) -> TextFile:
    if b"\x00" in raw[:8192]:
        raise EditError("binary file")
    bom = raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:] if bom else raw
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            text = body.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    crlf = text.count("\r\n")
    newline = "\r\n" if crlf and crlf >= text.count("\n") - crlf else "\n"
    return TextFile(text.replace("\r\n", "\n"), encoding, bom, newline, _sha(raw))


def editable_path(ws: Workspace, rel: str) -> tuple[Path, str]:
    """Resolve a path the agent may propose changes to, or raise EditError."""
    try:
        path = ws.resolve(rel)
    except WorkspaceError as exc:
        raise EditError(str(exc)) from exc
    key = ws.relative(path)
    if key == ".":
        raise EditError("a file path is required.")
    if ws.is_secret(path.name):
        raise EditError(f"'{key}' looks like a credentials file and cannot be edited.")
    if any(part in IGNORED_DIRS for part in Path(key).parts):
        raise EditError(f"'{key}' is inside an ignored folder (such as .git) and cannot be edited.")
    if path.suffix.lower() in NOT_EDITABLE:
        raise EditError(
            f"'{key}' is a {path.suffix} file; only plain-text files (code, Markdown, CSV, config) can be edited."
        )
    if path.exists() and path.is_dir():
        raise EditError(f"'{key}' is a folder.")
    return path, key


# --------------------------------------------------------------------------- staged changes


@dataclass
class FileChange:
    path: str
    kind: str  # "edit" or "create"
    original: str | None
    proposed: str
    base_sha: str | None  # sha256 of the bytes on disk when proposed (None for create)
    encoding: str = "utf-8"
    bom: bool = False
    newline: str = "\n"
    status: str = "pending"  # pending | applied | rejected | undone | conflict
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backup_path: str | None = None
    applied_sha: str | None = None
    applied_at: str | None = None
    error: str | None = None

    def diff(self, context: int = 3) -> str:
        before = (self.original or "").splitlines(keepends=True)
        after = self.proposed.splitlines(keepends=True)
        lines = difflib.unified_diff(
            before, after, fromfile="/dev/null" if self.kind == "create" else f"a/{self.path}",
            tofile=f"b/{self.path}", n=context,
        )
        return "".join(ln if ln.endswith("\n") else ln + "\n\\ No newline at end of file\n" for ln in lines)

    def stats(self) -> tuple[int, int]:
        added = removed = 0
        for line in self.diff(context=0).splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return added, removed

    def encoded(self) -> bytes:
        return TextFile("", self.encoding, self.bom, self.newline, "").encode(self.proposed)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "FileChange":
        return cls(**data)


def _validate(path: str, text: str) -> list[str]:
    """Cheap syntax checks so broken code/config is flagged before anyone applies it."""
    ext = Path(path).suffix.lower()
    try:
        if ext == ".py":
            compile(text, path, "exec", dont_inherit=True)
        elif ext == ".json":
            json.loads(text)
        elif ext == ".toml" and tomllib is not None:
            tomllib.loads(text)
    except SyntaxError as exc:
        return [f"Python syntax error at line {exc.lineno}: {exc.msg}"]
    except ValueError as exc:  # json.JSONDecodeError and tomllib.TOMLDecodeError subclass it
        return [f"{ext[1:].upper()} parse error: {exc}"]
    return []


def _strip_line_prefixes(old: str, new: str) -> tuple[str, str]:
    """Remove `12| ` prefixes copied from read_file output (a common model mistake)."""
    lines = [ln for ln in old.split("\n") if ln.strip()]
    if lines and all(_LINE_PREFIX.match(ln) for ln in lines):
        old = "\n".join(_LINE_PREFIX.sub("", ln, count=1) for ln in old.split("\n"))
        if all(_LINE_PREFIX.match(ln) for ln in new.split("\n") if ln.strip()):
            new = "\n".join(_LINE_PREFIX.sub("", ln, count=1) for ln in new.split("\n"))
    return old, new


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _not_found_hint(text: str, old: str) -> str:
    collapse = lambda s: re.sub(r"[ \t]+", " ", s.strip())  # noqa: E731
    if collapse(old) and collapse(old) in collapse(text):
        return " The text exists but with different spaces/indentation: copy it exactly from read_file."
    first = next((ln.strip() for ln in old.split("\n") if ln.strip()), "")
    lines = text.split("\n")
    candidates = difflib.get_close_matches(first, [ln.strip() for ln in lines], n=3, cutoff=0.6)
    if not candidates:
        return " Read the file again and copy the exact lines to replace."
    shown = []
    for cand in candidates:
        idx = next(i for i, ln in enumerate(lines) if ln.strip() == cand)
        shown.append(f"line {idx + 1}: {lines[idx].strip()[:120]}")
    return " Closest lines in the file: " + "; ".join(shown) + "."


class ChangeSet:
    """Changes proposed during one agent run, keyed by relative path."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.changes: dict[str, FileChange] = {}
        # Shared with Workspace.overlay so reads during the run see the proposals.
        self.overlay: dict[str, str] = {}

    def _current(self, rel: str) -> tuple[FileChange | None, str, Path]:
        path, key = editable_path(self.ws, rel)
        return self.changes.get(key), key, path

    def stage_edit(self, rel: str, old: str, new: str, replace_all: bool = False) -> tuple[FileChange, str, tuple[int, int]]:
        change, key, path = self._current(rel)
        if change is None:
            if not path.exists():
                raise EditError(f"'{key}' does not exist. Use a create block for new files.")
            raw = path.read_bytes()
            if len(raw) > MAX_EDIT_BYTES:
                raise EditError(f"'{key}' is too large to edit here ({len(raw) // 1024} KB).")
            try:
                tf = decode_text(raw)
            except EditError as exc:
                raise EditError(f"'{key}' is a {exc} and cannot be edited.") from None
            change = FileChange(key, "edit", tf.text, tf.text, tf.sha256, tf.encoding, tf.bom, tf.newline)
        text = change.proposed

        old, new = _strip_line_prefixes(old.replace("\r\n", "\n"), new.replace("\r\n", "\n"))
        if not old:
            raise EditError("the OLD section is empty; include the existing lines to replace (or insert after).")
        if old == new:
            raise EditError("OLD and NEW are identical; nothing to change.")
        count = text.count(old)
        if count == 0:
            raise EditError(f"the OLD text was not found in '{key}'.{_not_found_hint(text, old)}")
        if count > 1 and not replace_all:
            lines: list[str] = []
            start = 0
            for _ in range(count):
                idx = text.index(old, start)
                line = str(_line_of(text, idx))
                if line not in lines:
                    lines.append(line)
                start = idx + 1
            lines = lines[:6]
            raise EditError(
                f"the OLD text appears {count} times in '{key}' (line{'s' if len(lines) > 1 else ''} {', '.join(lines)}). Include more "
                "surrounding lines so it is unique, or set replace_all."
            )
        first_idx = text.index(old)
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        try:
            TextFile("", change.encoding, change.bom, change.newline, "").encode(updated)
        except UnicodeEncodeError as exc:
            raise EditError(
                f"the new text has characters that cannot be saved in this file's encoding ({change.encoding}): {exc.object[exc.start:exc.end]!r}."
            ) from None

        change.proposed = updated
        change.status = "pending"
        change.notes.append(f"replaced {count if replace_all else 1} occurrence(s) near line {_line_of(text, first_idx)}")
        change.warnings = _validate(key, updated)
        self.changes[key] = change
        self.overlay[key] = updated
        start_line = _line_of(updated, first_idx)
        end_line = start_line + max(new.count("\n"), 0)
        return change, key, (start_line, end_line)

    def stage_create(self, rel: str, content: str) -> tuple[FileChange, str, tuple[int, int]]:
        change, key, path = self._current(rel)
        if path.exists() or (change is not None and change.kind == "edit"):
            raise EditError(f"'{key}' already exists; read it and edit it instead.")
        content = content.replace("\r\n", "\n")
        if content and not content.endswith("\n"):
            content += "\n"
        if len(content.encode("utf-8")) > MAX_EDIT_BYTES:
            raise EditError("the new file is too large.")
        change = FileChange(key, "create", None, content, None)
        change.notes.append("new file")
        change.warnings = _validate(key, content)
        self.changes[key] = change
        self.overlay[key] = content
        return change, key, (1, max(1, content.count("\n")))


# --------------------------------------------------------------------------- apply / undo


class BackupStore:
    """Backups and journal for one workspace, kept outside it (default ~/.research_agent/backups)."""

    def __init__(self, ws: Workspace, base: str | os.PathLike | None = None):
        base = Path(base or os.environ.get("AGENT_BACKUP_DIR") or Path.home() / ".research_agent" / "backups")
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", ws.root.name or "root")[:40]
        self.dir = base.expanduser() / f"{slug}-{_sha(str(ws.root).encode())[:12]}"
        self.journal = self.dir / "journal.jsonl"

    def save(self, rel: str, data: bytes) -> Path:
        target = self.dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def log(self, **entry) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": _now(), **entry}) + "\n")

    def history(self, limit: int = 50) -> list[dict]:
        if not self.journal.exists():
            return []
        lines = self.journal.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def _atomic_write(path: Path, data: bytes, mode_from: Path | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.agent-tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_bytes(data)
        if mode_from is not None and mode_from.exists():
            shutil.copymode(mode_from, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def apply_change(ws: Workspace, change: FileChange, backups: BackupStore) -> None:
    """Write an approved change to disk (with backup). Raises EditError and sets status on failure."""
    path, key = editable_path(ws, change.path)
    if change.status == "applied":
        raise EditError(f"'{key}' is already applied.")
    if change.kind == "create":
        if path.exists():
            change.status, change.error = "conflict", f"'{key}' now exists on disk; the proposal is out of date."
            raise EditError(change.error)
        backup = None
    else:
        if not path.exists():
            change.status, change.error = "conflict", f"'{key}' no longer exists on disk."
            raise EditError(change.error)
        current = path.read_bytes()
        if _sha(current) != change.base_sha:
            change.status = "conflict"
            change.error = f"'{key}' changed on disk after this was proposed. Ask again to get a fresh proposal."
            raise EditError(change.error)
        backup = backups.save(key, current)
    data = change.encoded()
    _atomic_write(path, data, mode_from=path if path.exists() else None)
    change.status, change.error = "applied", None
    change.backup_path = str(backup) if backup else None
    change.applied_sha = _sha(data)
    change.applied_at = _now()
    added, removed = change.stats()
    backups.log(action="apply", path=key, kind=change.kind, added=added, removed=removed,
                backup=change.backup_path, sha_before=change.base_sha, sha_after=change.applied_sha)


def undo_change(ws: Workspace, change: FileChange, backups: BackupStore) -> None:
    """Restore the file as it was before `apply_change`. Refuses if it was modified since."""
    path, key = editable_path(ws, change.path)
    if change.status != "applied":
        raise EditError(f"'{key}' has not been applied, so there is nothing to undo.")
    if not path.exists() or _sha(path.read_bytes()) != change.applied_sha:
        raise EditError(f"'{key}' was modified after the change was applied; undo would overwrite those edits.")
    if change.kind == "create":
        path.unlink()
    else:
        if not change.backup_path or not Path(change.backup_path).exists():
            raise EditError(f"The backup for '{key}' is missing; cannot undo.")
        _atomic_write(path, Path(change.backup_path).read_bytes(), mode_from=path)
    change.status = "undone"
    backups.log(action="undo", path=key, kind=change.kind, backup=change.backup_path)
