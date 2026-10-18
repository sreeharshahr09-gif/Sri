import re

import numpy as np
import pandas as pd
import pytest

from analyst_agent.patents import analytics as an
from analyst_agent.patents import enrich
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan

from .patent_fixtures import make_export
from .test_patent_pipeline import FakeLLM
from .test_patent_plan import MODEL_PLAN

PLAN = AnalysisPlan.from_dict({**MODEL_PLAN, "competitors": ["Michelin", "Bridgestone", "Apollo Tyres", "Nokian"]})


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    enrich.CACHE_DIR = tmp_path_factory.mktemp("cache")
    raw = make_export(320, seed=21)
    return run_landscape(raw, detect_columns(list(raw.columns)), PLAN, llm=FakeLLM(PLAN),
                         options=PipelineOptions(embedding_model="tfidf"))


def _ex(result, key):
    return next(e for e in result.exhibits if e.key == key)


def test_all_exhibits_computed(result):
    assert [e.key for e in result.exhibits] == ["trend", "geography", "segments", "problem_solution", "players",
                                                "competitors", "complexity", "process", "roadmap", "white_space"]
    assert not [w for w in result.warnings if "could not be computed" in w]
    assert all(e.facts for e in result.exhibits)


def test_off_topic_patents_leave_the_analysis_set(result):
    frame = result.analysis_frame
    assert not (frame["relevance"] == "Low").any()
    step = result.corpus.flow[-1]
    assert step.step == "Removed: judged off-topic by the model (Relevance: Low)"
    assert step.remaining == len(frame) and step.removed == (result.enriched["Relevance"] == "Low").sum() > 0
    metrics = dict(result.blocks[1].metrics)
    assert metrics["Patents analysed"] == f"{len(frame):,}"
    flow = next(b.table for b in result.blocks if b.title == "How the patent set was built")
    assert flow["Step"].iloc[-1] == step.step


def test_trend_facts_match_the_data(result):
    frame = result.analysis_frame
    facts = _ex(result, "trend").facts
    counts = frame.dropna(subset=["year"]).groupby(frame["year"].dropna().astype(int)).size()
    for year in (2014, 2019, 2024):
        assert f"{year}: {counts.get(year, 0)}" in facts
    assert "2012" not in facts.split("CAGR")[0]  # outside the plan's 2014–2024 window
    first, last = counts.loc[2014:2016].mean(), counts.loc[2022:2024].mean()
    rate = (last / first) ** (1 / (2023 - 2015)) - 1
    assert (f"Growth (3-year averages, complete years): average {first:.1f} patents a year in 2014–2016, "
            f"{last:.1f} in 2022–2024: {rate * 100:+.1f} % a year.") in facts
    assert "Active/Electronic" in facts


def test_cagr():
    rate, y0, y1 = an.cagr(pd.Series({2014: 10, 2015: 0, 2016: 40}))
    assert (round(rate, 6), y0, y1) == (1.0, 2014, 2016)
    assert an.cagr(pd.Series({2020: 5})) == (None, None, None)


@pytest.mark.parametrize("basis,ref,expected", [
    ("priority date", 2025.9, [2024, 2025]),  # export in late 2025: filings since mid-2024 not all out
    ("priority date", 2026.04, [2024, 2025]),  # export in January 2026: 2024 still incomplete
    ("filing date", 2024.9, [2023, 2024, 2025]),
    ("publication date", 2025.5, [2025]),
    ("publication date", 2025.99, []),  # exported at the very end of 2025: that year is complete
    ("priority date", 2030, []),
])
def test_publication_lag_zone(basis, ref, expected):
    frame = pd.DataFrame({"year": [2014.0, 2020.0, 2025.0], "tech_group": ["A", "B", "A"]})
    ctx = an.make_context(frame, AnalysisPlan(), basis, ref_year=ref)
    assert ctx.incomplete_years == expected


def test_incomplete_years_are_shaded_and_left_out_of_growth():
    frame = pd.DataFrame({"year": [2018.0] * 5 + [2019.0] * 10 + [2020.0] * 2, "tech_group": "Structural / design",
                          "assignee": "X", "country": "EP", "title": "t", "id": "1", "first_claim": "",
                          "cited_by": np.nan})
    ctx = an.make_context(frame, AnalysisPlan(), "priority date", ref_year=2021.9)
    assert ctx.incomplete_years == [2020]
    ex = an.trend(frame, ctx)
    assert "Growth (complete years): CAGR 2018–2019 +100.0 % a year." in ex.facts
    fig = ex.blocks[0].figure
    assert any(shape.x0 == 2019.5 for shape in fig.layout.shapes)


def test_players_concentration_and_competitor_marks(result):
    frame = result.analysis_frame
    shares = frame["assignee"].value_counts(normalize=True)
    hhi = (shares**2).sum() * 10000
    ex = _ex(result, "players")
    assert f"HHI {hhi:,.0f}, which means fragmented" in ex.facts
    top = frame["assignee"].value_counts().index[0]
    assert ex.facts.startswith(f"Top assignees: {top} {frame['assignee'].value_counts().iloc[0]}")
    bar = ex.blocks[0].figure
    names = {y for t in bar.data for y in t.y}
    assert "★ Michelin" in names and "★ Bridgestone" in names
    assert len(ex.blocks) == 2  # bar + assignee x technology heatmap


def test_competitor_watch_and_crown_jewels(result):
    ex = _ex(result, "competitors")
    watch, jewels = ex.blocks[0].table, ex.blocks[1].table
    assert watch["Competitor"].tolist() == ["Michelin", "Bridgestone", "Apollo Tyres"]  # Nokian: not in the export
    frame = result.analysis_frame
    assert watch.loc[0, "Patents"] == (frame["assignee"] == "Michelin").sum()
    assert set(jewels["Competitor"]) == {"Michelin", "Bridgestone", "Apollo Tyres"}
    assert jewels.groupby("Competitor").size().max() == 3
    assert jewels["Score"].between(0, 100).all()
    assert (jewels.groupby("Competitor", sort=False)["Score"].apply(lambda s: s.is_monotonic_decreasing)).all()
    assert "relevance 35 %, claim breadth 35 %, recency 30 %" in ex.blocks[1].caption
    assert "Include forward citations" in ex.blocks[1].caption


def test_crown_jewel_weights_with_citations():
    frame = pd.DataFrame({"cited_by": [0, 5, 50], "relevance": ["High", "High", "Partial"],
                          "first_claim": ["a" * 300, "a" * 100, "a" * 200], "year": [2015, 2018, 2022]})
    weights = an.crown_jewel_weights(frame)
    assert weights == pytest.approx({"forward citations": 0.5, "relevance": 0.2, "claim breadth": 0.2, "recency": 0.1})
    scores = an.crown_jewel_scores(frame)
    assert scores.between(0, 100).all() and scores.idxmax() == 2  # most cited wins
    weights = an.crown_jewel_weights(frame.drop(columns="relevance").assign(cited_by=np.nan))
    assert weights == pytest.approx({"claim breadth": 0.35 / 0.65, "recency": 0.3 / 0.65})


def test_complexity_ranking_in_plan_order(result):
    ex = _ex(result, "complexity")
    table = ex.blocks[1].table
    assert table["Complexity"].tolist() == ["Simple", "Intermediate", "Complex"]
    assert table["Patents"].sum() == result.analysis_frame["complexity"].isin(["Simple", "Intermediate", "Complex"]).sum()
    complex_row = table[table["Complexity"] == "Complex"].iloc[0]
    assert complex_row["Main technology group"] == "Active/Electronic"
    assert "wear prediction model" in complex_row["Most frequent design levers"]
    assert re.search(r"Share of Complex solutions: \d+ % up to \d{4}, \d+ % after", ex.facts)


def test_problem_solution_map(result):
    ex = _ex(result, "problem_solution")
    heat = ex.blocks[0].figure
    assert list(heat.data[0].y)[:2] == ["Camber wear", "Cupping"] or "Cupping" in heat.data[0].y
    assert "Not specified" not in heat.data[0].y
    assert len(ex.blocks) == 2  # with the topic-cluster version
    assert "Densest combinations:" in ex.facts


def test_white_space_flags_missing_combinations():
    plan = AnalysisPlan.from_dict(MODEL_PLAN)
    rows = []
    for _ in range(12):
        rows.append(("Cupping", "Structural/Design", "Simple"))
        rows.append(("Cupping", "Active/Electronic", "Complex"))
        rows.append(("Shoulder wear", "Structural/Design", "Simple"))
    for _ in range(12):
        rows.append(("Shoulder wear", "Material Science", "Intermediate"))
        rows.append(("Cupping", "Material Science", "Intermediate"))
    frame = pd.DataFrame(rows, columns=["problem", "tech_group", "complexity"]).assign(
        year=2020.0, assignee="X", country="EP", title="t", id="1", first_claim="", cited_by=np.nan,
        relevance="High", design_lever="x")
    ctx = an.make_context(frame, plan, "publication date", ref_year=2030)
    ex = an.white_space(frame, ctx)
    table = ex.blocks[0].table
    gaps = set(zip(table["Wear type"], table["Technology group"]))
    assert ("Shoulder wear", "Active/Electronic") in gaps
    assert ("Shoulder wear", "Complex solutions") in gaps
    assert ("Camber wear", "any") in gaps  # a wear type of the plan with no patents
    assert ("Cupping", "Structural/Design") not in gaps


def test_process_insights_pick_the_process_group(result):
    ex = _ex(result, "process")
    assert ex.facts.startswith("Process/Manufacturing: ")
    assert "curing temperature" in ex.facts
    assert ex.blocks[2].table["Design lever"].eq("curing temperature").all()


def test_roadmap_periods_and_momentum(result):
    ex = _ex(result, "roadmap")
    grid = ex.blocks[1].table
    frame = result.analysis_frame
    dated = frame[frame["year"].between(2014, 2024)]
    assert grid.drop(columns="Technology group").values.sum() == len(dated)
    momentum = ex.blocks[2].table
    sensor = momentum[momentum["Technology group"] == "Active/Electronic"]
    assert len(sensor) and (sensor["Momentum"] > 1).all()  # sensors only appear from 2018: growing


def test_without_model_details_fewer_exhibits_and_no_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    raw = make_export(120, seed=5)
    res = run_landscape(raw, detect_columns(list(raw.columns)), PLAN, options=PipelineOptions(embedding_model="tfidf"))
    keys = [e.key for e in res.exhibits]
    assert keys == ["trend", "geography", "players", "competitors", "roadmap"]
    assert not [w for w in res.warnings if "could not be computed" in w]
    assert set(res.analysis_frame["tech_group"]) == {"Unclassified"}


def test_one_broken_exhibit_does_not_sink_the_rest(result, monkeypatch):
    def broken(frame, ctx):
        raise ValueError("bad data")

    monkeypatch.setattr(an, "EXHIBITS", [an.trend, broken, an.geography])
    exhibits, notes = an.run_analytics(result.analysis_frame, PLAN, "publication date")
    assert [e.key for e in exhibits] == ["trend", "geography"]
    assert notes == ["Broken could not be computed (bad data)."]


def test_without_problem_categories_the_maps_say_why(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    plan = AnalysisPlan.from_dict({**MODEL_PLAN, "problems": []})
    raw = make_export(80, seed=9)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                        options=PipelineOptions(embedding_model="tfidf", write_report=False))
    for key in ("problem_solution", "white_space"):
        ex = _ex(res, key)
        assert [b.kind for b in ex.blocks] == ["note"]
        assert ex.facts.startswith("Not done: the plan defines no wear type categories")


def test_export_date_comes_from_the_latest_publication():
    from analyst_agent.patents.data import build_corpus
    from analyst_agent.patents.pipeline import export_date

    raw = pd.DataFrame({"Title": ["a", "b"], "Abstract": ["x", "y"], "Publication Date": ["2025-01-15", "2026-01-15"],
                        "Priority Year": [2023, 2024]})
    corpus = build_corpus(raw, detect_columns(list(raw.columns)))
    assert export_date(corpus) == pytest.approx(2026.038, abs=0.01)
    raw["Publication Date"] = [20250115, 20260115]  # numbers: not trusted as dates
    assert export_date(build_corpus(raw, detect_columns(list(raw.columns)))) is None


def test_growth_is_not_computed_from_a_tiny_base():
    text, fact = an.growth(pd.Series({2008: 1, 2009: 0, 2010: 2, 2011: 3, 2012: 5, 2013: 4, 2014: 6}))
    assert "too few patents at the start for a meaningful growth rate" in fact and "%" not in fact
    text, fact = an.growth(pd.Series({2015: 10, 2016: 12, 2017: 14, 2018: 15, 2019: 18, 2020: 20}))
    assert fact.startswith("Growth (3-year averages, complete years): average 12.0 patents a year in 2015–2017")


def test_momentum_and_periods_respect_the_publication_lag(result):
    ex = _ex(result, "roadmap")
    momentum = next(b for b in ex.blocks if b.title == "Topic clusters: momentum").table
    window = [c for c in momentum.columns if c.startswith("Patents in ")][0]
    assert set(momentum["Reading"]) <= {"growing faster than the field", "slowing", "about as the field"}
    assert (momentum.loc[momentum["Reading"] == "growing faster than the field", "Momentum"] >= 1.2).all()
    assert (momentum.loc[momentum["Reading"] == "slowing", "Momentum"] <= 0.8).all()
    assert "A momentum below 1 means slowing, never growth." in ex.facts and window[len("Patents in "):] in ex.facts


def test_periods_with_incomplete_years_are_starred():
    frame = pd.DataFrame({"year": [2015.0, 2016, 2018, 2019, 2021, 2022, 2023, 2024] * 3, "tech_group": "A",
                          "assignee": "X", "country": "EP", "title": "t", "id": "1", "first_claim": "", "cited_by": np.nan})
    ctx = an.make_context(frame, AnalysisPlan(), "priority date", ref_year=2025.5)
    assert ctx.incomplete_years == [2024]
    ex = an.roadmap(frame, ctx)
    grid = ex.blocks[1].table
    assert list(grid.columns)[-1].endswith("*") and "is not (yet) a decline" in ex.blocks[1].caption


def test_pooled_competitors_are_disclosed():
    plan = AnalysisPlan.from_dict({**MODEL_PLAN, "competitors": ["Sumitomo"]})
    frame = pd.DataFrame({"assignee": ["Sumitomo Rubber"] * 4 + ["Sumitomo Electric"] * 2 + ["Bridgestone"] * 3,
                          "year": [2018.0] * 9, "tech_group": "A", "country": "JP", "title": "t", "id": [str(i) for i in range(9)],
                          "first_claim": "x", "cited_by": np.nan})
    ctx = an.make_context(frame, plan, "priority date", ref_year=2030)
    ex = an.competitors(frame, ctx)
    assert "Assignees counted together: Sumitomo = Sumitomo Rubber + Sumitomo Electric" in ex.blocks[0].caption
    assert "(counted together: Sumitomo Rubber + Sumitomo Electric)" in ex.facts


def test_too_many_other_answers_are_called_out():
    plan = AnalysisPlan.from_dict({**MODEL_PLAN, "problems": [{"name": "Weight reduction"}]})
    frame = pd.DataFrame({"problem": ["Other"] * 7 + ["Weight reduction"] * 3, "tech_group": "Structural/Design",
                          "year": 2020.0, "assignee": "X", "country": "EP", "title": "t", "id": "1", "first_claim": "",
                          "cited_by": np.nan})
    ex = an.problem_solution(frame, an.make_context(frame, plan, "priority date", ref_year=2030))
    assert ex.blocks[0].kind == "note" and ex.facts.startswith("7 of 10 patents fit none of the plan's")
