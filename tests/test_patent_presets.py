import json

import numpy as np
import pandas as pd
import pytest

from analyst_agent.llm import LLMError
from analyst_agent.patents import analytics as an
from analyst_agent.patents import enrich
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.html_report import report_html
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan, parse_brief
from analyst_agent.patents.presets import PRESETS, preset_names, preset_plan

from .conftest import ScriptedLLM
from .patent_fixtures import make_export
from .test_patent_pipeline import FakeLLM

EXPECTED = ["Lightweighting", "Low rolling resistance", "Durability", "Uneven wear", "Smart / connected tyres",
            "NVH (noise, vibration, harshness)", "Wet / dry traction", "Dynamic growth", "High performance",
            "Sustainable materials"]


# --------------------------------------------------------------------------- the objectives


def test_every_objective_is_a_complete_plan():
    assert preset_names() == EXPECTED
    for name in preset_names():
        plan = preset_plan(name)
        assert plan.source == "preset" and plan.title and plan.objective and plan.in_scope, name
        assert 6 <= len(plan.problems) <= 8 and all(c.description for c in plan.problems), name
        assert 4 <= len(plan.tech_buckets) <= 5 and [c.name for c in plan.complexity_levels] == [
            "Simple", "Intermediate", "Complex"], name
        assert 3 <= len(plan.extra_fields) <= 4 and len(plan.analysis_questions) == 4, name
        assert plan.keywords and plan.classification_codes, name
        names = [c.name.lower() for c in plan.problems + plan.tech_buckets]
        assert len(names) == len(set(names)), name
        assert "OUTPUT FORMAT" in plan.brief and plan.analysis_questions[0] in plan.brief


def test_objective_plans_are_fresh_copies():
    plan = preset_plan("Lightweighting")
    plan.problems.append(plan.problems[0])
    plan.analysis_questions.clear()
    again = preset_plan("Lightweighting")
    assert len(again.problems) == len(PRESETS["Lightweighting"]["problems"]) and again.analysis_questions


def test_extraction_asks_for_the_objectives_columns():
    fields = enrich.build_fields(preset_plan("Lightweighting"))
    by_column = {f.column: f for f in fields}
    assert by_column["Component lightened"].choices[:2] == ["Tread & under-tread", "Sidewall"]
    assert by_column["Component lightened"].multi and "Other" in by_column["Component lightened"].choices
    assert "Lighter reinforcement" in by_column["Technology group"].choices
    assert {"Weight saving claimed", "Material replaced -> by", "Performance kept or traded"} <= set(by_column)


# --------------------------------------------------------------------------- objective + brief


def test_brief_adapts_the_objectives_plan():
    base = preset_plan("Low rolling resistance")
    reply = {"title": "Low rolling resistance TBR tyres", "segments": ["TBR"], "years": [2015, 2025],
             "competitors": ["Michelin", "Bridgestone"],
             "analysis_questions": ["Which levers do Michelin and Bridgestone use?"]}
    llm = ScriptedLLM([json.dumps(reply)])
    plan = parse_brief(llm, "Focus on TBR tyres 2015-2025; compare Michelin and Bridgestone.", base=base)
    user = llm.calls[0][1]["content"]
    assert user.startswith("Start from this standard plan for the objective.") and '"Base & under-tread"' in user
    assert plan.title == "Low rolling resistance TBR tyres" and plan.segments == ["TBR"] and plan.years == (2015, 2025)
    assert [c.name for c in plan.problems] == [c.name for c in base.problems]  # kept from the standard plan
    assert plan.extra_fields == base.extra_fields
    assert plan.analysis_questions == ["Which levers do Michelin and Bridgestone use?"]


def test_without_the_model_the_objectives_plan_is_used():
    class Down:
        def chat(self, messages):
            raise LLMError("Cannot reach model server")

    plan = parse_brief(Down(), "Focus on TBR.", base=preset_plan("Durability"))
    assert plan.title == "Tyre durability" and plan.brief == "Focus on TBR."
    assert any("standard objective's plan is used as it is" in n for n in plan.not_possible)


# --------------------------------------------------------------------------- extra columns


def _frame(values, groups=None):
    n = len(values)
    return pd.DataFrame({"x_weight_saving_claimed": values, "tech_group": groups or ["Structural design"] * n,
                         "id": [f"P{i}" for i in range(n)], "title": "t", "year": 2020.0, "assignee": "X",
                         "country": "EP", "first_claim": "", "cited_by": np.nan})


def _ctx(frame):
    plan = AnalysisPlan.from_dict({**preset_plan("Lightweighting").to_dict(),
                                   "extra_fields": [{"name": "Weight saving claimed", "description": ""}]})
    return an.make_context(frame, plan, "priority date", ref_year=2030)


def test_extra_column_with_numbers_gets_a_distribution():
    values = ["12 %", "8%", "about 15 % lighter", "not stated", "20 %", "5 %", "none", "10 %"]
    groups = ["Reinforcement materials", "Structural design"] * 4
    frame = _frame(values, groups)
    ex = an.extra_columns(frame, _ctx(frame))
    assert [b.kind for b in ex.blocks] == ["heading", "plotly", "table"]
    assert ex.facts.startswith("Weight saving claimed: 6 of 8 patents state a value, 6 in %; median 11.0 %")
    table = ex.blocks[2].table
    assert table["Patents"].sum() == 6


def test_extra_column_with_categories_or_free_text():
    frame = _frame(["bead core", "bead core", "belt", "belt", "belt", "apex", "not stated"])
    ex = an.extra_columns(frame, _ctx(frame))
    assert [b.kind for b in ex.blocks] == ["heading", "plotly"] and "most frequent: belt 3; bead core 2" in ex.facts
    frame = _frame([f"a different statement number {i}x" for i in range(6)])
    ex = an.extra_columns(frame, _ctx(frame))
    assert [b.kind for b in ex.blocks] == ["heading", "table"] and "values vary" in ex.facts
    frame = _frame(["not stated"] * 4)
    assert "no patent states a value" in an.extra_columns(frame, _ctx(frame)).facts


def test_extra_column_with_several_values_per_patent_counts_patents_per_value():
    """'durability; handling' in one patent: 'durability' counts once for it. Counting whole values gave the user's
    report 18 patents for durability where 36 name it."""
    values = ["durability; handling", "durability", "rolling resistance, durability", "handling; noise",
              "durability; rolling resistance", "not stated", "durability; durability"]
    frame = _frame(values)
    ex = an.extra_columns(frame, _ctx(frame))
    assert [b.kind for b in ex.blocks] == ["heading", "plotly"]
    assert "patents naming each value (a patent can name several): durability 5; handling 2; rolling resistance 2" \
        in ex.facts
    assert "a patent can name several" in ex.blocks[1].caption


# --------------------------------------------------------------------------- questions answered in the report


@pytest.fixture(scope="module")
def answered(tmp_path_factory):
    enrich.CACHE_DIR = tmp_path_factory.mktemp("cache")
    plan = preset_plan("Lightweighting")
    plan.analysis_questions = plan.analysis_questions[:2]
    raw = make_export(90, seed=17)
    model = FakeLLM(plan)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=model,
                        options=PipelineOptions(embedding_model="tfidf"))
    res.model = model
    return res


def test_brief_questions_are_answered_with_code(answered):
    res = answered
    assert len(res.question_runs) == 2 and res.model.calls["agent"] == 4  # code, then answer, per question
    for question, run in res.question_runs:
        assert run.status == "answered" and run.executed and run.grounding.fully_traced
    assert res.methods["Brief-specific questions"].startswith("2 of 2 answered by the data agent")
    assert "[Brief question]" in res.all_facts


def test_other_sections_are_told_the_questions_are_answered(answered):
    """The user's report said under Research Gaps that the trend and trade-off questions were 'not covered',
    while the Brief-specific section answered them."""
    prompts = [user for _, user in answered.model.prompts]
    assert prompts and all("never write that they are missing or not covered" in u for u in prompts)
    assert all(q in prompts[0] for q, _ in answered.question_runs)
    system = answered.model.prompts[0][0]
    assert 'say "in this patent set"' in system


def test_data_agent_is_told_how_to_count_and_what_the_set_is(answered):
    from analyst_agent.patents.followup import analysis_dataset, landscape_profile

    profile = landscape_profile(answered, analysis_dataset(answered))
    assert "Split before counting and count patents per value" in profile
    assert "one patent export, not the whole industry" in profile


def test_brief_specific_section_sits_before_the_methodology(answered):
    titles = [b.title for b in answered.blocks]
    start, meth = titles.index("🎯 Brief-specific analyses"), titles.index("Methodology and limitations")
    assert start < meth
    section = answered.blocks[start:meth]
    kinds = [b.kind for b in section]
    assert "code" in kinds and "plotly" in kinds
    assert any(b.title == "Your extra columns" for b in section)
    assert any(b.title == "Weight saving claimed" for b in section)
    question = answered.question_runs[0][0]
    assert question in titles
    page = next(b for b in answered.blocks if b.kind == "download" and b.file_name.endswith(".html")).data.decode()
    assert "🎯 Brief-specific analyses" in page and '<details class="code">' in page and "value_counts" in page


def test_questions_can_be_switched_off_and_need_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    plan = preset_plan("Durability")
    raw = make_export(30, seed=2)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                        options=PipelineOptions(semantic_map=False, answer_questions=False, write_report=False))
    assert res.question_runs == [] and "Brief-specific questions" not in res.methods
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan,
                        options=PipelineOptions(semantic_map=False, write_report=False))
    assert any("brief-specific questions were not answered" in w for w in res.warnings)


def test_html_shows_images_and_code():
    from analyst_agent.patents import report as rb

    page = report_html([rb.heading("T", 1), rb.image(b"\x89PNG fake", title="Chart"), rb.code("print(1 < 2)")], "T").decode()
    assert 'src="data:image/png;base64,' in page and "print(1 &lt; 2)" in page


def test_stop_reaches_the_data_agent_inside_a_question(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    plan = preset_plan("Durability")
    plan.analysis_questions = plan.analysis_questions[:2]
    raw = make_export(30, seed=2)
    model = FakeLLM(plan)
    events = []
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=model,
                        options=PipelineOptions(semantic_map=False, write_report=False),
                        on_event=lambda kind, payload: events.append(kind),
                        should_stop=lambda: model.calls["agent"] >= 1)  # Stop once the agent is at work
    assert res.status == "cancelled" and res.question_runs == []
    assert model.calls["agent"] == 1  # it stopped after its first step, not after finishing the question
    assert "llm_call" in events and "executing" in events  # the agent's steps reach the status bar
