"""The crash audit: a landscape run survives a misbehaving model and broken parts, and always ends with a report.
Each test pins one weakness the audit found."""

import io
import random

import pandas as pd
import pytest
import requests

from analyst_agent.llm import Completion, LLMError, RequestLimitReached
from analyst_agent.patents import enrich, narrative, semantic
from analyst_agent.patents import report as rb
from analyst_agent.patents import report_blocks as rbk
from analyst_agent.patents.data import build_corpus, detect_columns
from analyst_agent.patents.html_report import report_html
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan
from analyst_agent.patents.presets import preset_plan
from analyst_agent.patents.workbook import report_workbook

from .patent_fixtures import make_export
from .test_llm import OK, client
from .test_patent_pipeline import FakeLLM


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    monkeypatch.setattr("analyst_agent.llm.time.sleep", lambda s: None)


# --------------------------------------------------------------------------- the model client


def test_a_reply_cut_off_in_transfer_is_asked_again():
    llm, session = client([requests.exceptions.ChunkedEncodingError("cut"), OK])
    assert llm.chat([{"role": "user", "content": "x"}]).content == "hi" and len(session.posts) == 2
    llm, _ = client([requests.exceptions.ContentDecodingError("bad")] * 3, max_retries=2)
    with pytest.raises(LLMError, match="ContentDecodingError"):  # an LLMError, which every step handles
        llm.chat([{"role": "user", "content": "x"}])


# --------------------------------------------------------------------------- one bad item, not the whole step


class Faulty(FakeLLM):
    """FakeLLM, except that calls whose system prompt starts with `prefix` raise an unexpected error `times` times."""

    def __init__(self, plan, prefix: str, times: int = 1):
        super().__init__(plan)
        self.prefix, self.left = prefix, times

    def chat(self, messages):
        if messages[0]["content"].startswith(self.prefix) and self.left > 0:
            self.left -= 1
            raise RuntimeError("unexpected failure")
        return super().chat(messages)


def _run(model, plan, **options):
    raw = make_export(48, seed=21)
    return run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=model,
                         options=PipelineOptions(embedding_model="tfidf", notable_count=3, **options))


def test_an_unexpected_error_in_one_batch_keeps_the_others():
    plan = AnalysisPlan(title="Wear")
    res = _run(Faulty(plan, "You extract structured facts"), plan, semantic_map=False)
    assert res.extraction is not None and res.extraction.stats["extracted"] == res.extraction.stats["patents"]


def test_an_unexpected_error_in_one_section_keeps_the_others():
    plan = AnalysisPlan(title="Wear")
    res = _run(Faulty(plan, "You write one section"), plan, semantic_map=False)
    written = [s for s in res.sections if s.text]
    assert len(written) == len(res.sections) - 1 and any("could not be written" in s.note for s in res.sections)


def test_cluster_naming_failures_keep_the_map():
    plan = AnalysisPlan(title="Wear")
    res = _run(Faulty(plan, "You name topic clusters", times=10), plan)
    assert res.semantic is not None and res.methods["Cluster names"] == "Cluster names are their top keywords."


def test_one_unanswerable_question_keeps_the_others(monkeypatch):
    from analyst_agent.agent import DataAnalystAgent

    real = DataAnalystAgent.run
    calls = {"n": 0}

    def flaky(self, question, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("agent failure")
        return real(self, question, **kwargs)

    monkeypatch.setattr(DataAnalystAgent, "run", flaky)
    plan = preset_plan("Uneven wear")
    plan.analysis_questions = plan.analysis_questions[:2]
    res = _run(FakeLLM(plan), plan, semantic_map=False)
    assert [q for q, _ in res.question_runs] == plan.analysis_questions[1:]
    assert any(w.startswith("Question 1 could not be answered (RuntimeError") for w in res.warnings)


# --------------------------------------------------------------------------- laying out the report


def test_a_broken_report_part_keeps_the_rest(monkeypatch):
    plan = AnalysisPlan(title="Wear")

    def broken(result):
        raise TypeError("cards")

    monkeypatch.setattr(rbk, "notable_blocks", broken)
    res = _run(FakeLLM(plan), plan, semantic_map=False)
    assert res.status == "complete" and any(b.kind == "note" and b.text.startswith("The notable patents could not")
                                            for b in res.blocks)
    titles = [b.title for b in res.blocks]
    assert "Methodology and limitations" in titles and any(t.endswith("_report.html") for t in
                                                           [b.file_name for b in res.blocks])


def test_html_and_workbook_skip_only_the_broken_piece():
    class BadFigure:
        data = ()

        def to_html(self, **kwargs):
            raise ValueError("bad figure")

    blocks = [rb.heading("T", 1), rb.chart(BadFigure(), "Broken"), rb.markdown("Still here.")]
    page = report_html(blocks, "T").decode()
    assert "This plotly could not be shown (ValueError)" in page and "Still here." in page
    weird = pd.DataFrame({"a": [object()]})  # Excel cannot take it
    book = report_workbook([rb.heading("S"), rb.table(weird, title="Odd"), rb.table(pd.DataFrame({"b": [1]}), title="Ok")],
                           "T")
    sheets = pd.read_excel(io.BytesIO(book), sheet_name=None)
    assert sheets["02 Ok"]["b"].tolist() == [1]
    assert "could not be written" in str(sheets["01 Odd"].iloc[0, 0]) or len(sheets["01 Odd"]) == 1


def test_repeated_column_names_in_an_export():
    raw = make_export(20, seed=2)
    raw = pd.concat([raw, raw[["Title"]]], axis=1)  # "Title" twice
    corpus = build_corpus(raw, detect_columns(list(raw.columns)))
    assert len(corpus) > 0 and corpus.std["title"].str.len().gt(0).all()


def test_analysed_topics_with_clusters_without_a_known_group():
    from analyst_agent.patents.result import analysed_topics

    plan = AnalysisPlan(title="Wear")
    res = _run(FakeLLM(plan), plan)
    res.analysis_frame = res.analysis_frame.assign(tech_group=float("nan"))
    res.analysis_frame["tech_group"] = res.analysis_frame["tech_group"].astype(object)
    topics = analysed_topics(res)  # was: IndexError, and the facts and the cluster table were lost
    assert topics is not None and len(topics)


# --------------------------------------------------------------------------- a misbehaving model throughout


GARBAGE = ["", "not json", "[1, 2, 3]", '{"a": 1}', "[]", "null",
           '[{"ref": "P1", "problem": ["x", {"y": 1}], "relevance": null, "segment": 5, "tech_group": [1]}]',
           '{"figures": [{"image": "a"}, {"image": 99}, 3]}', "[" * 50, "x" * 5000, "Final Answer:",
           "```python\nraise SystemExit(1)\n```", "```python\nshow(None)\nshow({'a': [1, 2]})\n```"]


class Chaos:
    def __init__(self, plan, seed: int, rate: float):
        self.good, self.rng, self.rate = FakeLLM(plan), random.Random(seed), rate

    def chat(self, messages):
        r = self.rng.random()
        if r < self.rate * 0.15:
            raise LLMError("server error")
        if r < self.rate * 0.2:
            raise RuntimeError("unexpected client failure")
        if r < self.rate * 0.21:
            raise RequestLimitReached("daily limit")
        if r < self.rate:
            return Completion(content=self.rng.choice(GARBAGE), latency_s=0.0)
        try:
            return self.good.chat(messages)
        except Exception:
            return Completion(content="ok", latency_s=0.0)


@pytest.mark.parametrize("seed, rate, objective", [(0, 0.5, "Uneven wear"),
                                                   (1, 0.9, "Under-belt layer (belt–carcass interlayer)"),
                                                   (2, 1.0, "Lightweighting")])
def test_a_misbehaving_model_still_gives_a_report(seed, rate, objective):
    plan = preset_plan(objective)
    plan.analysis_questions = plan.analysis_questions[:2]
    raw = make_export(36, seed=seed)
    raw["Legal Status"] = ["Alive" if i % 2 else "Dead" for i in range(len(raw))]
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=Chaos(plan, seed, rate),
                        options=PipelineOptions(embedding_model="tfidf", notable_count=3, reuse_replies=False))
    files = [b.file_name for b in res.blocks if b.kind == "download"]
    assert any(f.endswith("_report.html") for f in files) and any(f.endswith("_patents.csv") for f in files)
    assert not [w for w in res.warnings if "Collecting the facts failed" in w]


def test_a_failing_section_writer_reaches_no_further_than_its_section():
    sections = narrative.plan_sections(["📊 Executive summary", "🔮 Gaps"])

    class Down:
        def chat(self, messages):
            raise KeyError("odd")

    narrative.write_sections(sections, {"summary": "x 12", "gaps": "y 13"}, "x 12 y 13", "brief", {}, Down())
    with_facts = [sec for sec in sections if "summary" in sec.kinds or "gaps" in sec.kinds]
    assert len(with_facts) == 2 and all("could not be written" in sec.note for sec in with_facts)


def test_cluster_names_survive_a_client_that_breaks():
    topics = pd.DataFrame({"id": [0, 1], "patents": [3, 4], "keywords": [["a", "b"], ["c", "d"]],
                           "sample_titles": [["t1"], ["t2"]]})

    class Down:
        def chat(self, messages):
            raise AttributeError("odd")

    named, note = semantic.name_topics(Down(), topics, AnalysisPlan())
    assert note == "Cluster names are their top keywords." and named["label"].tolist() == ["A, b", "C, d"]
