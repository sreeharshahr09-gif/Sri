"""The under-belt layer study template (TYRE-LAYER-LANDSCAPE-01) and the plan options it brought: fixed-value extra
columns, a relevance rule with Partial patents set aside to check, a focus set, a white-space support rule, the
summary's own structure, rules for the data agent, alive status and the Excel workbook."""

import io
import json

import numpy as np
import pandas as pd
import plotly.express as px
import pytest

from analyst_agent.llm import Completion
from analyst_agent.patents import analytics as an
from analyst_agent.patents import enrich
from analyst_agent.patents import narrative as nar
from analyst_agent.patents.data import alive_status, build_corpus, detect_columns
from analyst_agent.patents.followup import analysis_dataset, apply_changes, landscape_profile
from analyst_agent.patents.pipeline import MAX_QUESTIONS, PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan, clean_plan, focus_names
from analyst_agent.patents.presets import BRIEF_CHARS, preset_plan
from analyst_agent.patents.workbook import figure_table, report_workbook

from .patent_fixtures import extract_reply, make_export
from .test_patent_pipeline import FakeLLM

NAME = "Under-belt layer (belt–carcass interlayer)"
FOCUS = ["Footprint shape", "Uniform contact pressure", "Tyre uniformity correction"]


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)


# --------------------------------------------------------------------------- the template as a plan


def test_template_plan_keeps_the_templates_content():
    plan = preset_plan(NAME)
    assert plan.problem_label == "Objective" and [c.name for c in plan.problems][:3] == FOCUS
    assert focus_names(plan) == FOCUS and plan.max_problems == 3 and plan.white_space_support == 5
    assert len(plan.tech_buckets) == 7 and [c.name for c in plan.complexity_levels] == [
        "Basic", "Parametrised", "Multi-parameter", "System-level"]
    assert plan.segments == ["TBR", "PCR", "LT", "OTR"] and plan.check_partial
    assert "tie gum" in plan.in_scope and "squeegee" in plan.in_scope  # out of scope and alternative terms
    assert len(plan.analysis_questions) == 12 <= MAX_QUESTIONS
    assert len(plan.brief) <= BRIEF_CHARS  # the report text reads the brief up to there
    assert "not a legal opinion" in plan.brief and "Specific questions to answer:" in plan.brief
    assert "Focus findings" in plan.summary_format and "insufficient data" in plan.summary_format
    assert plan.questions and "queries" in plan.questions[0]


def test_template_columns_are_fixed_values_read_before_relevance():
    fields = enrich.build_fields(preset_plan(NAME))
    by_key = {f.key: f for f in fields}
    assert by_key["x_placement"].choices == ["BELT-CARCASS", "INTER-BELT", "BELT-TREAD", "CARCASS-LINER", "UNCLEAR"]
    assert by_key["x_coverage"].choices == ["WHOLE_WIDTH", "SEGMENTED", "CENTRE_ONLY", "EDGE_ONLY", "UNCLEAR"]
    assert by_key["x_coverage"].notes["CENTRE_ONLY"] == "a single piece in the crown centre"
    assert by_key["x_thickness_profile"].choices == ["Uniform", "Varies across width", "Not stated"]
    assert by_key["x_segment_details"].choices is None  # free text
    assert by_key["problem"].max_choices == 3
    keys = [f.key for f in fields]
    assert keys.index("x_coverage") < keys.index("relevance") and keys[-2:] == ["relevance", "confidence"]
    system = enrich.system_prompt(preset_plan(NAME), fields)
    assert "by this rule: High = eligible: Placement BELT-CARCASS" in system and "layer.." not in system
    assert "CENTRE_ONLY (a single piece in the crown centre)" in system


def test_standard_plans_keep_their_field_order():
    keys = [f.key for f in enrich.build_fields(preset_plan("Lightweighting"))]
    assert keys.index("relevance") < keys.index("x_weight_saving_claimed")


# --------------------------------------------------------------------------- fixed values


def test_choice_lists_in_descriptions():
    assert enrich.extra_choices("Where it sits. One of: A = first | B | C = third.") == (
        "Where it sits", ["A", "B", "C"], {"A": "first", "C": "third"}, False)
    assert enrich.extra_choices("Any of: x | y")[1:] == (["x", "y"], {}, True)
    assert enrich.extra_choices("the saving with unit (% or kg); 'not stated' if none") is None
    assert enrich.extra_choices("One of: only") is None


def test_unstated_answers_map_to_the_columns_own_value():
    assert enrich.match_choice("not stated", ["Uniform", "Not stated"]) == "Not stated"
    assert enrich.match_choice("unknown", ["BELT-CARCASS", "UNCLEAR"]) == "UNCLEAR"
    assert enrich.match_choice("n/a", ["High", "Low"]) is None
    assert enrich.match_choice("not stated", ["TBR", "Not specified"]) == "Not specified"  # as before
    spec = enrich.FieldSpec("x", "X", "", choices=["Uniform", "Not stated"])
    assert enrich.clean_value(spec, "something else") == ("Not stated", True)
    assert enrich.clean_value(spec, "uniform") == ("Uniform", False)


def test_most_problems_per_patent_keeps_the_first():
    spec = enrich.FieldSpec("problem", "Objective", "", choices=FOCUS + ["Other", "Not specified"], multi=True,
                            max_choices=2)
    assert enrich.clean_value(spec, "Uniform contact pressure; Footprint shape; Tyre uniformity correction")[0] == \
        "Uniform contact pressure; Footprint shape"


# --------------------------------------------------------------------------- legal status


def test_alive_status_from_legal_status_texts():
    assert [alive_status(t) for t in ["Alive", "ALIVE - Granted", "Pending", "Dead", "Expired - fee related",
                                      "Lapsed", "Not in force", "Inactive", "", "Unknown"]] == [
        "Yes", "Yes", "Yes", "No", "No", "No", "No", "No", "Unknown", "Unknown"]
    raw = make_export(20, seed=3)
    raw["Legal Status"] = ["Alive", "Dead"] * (len(raw) // 2) + ["Alive"] * (len(raw) % 2)
    mapping = detect_columns(list(raw.columns))
    assert mapping["legal_status"] == "Legal Status"
    corpus = build_corpus(raw, mapping)
    assert set(corpus.std["alive"]) == {"Yes", "No"}
    assert "alive" in an.build_frame(corpus.std, None, [])
    assert "alive" not in an.build_frame(build_corpus(make_export(20, seed=3), detect_columns(list(raw.columns)[:-1])).std,
                                         None, [])


# --------------------------------------------------------------------------- screening


def test_partial_patents_are_set_aside_only_when_the_plan_says_so():
    frame = pd.DataFrame({"relevance": ["High", "Partial", "Low", None]})
    keep, check = an.screen(frame, AnalysisPlan())
    assert list(keep) == [True, True, False, True] and not check.any()
    keep, check = an.screen(frame, AnalysisPlan(check_partial=True))
    assert list(keep) == [True, False, False, True] and list(check) == [False, True, False, False]


# --------------------------------------------------------------------------- analytics


def _frame(rows):
    out = pd.DataFrame(rows)
    n = len(out)
    defaults = {"id": [f"EP{i}" for i in range(n)], "title": "t", "year": 2020.0, "assignee": "A", "country": "EP",
                "first_claim": "", "cited_by": np.nan, "relevance": "High", "complexity": "Basic"}
    for key, value in defaults.items():
        if key not in out:
            out[key] = value
    return out


def _plan(**kwargs):
    plan = preset_plan(NAME)
    for key, value in kwargs.items():
        setattr(plan, key, value)
    return plan


def test_white_space_needs_support_and_stays_on_the_focus():
    groups = [b.name for b in preset_plan(NAME).tech_buckets]
    rows = ([{"problem": "Footprint shape", "tech_group": groups[0]}] * 12
            + [{"problem": "Belt durability", "tech_group": groups[1]}] * 12
            + [{"problem": "Tyre uniformity correction", "tech_group": groups[2]}] * 2)
    a = _frame(rows)
    ex = an.white_space(a, an.make_context(a, _plan(), "priority date", ref_year=2030))
    table = ex.blocks[0].table
    assert set(table["Objective"]) <= set(FOCUS)  # Belt durability is not a focus objective
    footprint = table[(table["Objective"] == "Footprint shape") & (table["Technology group"] == groups[1])]
    assert footprint["Gap"].iat[0] == "few patents for this combination"  # 12 + 12 patents around it
    thin = table[table["Objective"] == "Tyre uniformity correction"]
    assert thin["Gap"].str.startswith("insufficient data").all()  # 2 + 0 patents around: not an opening
    assert "Insufficient data (not white space" in ex.facts and "Uniform contact pressure" in ex.facts
    assert "Only the focus objective categories" in ex.blocks[0].caption
    # Without the rule the standard flags come back, for every category.
    ex = an.white_space(a, an.make_context(a, _plan(focus=[], white_space_support=0), "priority date", ref_year=2030))
    assert "Belt durability" in set(ex.blocks[0].table["Objective"])
    assert not ex.blocks[0].table["Gap"].str.startswith("insufficient").any()


def test_white_space_check_skips_insufficient_data():
    table = pd.DataFrame({"Objective": ["Footprint shape", "Tyre uniformity correction"],
                          "Technology group": ["Segmented layer", "any"], "Patents": [0, 0],
                          "Expected": [3.0, np.nan], "Gap": ["few patents for this combination",
                                                             "insufficient data: no patents in this set"]})
    ex = an.Exhibit("white_space", "gaps", "White space", [an.rb.table(table)], "")

    class Judge:
        def __init__(self):
            self.users = []

        def chat(self, messages):
            self.users.append(messages[-1]["content"])
            return Completion(content=json.dumps([{"n": 1, "verdict": "plausible", "reason": "can work"}]),
                              latency_s=0.0)

    judge = Judge()
    nar.check_white_space(ex, preset_plan(NAME), judge)
    combinations = judge.users[0].split("Combinations with few or no patents:")[1]
    assert "1. Footprint shape × Segmented layer" in combinations and "Tyre uniformity" not in combinations
    out = ex.blocks[0].table
    assert list(out[nar.CHECK_COLUMN]) == ["plausible: can work", "not judged: insufficient data"]
    assert "Plausible leads: Footprint shape × Segmented layer" in ex.facts
    assert "Insufficient data (too few patents nearby to judge; not white space" in ex.facts


def test_focus_set_leads_the_summary():
    rows = [{"problem": "Footprint shape; Uneven wear", "x_coverage": "WHOLE_WIDTH", "alive": "Yes",
             "assignee": "Michelin", "key_parameters": "thickness 1.0-3.0 mm", "segment": "TBR"}] * 4
    rows += [{"problem": "Tyre uniformity correction", "x_coverage": "SEGMENTED", "alive": "No",
              "assignee": "Bridgestone", "key_parameters": "none", "segment": "Not specified"}] * 2
    rows += [{"problem": "Belt durability", "x_coverage": "WHOLE_WIDTH", "alive": "Yes", "assignee": "Toyo Tire",
              "key_parameters": "none", "segment": "TBR"}] * 4
    a = _frame(rows).assign(tech_group="Segmented layer", core_claim="A tire with a layer.")
    ex = an.focus_set(a, an.make_context(a, _plan(), "priority date", ref_year=2030))
    assert ex.section == "summary" and ex.facts.startswith("Focus set (patents whose objective includes")
    assert "6 of 10 analysed patents (60 %)" in ex.facts
    assert "Legal status of the focus patents: 4 alive, 2 dead" in ex.facts
    assert "Michelin 4 (4 alive); Bridgestone 2 (0 alive)" in ex.facts
    assert "Coverage in the focus set: WHOLE_WIDTH 4 (67 %), SEGMENTED 2 (33 %)" in ex.facts
    assert "Segment not stated: 2 of 10" in ex.facts and "thickness 1.0-3.0 mm" in ex.facts
    best = next(b.table for b in ex.blocks if b.title == "Most relevant focus patents")
    assert list(best["Alive"])[:4] == ["Yes"] * 4 and "Coverage" in best
    assert an.focus_set(a, an.make_context(a, _plan(focus=[]), "priority date", ref_year=2030)) is None


def test_fixed_value_column_is_mapped_against_the_objectives():
    rows = ([{"problem": "Footprint shape", "x_coverage": "WHOLE_WIDTH"}] * 3
            + [{"problem": "Tyre uniformity correction; Footprint shape", "x_coverage": "SEGMENTED"}] * 2
            + [{"problem": "Belt durability", "x_coverage": ""}])
    a = _frame(rows).assign(tech_group="Segmented layer", x_primary_objective="Footprint shape")
    ex = an.extra_columns(a, an.make_context(a, _plan(), "priority date", ref_year=2030))
    coverage = ex.blocks[[b.title for b in ex.blocks].index("Coverage") + 1:]
    assert coverage[0].kind == "plotly" and "Coverage × Objective" in coverage[0].figure.layout.title.text
    grid = coverage[1].table.set_index("Coverage")
    assert grid.loc["SEGMENTED", "Tyre uniformity correction"] == 2 and grid.loc["Not stated", "Belt durability"] == 1
    assert "Coverage (n = 6): WHOLE_WIDTH 3 (50 %), SEGMENTED 2 (33 %), Not stated 1 (17 %)." in ex.facts
    # A column holding the objectives themselves is not mapped against them: a bar chart instead.
    primary = ex.blocks[[b.title for b in ex.blocks].index("Primary objective") + 1]
    assert primary.kind == "plotly" and primary.figure.data[0].type == "bar"


# --------------------------------------------------------------------------- plan handling


def test_rules_survive_saving_and_plan_changes():
    plan = preset_plan(NAME)
    again = AnalysisPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert again == plan
    old = AnalysisPlan.from_dict({"title": "Old saved plan"})  # saved before these options existed
    assert old.focus == [] and not old.check_partial and old.summary_format == ""
    changed, diff = apply_changes(plan, {"title": "Under-belt layer, TBR only"})
    assert changed.relevance_rule == plan.relevance_rule and changed.check_partial and changed.focus == FOCUS
    assert changed.summary_format == plan.summary_format and diff == [
        "Title: “Under-belt rubber layer (belt package–carcass interlayer)” → “Under-belt layer, TBR only”"]
    model = clean_plan({"title": "T", "focus": ["Footprint shape"]}, "brief", fallback=plan)
    assert model.focus == ["Footprint shape"] and model.white_space_support == 5
    assert model.report_sections == plan.report_sections  # the model named none: the plan's own stay


def test_summary_follows_the_plans_structure():
    assert nar.summary_system("") == nar.SUMMARY_SYSTEM
    system = nar.summary_system("1. **Focus findings**\n2. **Caveats**")
    assert "1. **Focus findings**" in system and "5 to 8 bullet points" not in system
    assert "Every number you write must appear in the facts" in system  # the safety rules stay


def test_workbook_tables_and_chart_numbers():
    bar = px.bar(pd.DataFrame({"Office": ["EP", "US"], "Patents": [3, 5]}), x="Office", y="Patents")
    assert figure_table(bar).to_dict("list") == {"Office": ["EP", "US"], "Patents": [3, 5]}
    stacked = px.bar(pd.DataFrame({"a": ["X", "X", "Y"], "p": [1, 2, 3], "s": ["Yes", "No", "Yes"]}),
                     y="a", x="p", color="s", orientation="h")
    assert figure_table(stacked).set_index("a").fillna(0).to_dict() == {"Yes": {"X": 1.0, "Y": 3.0},
                                                                        "No": {"X": 2.0, "Y": 0.0}}
    heat = px.imshow([[1, 0], [2, 4]], x=["g1", "g2"], y=["p1", "p2"])
    assert figure_table(heat).iloc[:, 1:].values.tolist() == [[1, 0], [2, 4]]
    scatter = px.scatter(pd.DataFrame({"x": range(6000), "y": range(6000)}), x="x", y="y")
    assert figure_table(scatter) is None
    blocks = [an.rb.heading("Players"), an.rb.table(pd.DataFrame({"A": ["x\x0by"]}), title="Top: [all]/x?"),
              an.rb.chart(bar)]
    book = pd.read_excel(io.BytesIO(report_workbook(blocks, "T")), sheet_name=None)
    assert list(book) == ["Contents", "01 Top   all  x", "02 Players"] and book["01 Top   all  x"]["A"][0] == "xy"
    assert report_workbook([an.rb.heading("Empty")], "T") is None


# --------------------------------------------------------------------------- a whole run


class TemplateLLM(FakeLLM):
    """Reads each patent the way the template asks: placement, coverage, objectives and relevance by the rule."""

    COVERAGE = ["WHOLE_WIDTH", "SEGMENTED", "WHOLE_WIDTH", "UNCLEAR", "SEGMENTED"]

    def chat(self, messages):
        system = messages[0]["content"]
        if "You extract structured facts from patents" not in system:
            return super().chat(messages)
        self.calls["extract"] += 1
        items = json.loads(extract_reply(system, messages[-1]["content"]))
        for item in items:
            ref = int(item["ref"][1:])
            coverage = self.COVERAGE[ref % 5]
            item.update(x_placement="BELT-CARCASS", x_coverage=coverage, x_primary_objective=FOCUS[ref % 3],
                        x_thickness_profile="Varies across width", x_segment_details="centre and two shoulder pieces"
                        if coverage == "SEGMENTED" else "not stated",
                        problem=f"{FOCUS[ref % 3]}; Belt durability",
                        relevance="Low" if item["relevance"] == "Low" else "Partial" if coverage == "UNCLEAR" else "High")
        return Completion(content=json.dumps(items), latency_s=0.0)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    enrich.CACHE_DIR = tmp_path_factory.mktemp("cache")
    plan = preset_plan(NAME)
    plan.analysis_questions = plan.analysis_questions[:1]
    raw = make_export(90, seed=5)
    raw["Legal Status"] = ["Alive" if i % 3 else "Dead" for i in range(len(raw))]
    model = TemplateLLM(plan)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=model,
                        options=PipelineOptions(embedding_model="tfidf"))
    res.model = model
    return res


def test_run_sets_partial_patents_aside_and_lists_them(run):
    flow = [f.step for f in run.corpus.flow]
    assert "Set aside for a manual check (Relevance: Partial), not counted" in flow
    check = run.check_frame
    assert len(check) and (check["relevance"] == "Partial").all() and (check["x_coverage"] == "UNCLEAR").all()
    assert not run.analysis_frame.index.isin(check.index).any()
    titles = [b.title for b in run.blocks]
    at = titles.index("🔍 Records to check (not counted)")
    table = run.blocks[at + 1].table
    assert len(table) == len(check) and {"Coverage", "Placement", "Alive", "Confidence"} <= set(table.columns)
    assert at < titles.index("Methodology and limitations")
    assert len(analysis_dataset(run).df) == len(run.analysis_frame)  # the data agent counts what the report counts
    profile = landscape_profile(run, analysis_dataset(run))
    assert "set aside for a manual check" in profile and "Count patent families" in profile
    assert "'Alive (from legal status)' is Yes / No / Unknown" in profile


def test_run_report_follows_the_template(run):
    sections = [s.heading for s in run.sections]
    assert sections[:2] == ["📊 Executive summary", "📈 Filing trends and geography"]
    summary_system = next(system for system, user in run.model.prompts if "Section to write: 📊" in user)
    assert "1. **Focus findings**" in summary_system and "Recommended R&D directions" not in summary_system
    assert run.all_facts.split("[")[1].startswith("Focus set: ")  # the focus leads the summary's facts
    titles = [b.title for b in run.blocks]
    assert "Focus set: " + ", ".join(FOCUS) in titles and "Coverage" in titles
    mapping = next(b for b in run.blocks if b.title == "Columns used from the export")
    assert mapping.table.set_index("Field").loc["Legal status", "Export column"] == "Legal Status"
    assert "Alive (from legal status)" in run.enriched
    downloads = {b.file_name for b in run.blocks if b.kind == "download"}
    assert any(name.endswith("_tables.xlsx") for name in downloads)
    defs = next(b.table for b in run.blocks if b.title == "Definitions").set_index("Measure")["Definition"]
    assert "Only the focus categories are assessed" in defs["White space"]
    assert "set aside for a manual check" in defs["Off-topic screening"]


# --------------------------------------------------------------------------- generous budgets


def test_long_patent_text_reads_whole_claims():
    claim = "1. A pneumatic tire comprising " + "a layer of rubber between belt and carcass, " * 60 + "wherein t = 2 mm."
    row = pd.Series({"id": "EP1", "year": 2020, "assignee": "A", "title": "T", "abstract": "x " * 900,
                     "first_claim": claim})
    standard, long = enrich.patent_prompt(row), enrich.patent_prompt(row, long=True)
    assert "…" in standard.split("First claim: ")[1] and "…" not in long.split("First claim: ")[1]
    assert long.split("First claim: ")[1].endswith("wherein t = 2 mm.")
    assert enrich.batch_size_for(16384) == 6 and enrich.batch_size_for(16384, long=True) == 4
    assert preset_plan(NAME).long_text and not preset_plan("Lightweighting").long_text


def test_report_text_budget_follows_the_context_window():
    from analyst_agent import LLMClient, LLMConfig

    assert nar.text_budget(object()) == (nar.BRIEF_CHARS, nar.MAX_FACTS)  # a test double: the small defaults
    assert nar.text_budget(LLMClient(LLMConfig(context_tokens=8192))) == (5000, 9000)
    assert nar.text_budget(LLMClient(LLMConfig(context_tokens=32768))) == (12000, 40000)
    assert nar.section_reply_tokens(LLMConfig(context_tokens=8192, max_tokens=2048)) == 2730
    assert nar.section_reply_tokens(LLMConfig(context_tokens=65536, max_tokens=2048)) == 6000
