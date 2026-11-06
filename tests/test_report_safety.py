"""A finished analysis must reach the page: charts from the analysis sandbox (JSON round trip, packed arrays) in
the workbook, and no failure while laying out the downloads may lose the report."""

import io
import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import pytest

from analyst_agent.llm import Completion
from analyst_agent.patents import enrich
from analyst_agent.patents import report_blocks as rbk
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.presets import preset_plan
from analyst_agent.patents.workbook import figure_table

from .patent_fixtures import make_export
from .test_patent_pipeline import FakeLLM


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)


def _round_trip(fig):
    """A chart as the analysis sandbox hands it over: serialised to JSON and read back."""
    return pio.from_json(fig.to_json())


def test_charts_from_the_sandbox_give_their_numbers():
    grid = pd.DataFrame(np.arange(12).reshape(3, 4) * 1.5, index=list("abc"), columns=list("WXYZ"))
    for fig in (px.imshow(grid), go.Figure(go.Heatmap(z=grid.values, x=list(grid.columns), y=list(grid.index)))):
        table = figure_table(_round_trip(fig))  # was: "Must pass 2-d input. shape=()", and the run was lost
        assert table.iloc[:, 1:].values.tolist() == grid.values.tolist() and list(table.iloc[:, 0]) == list("abc")
    bar = figure_table(_round_trip(px.bar(pd.DataFrame({"k": ["a", "b"], "v": np.array([3, 5])}), x="k", y="v")))
    assert bar.to_dict("list") == {"k": ["a", "b"], "v": [3, 5]}  # was: rows named "dtype" and "bdata"
    stacked = figure_table(_round_trip(px.bar(pd.DataFrame({"k": ["a", "a", "b"], "v": [1, 2, 3],
                                                            "s": ["x", "y", "x"]}), x="k", y="v", color="s")))
    assert stacked.set_index("k").fillna(0).to_dict() == {"x": {"a": 1, "b": 3}, "y": {"a": 2, "b": 0}}
    odd = go.Figure(go.Heatmap(z={"dtype": "i1", "bdata": "!!"}))  # unreadable: no sheet, no failure
    assert figure_table(odd) is None


class HeatmapAgent(FakeLLM):
    """The analysis step answers with a heatmap, as the template's assignee × year question asks."""

    def chat(self, messages):
        if messages[0]["content"].startswith("You are a meticulous data analyst"):
            self.calls["agent"] += 1
            last = messages[-1]["content"]
            if "Status: success" in last:
                rows = re.findall(r"^(\S[^\n]*?)\s{2,}(\d+)$", last, re.MULTILINE)
                return Completion(content=f"Final Answer: {rows[0][0] if rows else 'n/a'} leads.", latency_s=0.0)
            return Completion(content=(
                "Heatmap of patents per assignee and technology group.\n```python\n"
                "import plotly.express as px\n"
                "g = pd.crosstab(df['Assignee (normalised)'], df['Technology group'])\n"
                "show(px.imshow(g, title='Assignee × technology group'))\n"
                "print(g.sum(axis=1).sort_values(ascending=False).to_string())\n```"), latency_s=0.0)
        return super().chat(messages)


def test_a_run_whose_analysis_draws_a_heatmap_completes(tmp_path):
    plan = preset_plan("Uneven wear")
    plan.analysis_questions = plan.analysis_questions[:1]
    raw = make_export(60, seed=4)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=HeatmapAgent(plan),
                        options=PipelineOptions(semantic_map=False, notable_count=2))
    assert res.status == "complete" and res.blocks
    book = next(b.data for b in res.blocks if b.kind == "download" and b.file_name.endswith(".xlsx"))
    contents = pd.read_excel(io.BytesIO(book), skiprows=2)
    sheet = contents.loc[contents["Title"].astype(str).str.startswith("Assignee × technology group"), "Sheet"].iat[0]
    grid = pd.read_excel(io.BytesIO(book), sheet_name=sheet)
    assert len(grid) >= 2 and grid.iloc[:, 1:].to_numpy().sum() > 0


def test_a_download_that_fails_never_loses_the_report(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("Must pass 2-d input. shape=()")

    monkeypatch.setattr(rbk, "report_workbook", broken)
    plan = preset_plan("Uneven wear")
    plan.analysis_questions = []
    raw = make_export(40, seed=5)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                        options=PipelineOptions(semantic_map=False, notable_count=2))
    files = [b.file_name for b in res.blocks if b.kind == "download"]
    assert any(f.endswith(".html") for f in files) and not any(f.endswith(".xlsx") for f in files)
    assert any(w.startswith("The Excel workbook could not be built (ValueError") for w in res.warnings)
    assert any(b.title == "Methodology and limitations" for b in res.blocks)


def test_even_a_failed_layout_shows_the_results(monkeypatch):
    from analyst_agent.patents import pipeline

    def broken(*args, **kwargs):
        raise KeyError("layout")

    monkeypatch.setattr(pipeline, "assemble_blocks", broken)
    plan = preset_plan("Uneven wear")
    plan.analysis_questions = []
    raw = make_export(40, seed=6)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                        options=PipelineOptions(semantic_map=False, notable_count=2))
    assert res.status == "error" and res.error.startswith("Laying out the report failed (KeyError")
    titles = [b.title for b in res.blocks]
    assert titles[0].startswith("Patent landscape") and "Methodology and limitations" in titles  # the body is kept
