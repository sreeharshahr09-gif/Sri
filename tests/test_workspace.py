import json
import os
import zipfile

import pytest

from analyst_agent.workspace import Workspace, WorkspaceError, is_local_url

FIT_PY = '''"""Fit the Pacejka Magic Formula to longitudinal force data."""
import numpy as np


def magic_formula(slip, B, C, D, E):
    """Longitudinal force Fx as a function of slip ratio."""
    x = B * slip
    return D * np.sin(C * np.arctan(x - E * (x - np.arctan(x))))


def peak_mu(fx, fz):
    return np.max(np.abs(fx)) / fz
'''


def _docx(path, paragraphs, table=None):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = ""
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    if table:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
            for row in table
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')


def _pptx(path, slides):
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    with zipfile.ZipFile(path, "w") as zf:
        for i, texts in enumerate(slides, 1):
            paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
            zf.writestr(f"ppt/slides/slide{i}.xml", f'<p:sld xmlns:p="x" xmlns:a="{a}"><p:cSld>{paras}</p:cSld></p:sld>')


def _pdf(path, text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


@pytest.fixture
def ws_dir(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "fit.py").write_text(FIT_PY)
    (tmp_path / "README.md").write_text("# Tyre study\n\nWe fit the magic formula to rig data.\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tyres.csv").write_text("size,mu\nA,1.1\nB,1.05\nA,1.08\n")
    _docx(tmp_path / "report.docx", [("Heading1", "Results"), ("", "Peak mu was 1.10 for size A.")],
          table=[["Size", "Mu"], ["A", "1.10"]])
    _pptx(tmp_path / "talk.pptx", [["Intro", "Why tyres matter"], ["Method", "Magic formula fit"]])
    nb = {"cells": [{"cell_type": "markdown", "source": ["# Analysis"]},
                    {"cell_type": "code", "source": "print(42)", "outputs": [{"text": ["42\n"]}]}]}
    (tmp_path / "analysis.ipynb").write_text(json.dumps(nb))
    _pdf(tmp_path / "paper.pdf", "Slip stiffness increases with load")
    (tmp_path / ".env").write_text("API_KEY=supersecret\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("magic formula in git internals\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02magic formula")
    return tmp_path


@pytest.fixture
def ws(ws_dir):
    return Workspace(ws_dir)


def test_root_must_be_an_existing_folder(tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "missing")
    (tmp_path / "f.txt").write_text("x")
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "f.txt")


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "src/../../x"])
def test_paths_cannot_escape_the_root(ws, path):
    with pytest.raises(WorkspaceError, match="outside"):
        ws.read(path)


def test_symlinks_cannot_escape_the_root(ws, ws_dir, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("do not read")
    os.symlink(outside, ws_dir / "link.txt")
    with pytest.raises(WorkspaceError, match="outside"):
        ws.read("link.txt")
    assert "link.txt" not in [p.name for p in ws.iter_files()]


def test_secrets_and_ignored_folders_are_never_read(ws):
    with pytest.raises(WorkspaceError, match="credentials"):
        ws.read(".env")
    with pytest.raises(WorkspaceError, match="ignored"):
        ws.read(".git/config")
    listing = ws.list_files(".", depth=3)
    assert ".env" not in listing and ".git" not in listing
    text, hits = ws.search("supersecret")
    assert hits == []


def test_list_files_and_overview(ws):
    listing = ws.list_files(".", depth=2)
    assert "src/ (1 items)" in listing and "  fit.py" in listing.replace("    ", "  ")
    overview = ws.overview()
    assert "files" in overview and ".py ×1" in overview


def test_read_numbers_lines_and_respects_ranges(ws):
    text, doc, start, end = ws.read("src/fit.py", start=5, end=8)
    assert (start, end) == (5, 8)
    assert " 5| def magic_formula(slip, B, C, D, E):" in text
    assert "more lines; read_file with start=9" in text
    with pytest.raises(WorkspaceError, match="only"):
        ws.read("src/fit.py", start=999)
    with pytest.raises(WorkspaceError, match="No such file"):
        ws.read("src/nope.py")


def test_long_files_are_read_in_chunks(ws, ws_dir):
    (ws_dir / "long.txt").write_text("\n".join(f"line {i}" for i in range(1, 1001)))
    text, _, start, end = ws.read("long.txt")
    assert start == 1 and end == 250 and "start=251" in text


def test_search_literal_regex_and_glob(ws):
    text, hits = ws.search("magic formula")
    files = {p for p, _ in hits}
    assert {"README.md", "src/fit.py", "talk.pptx"} <= files
    assert "blob.bin" not in files and ".git/config" not in files
    _, hits = ws.search(r"def \w+\(", regex=True, glob="*.py")
    assert [line for _, line in hits] == [5, 11]
    with pytest.raises(WorkspaceError, match="regular expression"):
        ws.search("(", regex=True)


def test_rich_documents_become_text(ws):
    word = ws.document("report.docx")
    assert word.kind == "word" and word.lines[:2] == ["# Results", "Peak mu was 1.10 for size A."]
    assert "| A | 1.10 |" in word.lines
    slides = ws.document("talk.pptx")
    assert slides.lines[:3] == ["## Slide 1", "Intro", "Why tyres matter"]
    nb = ws.document("analysis.ipynb")
    assert "# %% [cell 2: code]" in nb.lines and "# out: 42" in nb.lines
    data = ws.document("data/tyres.csv")
    assert data.kind == "text"  # small CSVs are read as text


def test_pdf_text_extraction(ws):
    pytest.importorskip("pypdf")
    pdf = ws.document("paper.pdf")
    assert pdf.kind == "pdf" and "Slip stiffness increases with load" in "\n".join(pdf.lines)


def test_excel_is_profiled(ws, ws_dir):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    with pd.ExcelWriter(ws_dir / "book.xlsx") as writer:
        pd.DataFrame({"load": [4, 5]}).to_excel(writer, sheet_name="rig", index=False)
    doc = ws.document("book.xlsx")
    assert doc.kind == "data" and "load" in "\n".join(doc.lines) and "Sheets: rig" in doc.note


def test_binary_files_are_not_dumped(ws):
    doc = ws.document("blob.bin")
    assert doc.kind == "binary" and doc.lines == []


def test_document_cache_refreshes_on_change(ws, ws_dir):
    assert ws.document("README.md").lines[0] == "# Tyre study"
    path = ws_dir / "README.md"
    path.write_text("# Changed\n")  # same second is fine: the cache keys on mtime_ns and size
    assert ws.document("README.md").lines[0] == "# Changed"


def test_is_local_url():
    assert is_local_url("http://localhost:8080") and is_local_url("http://127.0.0.1:8080/v1")
    assert not is_local_url("https://api.example.com/v1")
