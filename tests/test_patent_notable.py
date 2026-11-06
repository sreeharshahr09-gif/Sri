"""Notable patents: value and novelty, the cards, the PDFs (links, Excel hyperlinks, lookup by number) and the
figures found in them."""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from analyst_agent.data import load_dataset
from analyst_agent.patents import enrich, figures
from analyst_agent.patents import notable as nb
from analyst_agent.patents.data import build_corpus, detect_columns
from analyst_agent.patents.html_report import report_html
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan
from analyst_agent.patents.presets import preset_plan
from analyst_agent.patents.workbook import report_workbook

from . import patent_pdfs as pp
from .patent_fixtures import make_export
from .test_patent_pipeline import FakeLLM

pytest.importorskip("pypdfium2")


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(figures, "PDF_DIR", tmp_path / "pdf")


# --------------------------------------------------------------------------- figures in a PDF


def test_front_page_figure_then_another_drawing():
    found = figures.find_figures(pp.us_application())
    assert [f.label for f in found] == ["Representative figure (front page)", "Drawing sheet, page 3"]
    assert all(f.png.startswith(b"\x89PNG") and len(f.png) < 120_000 for f in found)
    # FIG. 1 on page 2 repeats the front-page figure, so the second figure is the other drawing.


def test_drawings_at_the_end_and_no_drawings():
    assert [f.label for f in figures.find_figures(pp.ep_application())] == ["Drawing sheet, page 5",
                                                                             "Drawing sheet, page 6"]
    assert [f.label for f in figures.find_figures(pp.us_application(front_figure=False), limit=1)] == [
        "Drawing sheet, page 2"]
    assert figures.find_figures(pp.text_only()) == []  # text and a ruled table: no drawing
    assert [f.label for f in figures.find_figures(pp.vector_pdf())] == ["Drawing sheet, page 2"]


def test_image_only_cn_jp_kr_layout():
    """Scanned pages with dense square characters: the abstract drawing at the bottom of the front page, then the
    drawings after the description."""
    assert [f.label for f in figures.find_figures(pp.cn_publication())] == ["Representative figure (front page)",
                                                                             "Drawing sheet, page 6"]


def test_a_table_is_not_a_drawing():
    import random

    gray = np.asarray(pp.table_page(random.Random(1)).resize((695, 900)))
    layout = figures.page_layout(gray.copy())
    assert not layout.is_drawing_sheet and not layout.has_figure
    sheet = figures.page_layout(np.asarray(pp.drawing_sheet(random.Random(1), 1).resize((695, 900))).copy())
    assert sheet.is_drawing_sheet


# --------------------------------------------------------------------------- fetching the PDF


class _Server:
    """A local web server: /doc.pdf is a PDF, /page/<number> a patent page naming its PDF, /text a page without."""

    def __init__(self, pdf: bytes):
        self.hits = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                if self.path.endswith(".pdf"):
                    body, kind = pdf, "application/pdf"
                elif self.path.startswith("/page/"):
                    body = (b'<html><head><meta name="citation_pdf_url" content="/files/'
                            + self.path.split("/")[-1].encode() + b'.pdf"></head></html>')
                    kind = "text/html"
                else:
                    body, kind = b"<html>no pdf here</html>", "text/html"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    srv = _Server(pp.us_application())
    yield srv
    srv.close()


def test_pdf_from_the_link_the_page_or_the_number(server, tmp_path):
    direct = figures.fetch_pdf("US 2026/0307433 A1", f"{server.url}/doc.pdf", lookup="", cache_dir=tmp_path)
    assert direct.data.startswith(b"%PDF") and direct.source.endswith("/doc.pdf")
    assert (tmp_path / "US20260307433A1.pdf").is_file()  # cached under the normalised number
    again = figures.fetch_pdf("US20260307433A1", f"{server.url}/doc.pdf", lookup="", cache_dir=tmp_path)
    assert again.source == "cache" and server.hits.count("/doc.pdf") == 1
    assert again.url == direct.url == f"{server.url}/doc.pdf"  # the address is kept with the cached PDF
    page = figures.fetch_pdf("EP1234567A1", f"{server.url}/page/EP1234567A1", lookup="", cache_dir=tmp_path)
    assert page.data.startswith(b"%PDF") and page.source.endswith("/files/EP1234567A1.pdf")
    # No usable link ("PDF Link" text only): looked up by number.
    looked = figures.fetch_pdf("CN107848341A", "PDF Link", lookup=server.url + "/page/{number}", cache_dir=tmp_path)
    assert looked.source.endswith("/files/CN107848341A.pdf")
    missing = figures.fetch_pdf("JP2019000001A", f"{server.url}/text", lookup="", cache_dir=tmp_path)
    assert missing.data is None and "no PDF found on the page" in missing.note
    nothing = figures.fetch_pdf("", "", lookup="", cache_dir=tmp_path)
    assert nothing.data is None and "no link" in nothing.note


def test_a_pdf_put_into_the_cache_is_used(tmp_path):
    (tmp_path / "KR20190012345A.pdf").write_bytes(pp.ep_application())
    result = figures.patent_figures("KR 10-2019-0012345 A".replace(" 10-", "").replace("-", ""), "",
                                    lookup="", cache_dir=tmp_path)
    assert result.source == "cache" and [f.label for f in result.figures][0] == "Drawing sheet, page 5"


def test_oversized_and_unreachable_pdfs_give_a_reason(server, tmp_path, monkeypatch):
    monkeypatch.setattr(figures, "MAX_PDF_BYTES", 1000)
    big = figures.fetch_pdf("US1A", f"{server.url}/doc.pdf", lookup="", cache_dir=tmp_path)
    assert big.data is None and "larger than" in big.note
    down = figures.fetch_pdf("US2A", "http://127.0.0.1:9/doc.pdf", lookup="", cache_dir=tmp_path, timeout=2)
    assert down.data is None and "127.0.0.1:9" in down.note


def test_excel_link_cells_give_their_addresses(tmp_path):
    import openpyxl

    book = openpyxl.Workbook()
    ws = book.active
    ws.append(["Publication Number", "Title", "Abstract", "PDF Link", "Document type"])
    for i in range(3):
        ws.append([f"US{i}A1", "Tyre", "A tyre.", "PDF Link", "Patent"])
    ws.cell(row=2, column=4).hyperlink = "https://example.org/US0A1.pdf"
    ws.cell(row=3, column=4).value = '=HYPERLINK("https://example.org/US1A1.pdf","PDF Link")'
    ws.cell(row=4, column=4).hyperlink = "https://example.org/US2A1.pdf"
    buf = io.BytesIO()
    book.save(buf)
    ds = load_dataset(buf.getvalue(), "export.xlsx")
    assert list(ds.df["PDF Link"]) == [f"https://example.org/US{i}A1.pdf" for i in range(3)]
    assert list(ds.df["Document type"]) == ["Patent"] * 3
    assert any("PDF Link (3)" in n for n in ds.notes)
    corpus = build_corpus(ds.df, detect_columns(list(ds.df.columns)))
    assert corpus.std["pdf_link"].str.startswith("https://").all()


def test_export_columns_of_a_consolidated_file():
    raw = make_export(30, seed=4)
    raw = raw.rename(columns={"Record Number": "Representative_Publication"})
    raw["Family_ID_Source"] = "extended"  # the same word in every row: no family ID
    raw["Family_Key"] = [f"E_{i}" for i in range(len(raw))]
    raw["PDF_Link"] = "PDF Link"
    mapping = detect_columns(list(raw.columns))
    assert mapping["id"] == "Representative_Publication" and mapping["family"] == "Family_Key"
    assert mapping["pdf_link"] == "PDF_Link"
    corpus = build_corpus(raw, mapping)
    assert (corpus.std["pdf_link"] == "").all()  # "PDF Link" alone is no address
    corpus = build_corpus(raw, {**mapping, "family": "Family_ID_Source"})
    assert len(corpus) > 1 and any("no family ID" in n for n in corpus.notes)


# --------------------------------------------------------------------------- value and novelty


def _frame():
    rows = []
    for i in range(40):
        rows.append({"id": f"P{i}", "title": f"Tyre {i}", "assignee": "A" if i % 2 else "B", "country": "EP",
                     "year": 2010 + i % 12, "first_claim": "claim " * (10 + i), "cited_by": np.nan,
                     "relevance": "High", "problem": "Uneven wear", "tech_group": "Tread geometry",
                     "core_claim": "A tire with grooves.", "key_parameters": "none", "alive": "Yes" if i % 3 else "No",
                     "design_lever": "groove depth", "performance_target": "even wear"})
    rows[7].update(problem="Noise", tech_group="Compound", key_parameters="tan d 0.10-0.20 at 60 °C")
    return pd.DataFrame(rows, index=range(100, 140))


def test_value_and_novelty_scores():
    a = _frame()
    std = a.assign(pdf_link="")
    emb = np.tile(np.array([1.0, 0.0, 0.0]), (len(a), 1)) + np.random.default_rng(0).normal(0, 0.01, (len(a), 3))
    emb[7] = [0.0, 1.0, 0.0]  # far from the rest
    scores = nb.score(a, std, emb)
    assert scores["novelty"].idxmax() == 107 and scores.loc[107, "combination"] == "Noise × Compound"
    assert scores.loc[107, "combination_patents"] == 1
    assert (scores.loc[a.index[a["alive"] == "Yes"], "value"].mean()
            > scores.loc[a.index[a["alive"] == "No"], "value"].mean())
    cards = nb.pick(a, std, emb, n=5)
    assert len(cards) == 5 and cards[0].id == "P7"
    first = cards[0]
    assert {"Core", "Top novelty"} <= set(first.badges)
    assert "rare combination: Noise × Compound (1 patent)" in first.reasons
    assert "few similar patents in this set" in first.reasons
    assert first.summary == "A tire with grooves. Claimed: tan d 0.10-0.20 at 60 °C."
    assert nb.pick(a, std, None, n=0) == []


def test_notes_from_the_model_are_checked():
    a = _frame()
    cards = nb.pick(a, a.assign(pdf_link=""), None, n=2)

    class Model:
        def chat(self, messages):
            from analyst_agent.llm import Completion

            return Completion(content=json.dumps([
                {"ref": "N1", "why": "Its tan d window of 0.10-0.20 targets noise.", "check": "Check the 60 °C test."},
                {"ref": "N2", "why": "Cuts wear by 35 % with deeper grooves.", "check": "Check the groove depth."}]),
                latency_s=0.0)

    note = nb.write_notes(cards, "Study", Model())
    assert cards[0].why == "Its tan d window of 0.10-0.20 targets noise." and cards[0].check == "Check the 60 °C test."
    assert cards[1].why == "Groove depth for even wear."  # "35 %" is not in the patent: the plain line instead
    assert note.startswith("Notes written for 1 of 2")
    plain = nb.pick(a, a.assign(pdf_link=""), None, n=1)
    nb.write_notes(plain, "Study", None)
    assert plain[0].why and plain[0].check.startswith("Check the claimed values")


# --------------------------------------------------------------------------- in the report


def test_cards_in_the_report_html_and_workbook(tmp_path, monkeypatch):
    pdfs = {"front": pp.us_application(), "none": pp.text_only()}

    def fake_figures(number, link="", session=None, limit=2, **kwargs):
        data = pdfs["none" if number.endswith("3") else "front"]
        out = figures.PatentPdf(number, data=data, source="cache", url="https://example.org/" + number + ".pdf")
        out.figures = figures.find_figures(data, limit)
        out.note = "" if out.figures else "no drawing found in the PDF"
        return out

    monkeypatch.setattr(figures, "patent_figures", fake_figures)
    plan = preset_plan("Uneven wear")
    plan.analysis_questions = []
    raw = make_export(80, seed=9)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                        options=PipelineOptions(embedding_model="tfidf", notable_count=6, fetch_figures=True))
    assert res.status == "complete" and len(res.notable) == 6
    assert all(c.why.endswith("pairs a design lever with a performance target.") for c in res.notable)
    titles = [b.title for b in res.blocks]
    at = titles.index("🏅 Notable patents")
    assert at < titles.index("Methodology and limitations")
    cards = res.blocks[at + 1]
    assert cards.kind == "cards" and len(cards.items) == 6
    with_figures = [i for i in cards.items if i["figures"]]
    assert with_figures and with_figures[0]["figures"][0][1] == "Representative figure (front page)"
    assert all(i["link"].startswith("https://example.org/") for i in cards.items)
    assert "[Notable patents]" in res.all_facts and "PDFs: 6 of 6 fetched" in res.methods["Notable patents"]
    page = next(b for b in res.blocks if b.kind == "download" and b.file_name.endswith(".html")).data.decode()
    assert page.count('class="card"') == 6 and 'src="data:image/png;base64,' in page and "Check:" in page
    book = next(b for b in res.blocks if b.kind == "download" and b.file_name.endswith(".xlsx")).data
    contents = pd.read_excel(io.BytesIO(book), skiprows=2)
    sheet = contents.loc[contents["Section"] == "🏅 Notable patents", "Sheet"].iat[0]
    assert len(pd.read_excel(io.BytesIO(book), sheet_name=sheet)) == 6


def test_without_fetching_the_cards_say_so():
    plan = AnalysisPlan(title="Uneven wear")
    raw = make_export(40, seed=2)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                        options=PipelineOptions(semantic_map=False, notable_count=3))
    assert len(res.notable) == 3 and all("not fetched" in c.figure_note for c in res.notable)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan,
                        options=PipelineOptions(semantic_map=False, notable_count=0))
    assert res.notable == [] and "🏅 Notable patents" not in [b.title for b in res.blocks]


def test_html_cards_escape_text_and_skip_bad_links():
    from analyst_agent.patents import report as rb

    item = {"id": "US1<b>", "link": "javascript:alert(1)", "title": "A & B", "badges": ["Core"], "summary": "x",
            "figures": [], "note": "no drawing found in the PDF"}
    page = report_html([rb.cards([item])], "T").decode()
    assert "US1&lt;b&gt;" in page and "javascript:" not in page and "no drawing found in the PDF" in page
    assert report_workbook([rb.heading("N"), rb.cards([item])], "T") is not None


# --------------------------------------------------------------------------- a vision model choosing the figures


class VisionModel:
    """Answers like a vision model: picks the given images in the given order, with descriptions."""

    def __init__(self, picks, fail: bool = False):
        self.picks, self.fail, self.calls = picks, fail, []

    def chat(self, messages):
        from analyst_agent.llm import Completion, LLMError

        self.calls.append(messages)
        if self.fail:
            raise LLMError("No endpoints found that support image input")
        return Completion(content=json.dumps({"figures": [{"image": k, "description": f"view {k} of the layer 12"}
                                                          for k in self.picks]}), latency_s=0.0)


def test_vision_model_picks_and_describes():
    model = VisionModel([2, 1])
    found = figures.find_figures(pp.us_application(), chooser=figures.vision_chooser(model, "Patent US1: Tyre."))
    assert [(f.label, f.description) for f in found] == [("Drawing sheet, page 3", "view 2 of the layer 12"),
                                                          ("Representative figure (front page)", "view 1 of the layer 12")]
    content = model.calls[0][1]["content"]
    images = [p for p in content if p["type"] == "image_url"]
    assert len(images) == 2 and images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[0]["text"].startswith("Patent US1: Tyre.") and "at most 2 images" in model.calls[0][0]["content"]


def test_vision_model_failing_or_finding_nothing():
    down = figures.find_figures(pp.us_application(), chooser=figures.vision_chooser(VisionModel([], fail=True), ""))
    assert [f.label for f in down] == ["Representative figure (front page)", "Drawing sheet, page 3"]
    assert all(f.description == "" for f in down)  # the page analysis decided alone
    none = figures.find_figures(pp.us_application(), chooser=figures.vision_chooser(VisionModel([]), ""))
    assert none == []  # the model looked and found no drawing
    # No drawing found by the page analysis: whole pages go to the model, which may still find one.
    model = VisionModel([3])
    pages = figures.find_figures(pp.text_only(), chooser=figures.vision_chooser(model, ""))
    assert [f.label for f in pages] == ["Page 3"] and len(
        [p for p in model.calls[0][1]["content"] if p["type"] == "image_url"]) == 3
    assert figures.find_figures(pp.text_only()) == []  # without the model: whole pages are never shown


def test_vision_client_uses_the_server_settings(monkeypatch):
    from analyst_agent import LLMClient, LLMConfig

    base = LLMClient(LLMConfig(base_url="https://openrouter.ai/api", model="text/model", api_key="k"))
    vision = nb.vision_client(base, "nvidia/nemotron-3.5-super-vl:free")
    assert vision.config.model == "nvidia/nemotron-3.5-super-vl:free" and vision.config.api_key == "k"
    monkeypatch.setenv("LLM_VISION_BASE_URL", "https://other.example/api")
    monkeypatch.setenv("LLM_VISION_API_KEY", "v")
    other = nb.vision_client(base, "vl")
    assert other.config.base_url == "https://other.example/api" and other.config.api_key == "v"
    assert nb.vision_client(base, " ") is None and nb.vision_client(None, "vl") is None


# --------------------------------------------------------------------------- top patents by technology group


def test_top_patents_of_chosen_groups(monkeypatch):
    from analyst_agent.patents.pipeline import group_gallery
    from analyst_agent.patents.report_blocks import gallery_blocks

    plan = preset_plan("Uneven wear")
    plan.analysis_questions = []
    raw = make_export(90, seed=12)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                        options=PipelineOptions(embedding_model="tfidf", notable_count=3))
    groups = res.analysis_frame["tech_group"].value_counts().index[:2].tolist()

    def fake_figures(number, link="", session=None, limit=2, chooser=None, **kwargs):
        out = figures.PatentPdf(number, data=pp.us_application(), source="https://example.org/x.pdf")
        out.figures = figures.find_figures(out.data, limit, chooser)
        return out

    monkeypatch.setattr(figures, "patent_figures", fake_figures)
    vision = VisionModel([1])
    events = []

    class Both(FakeLLM):  # the notes from the text model, the figures from the "vision" one
        def chat(self, messages):
            if isinstance(messages[-1]["content"], list):
                return vision.chat(messages)
            return super().chat(messages)

    gallery = group_gallery(res, groups, llm=Both(plan), on_event=lambda k, p: events.append(k),
                            options=PipelineOptions(notable_count=5, fetch_figures=True, vision_model="vl-model"))
    assert 1 <= len(gallery.cards) <= 5
    assert set(res.analysis_frame.loc[[c.index for c in gallery.cards], "tech_group"]) <= set(groups)
    assert all(c.figures and c.figures[0].description == "view 1 of the layer 12" for c in gallery.cards)
    assert len(vision.calls) == len(gallery.cards) and "progress" in events
    assert "chosen and described among the drawings found on the pages" in gallery.note and "vl-model" not in gallery.note
    blocks = gallery_blocks(gallery)
    assert blocks[0].title == f"🖼️ Top patents: {', '.join(groups)}" and blocks[1].kind == "cards"
    assert blocks[1].items[0]["drawings"].startswith("Representative figure (front page): view 1")
    page = blocks[-1].data.decode()
    assert blocks[-1].file_name.endswith(".html") and "<strong>Drawings:</strong>" in page
    empty = group_gallery(res, ["No such group"], options=PipelineOptions(notable_count=5))
    assert empty.cards == [] and "No analysed patent" in gallery_blocks(empty)[1].text
