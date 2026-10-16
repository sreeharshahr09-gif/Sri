import io
import re

import pandas as pd
import plotly.graph_objects as go
import pytest

from analyst_agent.llm import Completion, LLMError
from analyst_agent.patents import enrich
from analyst_agent.patents import narrative as nar
from analyst_agent.patents import report as rb
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.html_report import markdown_to_html, report_html
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import DEFAULT_SECTIONS, AnalysisPlan

from .patent_fixtures import make_export
from .test_patent_pipeline import FakeLLM
from .test_patent_plan import BRIEF, MODEL_PLAN


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    enrich.CACHE_DIR = tmp_path_factory.mktemp("cache")
    plan = AnalysisPlan.from_dict({**MODEL_PLAN, "brief": BRIEF, "answers": {"Companies: competitors?": "Apollo Tyres"}})
    raw = make_export(260, seed=8)
    model = FakeLLM(plan, invent=True)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=model,
                        options=PipelineOptions(embedding_model="tfidf"), dataset_name="uneven_wear.xlsx",
                        dataset_sha256="ab" * 32)
    res.model = model
    return res


# --------------------------------------------------------------------------- sections


def test_brief_headings_get_their_exhibits():
    sections = nar.plan_sections(DEFAULT_SECTIONS)
    assert [(s.heading, s.kinds) for s in sections] == [
        ("📊 Executive Summary", ["summary"]),
        ("📈 Data Visualizations", ["visuals"]),
        ("Players and competitors", ["players"]),  # the brief has no heading for it: placed after visuals
        ("🧠 Technology Clusters & Solutions", ["clusters"]),
        ("🏭 Process & Manufacturing Insights", ["process"]),
        ("🔮 Research Gaps & Future Trends", ["gaps"]),
    ]


def test_other_briefs_headings():
    sections = nar.plan_sections(["Introduction", "Competitive landscape", "Key findings", "Outlook"])
    assert [(s.heading, s.kinds) for s in sections] == [  # the brief's order kept; the rest slotted in
        ("Introduction", []),
        ("Competitive landscape", ["players"]),
        ("Key findings", ["summary"]),
        ("Data visualisations", ["visuals"]),
        ("Technology clusters and solutions", ["clusters"]),
        ("Process and manufacturing", ["process"]),
        ("Outlook", ["gaps"]),
    ]
    assert nar.plan_sections([])[0].heading == "Executive summary"


# --------------------------------------------------------------------------- numbers


def test_unmatched_numbers():
    facts = "Patents per year: 2014: 12, 2015: 18. CAGR +8.3 % a year. Michelin 1,250 (36 %). HHI 1,042."
    assert nar.unmatched_numbers("Filings grew 8.3 % a year to 18 in 2015; Michelin holds 1,250 (36 %).", facts) == []
    assert nar.unmatched_numbers("Growth was about 8 % and HHI 1042.", facts) == []  # rounding and thousands
    assert nar.unmatched_numbers("Michelin holds 41 % with 1,300 patents; top 3 players.", facts) == ["41", "1,300"]
    assert nar.unmatched_numbers("Patent EP1234567A1 is key.", facts) == []  # digits inside IDs are not figures


def test_clean_text_drops_repeated_heading_and_fences():
    text = "<think>plan</think>```markdown\n## 📈 Data Visualizations\n- **A:** b\n```"
    assert nar._clean_text(text, "📈 Data Visualizations") == "- **A:** b"
    text = "- **A:** one\n- **B:** two\n**Recommended R&D directions:**\n- go"
    assert nar._clean_text(text, "x") == "- **A:** one\n- **B:** two\n\n**Recommended R&D directions:**\n- go"
    html = markdown_to_html(nar._clean_text(text, "x"))
    assert "<p><strong>Recommended R&amp;D directions:</strong></p>" in html and html.count("<ul>") == 2


def test_write_sections_without_model_or_with_errors():
    sections = nar.plan_sections(["Summary", "Gaps"])
    nar.write_sections(sections, {"gaps": "x 12"}, "facts 12", "brief", {}, None)
    assert all("No model connected" in s.note for s in sections)

    class Down:
        def chat(self, messages):
            raise LLMError("Model server timed out after 600s.")

    sections = nar.plan_sections(["Summary"])
    nar.write_sections(sections, {}, "facts 12", "brief", {}, Down())
    assert sections[0].text == "" and "timed out" in sections[0].note


def test_write_sections_prompt_and_flags():
    seen = []

    class Writer:
        def chat(self, messages):
            seen.append(messages)
            return Completion(content="- **Growth:** 18 patents in 2015, 44 % share.\n**Implication for R&D:** x",
                              latency_s=0.0)

    sections = [nar.Section("📈 Data Visualizations", ["visuals"])]
    nar.write_sections(sections, {"visuals": "[Filing trend] 2015: 18. Share 36 %."}, "all", "THE BRIEF",
                       {"Region?": "US and CN"}, Writer())
    assert sections[0].flagged == ["44"]
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert "Every number you write must appear in the facts" in system
    assert "THE BRIEF" in user and "- Region? US and CN" in user and "Section to write: 📈 Data Visualizations" in user
    assert user.endswith("Facts:\n[Filing trend] 2015: 18. Share 36 %.")
    blocks = nar.section_blocks(sections[0])
    assert [b.kind for b in blocks] == ["markdown", "note", "caption"] and "44" in blocks[1].text


# --------------------------------------------------------------------------- the assembled report


def test_report_follows_the_brief(result):
    titles = [b.title for b in result.blocks if b.kind == "heading"]
    order = [t for t in titles if t in DEFAULT_SECTIONS + ["Players and competitors", "Methodology and limitations",
                                                          "Patent table and downloads"]]
    assert order == ["📊 Executive Summary", "📈 Data Visualizations", "Players and competitors",
                     "🧠 Technology Clusters & Solutions", "🏭 Process & Manufacturing Insights",
                     "🔮 Research Gaps & Future Trends", "Methodology and limitations", "Patent table and downloads"]
    # charts sit in the right sections
    def section_of(title):
        idx = [b.title for b in result.blocks].index(title)
        return next(b.title for b in reversed(result.blocks[:idx]) if b.kind == "heading" and b.level == 2)
    assert section_of("Filing trend") == "📈 Data Visualizations"
    assert section_of("Patent map and topic clusters") == "🧠 Technology Clusters & Solutions"
    assert section_of("White space") == "🔮 Research Gaps & Future Trends"
    assert section_of("Process and manufacturing") == "🏭 Process & Manufacturing Insights"
    assert section_of("Competitors and crown jewels") == "Players and competitors"


def test_each_section_has_checked_text(result):
    assert result.model.calls["write"] == len(result.sections) == 6
    for sec in result.sections:
        assert sec.text.startswith("- **Main finding:**")  # the heading the model repeated was dropped
        assert sec.flagged == ["987.5"]  # the made-up number, and only that
    summary_prompt = next(u for s, u in result.model.prompts if "executive summary" in s)
    assert "[Filing trend]" in summary_prompt and "[Topic clusters]" in summary_prompt and "[White space]" in summary_prompt
    assert "Apollo Tyres" in summary_prompt  # the user's answer to the brief's question
    notes = [b.text for b in result.blocks if b.kind == "note" and "Check before quoting" in b.text]
    assert len(notes) == 6
    assert "6 figures could not be matched" in result.methods["Report text"]


def test_methodology_records_how(result):
    i = [b.title for b in result.blocks].index("Methodology and limitations")
    meth = result.blocks[i:]
    assert "uneven_wear.xlsx (SHA-256 abababababab…)" in meth[1].text
    flow = next(b.table for b in meth if b.title == "How the patent set was built")
    assert flow["Remaining"].iloc[-1] == len(result.analysis_frame)
    methods = next(b.table for b in meth if b.title == "Methods used in this run")
    assert {"Year basis", "Text embedding", "Topic clusters", "Patent details", "Report text"} <= set(methods["Method"])
    defs = next(b.table for b in meth if b.title == "Definitions")
    assert {"Growth (CAGR)", "Concentration (HHI)", "White space", "Crown-jewel score"} <= set(defs["Measure"])
    merged = next(b.table for b in meth if b.title == "Assignee names merged")
    assert (merged["Name variants merged"] > 1).all() and "Michelin" in set(merged["Assignee (normalised)"])
    limits = next(b.text for b in meth if b.kind == "markdown" and b.text.startswith("**Limitations**"))
    assert "freedom to operate" in limits and "Not done here: Web search" in limits


def test_downloads(result):
    downloads = {b.file_name: b for b in result.blocks if b.kind == "download"}
    assert set(downloads) == {"uneven_wear_in_tbr_and_pcr_tires_patents.csv",
                              "uneven_wear_in_tbr_and_pcr_tires_validation_sample.csv",
                              "uneven_wear_in_tbr_and_pcr_tires_report.html"}
    sample = pd.read_csv(io.BytesIO(downloads["uneven_wear_in_tbr_and_pcr_tires_validation_sample.csv"].data),
                         encoding="utf-8-sig", keep_default_na=False)
    assert len(sample) == 30
    assert list(sample.columns) == ["Patent ID", "Title", "Abstract", "Topic", "Segment", "Wear type",
                                    "Technology group", "Complexity", "Design lever", "Core claim", "Relevance",
                                    "Confidence", "Correct? (Y/N)", "Reviewer comment"]
    assert sample["Abstract"].str.len().gt(50).all() and sample["Correct? (Y/N)"].eq("").all()
    page = downloads["uneven_wear_in_tbr_and_pcr_tires_report.html"].data.decode("utf-8")
    assert page.startswith("<!doctype html>") and "<title>Patent landscape: Uneven wear in TBR and PCR tires</title>" in page
    assert page.count("plotly.js v") == 1 or page.count("Plotly.newPlot") >= 10
    assert page.count('class="js-plotly-plot"') + page.count("Plotly.newPlot") >= 11
    toc = re.search(r'<nav class="toc">(.*?)</nav>', page).group(1)
    assert toc.index("Executive Summary") < toc.index("Research Gaps") < toc.index("Methodology")
    assert "Check before quoting" in page and "Download in the app: uneven_wear_in_tbr_and_pcr_tires_patents.csv" in page


def test_validation_sample_needs_answers():
    table = pd.DataFrame({"Patent ID": ["a", "b"], "Extraction": ["failed: x", "not run (stopped)"]})
    assert nar.validation_sample(table, ["Topic"]).empty


# --------------------------------------------------------------------------- HTML


def test_markdown_to_html():
    out = markdown_to_html("Intro **bold** & <tag>\n\n- **A:** one\n- two\n  - nested\n1. first\n\n## Sub")
    assert "<p>Intro <strong>bold</strong> &amp; &lt;tag&gt;</p>" in out
    assert out.count("<ul>") == 2 and "<li><strong>A:</strong> one</li>" in out and "<ol>" in out
    assert "<h4>Sub</h4>" in out


def test_report_html_escapes_and_embeds_plotly_once():
    fig = go.Figure(go.Bar(x=["a"], y=[1]))
    blocks = [rb.heading("T <1>", level=1), rb.heading("Sec & co"), rb.markdown("- x"), rb.chart(fig, caption="c"),
              rb.chart(fig), rb.table(pd.DataFrame({"A": ["<b>"]}), title="Tab"), rb.metrics([("N", "5")]),
              rb.note("careful"), rb.download("Get", b"x", "f.csv")]
    page = report_html(blocks, "T", "sub").decode()
    assert "T &lt;1&gt;" in page and "&lt;b&gt;" in page and 'id="sec-co"' in page
    assert len(re.findall(r"plotly\.js v\d", page)) == 1  # the library once, both charts use it
    assert page.count("Plotly.newPlot") == 2
    assert '<div class="metric"><div class="label">N</div><div class="value">5</div></div>' in page
