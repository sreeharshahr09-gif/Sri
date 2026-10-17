"""The landscape is not tied to the uneven-wear example: another topic, another database's columns."""

import numpy as np
import pandas as pd
import pytest

from analyst_agent.patents import analytics as an
from analyst_agent.patents import enrich
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import fallback_plan

from .patent_fixtures import make_export
from .test_patent_pipeline import FakeLLM

BRIEF = """Act as a senior R&D analyst. Conduct a patent landscape on low rolling resistance tires for EVs, 2015-2025.
Cluster solutions into compound, construction and tread design. Keywords: "rolling resistance", "hysteresis".
OUTPUT FORMAT:
- Key findings
- Who is active
- Solution families
- White space and outlook
"""


def lens_export(n=260, seed=6) -> pd.DataFrame:
    """The synthetic export renamed to Lens.org column names, with priority dates and forward citations."""
    raw = make_export(n, seed=seed, with_family=True)
    rng = np.random.default_rng(seed)
    pub = pd.to_datetime(raw["Publication/Issue Date"], format="mixed")
    out = raw.rename(columns={"Record Number": "Display Key", "Publication/Issue Date": "Publication Date",
                              "Assignee": "Applicants", "Family ID": "Simple Family ID"})
    out = out.drop(columns=["Problem Being Solved (AI Sum.)", "Method Used (AI Sum.)", "Advantages (AI Sum.)"])
    out["Earliest Priority Date"] = (pub - pd.to_timedelta(rng.integers(300, 700, len(out)), unit="D")).dt.strftime("%Y-%m-%d")
    out["Cited by Patent Count"] = rng.poisson(4, len(out))
    out["Jurisdiction"] = out["Display Key"].str[:2]
    return out


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    enrich.CACHE_DIR = tmp_path_factory.mktemp("cache")
    raw = lens_export()
    plan = fallback_plan(BRIEF)  # as if the model were unavailable when the plan was prepared
    return run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                         options=PipelineOptions(embedding_model="tfidf"))


def test_lens_columns_are_recognised():
    mapping = detect_columns(list(lens_export(20).columns))
    assert mapping["id"] == "Display Key" and mapping["assignee"] == "Applicants"
    assert mapping["priority_date"] == "Earliest Priority Date" and mapping["cited_by"] == "Cited by Patent Count"
    assert mapping["family"] == "Simple Family ID" and mapping["country"] == "Jurisdiction"


def test_other_topic_runs_end_to_end(result):
    plan = result.plan
    assert plan.title.startswith("low rolling resistance tires for EVs") and plan.years == (2015, 2025)
    assert result.status == "complete" and not [w for w in result.warnings if "could not be computed" in w]
    assert "priority date" in result.corpus.year_basis
    assert any(f.step == "Removed: duplicates (same patent family ID)" and f.removed for f in result.corpus.flow)
    headings = [b.title for b in result.blocks if b.kind == "heading" and b.level == 2]
    assert headings[:4] == ["Key findings", "Data visualisations", "Who is active", "Solution families"]
    assert "Players and competitors" not in headings  # "Who is active" holds them
    assert "White space and outlook" in headings


def test_priority_dates_shade_the_lag_and_citations_drive_crown_jewels(result):
    last = int(result.analysis_frame["year"].max())
    ctx = an.make_context(result.analysis_frame, result.plan, result.corpus.year_basis, ref_year=last + 1.9)
    assert ctx.incomplete_years == [last]  # priority basis: the year before the export is still incomplete
    jewels = next(e for e in result.exhibits if e.key == "competitors").blocks[1]
    assert "forward citations 50 %" in jewels.caption
    assert an.crown_jewel_weights(result.analysis_frame)["forward citations"] == 0.5


def test_csv_keeps_the_lens_columns(result):
    cols = list(result.enriched.columns)
    for c in ("Display Key", "Earliest Priority Date", "Cited by Patent Count", "Simple Family ID", "Topic",
              "Technology group", "Topic cluster"):
        assert c in cols
