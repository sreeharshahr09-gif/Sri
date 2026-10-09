import json
import os
import stat
import sys

import pytest

from analyst_agent.editing import BackupStore, ChangeSet, EditError, apply_change, decode_text, undo_change
from analyst_agent.workspace import Workspace

from .test_workspace import FIT_PY


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "fit.py").write_text(FIT_PY)
    (root / "notes.md").write_bytes(b"# Notes\r\nline two\r\n")  # CRLF
    (root / "legacy.txt").write_bytes("Gr\xfc\xdfe\n".encode("cp1252"))  # cp1252
    (root / "bom.csv").write_bytes(b"\xef\xbb\xbfa,b\n1,2\n")  # UTF-8 with BOM
    (root / ".env").write_text("KEY=1\n")
    (root / "report.docx").write_bytes(b"PK\x03\x04")
    return Workspace(root)


@pytest.fixture
def backups(ws, tmp_path):
    return BackupStore(ws, base=tmp_path / "backups")


def test_decode_text_detects_encoding_bom_and_newlines():
    tf = decode_text(b"\xef\xbb\xbfa\r\nb\r\n")
    assert (tf.text, tf.encoding, tf.bom, tf.newline) == ("a\nb\n", "utf-8", True, "\r\n")
    assert tf.encode(tf.text) == b"\xef\xbb\xbfa\r\nb\r\n"
    tf = decode_text("Gr\xfc\xdfe\n".encode("cp1252"))
    assert tf.encoding == "cp1252" and tf.text == "Grüße\n"
    with pytest.raises(EditError):
        decode_text(b"\x00\x01binary")


def test_stage_edit_is_in_memory_only(ws):
    before = (ws.root / "src" / "fit.py").read_text()
    cs = ChangeSet(ws)
    change, key, (lo, hi) = cs.stage_edit("src/fit.py", "    x = B * slip", "    x = B * np.asarray(slip)")
    assert key == "src/fit.py" and lo == 7 and change.status == "pending"
    assert (ws.root / "src" / "fit.py").read_text() == before  # nothing written
    assert "+    x = B * np.asarray(slip)" in change.diff() and change.stats() == (1, 1)
    assert cs.overlay["src/fit.py"] == change.proposed


def test_successive_edits_accumulate_on_the_proposal(ws):
    cs = ChangeSet(ws)
    cs.stage_edit("src/fit.py", "    x = B * slip", "    x = B * np.asarray(slip)")
    change, _, _ = cs.stage_edit("src/fit.py", "def peak_mu(fx, fz):", "def peak_mu(fx, fz):\n    \"\"\"Peak friction.\"\"\"")
    assert "np.asarray" in change.proposed and "Peak friction" in change.proposed
    assert len(change.notes) == 2 and change.stats() == (2, 1)


def test_line_number_prefixes_are_stripped(ws):
    cs = ChangeSet(ws)
    change, _, _ = cs.stage_edit("src/fit.py", " 7|     x = B * slip", " 7|     x = 2 * B * slip")
    assert "    x = 2 * B * slip" in change.proposed and "7|" not in change.proposed


def test_not_found_and_ambiguous_edits_explain_themselves(ws):
    cs = ChangeSet(ws)
    with pytest.raises(EditError, match="different spaces"):
        cs.stage_edit("src/fit.py", "x = B *   slip", "x = 1")
    with pytest.raises(EditError, match="Closest lines"):
        cs.stage_edit("src/fit.py", "def peak_muu(fx, fz):", "x")
    with pytest.raises(EditError, match=r"appears 2 times in 'src/fit.py' \(line 8\)"):
        cs.stage_edit("src/fit.py", "np.arctan", "np.atan")
    change, _, _ = cs.stage_edit("src/fit.py", "np.arctan", "np.atan", replace_all=True)
    assert change.proposed.count("np.atan(") == 2
    with pytest.raises(EditError, match="identical"):
        cs.stage_edit("src/fit.py", "import numpy", "import numpy")


@pytest.mark.parametrize(
    "path,match",
    [(".env", "credentials"), ("report.docx", "only plain-text"), ("../x.py", "outside"), ("missing.py", "does not exist")],
)
def test_refused_targets(ws, path, match):
    with pytest.raises(EditError, match=match):
        ChangeSet(ws).stage_edit(path, "a", "b")


def test_syntax_errors_are_flagged(ws):
    change, _, _ = ChangeSet(ws).stage_edit("src/fit.py", "def peak_mu(fx, fz):", "def peak_mu(fx, fz)")
    assert change.warnings and "syntax error" in change.warnings[0]
    change, _, _ = ChangeSet(ws).stage_create("cfg.json", '{"a": 1,}')
    assert "JSON" in change.warnings[0]


def test_create_refuses_existing_files(ws):
    cs = ChangeSet(ws)
    with pytest.raises(EditError, match="already exists"):
        cs.stage_create("notes.md", "x")
    change, key, _ = cs.stage_create("docs/new.md", "# New")
    assert change.kind == "create" and change.proposed == "# New\n" and key == "docs/new.md"


def test_unencodable_text_is_rejected_for_legacy_files(ws):
    with pytest.raises(EditError, match="cp1252"):
        ChangeSet(ws).stage_edit("legacy.txt", "Grüße", "Grüße 👋")


def test_apply_writes_atomically_with_backup_and_journal(ws, backups):
    path = ws.root / "src" / "fit.py"
    if sys.platform != "win32":
        path.chmod(0o750)
    original = path.read_bytes()
    change, _, _ = ChangeSet(ws).stage_edit("src/fit.py", "    x = B * slip", "    x = B * np.asarray(slip)")
    apply_change(ws, change, backups)
    assert change.status == "applied" and "np.asarray(slip)" in path.read_text()
    assert open(change.backup_path, "rb").read() == original
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o750
    assert not [p for p in path.parent.iterdir() if "agent-tmp" in p.name]
    entry = backups.history()[0]
    assert entry["action"] == "apply" and entry["path"] == "src/fit.py" and entry["added"] == 1
    assert not str(backups.dir).startswith(str(ws.root))  # backups live outside the workspace


def test_encoding_and_line_endings_are_preserved(ws, backups):
    cs = ChangeSet(ws)
    for path, old, new in [("notes.md", "line two", "line 2"), ("legacy.txt", "Grüße", "Grüße!"), ("bom.csv", "1,2", "1,3")]:
        change, _, _ = cs.stage_edit(path, old, new)
        apply_change(ws, change, backups)
    assert (ws.root / "notes.md").read_bytes() == b"# Notes\r\nline 2\r\n"
    assert (ws.root / "legacy.txt").read_bytes() == "Grüße!\n".encode("cp1252")
    assert (ws.root / "bom.csv").read_bytes() == b"\xef\xbb\xbfa,b\n1,3\n"


def test_apply_refuses_when_file_changed_since_proposal(ws, backups):
    change, _, _ = ChangeSet(ws).stage_edit("notes.md", "line two", "line 2")
    (ws.root / "notes.md").write_text("someone else edited this\n")
    with pytest.raises(EditError, match="changed on disk"):
        apply_change(ws, change, backups)
    assert change.status == "conflict"
    assert (ws.root / "notes.md").read_text() == "someone else edited this\n"


def test_undo_restores_and_can_reapply(ws, backups):
    path = ws.root / "src" / "fit.py"
    original = path.read_bytes()
    change, _, _ = ChangeSet(ws).stage_edit("src/fit.py", "    x = B * slip", "    x = 0")
    apply_change(ws, change, backups)
    undo_change(ws, change, backups)
    assert path.read_bytes() == original and change.status == "undone"
    apply_change(ws, change, backups)  # re-apply after undo
    assert "    x = 0" in path.read_text()
    assert [e["action"] for e in backups.history()] == ["apply", "undo", "apply"]


def test_undo_refuses_after_later_manual_edits(ws, backups):
    change, _, _ = ChangeSet(ws).stage_edit("notes.md", "line two", "line 2")
    apply_change(ws, change, backups)
    (ws.root / "notes.md").write_text("edited by hand afterwards\n")
    with pytest.raises(EditError, match="modified after"):
        undo_change(ws, change, backups)
    assert (ws.root / "notes.md").read_text() == "edited by hand afterwards\n"


def test_create_apply_and_undo(ws, backups):
    change, _, _ = ChangeSet(ws).stage_create("docs/summary.md", "# Summary\n")
    apply_change(ws, change, backups)
    assert (ws.root / "docs" / "summary.md").read_text() == "# Summary\n"
    undo_change(ws, change, backups)
    assert not (ws.root / "docs" / "summary.md").exists()


def test_create_conflicts_if_file_appeared(ws, backups):
    change, _, _ = ChangeSet(ws).stage_create("new.md", "x")
    (ws.root / "new.md").write_text("made by hand")
    with pytest.raises(EditError, match="now exists"):
        apply_change(ws, change, backups)
    assert (ws.root / "new.md").read_text() == "made by hand"


def test_change_round_trips_through_json(ws):
    from analyst_agent.editing import FileChange

    change, _, _ = ChangeSet(ws).stage_edit("notes.md", "line two", "line 2")
    restored = FileChange.from_dict(json.loads(json.dumps(change.to_dict())))
    assert restored.diff() == change.diff() and restored.encoded() == change.encoded()


def test_workspace_overlay_shows_proposals_only_while_set(ws):
    cs = ChangeSet(ws)
    cs.stage_edit("notes.md", "line two", "line 2")
    ws.overlay = cs.overlay
    assert ws.document("notes.md").lines == ["# Notes", "line 2"]
    ws.overlay = {}
    assert ws.document("notes.md").lines == ["# Notes", "line two"]
    assert os.path.exists(ws.root / "notes.md")
